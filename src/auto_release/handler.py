import json
import os
from datetime import datetime, timedelta, timezone

import boto3
from botocore.exceptions import ClientError

from audit import build_audit_event, record_audit
from emergency_release import commit_emergency_release, emergency_release_conflict
from observability import begin_request, log_event, log_result


RELEASE_AFTER_MINUTES = 30
STATUS_INDEX = "AllocationStatusIndex"
HOLDER_INDEX = "OrganizationLocationIndex"
# The holder index is partitioned by organization only, so one due allocation
# can sit behind a long history. Each query page is capped, and so is the
# number of pages. Stopping at the cap is incomplete: the worker skips that
# release and leaves the resource held. A missing later page is not evidence
# that no other allocation still holds it.
HOLDER_PAGE_SIZE = 100
HOLDER_PAGE_LIMIT = 25
RELEASED_EVENT_SOURCE = "emergency.resource.allocation"
RELEASED_EVENT_DETAIL = "ResourceReleased"


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


def events_client():
    return boto3.client("events")


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


def lookup_other_holders(allocations_table, allocation):
    """Page this organization's allocations until another holder is known.

    The query key is the allocation's own organization. A holder on a later
    page keeps the resource held. The lookup stops early once that holder is
    found. If the page cap is reached first, the result is incomplete.
    """
    organization_id = allocation.get("organization_id")
    resource_id = allocation.get("resource_id")
    allocation_id = allocation.get("allocation_id")
    holders = []
    start_key = None
    pages = 0

    while pages < HOLDER_PAGE_LIMIT:
        kwargs = {
            "IndexName": HOLDER_INDEX,
            "KeyConditionExpression": "organization_id = :organization_id",
            "ExpressionAttributeValues": {":organization_id": organization_id},
            "Limit": HOLDER_PAGE_SIZE,
        }
        if start_key:
            kwargs["ExclusiveStartKey"] = start_key
        result = allocations_table.query(**kwargs)
        pages += 1
        for item in result.get("Items") or []:
            if item.get("organization_id") != organization_id:
                continue
            if item.get("allocation_id") == allocation_id:
                continue
            if item.get("resource_id") != resource_id:
                continue
            if str(item.get("status", "")).upper() != "ALLOCATED":
                continue
            holders.append(item)
            return {"complete": True, "holders": holders}
        start_key = result.get("LastEvaluatedKey")
        if not start_key:
            return {"complete": True, "holders": holders}

    return {"complete": False, "holders": holders}


def emit_resource_released(resource_id, allocation_id, request_id, organization_id):
    """Publish the same ResourceReleased event manual release publishes."""
    result = events_client().put_events(
        Entries=[
            {
                "Source": RELEASED_EVENT_SOURCE,
                "DetailType": RELEASED_EVENT_DETAIL,
                "Detail": json.dumps(
                    {
                        "resource_id": resource_id,
                        "allocation_id": allocation_id,
                        "request_id": request_id,
                        "organization_id": organization_id,
                        "status": "RELEASED",
                    }
                ),
                "EventBusName": "default",
            }
        ]
    )
    if int((result or {}).get("FailedEntryCount") or 0):
        log_event(
            "WARNING",
            "auto-release",
            "allocation.auto_release",
            "event_failed",
            error_code="FailedEntryCount",
            allocation_id=allocation_id,
        )


def release_allocation(store, allocation, now):
    organization_id = allocation.get("organization_id")

    if not organization_id:
        return plan_release(allocation, None, None, [])

    resource_id = allocation.get("resource_id")
    request_id = allocation.get("request_id")
    resource = store["resources"].get_item(Key={"resource_id": resource_id}).get("Item")
    request = store["requests"].get_item(Key={"request_id": request_id}).get("Item")
    try:
        lookup = lookup_other_holders(store["allocations"], allocation)
    except ClientError as error:
        code = error.response.get("Error", {}).get("Code", "")
        log_event(
            "WARNING",
            "auto-release",
            "allocation.auto_release",
            "skipped",
            error_code=code,
            allocation_id=allocation.get("allocation_id"),
        )
        return {
            "action": "skip",
            "reason": "holder lookup failed",
            "allocation_id": allocation.get("allocation_id"),
        }

    if not lookup["complete"]:
        log_event(
            "WARNING",
            "auto-release",
            "allocation.auto_release",
            "skipped",
            error_code="HolderLookupIncomplete",
            allocation_id=allocation.get("allocation_id"),
        )
        return {
            "action": "skip",
            "reason": "holder lookup incomplete",
            "allocation_id": allocation.get("allocation_id"),
        }

    decision = plan_release(allocation, resource, request, lookup["holders"])

    if decision["action"] != "release":
        return decision

    allocation_id = decision["allocation_id"]

    try:
        commit_emergency_release(
            resource_id=resource_id,
            allocation_id=allocation_id,
            request_id=request_id,
            organization_id=organization_id,
            released_at=now.isoformat(),
            free_resource=bool(decision["free_resource"]),
        )
    except ClientError as error:
        code = error.response.get("Error", {}).get("Code", "")
        if code not in {"TransactionCanceledException", "ConditionalCheckFailedException"}:
            raise
        log_event(
            "WARNING",
            "auto-release",
            "allocation.auto_release",
            "skipped",
            error_code=code,
            allocation_id=allocation_id,
        )
        return {
            "action": "skip",
            "reason": emergency_release_conflict(error, free_resource=bool(decision["free_resource"])),
            "allocation_id": allocation_id,
        }

    try:
        emit_resource_released(resource_id, allocation_id, request_id, organization_id)
    except Exception as error:
        log_event(
            "WARNING",
            "auto-release",
            "allocation.auto_release",
            "event_failed",
            error_code=error.__class__.__name__,
            allocation_id=allocation_id,
        )

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
            "actor_sub": "system",
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
        try:
            decision = release_allocation(store, allocation, now)
        except Exception as error:
            log_event(
                "ERROR",
                "auto-release",
                "allocation.auto_release",
                "skipped",
                error_code=error.__class__.__name__,
                allocation_id=allocation.get("allocation_id"),
            )
            skipped.append(
                {
                    "allocation_id": allocation.get("allocation_id"),
                    "reason": "processing failed",
                }
            )
            continue

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
