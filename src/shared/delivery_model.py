"""Allocation delivery tracking. Local application records, not GPS or AWS proof.

This module does not create AWS tables, API Gateway routes, or production
delivery operations. Application status is not evidence that transport occurred.
"""

from datetime import datetime, timezone

DELIVERY_ID_PREFIX = "DELIV-"
HANDOVER_ID_PREFIX = "HO-"

STATUS_ALLOCATED = "ALLOCATED"
STATUS_READY_FOR_DISPATCH = "READY_FOR_DISPATCH"
STATUS_DISPATCHED = "DISPATCHED"
STATUS_IN_TRANSIT = "IN_TRANSIT"
STATUS_DELIVERED = "DELIVERED"
STATUS_PARTIALLY_DELIVERED = "PARTIALLY_DELIVERED"
STATUS_RECEIVED = "RECEIVED"
STATUS_PARTIALLY_RECEIVED = "PARTIALLY_RECEIVED"
STATUS_HANDOVER_PENDING = "HANDOVER_PENDING"
STATUS_HANDOVER_COMPLETED = "HANDOVER_COMPLETED"
STATUS_DELIVERY_FAILED = "DELIVERY_FAILED"
STATUS_CANCELLED = "CANCELLED"

DELIVERY_STATUSES = (
    STATUS_ALLOCATED,
    STATUS_READY_FOR_DISPATCH,
    STATUS_DISPATCHED,
    STATUS_IN_TRANSIT,
    STATUS_DELIVERED,
    STATUS_PARTIALLY_DELIVERED,
    STATUS_RECEIVED,
    STATUS_PARTIALLY_RECEIVED,
    STATUS_HANDOVER_PENDING,
    STATUS_HANDOVER_COMPLETED,
    STATUS_DELIVERY_FAILED,
    STATUS_CANCELLED,
)

TERMINAL_STATUSES = {
    STATUS_HANDOVER_COMPLETED,
    STATUS_DELIVERY_FAILED,
    STATUS_CANCELLED,
}

ACTIVE_DELIVERY_STATUSES = set(DELIVERY_STATUSES) - TERMINAL_STATUSES

ELIGIBLE_ALLOCATION_STATUSES = {"ALLOCATED"}

MANUAL_STATUS_TRANSITIONS = {
    STATUS_ALLOCATED: {STATUS_READY_FOR_DISPATCH, STATUS_CANCELLED},
    STATUS_READY_FOR_DISPATCH: {STATUS_DISPATCHED, STATUS_CANCELLED},
    STATUS_DISPATCHED: {
        STATUS_IN_TRANSIT,
        STATUS_DELIVERED,
        STATUS_PARTIALLY_DELIVERED,
        STATUS_DELIVERY_FAILED,
    },
    STATUS_IN_TRANSIT: {
        STATUS_DELIVERED,
        STATUS_PARTIALLY_DELIVERED,
        STATUS_DELIVERY_FAILED,
    },
}

RECEIPT_FROM = {STATUS_DELIVERED, STATUS_PARTIALLY_DELIVERED}
HANDOVER_FROM = {STATUS_RECEIVED, STATUS_PARTIALLY_RECEIVED, STATUS_HANDOVER_PENDING}

LIST_FIELDS = (
    "delivery_id",
    "allocation_id",
    "request_id",
    "resource_id",
    "resource_type",
    "organization_id",
    "location_id",
    "source_location",
    "destination_location",
    "quantity_allocated",
    "quantity_dispatched",
    "quantity_received",
    "quantity_damaged",
    "quantity_missing",
    "quantity_handed_over",
    "status",
    "discrepancy",
    "created_at",
    "updated_at",
    "dispatch_at",
    "estimated_arrival",
    "actual_arrival",
)

DETAIL_FIELDS = LIST_FIELDS + (
    "vehicle_number",
    "transport_provider",
    "driver_name",
    "driver_contact",
    "transport_remarks",
    "receiver_name",
    "receiver_organization",
    "receiver_contact",
    "receipt_notes",
    "receipt_at",
    "receipt_confirmed",
    "handover_id",
    "handover_sender",
    "handover_receiver",
    "handover_remarks",
    "handover_at",
    "handover_recorded",
    "handover_verified",
    "handover_verified_at",
    "events",
)

SENSITIVE_LIST_FIELDS = {
    "driver_contact",
    "receiver_contact",
}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def as_quantity(value, field_name):
    if value is None or value == "":
        raise ValueError(f"{field_name} is required")
    if isinstance(value, bool):
        raise ValueError(f"{field_name} is invalid")
    try:
        quantity = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field_name} is invalid")
    if quantity < 0:
        raise ValueError(f"{field_name} cannot be negative")
    return quantity


def optional_quantity(value, field_name):
    if value is None or value == "":
        return 0
    return as_quantity(value, field_name)


def allocation_quantity(allocation):
    raw = allocation.get("quantity")
    if raw is None or raw == "":
        return 1
    return as_quantity(raw, "quantity")


def project(item, fields):
    if not isinstance(item, dict):
        return {}
    view = {}
    for name in fields:
        if name not in item:
            continue
        value = item[name]
        if isinstance(value, list):
            value = [dict(entry) if isinstance(entry, dict) else entry for entry in value]
        elif isinstance(value, dict):
            value = dict(value)
        view[name] = value
    return view


def list_view(item):
    return project(item, LIST_FIELDS)


def detail_view(item):
    return project(item, DETAIL_FIELDS)


def has_discrepancy(item):
    allocated = int(item.get("quantity_allocated") or 0)
    dispatched = int(item.get("quantity_dispatched") or 0)
    received = int(item.get("quantity_received") or 0)
    damaged = int(item.get("quantity_damaged") or 0)
    missing = int(item.get("quantity_missing") or 0)
    handed = int(item.get("quantity_handed_over") or 0)
    if dispatched > allocated:
        return True
    if received + damaged + missing > dispatched:
        return True
    if received < dispatched and item.get("receipt_confirmed") is True:
        return True
    if damaged > 0 or missing > 0:
        return True
    if handed > received:
        return True
    if dispatched < allocated and item.get("status") in TERMINAL_STATUSES:
        return True
    return False


def tally_bucket(item):
    status = str(item.get("status") or "")
    receipt_pending = status in {STATUS_DELIVERED, STATUS_PARTIALLY_DELIVERED}
    handover_pending = status in {
        STATUS_RECEIVED,
        STATUS_PARTIALLY_RECEIVED,
        STATUS_HANDOVER_PENDING,
    }
    completed = status == STATUS_HANDOVER_COMPLETED
    failed = status == STATUS_DELIVERY_FAILED
    return {
        "status": status,
        "receipt_pending": receipt_pending,
        "handover_pending": handover_pending,
        "completed": completed,
        "failed": failed,
    }
