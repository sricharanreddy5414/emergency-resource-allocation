"""Everyday resource reservation, allocation, and return."""

import uuid
from datetime import datetime, timezone

from botocore.exceptions import ClientError

from audit import build_audit_event, record_audit
from resource_state import normalize_tracking_mode


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
    history_table.put_item(Item=fields)


def _conflict_from_client(error):
    if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
        raise EverydayOperationError(409, "Resource state conflict") from error

    raise error


def reserve_individual(body, organization_id, actor_sub, actor_role, resource, tables):
    if normalize_tracking_mode(resource.get("tracking_mode")) != "INDIVIDUAL":
        raise EverydayOperationError(409, "Resource is not an individual item")

    now = _now()
    resource_id = resource["resource_id"]

    try:
        tables["resources"].update_item(
            Key={"resource_id": resource_id},
            UpdateExpression=(
                "SET operational_status = :reserved, Available = :false, "
                "reserved_by = :actor, reserved_at = :now"
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


def release_reservation(body, organization_id, actor_sub, actor_role, resource, tables):
    if normalize_tracking_mode(resource.get("tracking_mode")) != "INDIVIDUAL":
        raise EverydayOperationError(409, "Resource is not an individual item")

    now = _now()
    resource_id = resource["resource_id"]

    try:
        tables["resources"].update_item(
            Key={"resource_id": resource_id},
            UpdateExpression=(
                "SET operational_status = :available, Available = :true "
                "REMOVE reserved_by, reserved_at"
            ),
            ConditionExpression=(
                "organization_id = :organization_id AND operational_status = :reserved"
            ),
            ExpressionAttributeValues={
                ":available": "AVAILABLE",
                ":true": True,
                ":reserved": "RESERVED",
                ":organization_id": organization_id,
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
    resource_id = resource["resource_id"]
    allocation_id = _everyday_allocation_id(resource_id)
    purpose = (body or {}).get("purpose")

    if from_reserved:
        condition = "organization_id = :organization_id AND operational_status = :reserved"
        values = {":allocated": "ALLOCATED", ":false": False, ":reserved": "RESERVED", ":organization_id": organization_id}
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

    client = tables["resources"].meta.client
    allocation = _everyday_allocation_item(allocation_id, resource, organization_id, actor_sub, 1, purpose, now)

    try:
        client.transact_write_items(
            TransactItems=[
                {
                    "Update": {
                        "TableName": tables["resources"].name,
                        "Key": {"resource_id": {"S": resource_id}},
                        "UpdateExpression": "SET operational_status = :allocated, Available = :false",
                        "ConditionExpression": condition,
                        "ExpressionAttributeValues": {key: _ddb(value) for key, value in values.items()},
                    }
                },
                {
                    "Put": {
                        "TableName": tables["allocations"].name,
                        "Item": _ddb_item(allocation),
                        "ConditionExpression": "attribute_not_exists(allocation_id)",
                    }
                },
            ]
        )
    except ClientError as error:
        _conflict_from_client(error)

    if from_reserved:
        try:
            tables["resources"].update_item(
                Key={"resource_id": resource_id},
                UpdateExpression="REMOVE reserved_by, reserved_at",
                ConditionExpression="organization_id = :organization_id",
                ExpressionAttributeValues={":organization_id": organization_id},
            )
        except ClientError:
            pass

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
    resource_id = resource["resource_id"]
    quantity = int(allocation.get("quantity") or 1)

    if mode == "INDIVIDUAL":
        transact = [
            {
                "Update": {
                    "TableName": tables["resources"].name,
                    "Key": {"resource_id": {"S": resource_id}},
                    "UpdateExpression": "SET operational_status = :available, Available = :true",
                    "ConditionExpression": (
                        "organization_id = :organization_id AND operational_status = :allocated"
                    ),
                    "ExpressionAttributeValues": {
                        ":available": {"S": "AVAILABLE"},
                        ":true": {"BOOL": True},
                        ":allocated": {"S": "ALLOCATED"},
                        ":organization_id": {"S": organization_id},
                    },
                }
            },
            {
                "Update": {
                    "TableName": tables["allocations"].name,
                    "Key": {"allocation_id": {"S": allocation_id}},
                    "UpdateExpression": (
                        "SET #status = :returned, returned_at = :now, returned_by = :actor, updated_at = :now"
                    ),
                    "ConditionExpression": "#status = :open AND organization_id = :organization_id",
                    "ExpressionAttributeNames": {"#status": "status"},
                    "ExpressionAttributeValues": {
                        ":returned": {"S": EVERYDAY_STATUS_RETURNED},
                        ":open": {"S": EVERYDAY_STATUS_OPEN},
                        ":now": {"S": now},
                        ":actor": {"S": actor_sub},
                        ":organization_id": {"S": organization_id},
                    },
                }
            },
        ]
        previous = "ALLOCATED"
    else:
        transact = [
            {
                "Update": {
                    "TableName": tables["resources"].name,
                    "Key": {"resource_id": {"S": resource_id}},
                    "UpdateExpression": (
                        "SET quantity_allocated = quantity_allocated - :qty, "
                        "quantity_available = quantity_available + :qty"
                    ),
                    "ConditionExpression": (
                        "organization_id = :organization_id AND tracking_mode = :quantity "
                        "AND quantity_allocated >= :qty"
                    ),
                    "ExpressionAttributeValues": {
                        ":qty": {"N": str(quantity)},
                        ":organization_id": {"S": organization_id},
                        ":quantity": {"S": "QUANTITY"},
                    },
                }
            },
            {
                "Update": {
                    "TableName": tables["allocations"].name,
                    "Key": {"allocation_id": {"S": allocation_id}},
                    "UpdateExpression": (
                        "SET #status = :returned, returned_at = :now, returned_by = :actor, updated_at = :now"
                    ),
                    "ConditionExpression": "#status = :open AND organization_id = :organization_id",
                    "ExpressionAttributeNames": {"#status": "status"},
                    "ExpressionAttributeValues": {
                        ":returned": {"S": EVERYDAY_STATUS_RETURNED},
                        ":open": {"S": EVERYDAY_STATUS_OPEN},
                        ":now": {"S": now},
                        ":actor": {"S": actor_sub},
                        ":organization_id": {"S": organization_id},
                    },
                }
            },
        ]
        previous = "ALLOCATED"

    try:
        tables["resources"].meta.client.transact_write_items(TransactItems=transact)
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
        previous_status=previous,
        new_status="AVAILABLE",
        changed_at=now,
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

    if from_reserved:
        update_expression = (
            "SET quantity_reserved = quantity_reserved - :qty, "
            "quantity_allocated = quantity_allocated + :qty"
        )
        condition = (
            "organization_id = :organization_id AND tracking_mode = :quantity "
            "AND quantity_reserved >= :qty"
        )
    else:
        update_expression = (
            "SET quantity_available = quantity_available - :qty, "
            "quantity_allocated = quantity_allocated + :qty"
        )
        condition = (
            "organization_id = :organization_id AND tracking_mode = :quantity "
            "AND quantity_available >= :qty"
        )

    allocation = _everyday_allocation_item(
        allocation_id, resource, organization_id, actor_sub, quantity, purpose, now
    )

    try:
        tables["resources"].meta.client.transact_write_items(
            TransactItems=[
                {
                    "Update": {
                        "TableName": tables["resources"].name,
                        "Key": {"resource_id": {"S": resource_id}},
                        "UpdateExpression": update_expression,
                        "ConditionExpression": condition,
                        "ExpressionAttributeValues": {
                            ":qty": {"N": str(quantity)},
                            ":organization_id": {"S": organization_id},
                            ":quantity": {"S": "QUANTITY"},
                        },
                    }
                },
                {
                    "Put": {
                        "TableName": tables["allocations"].name,
                        "Item": _ddb_item(allocation),
                        "ConditionExpression": "attribute_not_exists(allocation_id)",
                    }
                },
            ]
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


def _ddb(value):
    if isinstance(value, bool):
        return {"BOOL": value}

    if isinstance(value, int):
        return {"N": str(value)}

    return {"S": str(value)}


def _ddb_item(item):
    encoded = {}

    for key, value in item.items():
        if value is None:
            continue

        if isinstance(value, bool):
            encoded[key] = {"BOOL": value}
        elif isinstance(value, int):
            encoded[key] = {"N": str(value)}
        else:
            encoded[key] = {"S": str(value)}

    return encoded


def dispatch_everyday(method, path, body, organization_id, actor_sub, actor_role, load_resource, tables):
    resource = load_resource(_resource_id(body))

    if path.endswith("/reservation-release"):
        return release_reservation(body, organization_id, actor_sub, actor_role, resource, tables)

    if path.endswith("/reserve"):
        mode = normalize_tracking_mode(resource.get("tracking_mode"))

        if mode == "QUANTITY":
            return reserve_quantity(body, organization_id, actor_sub, actor_role, resource, tables)

        return reserve_individual(body, organization_id, actor_sub, actor_role, resource, tables)

    if path.endswith("/everyday/return"):
        return everyday_return(body, organization_id, actor_sub, actor_role, resource, tables)

    if path.endswith("/everyday"):
        mode = normalize_tracking_mode(resource.get("tracking_mode"))

        if mode == "QUANTITY":
            return everyday_allocate_quantity(body, organization_id, actor_sub, actor_role, resource, tables)

        return everyday_allocate_individual(body, organization_id, actor_sub, actor_role, resource, tables)

    raise EverydayOperationError(404, "Not found")
