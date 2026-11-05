import json
import re
from datetime import datetime, timezone

from botocore.exceptions import ClientError

from access import (
    OPERATE_ROLES,
    REQUEST_ROLES,
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
from api_views import request_view
from emergency_notify import (
    notify_request_approved,
    notify_request_cancelled,
    notify_request_completed,
    notify_request_created,
    notify_request_rejected,
)
from request_lifecycle import (
    REVIEW_APPROVED,
    REVIEW_PENDING,
    REVIEW_REJECTED,
    append_workflow_event,
    normalize_description,
    receipt_allows_completion,
    utc_now,
)
from transitions import can_complete_request, can_reject_request
from observability import begin_request, error_body, load_object, log_event, log_result
from resource_state import (
    ResourceStateError,
    emergency_matchable,
    normalize_quantity_unit,
    normalize_tracking_mode,
    quantity_snapshot,
)


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


def resources_table():
    import os

    import boto3

    return boto3.resource("dynamodb").Table(os.environ.get("RESOURCES_TABLE", "Resources"))


def allocations_table():
    import boto3

    return boto3.resource("dynamodb").Table("Allocations")


def deliveries_table():
    import os

    import boto3

    return boto3.resource("dynamodb").Table(os.environ.get("DELIVERIES_TABLE", "Deliveries"))


def _require_operator(role):
    if str(role or "").upper() not in OPERATE_ROLES:
        raise AccessError(403, "You are not allowed to perform this action")


def _notify_safe(operation, organization_id, request_id, callback):
    try:
        callback()
    except Exception as error:
        log_event(
            "ERROR",
            "requests",
            operation,
            "notification_failed",
            organization_id=organization_id,
            request_id=request_id,
            error=error.__class__.__name__,
        )


def _positive_int(value, label):
    if isinstance(value, bool) or value is None:
        raise ValueError(f"{label} is invalid")

    if isinstance(value, int):
        number = value
    else:
        try:
            from decimal import Decimal
        except ImportError:  # pragma: no cover
            Decimal = ()  # type: ignore

        if not isinstance(value, Decimal) or value != value.to_integral_value():
            raise ValueError(f"{label} is invalid")
        number = int(value)

    if number < 1:
        raise ValueError(f"{label} is invalid")

    return number


def requested_resource_fields(body, organization_id, location):
    """Attach a specific resource and quantity without changing inventory."""
    resource_id = str((body or {}).get("resource_id") or "").strip()
    if not resource_id:
        return {}

    resource = resources_table().get_item(Key={"resource_id": resource_id}).get("Item")
    require_owned(resource, organization_id)

    if str(resource.get("location_id") or "") != location["location_id"]:
        raise AccessError(409, "Resource location does not match")

    quantity = _positive_int(
        (body or {}).get("quantity_requested", (body or {}).get("quantity")),
        "Requested quantity",
    )
    unit = normalize_quantity_unit((body or {}).get("quantity_unit") or resource.get("quantity_unit"))
    mode = normalize_tracking_mode(resource.get("tracking_mode"))

    if mode == "QUANTITY":
        snapshot = quantity_snapshot(resource)
        if quantity > snapshot["quantity_available"]:
            raise ValueError("Requested quantity is not available")
        if unit is None:
            unit = normalize_quantity_unit(resource.get("quantity_unit")) or "UNITS"
    else:
        if quantity != 1:
            raise ValueError("Individual resources require quantity 1")
        if not emergency_matchable(resource):
            raise ValueError("Resource is not available")
        if unit is None:
            unit = "UNITS"

    return {
        "resource_id": resource_id,
        "quantity_requested": quantity,
        "quantity_unit": unit,
    }


def request_path(event):
    return str((event or {}).get("path") or (event or {}).get("rawPath") or "")


def cancel_request(organization_id, actor_sub, actor_role, request_id):
    """Cancel one pending request. Allocation can still win the status condition."""
    if not request_id:
        return response(400, {"message": "Request ID is required"})

    current = requests_table().get_item(Key={"request_id": request_id}).get("Item")
    require_owned(current, organization_id)

    if str(current.get("Status", "")).upper() != "PENDING":
        return response(409, {"message": "Request is not eligible for cancellation"})

    try:
        requests_table().update_item(
            Key={"request_id": request_id},
            UpdateExpression="SET #status = :cancelled",
            ConditionExpression="organization_id = :organization_id AND #status = :pending",
            ExpressionAttributeNames={"#status": "Status"},
            ExpressionAttributeValues={
                ":cancelled": "CANCELLED",
                ":organization_id": organization_id,
                ":pending": "PENDING",
            },
        )
    except ClientError as error:
        code = error.response.get("Error", {}).get("Code")
        if code != "ConditionalCheckFailedException":
            raise
        log_event(
            "WARNING",
            "requests",
            "request.cancel",
            "conflict",
            organization_id=organization_id,
            request_id=request_id,
            error_code=code,
        )
        return response(409, {"message": "Request is not eligible for cancellation"})

    updated = dict(current)
    updated["Status"] = "CANCELLED"
    record_audit(
        audit_table(),
        build_audit_event(
            organization_id,
            actor_sub,
            actor_role,
            "request.cancel",
            "request",
            request_id,
            location_id=current.get("location_id") or "",
        ),
    )
    try:
        notify_request_cancelled(
            organization_id=organization_id,
            actor_sub=actor_sub,
            request_id=request_id,
            resource_type=current.get("ResourceType") or "",
            location=current.get("Location") or "",
        )
    except Exception as error:
        log_event(
            "ERROR",
            "requests",
            "request.cancel",
            "notification_failed",
            organization_id=organization_id,
            request_id=request_id,
            error=error.__class__.__name__,
        )
    return response(200, {"message": "Request cancelled", "request": request_view(updated)})


def _related_deliveries(organization_id, request_id, allocation_id):
    try:
        rows = query_by_organization(deliveries_table(), organization_id)
    except Exception:
        return []
    matched = []
    for row in rows:
        if allocation_id and row.get("allocation_id") == allocation_id:
            matched.append(row)
        elif row.get("request_id") == request_id:
            matched.append(row)
    return matched


def review_request(organization_id, actor_sub, actor_role, request_id, decision, reason=""):
    _require_operator(actor_role)
    if not request_id:
        return response(400, {"message": "Request ID is required"})
    current = requests_table().get_item(Key={"request_id": request_id}).get("Item")
    require_owned(current, organization_id)
    if str(current.get("Status", "")).upper() != "PENDING":
        return response(409, {"message": "Request is not eligible for review"})
    if str(current.get("review_status") or "").upper() not in {"", REVIEW_PENDING}:
        return response(409, {"message": "Request is not eligible for review"})
    created_by = str(current.get("created_by") or "")
    if created_by and created_by == actor_sub:
        return response(403, {"message": "Reviewers cannot approve or reject their own requests"})

    approved = str(decision or "").upper() == "APPROVED"
    if approved:
        new_review = REVIEW_APPROVED
        new_status = "PENDING"
        action = "request.approve"
        message = "Request approved"
        event_name = "APPROVED"
    else:
        if not can_reject_request(current.get("Status")):
            return response(409, {"message": "Request is not eligible for review"})
        reason_text = str(reason or "").strip()
        if len(reason_text) < 3:
            return response(400, {"message": "Rejection reason is required"})
        new_review = REVIEW_REJECTED
        new_status = "REJECTED"
        action = "request.reject"
        message = "Request rejected"
        event_name = "REJECTED"

    names = {"#status": "Status"}
    values = {
        ":review": new_review,
        ":reviewed_at": utc_now(),
        ":events": append_workflow_event(current, event_name, actor_sub, reason if not approved else "Approved"),
        ":organization_id": organization_id,
        ":pending": "PENDING",
        ":status": new_status,
        ":pending_review": REVIEW_PENDING,
    }
    assignments = [
        "review_status = :review",
        "review_at = :reviewed_at",
        "workflow_events = :events",
        "#status = :status",
    ]
    condition = (
        "organization_id = :organization_id AND #status = :pending "
        "AND (attribute_not_exists(review_status) OR review_status = :pending_review)"
    )
    if not approved:
        values[":reason"] = str(reason).strip()[:500]
        assignments.append("rejection_reason = :reason")

    try:
        requests_table().update_item(
            Key={"request_id": request_id},
            UpdateExpression="SET " + ", ".join(assignments),
            ConditionExpression=condition,
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
        )
    except ClientError as error:
        code = error.response.get("Error", {}).get("Code")
        if code != "ConditionalCheckFailedException":
            raise
        return response(409, {"message": "Request is not eligible for review"})

    updated = dict(current)
    updated["review_status"] = new_review
    updated["review_at"] = values[":reviewed_at"]
    updated["workflow_events"] = values[":events"]
    updated["Status"] = new_status
    if not approved:
        updated["rejection_reason"] = values[":reason"]
    record_audit(
        audit_table(),
        build_audit_event(
            organization_id,
            actor_sub,
            actor_role,
            action,
            "request",
            request_id,
            location_id=current.get("location_id") or "",
        ),
    )
    notify_fn = notify_request_approved if approved else notify_request_rejected
    _notify_safe(
        action,
        organization_id,
        request_id,
        lambda: notify_fn(
            organization_id=organization_id,
            actor_sub=actor_sub,
            request_id=request_id,
            resource_type=current.get("ResourceType") or "",
            location=current.get("Location") or "",
        ),
    )
    return response(200, {"message": message, "request": request_view(updated)})


def complete_request(organization_id, actor_sub, actor_role, request_id):
    _require_operator(actor_role)
    if not request_id:
        return response(400, {"message": "Request ID is required"})
    current = requests_table().get_item(Key={"request_id": request_id}).get("Item")
    require_owned(current, organization_id)
    if not can_complete_request(current.get("Status")):
        return response(409, {"message": "Request is not eligible for completion"})
    allocation_id = "ALLOC-" + request_id
    try:
        allocation = allocations_table().get_item(Key={"allocation_id": allocation_id}).get("Item")
    except Exception:
        allocation = None
    if not allocation or allocation.get("organization_id") != organization_id:
        return response(409, {"message": "Allocation is required before completion"})
    if str(allocation.get("status") or "").upper() != "ALLOCATED":
        return response(409, {"message": "Allocation is not eligible for completion"})
    deliveries = _related_deliveries(organization_id, request_id, allocation_id)
    if not receipt_allows_completion(deliveries):
        return response(409, {"message": "Receipt confirmation is required before completion"})
    completed_at = utc_now()
    events = append_workflow_event(current, "COMPLETED", actor_sub, "Receipt confirmed")
    try:
        requests_table().update_item(
            Key={"request_id": request_id},
            UpdateExpression="SET #status = :completed, completed_at = :completed_at, workflow_events = :events",
            ConditionExpression="organization_id = :organization_id AND #status = :allocated",
            ExpressionAttributeNames={"#status": "Status"},
            ExpressionAttributeValues={
                ":completed": "COMPLETED",
                ":completed_at": completed_at,
                ":events": events,
                ":organization_id": organization_id,
                ":allocated": "ALLOCATED",
            },
        )
    except ClientError as error:
        code = error.response.get("Error", {}).get("Code")
        if code != "ConditionalCheckFailedException":
            raise
        return response(409, {"message": "Request is not eligible for completion"})
    updated = dict(current)
    updated["Status"] = "COMPLETED"
    updated["completed_at"] = completed_at
    updated["workflow_events"] = events
    record_audit(
        audit_table(),
        build_audit_event(
            organization_id,
            actor_sub,
            actor_role,
            "request.complete",
            "request",
            request_id,
            location_id=current.get("location_id") or "",
        ),
    )
    _notify_safe(
        "request.complete",
        organization_id,
        request_id,
        lambda: notify_request_completed(
            organization_id=organization_id,
            actor_sub=actor_sub,
            request_id=request_id,
            resource_type=current.get("ResourceType") or "",
            location=current.get("Location") or "",
        ),
    )
    return response(200, {"message": "Request completed", "request": request_view(updated)})


def update_request(proposed, organization_id, actor_sub, actor_role, request_id):
    """Update the fields PUT /requests already changes, only while the request is pending.

    Allocation moves Status with its own conditional update. Replacing the row
    after this read could write PENDING back over ALLOCATED.
    """
    current = requests_table().get_item(Key={"request_id": request_id}).get("Item")
    require_owned(current, organization_id)

    if str(current.get("Status", "")).upper() != "PENDING":
        return response(409, {"message": "Request is not eligible for update"})

    names = {
        "#resource_type": "ResourceType",
        "#location": "Location",
        "#priority": "Priority",
        "#status": "Status",
    }
    values = {
        ":resource_type": proposed["ResourceType"],
        ":location": proposed["Location"],
        ":location_id": proposed["location_id"],
        ":priority": proposed["Priority"],
        ":organization_id": organization_id,
        ":pending": "PENDING",
    }
    assignments = [
        "#resource_type = :resource_type",
        "#location = :location",
        "location_id = :location_id",
        "#priority = :priority",
    ]
    updated = dict(current)
    updated["ResourceType"] = proposed["ResourceType"]
    updated["Location"] = proposed["Location"]
    updated["location_id"] = proposed["location_id"]
    updated["Priority"] = proposed["Priority"]

    if proposed.get("request_type_id"):
        names["#request_type_id"] = "request_type_id"
        names["#attributes"] = "attributes"
        names["#matching_config"] = "matching_config"
        values[":request_type_id"] = proposed["request_type_id"]
        values[":attributes"] = proposed.get("attributes") or {}
        values[":matching_config"] = proposed.get("matching_config") or {}
        assignments.extend(
            [
                "#request_type_id = :request_type_id",
                "#attributes = :attributes",
                "#matching_config = :matching_config",
            ]
        )
        updated["request_type_id"] = proposed["request_type_id"]
        updated["attributes"] = proposed.get("attributes") or {}
        updated["matching_config"] = proposed.get("matching_config") or {}

    try:
        requests_table().update_item(
            Key={"request_id": request_id},
            UpdateExpression="SET " + ", ".join(assignments),
            ConditionExpression="organization_id = :organization_id AND #status = :pending",
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
        )
    except ClientError as error:
        code = error.response.get("Error", {}).get("Code")
        if code != "ConditionalCheckFailedException":
            raise
        log_event(
            "WARNING",
            "requests",
            "request.update",
            "conflict",
            organization_id=organization_id,
            request_id=request_id,
            error_code=code,
        )
        return response(409, {"message": "Request is not eligible for update"})

    record_audit(
        audit_table(),
        build_audit_event(
            organization_id,
            actor_sub,
            actor_role,
            "request.update",
            "request",
            request_id,
            location_id=updated["location_id"],
        ),
    )
    return response(200, {"message": "Request updated", "request": request_view(updated)})


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

        path = request_path(event).rstrip("/")
        if method == "POST" and path.endswith("/requests/cancel"):
            return cancel_request(organization_id, _user_sub, membership.get("role"), request_id)
        if method == "POST" and path.endswith("/requests/approve"):
            return review_request(
                organization_id, _user_sub, membership.get("role"), request_id, "APPROVED"
            )
        if method == "POST" and path.endswith("/requests/reject"):
            return review_request(
                organization_id,
                _user_sub,
                membership.get("role"),
                request_id,
                "REJECTED",
                str(body.get("reason") or body.get("rejection_reason") or ""),
            )
        if method == "POST" and path.endswith("/requests/complete"):
            return complete_request(organization_id, _user_sub, membership.get("role"), request_id)

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
            "created_by": _user_sub,
            "review_status": REVIEW_PENDING,
            "workflow_events": append_workflow_event({}, "SUBMITTED", _user_sub, "Request created"),
        }
        description = normalize_description(body.get("description") or body.get("notes"))
        if description:
            item["description"] = description

        if request_type:
            item["request_type_id"] = request_type["request_type_id"]
            item["attributes"] = attributes
            item["matching_config"] = matching_config

        try:
            item.update(requested_resource_fields(body, organization_id, location))
        except ResourceStateError as error:
            return response(400, {"message": str(error)})

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
        try:
            notify_request_created(
                organization_id=organization_id,
                actor_sub=_user_sub,
                request_id=request_id,
                resource_type=resource_type,
                location=location.get("name", ""),
            )
        except Exception as error:
            log_event(
                "ERROR",
                "requests",
                "request.create",
                "notification_failed",
                organization_id=organization_id,
                request_id=request_id,
                error=error.__class__.__name__,
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
