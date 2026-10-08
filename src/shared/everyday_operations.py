"""Everyday resource reservation, allocation, and return."""

import uuid
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.types import TypeSerializer
from botocore.exceptions import ClientError

from audit import build_audit_event, record_audit
from resource_state import (
    RESERVATION_HELD_ATTRIBUTES,
    ResourceStateError,
    normalize_tracking_mode,
    reservation_due_values,
    validate_transition,
)

_serializer = TypeSerializer()
RESOURCES_TABLE = "Resources"
ALLOCATIONS_TABLE = "Allocations"


class EverydayOperationError(Exception):
    def __init__(self, status_code, message):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


EVERYDAY_STATUS_OPEN = "OPEN"
EVERYDAY_STATUS_RETURNED = "RETURNED"
ALLOCATION_TYPE_EVERYDAY = "EVERYDAY"


def _now():
    return datetime.now(timezone.utc).isoformat()


def _history_id(prefix, resource_id):
    return prefix + resource_id + "-" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")


def _positive_quantity(value, label="Quantity"):
    if isinstance(value, bool) or not isinstance(value, int):
        raise EverydayOperationError(400, f"{label} is invalid")

    if value < 1:
        raise EverydayOperationError(400, f"{label} is invalid")

    return value


def _resource_id(body):
    resource_id = str((body or {}).get("resource_id") or "").strip()

    if not resource_id:
        raise EverydayOperationError(400, "resource_id is required")

    return resource_id


def _everyday_allocation_id(resource_id):
    return "EVERYDAY-" + resource_id + "-" + uuid.uuid4().hex[:12].upper()


def _write_history(history_table, **fields):
    actor = str(fields.get("actor_sub") or "").strip()
    if actor:
        fields["actor_sub"] = actor
    else:
        fields.pop("actor_sub", None)
    history_table.put_item(Item=fields)


def _encoded(values):
    encoded = {}
    for key, value in values.items():
        if value is None:
            continue
        encoded[key] = _serializer.serialize(value)
    return encoded


def _dynamodb_client():
    return boto3.client("dynamodb")


def _transact_write(transact_items):
    """Execute TransactWriteItems. Tests may replace this helper."""
    _dynamodb_client().transact_write_items(TransactItems=transact_items)


def _commit_everyday_allocation(_tables, transact_items):
    """Commit pre-encoded items once through the low-level DynamoDB client."""
    _transact_write(transact_items)


def _return_transact_items(resource_id, resource_expression, resource_condition, resource_values, allocation_id, allocation_values):
    """Resource return and allocation return are one conditional transaction."""
    return [
        {
            "Update": {
                "TableName": RESOURCES_TABLE,
                "Key": _encoded({"resource_id": resource_id}),
                "UpdateExpression": resource_expression,
                "ConditionExpression": resource_condition,
                "ExpressionAttributeValues": _encoded(resource_values),
            }
        },
        {
            "Update": {
                "TableName": ALLOCATIONS_TABLE,
                "Key": _encoded({"allocation_id": allocation_id}),
                "UpdateExpression": (
                    "SET #status = :returned, returned_at = :now, returned_by = :actor, updated_at = :now"
                ),
                "ConditionExpression": (
                    "attribute_exists(allocation_id) AND #status = :open "
                    "AND organization_id = :organization_id AND resource_id = :resource_id "
                    "AND allocation_type = :everyday"
                ),
                "ExpressionAttributeNames": {"#status": "status"},
                "ExpressionAttributeValues": _encoded(allocation_values),
            }
        },
    ]


def _allocation_transact_items(resource_id, update_expression, condition, values, allocation):
    return [
        {
            "Update": {
                "TableName": RESOURCES_TABLE,
                "Key": _encoded({"resource_id": resource_id}),
                "UpdateExpression": update_expression,
                "ConditionExpression": condition,
                "ExpressionAttributeValues": _encoded(values),
            }
        },
        {
            "Put": {
                "TableName": ALLOCATIONS_TABLE,
                "Item": _encoded(allocation),
                "ConditionExpression": "attribute_not_exists(allocation_id)",
            }
        },
    ]


def _conflict_from_client(error):
    code = error.response["Error"]["Code"]

    if code == "ConditionalCheckFailedException":
        raise EverydayOperationError(409, "Resource state conflict") from error

    if code == "TransactionCanceledException":
        reasons = error.response.get("CancellationReasons") or []
        reason_codes = [str((reason or {}).get("Code") or "None") for reason in reasons]
        reason_messages = [
            str((reason or {}).get("Message") or "").strip()
            for reason in reasons
            if str((reason or {}).get("Message") or "").strip()
        ]
        if any(reason == "ConditionalCheckFailed" for reason in reason_codes):
            raise EverydayOperationError(409, "Resource state conflict") from error
        # Surface non-secret cancellation details for diagnosis.
        joined = ",".join(reason_codes) if reason_codes else "unknown"
        detail = "; ".join(reason_messages[:2])
        message = "Transaction canceled: " + joined
        if detail:
            message = message + " | " + detail[:240]
        raise EverydayOperationError(500, message) from error

    raise error


def reserve_individual(body, organization_id, actor_sub, actor_role, resource, tables):
    if normalize_tracking_mode(resource.get("tracking_mode")) != "INDIVIDUAL":
        raise EverydayOperationError(409, "Resource is not an individual item")

    now = _now()
    due = reservation_due_values(now)
    resource_id = resource["resource_id"]

    try:
        tables["resources"].update_item(
            Key={"resource_id": resource_id},
            UpdateExpression=(
                "SET operational_status = :reserved, Available = :false, "
                "reserved_by = :actor, reserved_at = :now, "
                "reservation_expires_at = :expires, reservation_due_key = :due"
            ),
            ConditionExpression=(
                "organization_id = :organization_id AND Available = :true AND "
                "(attribute_not_exists(operational_status) OR operational_status = :available)"
            ),
            ExpressionAttributeValues={
                ":reserved": "RESERVED",
                ":false": False,
                ":true": True,
                ":available": "AVAILABLE",
                ":organization_id": organization_id,
                ":actor": actor_sub,
                ":now": now,
                ":expires": due["reservation_expires_at"],
                ":due": due["reservation_due_key"],
            },
        )
    except ClientError as error:
        _conflict_from_client(error)

    _write_history(
        tables["history"],
        history_id=_history_id("HIST-RESERVE-", resource_id),
        resource_id=resource_id,
        organization_id=organization_id,
        location_id=resource.get("location_id", ""),
        resource_type=resource.get("Type", ""),
        location=resource.get("Location", ""),
        previous_status="AVAILABLE",
        new_status="RESERVED",
        changed_at=now,
        actor_sub=actor_sub,
        reason="RESOURCE_RESERVED",
    )
    record_audit(
        tables["audit"],
        build_audit_event(
            organization_id,
            actor_sub,
            actor_role,
            "resource.reserve",
            "resource",
            resource_id,
            location_id=resource.get("location_id", ""),
        ),
    )
    return {"message": "Resource reserved", "resource_id": resource_id, "operational_status": "RESERVED"}


def release_quantity_reservation(body, organization_id, actor_sub, actor_role, resource, tables):
    """Move reserved quantity back to available with one conditional update."""
    if normalize_tracking_mode(resource.get("tracking_mode")) != "QUANTITY":
        raise EverydayOperationError(409, "Resource is not a quantity pool")

    quantity = _positive_quantity((body or {}).get("quantity"), "Quantity")
    now = _now()
    resource_id = resource["resource_id"]

    try:
        tables["resources"].update_item(
            Key={"resource_id": resource_id},
            UpdateExpression=(
                "SET quantity_reserved = quantity_reserved - :qty, "
                "quantity_available = quantity_available + :qty"
            ),
            ConditionExpression=(
                "attribute_exists(resource_id) AND organization_id = :organization_id "
                "AND tracking_mode = :quantity AND quantity_reserved >= :qty "
                "AND quantity_available + :qty <= quantity_total "
                "AND quantity_available + quantity_reserved + quantity_allocated = quantity_total "
                "AND (attribute_not_exists(operational_status) OR operational_status <> :retired)"
            ),
            ExpressionAttributeValues={
                ":qty": quantity,
                ":organization_id": organization_id,
                ":quantity": "QUANTITY",
                ":retired": "RETIRED",
            },
        )
    except ClientError as error:
        _conflict_from_client(error)

    _write_history(
        tables["history"],
        history_id=_history_id("HIST-QTY-UNRESERVE-", resource_id),
        resource_id=resource_id,
        organization_id=organization_id,
        location_id=resource.get("location_id", ""),
        resource_type=resource.get("Type", ""),
        location=resource.get("Location", ""),
        previous_status="RESERVED",
        new_status="AVAILABLE",
        changed_at=now,
        actor_sub=actor_sub,
        reason="QUANTITY_RESERVATION_RELEASED",
        quantity=quantity,
    )
    record_audit(
        tables["audit"],
        build_audit_event(
            organization_id,
            actor_sub,
            actor_role,
            "resource.quantity_release_reservation",
            "resource",
            resource_id,
            location_id=resource.get("location_id", ""),
            metadata={"quantity": quantity},
        ),
    )
    return {"message": "Quantity reservation released", "resource_id": resource_id, "quantity": quantity}


def release_reservation(body, organization_id, actor_sub, actor_role, resource, tables):
    if normalize_tracking_mode(resource.get("tracking_mode")) == "QUANTITY":
        return release_quantity_reservation(body, organization_id, actor_sub, actor_role, resource, tables)

    if normalize_tracking_mode(resource.get("tracking_mode")) != "INDIVIDUAL":
        raise EverydayOperationError(409, "Resource is not an individual item")

    try:
        validate_transition("RESERVED", "AVAILABLE")
    except ResourceStateError as error:
        raise EverydayOperationError(409, "Resource state conflict") from error

    now = _now()
    resource_id = resource["resource_id"]

    try:
        tables["resources"].update_item(
            Key={"resource_id": resource_id},
            UpdateExpression=(
                "SET operational_status = :available, Available = :true "
                "REMOVE " + ", ".join(RESERVATION_HELD_ATTRIBUTES)
            ),
            ConditionExpression=(
                "organization_id = :organization_id AND operational_status = :reserved "
                "AND reserved_by = :actor"
            ),
            ExpressionAttributeValues={
                ":available": "AVAILABLE",
                ":true": True,
                ":reserved": "RESERVED",
                ":organization_id": organization_id,
                ":actor": actor_sub,
            },
        )
    except ClientError as error:
        _conflict_from_client(error)

    _write_history(
        tables["history"],
        history_id=_history_id("HIST-UNRESERVE-", resource_id),
        resource_id=resource_id,
        organization_id=organization_id,
        location_id=resource.get("location_id", ""),
        resource_type=resource.get("Type", ""),
        location=resource.get("Location", ""),
        previous_status="RESERVED",
        new_status="AVAILABLE",
        changed_at=now,
        actor_sub=actor_sub,
        reason="RESOURCE_RESERVATION_RELEASED",
    )
    record_audit(
        tables["audit"],
        build_audit_event(
            organization_id,
            actor_sub,
            actor_role,
            "resource.release_reservation",
            "resource",
            resource_id,
            location_id=resource.get("location_id", ""),
        ),
    )
    return {"message": "Reservation released", "resource_id": resource_id, "operational_status": "AVAILABLE"}


def _everyday_allocation_item(allocation_id, resource, organization_id, actor_sub, quantity, purpose, now):
    return {
        "allocation_id": allocation_id,
        "allocation_type": ALLOCATION_TYPE_EVERYDAY,
        "status": EVERYDAY_STATUS_OPEN,
        "resource_id": resource["resource_id"],
        "resource_type": resource.get("Type", ""),
        "location": resource.get("Location", ""),
        "location_id": resource.get("location_id"),
        "organization_id": organization_id,
        "quantity": quantity,
        "purpose": str(purpose or "").strip()[:200],
        "allocated_at": now,
        "created_at": now,
        "updated_at": now,
        "created_by": actor_sub,
    }


def everyday_allocate_individual(body, organization_id, actor_sub, actor_role, resource, tables):
    if normalize_tracking_mode(resource.get("tracking_mode")) != "INDIVIDUAL":
        raise EverydayOperationError(409, "Resource is not an individual item")

    from_reserved = str(resource.get("operational_status") or "").upper() == "RESERVED"
    now = _now()
    resource_id = str(resource["resource_id"]).strip()
    allocation_id = _everyday_allocation_id(resource_id)
    purpose = (body or {}).get("purpose")

    if from_reserved:
        condition = "organization_id = :organization_id AND operational_status = :reserved"
        values = {
            ":allocated": "ALLOCATED",
            ":false": False,
            ":reserved": "RESERVED",
            ":organization_id": organization_id,
        }
        previous = "RESERVED"
    else:
        condition = (
            "organization_id = :organization_id AND Available = :true AND "
            "(attribute_not_exists(operational_status) OR operational_status = :available)"
        )
        values = {
            ":allocated": "ALLOCATED",
            ":false": False,
            ":true": True,
            ":available": "AVAILABLE",
            ":organization_id": organization_id,
        }
        previous = "AVAILABLE"

    allocation = _everyday_allocation_item(allocation_id, resource, organization_id, actor_sub, 1, purpose, now)
    update_expression = "SET operational_status = :allocated, Available = :false"
    if from_reserved:
        update_expression += " REMOVE " + ", ".join(RESERVATION_HELD_ATTRIBUTES)

    try:
        _commit_everyday_allocation(
            tables,
            _allocation_transact_items(resource_id, update_expression, condition, values, allocation),
        )
    except ClientError as error:
        _conflict_from_client(error)

    _write_history(
        tables["history"],
        history_id=_history_id("HIST-EVERYDAY-", resource_id),
        resource_id=resource_id,
        organization_id=organization_id,
        location_id=resource.get("location_id", ""),
        resource_type=resource.get("Type", ""),
        location=resource.get("Location", ""),
        previous_status=previous,
        new_status="ALLOCATED",
        changed_at=now,
        actor_sub=actor_sub,
        reason="EVERYDAY_RESOURCE_ALLOCATED",
        allocation_id=allocation_id,
    )
    record_audit(
        tables["audit"],
        build_audit_event(
            organization_id,
            actor_sub,
            actor_role,
            "resource.everyday_allocate",
            "allocation",
            allocation_id,
            location_id=resource.get("location_id", ""),
        ),
    )
    return {
        "message": "Everyday allocation created",
        "allocation_id": allocation_id,
        "resource_id": resource_id,
        "status": EVERYDAY_STATUS_OPEN,
    }


def everyday_return(body, organization_id, actor_sub, actor_role, resource, tables):
    allocation_id = str((body or {}).get("allocation_id") or "").strip()

    if not allocation_id:
        raise EverydayOperationError(400, "allocation_id is required")

    allocation = tables["allocations"].get_item(Key={"allocation_id": allocation_id}).get("Item")

    if (
        not allocation
        or allocation.get("organization_id") != organization_id
        or allocation.get("resource_id") != resource["resource_id"]
        or allocation.get("allocation_type") != ALLOCATION_TYPE_EVERYDAY
    ):
        raise EverydayOperationError(404, "Record not found")

    if str(allocation.get("status", "")).upper() != EVERYDAY_STATUS_OPEN:
        raise EverydayOperationError(409, "Allocation is not open")

    mode = normalize_tracking_mode(resource.get("tracking_mode"))
    now = _now()
    resource_id = str(resource["resource_id"]).strip()
    quantity = int(allocation.get("quantity") or 1)
    previous = str(resource.get("operational_status") or "ALLOCATED").upper() or "ALLOCATED"

    if mode == "INDIVIDUAL":
        resource_expression = "SET operational_status = :available, Available = :true"
        resource_condition = (
            "organization_id = :organization_id AND "
            "(operational_status = :allocated OR operational_status = :in_use)"
        )
        resource_values = {
            ":available": "AVAILABLE",
            ":true": True,
            ":allocated": "ALLOCATED",
            ":in_use": "IN_USE",
            ":organization_id": organization_id,
        }
    else:
        resource_expression = (
            "SET quantity_allocated = quantity_allocated - :qty, "
            "quantity_available = quantity_available + :qty"
        )
        resource_condition = (
            "attribute_exists(resource_id) AND organization_id = :organization_id "
            "AND tracking_mode = :quantity AND quantity_allocated >= :qty "
            "AND quantity_available + quantity_reserved + quantity_allocated = quantity_total"
        )
        resource_values = {
            ":qty": quantity,
            ":organization_id": organization_id,
            ":quantity": "QUANTITY",
        }

    allocation_values = {
        ":returned": EVERYDAY_STATUS_RETURNED,
        ":open": EVERYDAY_STATUS_OPEN,
        ":now": now,
        ":actor": actor_sub,
        ":organization_id": organization_id,
        ":resource_id": resource_id,
        ":everyday": ALLOCATION_TYPE_EVERYDAY,
    }

    try:
        _commit_everyday_allocation(
            tables,
            _return_transact_items(
                resource_id,
                resource_expression,
                resource_condition,
                resource_values,
                allocation_id,
                allocation_values,
            ),
        )
    except ClientError as error:
        _conflict_from_client(error)

    _write_history(
        tables["history"],
        history_id=_history_id("HIST-EVERYDAY-RETURN-", resource_id),
        resource_id=resource_id,
        organization_id=organization_id,
        location_id=resource.get("location_id", ""),
        resource_type=resource.get("Type", ""),
        location=resource.get("Location", ""),
        previous_status=previous if previous in {"ALLOCATED", "IN_USE"} else "ALLOCATED",
        new_status="AVAILABLE",
        changed_at=now,
        actor_sub=actor_sub,
        reason="EVERYDAY_RESOURCE_RETURNED",
        allocation_id=allocation_id,
        quantity=quantity,
    )
    record_audit(
        tables["audit"],
        build_audit_event(
            organization_id,
            actor_sub,
            actor_role,
            "resource.everyday_return",
            "allocation",
            allocation_id,
            location_id=resource.get("location_id", ""),
        ),
    )
    return {
        "message": "Everyday allocation returned",
        "allocation_id": allocation_id,
        "status": EVERYDAY_STATUS_RETURNED,
    }


def reserve_quantity(body, organization_id, actor_sub, actor_role, resource, tables):
    if normalize_tracking_mode(resource.get("tracking_mode")) != "QUANTITY":
        raise EverydayOperationError(409, "Resource is not a quantity pool")

    quantity = _positive_quantity((body or {}).get("quantity"), "Quantity")
    now = _now()
    resource_id = resource["resource_id"]

    try:
        tables["resources"].update_item(
            Key={"resource_id": resource_id},
            UpdateExpression=(
                "SET quantity_available = quantity_available - :qty, "
                "quantity_reserved = quantity_reserved + :qty"
            ),
            ConditionExpression=(
                "organization_id = :organization_id AND tracking_mode = :quantity "
                "AND quantity_available >= :qty"
            ),
            ExpressionAttributeValues={
                ":qty": quantity,
                ":organization_id": organization_id,
                ":quantity": "QUANTITY",
            },
        )
    except ClientError as error:
        _conflict_from_client(error)

    _write_history(
        tables["history"],
        history_id=_history_id("HIST-QTY-RESERVE-", resource_id),
        resource_id=resource_id,
        organization_id=organization_id,
        location_id=resource.get("location_id", ""),
        resource_type=resource.get("Type", ""),
        location=resource.get("Location", ""),
        previous_status="AVAILABLE",
        new_status="RESERVED",
        changed_at=now,
        actor_sub=actor_sub,
        reason="QUANTITY_RESERVED",
        quantity=quantity,
    )
    record_audit(
        tables["audit"],
        build_audit_event(
            organization_id,
            actor_sub,
            actor_role,
            "resource.quantity_reserve",
            "resource",
            resource_id,
            location_id=resource.get("location_id", ""),
            metadata={"quantity": quantity},
        ),
    )
    return {"message": "Quantity reserved", "resource_id": resource_id, "quantity": quantity}


def everyday_allocate_quantity(body, organization_id, actor_sub, actor_role, resource, tables):
    if normalize_tracking_mode(resource.get("tracking_mode")) != "QUANTITY":
        raise EverydayOperationError(409, "Resource is not a quantity pool")

    quantity = _positive_quantity((body or {}).get("quantity"), "Quantity")
    from_reserved = (body or {}).get("from_reserved") is True
    now = _now()
    resource_id = resource["resource_id"]
    allocation_id = _everyday_allocation_id(resource_id)
    purpose = (body or {}).get("purpose")

    quantity_guard = (
        "attribute_exists(resource_id) AND organization_id = :organization_id "
        "AND tracking_mode = :quantity "
        "AND quantity_allocated + :qty <= quantity_total "
        "AND quantity_available + quantity_reserved + quantity_allocated = quantity_total "
        "AND (attribute_not_exists(operational_status) OR operational_status <> :retired)"
    )
    if from_reserved:
        update_expression = (
            "SET quantity_reserved = quantity_reserved - :qty, "
            "quantity_allocated = quantity_allocated + :qty"
        )
        condition = quantity_guard + " AND quantity_reserved >= :qty"
    else:
        update_expression = (
            "SET quantity_available = quantity_available - :qty, "
            "quantity_allocated = quantity_allocated + :qty"
        )
        condition = quantity_guard + " AND quantity_available >= :qty"

    allocation = _everyday_allocation_item(
        allocation_id, resource, organization_id, actor_sub, quantity, purpose, now
    )
    resource_id = str(resource_id).strip()
    values = {
        ":qty": quantity,
        ":organization_id": organization_id,
        ":quantity": "QUANTITY",
        ":retired": "RETIRED",
    }

    try:
        _commit_everyday_allocation(
            tables,
            _allocation_transact_items(resource_id, update_expression, condition, values, allocation),
        )
    except ClientError as error:
        _conflict_from_client(error)

    _write_history(
        tables["history"],
        history_id=_history_id("HIST-QTY-EVERYDAY-", resource_id),
        resource_id=resource_id,
        organization_id=organization_id,
        location_id=resource.get("location_id", ""),
        resource_type=resource.get("Type", ""),
        location=resource.get("Location", ""),
        previous_status="AVAILABLE",
        new_status="ALLOCATED",
        changed_at=now,
        actor_sub=actor_sub,
        reason="EVERYDAY_QUANTITY_ALLOCATED",
        allocation_id=allocation_id,
        quantity=quantity,
    )
    record_audit(
        tables["audit"],
        build_audit_event(
            organization_id,
            actor_sub,
            actor_role,
            "resource.quantity_allocate",
            "allocation",
            allocation_id,
            location_id=resource.get("location_id", ""),
            metadata={"quantity": quantity},
        ),
    )
    return {
        "message": "Everyday quantity allocation created",
        "allocation_id": allocation_id,
        "resource_id": resource_id,
        "quantity": quantity,
        "status": EVERYDAY_STATUS_OPEN,
    }


def _log_resource(operation, organization_id, actor_sub, resource_id):
    from observability import log_event

    log_event(
        "INFO",
        "resources",
        operation,
        "committed",
        organization_id=organization_id,
        actor_sub=actor_sub,
        resource_id=resource_id,
    )


def dispatch_everyday(method, path, body, organization_id, actor_sub, actor_role, load_resource, tables):
    resource = load_resource(_resource_id(body))
    resource_id = resource.get("resource_id") or _resource_id(body)

    if path.endswith("/reservation-release"):
        result = release_reservation(body, organization_id, actor_sub, actor_role, resource, tables)
        _log_resource("reservation.release", organization_id, actor_sub, resource_id)
        return result

    if path.endswith("/reserve"):
        mode = normalize_tracking_mode(resource.get("tracking_mode"))

        if mode == "QUANTITY":
            result = reserve_quantity(body, organization_id, actor_sub, actor_role, resource, tables)
            _log_resource("quantity.reserve", organization_id, actor_sub, resource_id)
            return result

        result = reserve_individual(body, organization_id, actor_sub, actor_role, resource, tables)
        _log_resource("reserve", organization_id, actor_sub, resource_id)
        return result

    if path.endswith("/everyday/return"):
        result = everyday_return(body, organization_id, actor_sub, actor_role, resource, tables)
        _log_resource("everyday.return", organization_id, actor_sub, resource_id)
        return result

    if path.endswith("/everyday"):
        mode = normalize_tracking_mode(resource.get("tracking_mode"))

        if mode == "QUANTITY":
            result = everyday_allocate_quantity(body, organization_id, actor_sub, actor_role, resource, tables)
            _log_resource("quantity.allocate", organization_id, actor_sub, resource_id)
            return result

        result = everyday_allocate_individual(body, organization_id, actor_sub, actor_role, resource, tables)
        _log_resource("everyday.allocate", organization_id, actor_sub, resource_id)
        return result

    raise EverydayOperationError(404, "Not found")
