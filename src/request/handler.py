import json
import re
from datetime import datetime, timezone

from botocore.exceptions import ClientError

from access import REQUEST_ROLES, AccessError, access_body, authorize, require_location, require_owned
from attributes import validate_attributes
from audit import build_audit_event, record_audit
from common import ALLOWED_ORIGIN, dumps_json
from api_views import request_view
from observability import begin_request, error_body, load_object, log_result


def response(status_code, body):
    payload = error_body(status_code, body)
    if isinstance(payload, dict):
        log_result(status_code, operation="request", error_code=payload.get("error", {}).get("code", ""))
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": ALLOWED_ORIGIN,
            "Access-Control-Allow-Headers": "Content-Type,Authorization",
            "Access-Control-Allow-Methods": "OPTIONS,POST,PUT",
        },
        "body": dumps_json(payload),
    }


def requests_table():
    import boto3

    return boto3.resource("dynamodb").Table("EmergencyRequests")


def audit_table():
    import os

    import boto3

    return boto3.resource("dynamodb").Table(os.environ.get("AUDIT_TABLE", "AuditEvents"))


def locations_table():
    import os

    import boto3

    return boto3.resource("dynamodb").Table(os.environ.get("LOCATIONS_TABLE", "Locations"))


def request_types_table():
    import os

    import boto3

    return boto3.resource("dynamodb").Table(os.environ.get("REQUEST_TYPES_TABLE", "RequestTypes"))


def update_request(proposed, organization_id, actor_sub, actor_role, request_id):
    current = requests_table().get_item(Key={"request_id": request_id}).get("Item")
    require_owned(current, organization_id)

    if str(current.get("Status", "")).upper() != "PENDING":
        return response(409, {"message": "Request is not eligible for update"})

    current.update(
        {
            "ResourceType": proposed["ResourceType"],
            "Location": proposed["Location"],
            "location_id": proposed["location_id"],
            "Priority": proposed["Priority"],
            "organization_id": organization_id,
        }
    )

    if proposed.get("request_type_id"):
        current["request_type_id"] = proposed["request_type_id"]
        current["attributes"] = proposed.get("attributes") or {}
        current["matching_config"] = proposed.get("matching_config") or {}

    requests_table().put_item(
        Item=current,
        ConditionExpression="organization_id = :organization_id",
        ExpressionAttributeValues={":organization_id": organization_id},
    )
    record_audit(
        audit_table(),
        build_audit_event(
            organization_id,
            actor_sub,
            actor_role,
            "request.update",
            "request",
            request_id,
            location_id=current["location_id"],
        ),
    )
    return response(200, {"message": "Request updated", "request": request_view(current)})


def active_request_type(organization_id, request_type_id):
    item = request_types_table().get_item(
        Key={"organization_id": organization_id, "request_type_id": request_type_id}
    ).get("Item")

    if not item or item.get("organization_id") != organization_id or item.get("status") != "ACTIVE":
        raise AccessError(404, "Request type not found")

    return item


def lambda_handler(event, context):
    begin_request(event)
    method = (
        event.get("httpMethod")
        or event.get("requestContext", {}).get("http", {}).get("method")
        or ""
    ).upper()

    if method == "OPTIONS":
        return response(200, {"message": "CORS preflight successful"})

    if method not in {"POST", "PUT"}:
        return response(405, {"message": "Method not allowed"})

    try:
        body = load_object(event.get("body") or {}, event.get("isBase64Encoded"))

        _user_sub, membership = authorize(event, body, allowed_roles=REQUEST_ROLES, access="write")
        organization_id = membership["organization_id"]
        request_id = str(body.get("request_id", "")).strip()
        request_type_id = str(body.get("request_type_id") or "").strip()
        resource_type = str(body.get("resource_type", "")).strip().upper()
        location_id = str(body.get("location_id", "")).strip()
        request_type = None
        attributes = {}
        matching_config = {}

        if request_type_id:
            request_type = active_request_type(organization_id, request_type_id)
            attributes = validate_attributes(body.get("attributes") or {}, request_type.get("attributes_schema"))
            resource_type = request_type.get("name", "")
            matching_config = request_type.get("matching_config") or {}

        try:
            priority = int(body.get("priority", request_type.get("default_priority") if request_type else None))
        except (TypeError, ValueError):
            priority = 0

        if not request_id:
            return response(400, {"message": "Request ID is required"})

        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", request_id):
            return response(400, {"message": "Request ID is invalid"})

        if not resource_type:
            return response(400, {"message": "Resource type is required"})

        if priority < 1 or priority > 5:
            return response(400, {"message": "Priority must be between 1 and 5"})

        location = require_location(locations_table(), organization_id, location_id)
        item = {
            "request_id": request_id,
            "ResourceType": resource_type,
            "Location": location.get("name", ""),
            "location_id": location["location_id"],
            "organization_id": organization_id,
            "Priority": priority,
            "Status": "PENDING",
            "CreatedAt": datetime.now(timezone.utc).isoformat(),
        }

        if request_type:
            item["request_type_id"] = request_type["request_type_id"]
            item["attributes"] = attributes
            item["matching_config"] = matching_config

        if method == "PUT":
            return update_request(item, organization_id, _user_sub, membership.get("role"), request_id)

        requests_table().put_item(
            Item=item,
            ConditionExpression="attribute_not_exists(request_id)",
        )
        record_audit(
            audit_table(),
            build_audit_event(
                organization_id,
                _user_sub,
                membership.get("role"),
                "request.create",
                "request",
                request_id,
                location_id=location["location_id"],
            ),
        )
        return response(201, {"message": "Request created successfully", "request": request_view(item)})
    except AccessError as error:
        return response(error.status_code, access_body(error))
    except json.JSONDecodeError:
        return response(400, {"message": "Invalid JSON request body"})
    except ValueError as error:
        return response(400, {"message": str(error)})
    except ClientError as error:
        code = error.response.get("Error", {}).get("Code")
        print("Request error:", code)

        if code == "ConditionalCheckFailedException":
            return response(409, {"message": "Request ID already exists"})

        return response(500, {"message": "Failed to create request"})
    except Exception as error:
        print("Request error:", error.__class__.__name__)
        return response(500, {"message": "Internal server error"})
