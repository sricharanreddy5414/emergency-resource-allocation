import json
import os
from datetime import datetime, timedelta, timezone

import boto3
from botocore.exceptions import ClientError

from audit import build_audit_event, record_audit
from observability import begin_request, log_result


RELEASE_AFTER_MINUTES = 30
STATUS_INDEX = "AllocationStatusIndex"


def response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, default=str),
    }


def tables():
    dynamodb = boto3.resource("dynamodb")
    return {
        "resources": dynamodb.Table("Resources"),
        "allocations": dynamodb.Table("Allocations"),
        "requests": dynamodb.Table("EmergencyRequests"),
        "history": dynamodb.Table("ResourceStatusHistory"),
        "audit": dynamodb.Table(os.environ.get("AUDIT_TABLE", "AuditEvents")),
    }


def plan_release(allocation, resource, request, other_allocations):
    """Decide one allocation without reading or writing DynamoDB."""
    allocation_id = allocation.get("allocation_id")

    if str(allocation.get("status", "")).upper() != "ALLOCATED":
        return {"action": "skip", "reason": "already released", "allocation_id": allocation_id}

    organization_id = allocation.get("organization_id")

    if not organization_id:
        return {"action": "skip", "reason": "missing organization", "allocation_id": allocation_id}

    if not resource or resource.get("organization_id") != organization_id:
        return {"action": "skip", "reason": "resource mismatch", "allocation_id": allocation_id}

    if not request or request.get("organization_id") != organization_id:
        return {"action": "skip", "reason": "request mismatch", "allocation_id": allocation_id}

    if resource.get("resource_id") != allocation.get("resource_id"):
        return {"action": "skip", "reason": "resource mismatch", "allocation_id": allocation_id}

    if request.get("request_id") != allocation.get("request_id"):
        return {"action": "skip", "reason": "request mismatch", "allocation_id": allocation_id}

    others = [
        item
        for item in other_allocations
        if item.get("allocation_id") != allocation_id
        and item.get("resource_id") == allocation.get("resource_id")
        and item.get("organization_id") == organization_id
        and str(item.get("status", "")).upper() == "ALLOCATED"
    ]

    return {
        "action": "release",
        "allocation_id": allocation_id,
        "organization_id": organization_id,
        "free_resource": not others,
    }


def query_expired(allocations_table, cutoff):
    kwargs = {
        "IndexName": STATUS_INDEX,
        "KeyConditionExpression": "#status = :allocated AND allocated_at < :cutoff",
        "ExpressionAttributeNames": {"#status": "status"},
        "ExpressionAttributeValues": {
            ":allocated": "ALLOCATED",
            ":cutoff": cutoff.isoformat(),
        },
    }
    result = allocations_table.query(**kwargs)
    items = result.get("Items", [])

    while "LastEvaluatedKey" in result:
        result = allocations_table.query(
            ExclusiveStartKey=result["LastEvaluatedKey"],
            **kwargs,
        )
        items.extend(result.get("Items", []))

    return items


def release_allocation(store, allocation, now):
    organization_id = allocation.get("organization_id")

    if not organization_id:
        return plan_release(allocation, None, None, [])

    resource_id = allocation.get("resource_id")
    request_id = allocation.get("request_id")
    resource = store["resources"].get_item(Key={"resource_id": resource_id}).get("Item")
    request = store["requests"].get_item(Key={"request_id": request_id}).get("Item")
    related = store["allocations"].query(
        IndexName="OrganizationLocationIndex",
        KeyConditionExpression="organization_id = :organization_id",
        ExpressionAttributeValues={":organization_id": organization_id},
    ).get("Items", [])
    decision = plan_release(allocation, resource, request, related)

    if decision["action"] != "release":
        return decision

    allocation_id = decision["allocation_id"]

    try:
        store["allocations"].update_item(
            Key={"allocation_id": allocation_id},
            UpdateExpression="SET #status = :released, released_at = :released_at",
            ConditionExpression="#status = :allocated AND organization_id = :organization_id",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":released": "RELEASED",
                ":allocated": "ALLOCATED",
                ":released_at": now.isoformat(),
                ":organization_id": organization_id,
            },
        )
    except ClientError as error:
        if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return {"action": "skip", "reason": "already released", "allocation_id": allocation_id}

        raise

    if decision["free_resource"]:
        try:
            store["resources"].update_item(
                Key={"resource_id": resource_id},
                UpdateExpression="SET Available = :available",
                ConditionExpression="organization_id = :organization_id AND Available = :held AND attribute_exists(resource_id)",
                ExpressionAttributeValues={
                    ":available": True,
                    ":held": False,
                    ":organization_id": organization_id,
                },
            )
        except ClientError as error:
            if error.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise

    if request_id:
        try:
            store["requests"].update_item(
                Key={"request_id": request_id},
                UpdateExpression="SET #status = :released",
                ConditionExpression="#status = :allocated AND organization_id = :organization_id",
                ExpressionAttributeNames={"#status": "Status"},
                ExpressionAttributeValues={
                    ":released": "RELEASED",
                    ":allocated": "ALLOCATED",
                    ":organization_id": organization_id,
                },
            )
        except ClientError as error:
            if error.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise

    store["history"].put_item(
        Item={
            "history_id": "HIST-AUTO-" + allocation_id + "-" + now.strftime("%Y%m%d%H%M%S%f"),
            "resource_id": resource_id,
            "organization_id": organization_id,
            "location_id": allocation.get("location_id", ""),
            "previous_status": "ALLOCATED",
            "new_status": "AVAILABLE" if decision["free_resource"] else "ALLOCATED",
            "changed_at": now.isoformat(),
            "reason": "AUTOMATIC_RESOURCE_RELEASE",
            "request_id": request_id,
            "allocation_id": allocation_id,
        }
    )
    record_audit(
        store.get("audit"),
        build_audit_event(
            organization_id,
            "system",
            "SYSTEM",
            "allocation.auto_release",
            "allocation",
            allocation_id,
            location_id=allocation.get("location_id", ""),
            metadata={"free_resource": decision["free_resource"]},
        ),
    )
    return decision


def lambda_handler(event, context, store=None, now=None):
    begin_request(event or {})
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=RELEASE_AFTER_MINUTES)
    store = store or tables()
    released = []
    skipped = []

    for allocation in query_expired(store["allocations"], cutoff):
        decision = release_allocation(store, allocation, now)

        if decision.get("action") == "release":
            released.append({"allocation_id": decision["allocation_id"]})
        else:
            skipped.append(
                {
                    "allocation_id": decision.get("allocation_id"),
                    "reason": decision.get("reason"),
                }
            )

    log_result(200, operation="allocation.auto_release", entity_type="allocation", entity_id=str(len(released)))
    return response(
        200,
        {
            "message": "Automatic release process completed",
            "released_count": len(released),
            "released": released,
            "skipped": skipped,
        },
    )
