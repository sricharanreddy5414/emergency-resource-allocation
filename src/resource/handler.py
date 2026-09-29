import json
import re
from datetime import datetime, timezone

from botocore.exceptions import ClientError

from access import (
    OPERATE_ROLES,
    READ_ROLES,
    AccessError,
    access_body,
    authorize,
    query_by_organization,
    query_history,
    require_location,
    require_owned,
)
from attributes import validate_attributes
from audit import build_audit_event, record_audit
from common import ALLOWED_ORIGIN
from observability import begin_request, error_body, load_object, log_result
from pages import decode_token, encode_token
from everyday_operations import EverydayOperationError, dispatch_everyday
from lifecycle_operations import LifecycleOperationError, dispatch_lifecycle
from resource_state import (
    ResourceStateError,
    initialize_new_resource_fields,
    lifecycle_fields_from_body,
    metadata_fields_from_body,
)
from visibility import PRIVATE_INDEX_ATTRIBUTES, publication_fields


def response(status_code, body):
    payload = error_body(status_code, body)
    if isinstance(payload, dict):
        log_result(status_code, operation="resource", error_code=payload.get("error", {}).get("code", ""))
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": ALLOWED_ORIGIN,
            "Access-Control-Allow-Headers": "Content-Type,Authorization",
            "Access-Control-Allow-Methods": "GET,POST,PUT,OPTIONS",
        },
        "body": json.dumps(payload, default=str),
    }


def parse_body(event):
    return load_object(event.get("body") or "{}", event.get("isBase64Encoded"))


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


def audit_table():
    import os

    import boto3

    return boto3.resource("dynamodb").Table(os.environ.get("AUDIT_TABLE", "AuditEvents"))


def resource_types_table():
    import os

    import boto3

    return boto3.resource("dynamodb").Table(os.environ.get("RESOURCE_TYPES_TABLE", "ResourceTypes"))


def everyday_route(body, path, organization_id, actor_sub, actor_role):
    def load_resource(resource_id):
        item = resources_table().get_item(Key={"resource_id": resource_id}).get("Item")
        require_owned(item, organization_id)
        return item

    tables = {
        "resources": resources_table(),
        "allocations": allocations_table(),
        "history": history_table(),
        "audit": audit_table(),
    }

    try:
        if any(
            path.endswith(suffix)
            for suffix in (
                "/maintenance/complete",
                "/maintenance",
                "/damage/recover",
                "/damage",
                "/retire",
                "/in-use/return",
                "/in-use",
                "/assign",
                "/unassign",
            )
        ):
            result = dispatch_lifecycle(
                path,
                body,
                organization_id,
                actor_sub,
                actor_role,
                load_resource,
                tables,
            )
        else:
            result = dispatch_everyday(
                "POST",
                path,
                body,
                organization_id,
                actor_sub,
                actor_role,
                load_resource,
                tables,
            )
    except EverydayOperationError as error:
        return response(error.status_code, {"message": error.message})
    except LifecycleOperationError as error:
        return response(error.status_code, {"message": error.message})

    return response(200, result)


def is_available(value):
    if isinstance(value, str):
        return value.lower() == "true"

    return value is True


def lambda_handler(event, context):
    begin_request(event)
    method = (
        event.get("httpMethod")
        or event.get("requestContext", {}).get("http", {}).get("method")
        or "GET"
    ).upper()
    path = event.get("path") or event.get("rawPath") or ""

    if method == "OPTIONS":
        return response(200, {"message": "CORS OK"})

    try:
        body = parse_body(event) if method in {"POST", "PUT"} else {}
        writing = method in {"POST", "PUT"}
        roles = OPERATE_ROLES if writing else READ_ROLES
        _user_sub, membership = authorize(
            event,
            body,
            allowed_roles=roles,
            access="write" if writing else "read",
        )
        organization_id = membership["organization_id"]

        if method == "POST" and path.endswith("/release"):
            return release_resource(body, organization_id, _user_sub, membership.get("role"))

        if method == "POST" and (
            path.endswith("/reserve")
            or path.endswith("/reservation-release")
            or path.endswith("/everyday")
            or path.endswith("/everyday/return")
            or path.endswith("/maintenance/complete")
            or path.endswith("/maintenance")
            or path.endswith("/damage/recover")
            or path.endswith("/damage")
            or path.endswith("/retire")
            or path.endswith("/in-use/return")
            or path.endswith("/in-use")
            or path.endswith("/assign")
            or path.endswith("/unassign")
        ):
            return everyday_route(body, path, organization_id, _user_sub, membership.get("role"))

        if method == "GET" and path.endswith("/history"):
            return resource_history(event, organization_id)

        if method == "GET":
            return list_resources(event, organization_id)

        if method == "PUT":
            return update_resource(body, organization_id, _user_sub, membership.get("role"))

        if method == "POST":
            return register_resource(body, organization_id, _user_sub, membership.get("role"))

        return response(405, {"message": "Method not allowed"})
    except AccessError as error:
        return response(error.status_code, access_body(error))
    except json.JSONDecodeError:
        return response(400, {"message": "Invalid JSON body"})
    except ValueError as error:
        return response(400, {"message": str(error)})
    except ResourceStateError as error:
        return response(400, {"message": str(error)})
    except EverydayOperationError as error:
        return response(error.status_code, {"message": error.message})
    except LifecycleOperationError as error:
        return response(error.status_code, {"message": error.message})
    except Exception as error:
        print("Resource error:", error.__class__.__name__)
        return response(500, {"message": "Failed to process resource request"})


def list_resources(event, organization_id):
    from boto3.dynamodb.conditions import Key

    query = event.get("queryStringParameters") or {}
    location_id = str(query.get("location_id") or "").strip()
    resource_type_id = str(query.get("resource_type_id") or "").strip()
    visibility = str(query.get("visibility") or "").strip().upper()
    status = str(query.get("status") or "").strip().upper()

    if location_id:
        require_location(locations_table(), organization_id, location_id)

    if visibility and visibility not in {"PRIVATE", "PUBLIC", "NETWORK"}:
        return response(400, {"message": "Visibility is invalid"})

    if status and status not in {"AVAILABLE", "ALLOCATED"}:
        return response(400, {"message": "Status is invalid"})

    try:
        limit = int(query.get("limit") or 100)
    except (TypeError, ValueError):
        return response(400, {"message": "Page size is invalid"})

    if limit < 1 or limit > 100:
        return response(400, {"message": "Page size is invalid"})

    start = decode_token(query.get("page_token"), ["organization_id", "location_id", "resource_id"])

    if start and start.get("organization_id") != organization_id:
        return response(400, {"message": "Invalid page token"})

    key = Key("organization_id").eq(organization_id)

    if location_id:
        key = key & Key("location_id").eq(location_id)

    kwargs = {
        "IndexName": "OrganizationLocationIndex",
        "KeyConditionExpression": key,
        "Limit": limit,
    }

    if start:
        kwargs["ExclusiveStartKey"] = start

    result = resources_table().query(**kwargs)
    resources = []

    for item in result.get("Items", []):
        if item.get("organization_id") != organization_id:
            continue

        if resource_type_id and item.get("resource_type_id") != resource_type_id and item.get("Type") != resource_type_id:
            continue

        if visibility and (item.get("visibility") or "PRIVATE") != visibility:
            continue

        if status == "AVAILABLE" and not is_available(item.get("Available")):
            continue

        if status == "ALLOCATED" and is_available(item.get("Available")):
            continue

        resources.append(item)

    payload = resources
    token = encode_token(result.get("LastEvaluatedKey"))

    if query.get("limit") or query.get("page_token"):
        payload = {"resources": resources, "next_token": token}

    return response(200, payload)


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


def active_resource_type(organization_id, resource_type_id):
    if not resource_type_id:
        raise AccessError(400, "Resource type is required")

    item = resource_types_table().get_item(
        Key={"organization_id": organization_id, "resource_type_id": resource_type_id}
    ).get("Item")

    if not item or item.get("organization_id") != organization_id or item.get("status") != "ACTIVE":
        raise AccessError(404, "Resource type not found")

    return item


def register_resource(body, organization_id, actor_sub="", actor_role=""):
    resource_id = str(body.get("resource_id") or "").strip()
    resource_type_id = str(body.get("resource_type_id") or "").strip()
    location_id = str(body.get("location_id") or "").strip()
    name = str(body.get("name") or "").strip()

    if not resource_id or not location_id:
        return response(400, {"message": "resource_id and location_id are required"})

    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", resource_id):
        return response(400, {"message": "resource_id is invalid"})

    if len(resource_id) > 80 or len(name) > 80:
        return response(400, {"message": "A resource field is too long"})

    location = require_location(locations_table(), organization_id, location_id)
    resource_type = active_resource_type(organization_id, resource_type_id)
    attributes = validate_attributes(body.get("attributes") or {}, resource_type.get("attributes_schema"))
    available = body.get("Available", True)

    if isinstance(available, str):
        available = available.lower() == "true"

    available = bool(available)

    try:
        state_fields = initialize_new_resource_fields(body, available=available)
    except ResourceStateError as error:
        return response(400, {"message": str(error)})

    item = {
        "resource_id": resource_id,
        "name": name or resource_type.get("name", ""),
        "Type": resource_type.get("name", ""),
        "resource_type_id": resource_type["resource_type_id"],
        "Location": location.get("name", ""),
        "location_id": location["location_id"],
        "organization_id": organization_id,
        "Available": available,
        "attributes": attributes,
    }
    item.update(state_fields)

    try:
        item.update(
            publication_fields(
                body,
                location,
                item["Type"],
                resource_id,
                is_available(item.get("Available")),
                operational_status=item.get("operational_status"),
            )
        )
    except ValueError as error:
        return response(400, {"message": str(error)})

    if item.get("visibility") in {"PRIVATE", "NETWORK"}:
        for attribute in PRIVATE_INDEX_ATTRIBUTES:
            item.pop(attribute, None)

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

    record_audit(
        audit_table(),
        build_audit_event(
            organization_id,
            actor_sub,
            actor_role,
            "resource.create",
            "resource",
            item["resource_id"],
            location_id=item["location_id"],
            metadata={"resource_type_id": item["resource_type_id"], "visibility": item["visibility"]},
        ),
    )
    return response(201, {"message": "Resource registered successfully", "resource": item})


def update_resource(body, organization_id, actor_sub="", actor_role=""):
    resource_id = str(body.get("resource_id") or "").strip()

    if lifecycle_fields_from_body(body):
        return response(400, {"message": "Resource state cannot be changed through update"})

    current = resources_table().get_item(Key={"resource_id": resource_id}).get("Item")
    require_owned(current, organization_id)
    location_id = str(body.get("location_id") or current.get("location_id") or "").strip()
    location = require_location(locations_table(), organization_id, location_id)
    resource_type_id = str(body.get("resource_type_id") or current.get("resource_type_id") or "").strip()
    resource_type = active_resource_type(organization_id, resource_type_id)
    attributes = validate_attributes(
        body.get("attributes", current.get("attributes") or {}),
        resource_type.get("attributes_schema"),
    )
    available = is_available(current.get("Available"))
    name = str(body.get("name") or current.get("name") or resource_type.get("name") or "").strip()

    if len(name) > 80:
        return response(400, {"message": "A resource field is too long"})

    updated = dict(current)
    updated.update(
        {
            "name": name,
            "Type": resource_type.get("name", ""),
            "resource_type_id": resource_type["resource_type_id"],
            "Location": location.get("name", ""),
            "location_id": location["location_id"],
            "organization_id": organization_id,
            "attributes": attributes,
        }
    )

    try:
        meta = metadata_fields_from_body(body, current)
    except ResourceStateError as error:
        return response(400, {"message": str(error)})

    for key, value in meta.items():
        if value is None or value == "":
            updated.pop(key, None)
        else:
            updated[key] = value

    try:
        updated.update(
            publication_fields(
                body,
                location,
                updated["Type"],
                resource_id,
                available,
                operational_status=updated.get("operational_status") or current.get("operational_status"),
            )
        )
    except ValueError as error:
        return response(400, {"message": str(error)})

    if updated["visibility"] in {"PRIVATE", "NETWORK"}:
        for attribute in PRIVATE_INDEX_ATTRIBUTES:
            updated.pop(attribute, None)

    resources_table().put_item(
        Item=updated,
        ConditionExpression="organization_id = :organization_id",
        ExpressionAttributeValues={":organization_id": organization_id},
    )
    action = "visibility.change" if current.get("visibility", "PRIVATE") != updated["visibility"] else "resource.update"
    record_audit(
        audit_table(),
        build_audit_event(
            organization_id,
            actor_sub,
            actor_role,
            action,
            "resource",
            resource_id,
            location_id=location["location_id"],
            metadata={"visibility": updated["visibility"], "resource_type_id": resource_type_id},
        ),
    )
    return response(200, {"message": "Resource updated", "resource": updated})


def release_resource(body, organization_id, actor_sub="", actor_role=""):
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
            UpdateExpression="SET Available = :available, operational_status = :op_available",
            ConditionExpression=(
                "attribute_exists(resource_id) AND Available = :allocated AND organization_id = :organization_id "
                "AND (attribute_not_exists(operational_status) OR operational_status = :op_allocated)"
            ),
            ExpressionAttributeValues={
                ":available": True,
                ":allocated": False,
                ":organization_id": organization_id,
                ":op_available": "AVAILABLE",
                ":op_allocated": "ALLOCATED",
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

    if resource.get("visibility") == "PUBLIC" and resource.get("show_availability") is True:
        try:
            resources_table().update_item(
                Key={"resource_id": resource_id},
                UpdateExpression="SET public_status = :status",
                ConditionExpression="organization_id = :organization_id AND visibility = :public",
                ExpressionAttributeValues={
                    ":status": "AVAILABLE",
                    ":organization_id": organization_id,
                    ":public": "PUBLIC",
                },
            )
        except ClientError as error:
            print("Public status update skipped:", error.response["Error"]["Code"])

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

    record_audit(
        audit_table(),
        build_audit_event(
            organization_id,
            actor_sub,
            actor_role,
            "resource.release",
            "resource",
            resource_id,
            location_id=resource.get("location_id", ""),
        ),
    )
    return response(
        200,
        {
            "message": "Resource released successfully",
            "resource": {"resource_id": resource_id, "Available": True},
            "allocation": {"allocation_id": allocation_id, "status": "RELEASED"},
            "request": {"request_id": request_id, "status": "RELEASED"},
        },
    )
