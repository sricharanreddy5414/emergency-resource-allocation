import json
from datetime import datetime, timezone

from botocore.exceptions import ClientError

from access import (
    OPERATE_ROLES,
    READ_ROLES,
    AccessError,
    authorize,
    query_by_organization,
    query_history,
    require_location,
    require_owned,
)
from common import ALLOWED_ORIGIN


def response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": ALLOWED_ORIGIN,
            "Access-Control-Allow-Headers": "Content-Type,Authorization",
            "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
        },
        "body": json.dumps(body, default=str),
    }


def parse_body(event):
    body = event.get("body") or "{}"

    if event.get("isBase64Encoded") and isinstance(body, str):
        import base64

        body = base64.b64decode(body).decode("utf-8")

    if isinstance(body, str):
        body = json.loads(body)

    if not isinstance(body, dict):
        raise ValueError("JSON body must be an object")

    return body


def resources_table():
    import boto3

    return boto3.resource("dynamodb").Table("Resources")


def history_table():
    import boto3

    return boto3.resource("dynamodb").Table("ResourceStatusHistory")


def allocations_table():
    import boto3

    return boto3.resource("dynamodb").Table("Allocations")


def requests_table():
    import boto3

    return boto3.resource("dynamodb").Table("EmergencyRequests")


def locations_table():
    import os

    import boto3

    return boto3.resource("dynamodb").Table(os.environ.get("LOCATIONS_TABLE", "Locations"))


def events_client():
    import boto3

    return boto3.client("events")


def is_available(value):
    if isinstance(value, str):
        return value.lower() == "true"

    return value is True


def lambda_handler(event, context):
    method = (
        event.get("httpMethod")
        or event.get("requestContext", {}).get("http", {}).get("method")
        or "GET"
    ).upper()
    path = event.get("path") or event.get("rawPath") or ""

    if method == "OPTIONS":
        return response(200, {"message": "CORS OK"})

    try:
        body = parse_body(event) if method == "POST" else {}
        roles = OPERATE_ROLES if method == "POST" else READ_ROLES
        _user_sub, membership = authorize(event, body, allowed_roles=roles)
        organization_id = membership["organization_id"]

        if method == "POST" and path.endswith("/release"):
            return release_resource(body, organization_id)

        if method == "GET" and path.endswith("/history"):
            return resource_history(event, organization_id)

        if method == "GET":
            return list_resources(event, organization_id)

        if method == "POST":
            return register_resource(body, organization_id)

        return response(405, {"message": "Method not allowed"})
    except AccessError as error:
        return response(error.status_code, {"message": error.message})
    except json.JSONDecodeError:
        return response(400, {"message": "Invalid JSON body"})
    except ValueError as error:
        return response(400, {"message": str(error)})
    except Exception as error:
        print("Resource error:", error.__class__.__name__)
        return response(500, {"message": "Failed to process resource request"})


def list_resources(event, organization_id):
    query = event.get("queryStringParameters") or {}
    location_id = str(query.get("location_id") or "").strip()

    if location_id:
        require_location(locations_table(), organization_id, location_id)

    resources = query_by_organization(
        resources_table(),
        organization_id,
        location_id or None,
    )
    return response(200, resources)


def resource_history(event, organization_id):
    query = event.get("queryStringParameters") or {}
    resource_id = str(query.get("resource_id") or "").strip()

    if not resource_id:
        return response(400, {"message": "resource_id is required"})

    resource = resources_table().get_item(Key={"resource_id": resource_id}).get("Item")
    require_owned(resource, organization_id)
    histories = query_history(history_table(), resource_id)
    histories.sort(key=lambda item: item.get("changed_at", ""), reverse=True)
    return response(200, histories)


def register_resource(body, organization_id):
    resource_id = str(body.get("resource_id") or "").strip()
    resource_type = str(body.get("Type") or "").strip()
    location_id = str(body.get("location_id") or "").strip()

    if not resource_id or not resource_type or not location_id:
        return response(400, {"message": "resource_id, Type and location_id are required"})

    location = require_location(locations_table(), organization_id, location_id)
    available = body.get("Available", True)

    if isinstance(available, str):
        available = available.lower() == "true"

    item = {
        "resource_id": resource_id,
        "Type": resource_type,
        "Location": location.get("name", ""),
        "location_id": location["location_id"],
        "organization_id": organization_id,
        "Available": bool(available),
    }

    try:
        resources_table().put_item(
            Item=item,
            ConditionExpression="attribute_not_exists(resource_id)",
        )
    except ClientError as error:
        if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return response(409, {"message": "Resource ID already exists"})

        print("Resource registration error:", error.response["Error"]["Code"])
        return response(500, {"message": "Failed to register resource"})

    return response(201, {"message": "Resource registered successfully", "resource": item})


def release_resource(body, organization_id):
    resource_id = str(body.get("resource_id") or "").strip()

    if not resource_id:
        return response(400, {"message": "resource_id is required"})

    resource = resources_table().get_item(Key={"resource_id": resource_id}).get("Item")
    require_owned(resource, organization_id)

    if is_available(resource.get("Available", False)):
        return response(409, {"message": "Resource is already available"})

    active = [
        item
        for item in query_by_organization(allocations_table(), organization_id)
        if item.get("resource_id") == resource_id
        and str(item.get("status", "")).upper() == "ALLOCATED"
    ]

    if not active:
        return response(409, {"message": "No active allocation found for resource"})

    active.sort(key=lambda item: item.get("allocated_at", ""), reverse=True)
    allocation = active[0]
    allocation_id = allocation.get("allocation_id")
    request_id = allocation.get("request_id")

    if not allocation_id or not request_id:
        return response(409, {"message": "Active allocation data is incomplete"})

    request = requests_table().get_item(Key={"request_id": request_id}).get("Item")
    require_owned(request, organization_id)

    if (
        allocation.get("organization_id") != organization_id
        or resource.get("organization_id") != organization_id
        or request.get("organization_id") != organization_id
    ):
        return response(404, {"message": "Record not found"})

    released_at = datetime.now(timezone.utc).isoformat()

    try:
        resources_table().update_item(
            Key={"resource_id": resource_id},
            UpdateExpression="SET Available = :available",
            ConditionExpression="attribute_exists(resource_id) AND Available = :allocated AND organization_id = :organization_id",
            ExpressionAttributeValues={
                ":available": True,
                ":allocated": False,
                ":organization_id": organization_id,
            },
        )
        allocations_table().update_item(
            Key={"allocation_id": allocation_id},
            UpdateExpression="SET #status = :released, released_at = :released_at",
            ConditionExpression="attribute_exists(allocation_id) AND #status = :allocated AND organization_id = :organization_id",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":released": "RELEASED",
                ":allocated": "ALLOCATED",
                ":released_at": released_at,
                ":organization_id": organization_id,
            },
        )
        requests_table().update_item(
            Key={"request_id": request_id},
            UpdateExpression="SET #status = :released",
            ConditionExpression="attribute_exists(request_id) AND #status = :allocated AND organization_id = :organization_id",
            ExpressionAttributeNames={"#status": "Status"},
            ExpressionAttributeValues={
                ":released": "RELEASED",
                ":allocated": "ALLOCATED",
                ":organization_id": organization_id,
            },
        )
    except ClientError as error:
        print("Resource release error:", error.response["Error"]["Code"])
        return response(409, {"message": "Resource could not be released"})

    history_table().put_item(
        Item={
            "history_id": "HIST-RELEASE-" + resource_id + "-" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f"),
            "resource_id": resource_id,
            "organization_id": organization_id,
            "location_id": resource.get("location_id", ""),
            "resource_type": resource.get("Type", ""),
            "location": resource.get("Location", ""),
            "previous_status": "ALLOCATED",
            "new_status": "AVAILABLE",
            "changed_at": released_at,
            "reason": "RESOURCE_RELEASED",
            "allocation_id": allocation_id,
            "request_id": request_id,
        }
    )

    try:
        events_client().put_events(
            Entries=[
                {
                    "Source": "emergency.resource.allocation",
                    "DetailType": "ResourceReleased",
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
    except Exception as error:
        print("Release event error:", error.__class__.__name__)

    return response(
        200,
        {
            "message": "Resource released successfully",
            "resource": {"resource_id": resource_id, "Available": True},
            "allocation": {"allocation_id": allocation_id, "status": "RELEASED"},
            "request": {"request_id": request_id, "status": "RELEASED"},
        },
    )
