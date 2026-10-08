import json
from datetime import datetime, timezone

from boto3.dynamodb.types import TypeSerializer
from botocore.exceptions import ClientError

from access import (
    OPERATE_ROLES,
    READ_ROLES,
    AccessError,
    access_body,
    authorize,
    query_by_organization,
    require_location,
    require_owned,
)
from attributes import validate_attributes
from audit import build_audit_event, record_audit
from common import ALLOWED_ORIGIN, dumps_json
from matching import choose_resource, explain_match
from resource_state import EMERGENCY_CLAIM_CONDITION
from api_views import allocation_view, request_view
from observability import begin_request, error_body, load_object, log_result


def response(status_code, body):
    payload = error_body(status_code, body)
    if isinstance(payload, dict):
        log_result(status_code, operation="allocation", error_code=payload.get("error", {}).get("code", ""))
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": ALLOWED_ORIGIN,
            "Access-Control-Allow-Headers": "Content-Type,Authorization",
            "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
        },
        "body": dumps_json(payload),
    }


def resources_table():
    import boto3

    return boto3.resource("dynamodb").Table("Resources")


def requests_table():
    import boto3

    return boto3.resource("dynamodb").Table("EmergencyRequests")


def allocations_table():
    import boto3

    return boto3.resource("dynamodb").Table("Allocations")


def history_table():
    import boto3

    return boto3.resource("dynamodb").Table("ResourceStatusHistory")


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


def request_types_table():
    import os

    import boto3

    return boto3.resource("dynamodb").Table(os.environ.get("REQUEST_TYPES_TABLE", "RequestTypes"))


def parse_body(event):
    return load_object(event.get("body") or {}, event.get("isBase64Encoded"))


def lambda_handler(event, context):
    begin_request(event)
    method = (
        event.get("httpMethod")
        or event.get("requestContext", {}).get("http", {}).get("method")
        or ""
    ).upper()
    path = event.get("path") or event.get("rawPath") or ""

    if method == "OPTIONS":
        return response(200, {"message": "OK"})

    try:
        body = parse_body(event) if method == "POST" else {}
        writing = method == "POST"
        roles = OPERATE_ROLES if writing else READ_ROLES
        _user_sub, membership = authorize(
            event,
            body,
            allowed_roles=roles,
            access="write" if writing else "read",
        )
        organization_id = membership["organization_id"]
        query = event.get("queryStringParameters") or {}
        location_id = str(query.get("location_id") or "").strip()

        if location_id:
            require_location(locations_table(), organization_id, location_id)

        if method == "GET" and path.endswith("/allocations"):
            allocations = query_by_organization(
                allocations_table(),
                organization_id,
                location_id or None,
            )
            return response(
                200,
                {
                    "message": "Allocations retrieved successfully",
                    "count": len(allocations),
                    "allocations": [allocation_view(item) for item in allocations],
                },
            )

        if method == "GET" and path.endswith("/requests"):
            requests = query_by_organization(
                requests_table(),
                organization_id,
                location_id or None,
            )
            return response(
                200,
                {
                    "message": "Requests retrieved successfully",
                    "count": len(requests),
                    "requests": [request_view(item) for item in requests],
                },
            )

        if method == "POST":
            return allocate(body, organization_id, _user_sub, membership.get("role"))

        return response(405, {"message": "Method not allowed"})
    except AccessError as error:
        return response(error.status_code, access_body(error))
    except json.JSONDecodeError:
        return response(400, {"message": "Invalid request format"})
    except ValueError:
        return response(400, {"message": "Invalid request format"})
    except Exception as error:
        from observability import log_event

        log_event(
            "ERROR",
            "allocation",
            "allocate",
            "failed",
            error_code=error.__class__.__name__,
        )
        return response(500, {"message": "Unable to process allocation"})


def _prefer_same_location(request):
    config = request.get("matching_config") or {}

    if not isinstance(config, dict):
        return True

    return config.get("same_location_preferred", True) is not False


def _load_org_resources(organization_id, location_id=None):
    """Read one organization partition, optionally one location sort key."""
    return [
        item
        for item in query_by_organization(resources_table(), organization_id, location_id)
        if item.get("organization_id") == organization_id
    ]


def _resources_for_match(organization_id, request, cache):
    """Use the location key when a same-location candidate is enough.

    Matching may fall back to another location in the same organization.
    That fallback reads the organization partition once and reuses it.
    """
    location_id = str(request.get("location_id") or "").strip()

    if _prefer_same_location(request) and location_id:
        key = ("location", location_id)

        if key not in cache:
            cache[key] = _load_org_resources(organization_id, location_id)

        if choose_resource(cache[key], request):
            return cache[key]

    if "organization" not in cache:
        cache["organization"] = _load_org_resources(organization_id)

    return cache["organization"]


_serializer = TypeSerializer()


def _dynamodb_client():
    import boto3

    return boto3.client("dynamodb")


def _transact_write(transact_items):
    """Execute TransactWriteItems. Tests may replace this helper."""
    _dynamodb_client().transact_write_items(TransactItems=transact_items)


def _encoded(values):
    encoded = {}
    for key, value in values.items():
        if value is None:
            continue
        encoded[key] = _serializer.serialize(value)
    return encoded


def _claim_transact_items(resource_id, organization_id, allocation, request_id):
    """Resource claim, allocation insert, and request transition are one transaction."""
    return [
        {
            "Update": {
                "TableName": "Resources",
                "Key": _encoded({"resource_id": resource_id}),
                "UpdateExpression": "SET #a = :false, operational_status = :op_allocated",
                "ConditionExpression": EMERGENCY_CLAIM_CONDITION,
                "ExpressionAttributeNames": {"#a": "Available"},
                "ExpressionAttributeValues": _encoded(
                    {
                        ":false": False,
                        ":true": True,
                        ":organization_id": organization_id,
                        ":op_available": "AVAILABLE",
                        ":indiv": "INDIVIDUAL",
                        ":op_allocated": "ALLOCATED",
                    }
                ),
            }
        },
        {
            "Put": {
                "TableName": "Allocations",
                "Item": _encoded(allocation),
                "ConditionExpression": "attribute_not_exists(allocation_id)",
            }
        },
        {
            "Update": {
                "TableName": "EmergencyRequests",
                "Key": _encoded({"request_id": request_id}),
                "UpdateExpression": "SET #s = :status",
                "ConditionExpression": "#s = :pending AND organization_id = :organization_id",
                "ExpressionAttributeNames": {"#s": "Status"},
                "ExpressionAttributeValues": _encoded(
                    {
                        ":status": "ALLOCATED",
                        ":pending": "PENDING",
                        ":organization_id": organization_id,
                    }
                ),
            }
        },
    ]


def _commit_emergency_claim(transact_items):
    """Commit pre-encoded claim items once through the low-level DynamoDB client."""
    _transact_write(transact_items)


def _claim_cancelled(error):
    reasons = error.response.get("CancellationReasons") or []
    return [str((reason or {}).get("Code") or "None") for reason in reasons]


def allocate(body, organization_id, actor_sub="", actor_role=""):
    request_id = str(body.get("request_id", "")).strip()
    resource_type = str(body.get("resource_type", "")).strip()
    request_type_id = str(body.get("request_type_id") or "").strip()
    location_id = str(body.get("location_id", "")).strip()
    request_type = None

    if request_type_id:
        request_type = request_types_table().get_item(
            Key={"organization_id": organization_id, "request_type_id": request_type_id}
        ).get("Item")

        if (
            not request_type
            or request_type.get("organization_id") != organization_id
            or request_type.get("status") != "ACTIVE"
        ):
            return response(404, {"message": "Request type not found"})

        try:
            validate_attributes(body.get("attributes") or {}, request_type.get("attributes_schema"))
        except AccessError as error:
            return response(error.status_code, access_body(error))

        resource_type = resource_type or request_type.get("name", "")

    try:
        priority = int(body.get("priority", request_type.get("default_priority") if request_type else 999))
    except (TypeError, ValueError):
        priority = 999

    if not request_id or not resource_type or not location_id:
        return response(400, {"message": "request_id, resource_type and location_id are required"})

    location = require_location(locations_table(), organization_id, location_id)
    existing = requests_table().get_item(Key={"request_id": request_id}).get("Item")
    require_owned(existing, organization_id)

    if str(existing.get("Status", "")).upper() != "PENDING":
        return response(409, {"message": "Request is not eligible for allocation"})

    if existing.get("location_id") != location["location_id"]:
        return response(409, {"message": "Request location does not match"})

    resource_cache = {}
    resources = _resources_for_match(organization_id, existing, resource_cache)
    resource = choose_resource(resources, existing)

    if (
        not resource
        or resource.get("organization_id") != organization_id
        or existing.get("organization_id") != organization_id
        or resource.get("location_id") is None
    ):
        return response(404, {"message": "No suitable resource available", "request_id": request_id})

    resource_location = require_location(
        locations_table(),
        organization_id,
        resource.get("location_id"),
    )
    resource_id = resource.get("resource_id")
    allocated_at = datetime.now(timezone.utc).isoformat()
    allocation_id = "ALLOC-" + request_id
    allocation = {
        "allocation_id": allocation_id,
        "request_id": request_id,
        "resource_id": resource_id,
        "resource_type": existing.get("ResourceType", resource_type),
        "location": resource_location.get("name", ""),
        "location_id": resource.get("location_id"),
        "organization_id": organization_id,
        "priority": existing.get("Priority", priority),
        "status": "ALLOCATED",
        "allocated_at": allocated_at,
    }
    try:
        _commit_emergency_claim(
            _claim_transact_items(
                resource_id,
                organization_id,
                allocation,
                request_id,
            )
        )
    except ClientError as error:
        code = error.response["Error"]["Code"]
        if code == "TransactionCanceledException":
            reasons = _claim_cancelled(error)
            if reasons and reasons[0] == "ConditionalCheckFailed":
                return response(404, {"message": "No suitable resource available", "request_id": request_id})
            if any(reason == "ConditionalCheckFailed" for reason in reasons[1:]):
                return response(409, {"message": "Request is not eligible for allocation"})

        from observability import log_event

        log_event(
            "ERROR",
            "allocation",
            "reserve",
            "failed",
            organization_id=organization_id,
            resource_id=resource_id,
            error_code=code,
        )
        return response(500, {"message": "Unable to process allocation"})

    history_table().put_item(
        Item={
            "history_id": "HIST-" + request_id + "-" + resource_id,
            "resource_id": resource_id,
            "organization_id": organization_id,
            "location_id": resource.get("location_id"),
            "resource_type": allocation["resource_type"],
            "location": allocation["location"],
            "previous_status": "AVAILABLE",
            "new_status": "ALLOCATED",
            "changed_at": allocated_at,
            "reason": "RESOURCE_ALLOCATED",
            "request_id": request_id,
            "allocation_id": allocation_id,
            "actor_sub": actor_sub,
        }
    )

    try:
        events_client().put_events(
            Entries=[
                {
                    "Source": "emergency.resource.allocation",
                    "DetailType": "ResourceAllocated",
                    "Detail": json.dumps(
                        {
                            "request_id": request_id,
                            "resource_id": resource_id,
                            "allocation_id": allocation_id,
                            "organization_id": organization_id,
                            "status": "ALLOCATED",
                        }
                    ),
                    "EventBusName": "default",
                }
            ]
        )
    except Exception as error:
        print("Allocation event error:", error.__class__.__name__)

    record_audit(
        audit_table(),
        build_audit_event(
            organization_id,
            actor_sub,
            actor_role,
            "allocation.create",
            "allocation",
            allocation_id,
            location_id=resource.get("location_id", ""),
        ),
    )

    from observability import log_event

    log_event(
        "INFO",
        "allocation",
        "allocate",
        "committed",
        organization_id=organization_id,
        actor_sub=actor_sub,
        request_id=request_id,
        resource_id=resource_id,
    )
    return response(
        200,
        {
            "message": "Resource allocated successfully",
            "request_id": request_id,
            "resource_id": resource_id,
            "allocation_id": allocation_id,
            "status": "ALLOCATED",
            "allocated_at": allocated_at,
            "match": explain_match(resource, existing),
        },
    )
