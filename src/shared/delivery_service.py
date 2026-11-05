"""Delivery records for existing allocations. Does not mutate allocation quantities."""

import os
import uuid

from botocore.exceptions import ClientError

from access import AccessError, query_by_organization, require_owned
from audit import build_audit_event, record_audit
from delivery_model import (
    ACTIVE_DELIVERY_STATUSES,
    DELIVERY_ID_PREFIX,
    ELIGIBLE_ALLOCATION_STATUSES,
    HANDOVER_FROM,
    HANDOVER_ID_PREFIX,
    MANUAL_STATUS_TRANSITIONS,
    RECEIPT_FROM,
    STATUS_ALLOCATED,
    STATUS_DISPATCHED,
    STATUS_HANDOVER_COMPLETED,
    STATUS_HANDOVER_PENDING,
    STATUS_PARTIALLY_RECEIVED,
    STATUS_READY_FOR_DISPATCH,
    STATUS_RECEIVED,
    TERMINAL_STATUSES,
    allocation_quantity,
    as_quantity,
    detail_view,
    has_discrepancy,
    list_view,
    optional_quantity,
    tally_bucket,
    utc_now,
)


class DeliveryError(Exception):
    def __init__(self, status_code, message):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def deliveries_table():
    import boto3

    return boto3.resource("dynamodb").Table(os.environ.get("DELIVERIES_TABLE", "Deliveries"))


def allocations_table():
    import boto3

    return boto3.resource("dynamodb").Table("Allocations")


def audit_table():
    import boto3

    return boto3.resource("dynamodb").Table(os.environ.get("AUDIT_TABLE", "AuditEvents"))


def _clean(value):
    if value is None:
        return ""
    return str(value).strip()


def _event(actor_sub, action, from_status, to_status, note=""):
    return {
        "at": utc_now(),
        "actor_sub": actor_sub or "",
        "action": action,
        "from_status": from_status or "",
        "to_status": to_status or "",
        "note": _clean(note)[:500],
    }


def _append_event(item, actor_sub, action, from_status, to_status, note=""):
    events = list(item.get("events") or [])
    events.append(_event(actor_sub, action, from_status, to_status, note))
    item["events"] = events
    item["updated_at"] = utc_now()
    item["discrepancy"] = has_discrepancy(item)
    item["version"] = int(item.get("version") or 0) + 1
    return item


def _put_new(item):
    deliveries_table().put_item(
        Item=item,
        ConditionExpression="attribute_not_exists(delivery_id)",
    )


def _put_versioned(item, expected_version, expected_status):
    try:
        deliveries_table().put_item(
            Item=item,
            ConditionExpression=(
                "attribute_exists(delivery_id) AND organization_id = :org "
                "AND version = :version AND #s = :status"
            ),
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":org": item["organization_id"],
                ":version": expected_version,
                ":status": expected_status,
            },
        )
    except ClientError as error:
        code = error.response.get("Error", {}).get("Code", "")
        if code == "ConditionalCheckFailedException":
            raise DeliveryError(409, "Delivery was updated by another request")
        raise


def _load(delivery_id, organization_id):
    delivery_id = _clean(delivery_id)
    if not delivery_id.startswith(DELIVERY_ID_PREFIX):
        raise DeliveryError(400, "Delivery ID is invalid")
    item = deliveries_table().get_item(Key={"delivery_id": delivery_id}).get("Item")
    owned = require_owned(item, organization_id)
    return owned


def _allocation(allocation_id, organization_id):
    allocation_id = _clean(allocation_id)
    if not allocation_id:
        raise DeliveryError(400, "allocation_id is required")
    item = allocations_table().get_item(Key={"allocation_id": allocation_id}).get("Item")
    owned = require_owned(item, organization_id)
    status = str(owned.get("status") or owned.get("Status") or "").upper()
    if status not in ELIGIBLE_ALLOCATION_STATUSES:
        raise DeliveryError(409, "Allocation is not eligible for delivery tracking")
    return owned


def _active_for_allocation(organization_id, allocation_id, location_id=None):
    rows = query_by_organization(deliveries_table(), organization_id, location_id)
    matches = []
    for row in rows:
        if row.get("organization_id") != organization_id:
            continue
        if row.get("allocation_id") != allocation_id:
            continue
        if str(row.get("status") or "") in ACTIVE_DELIVERY_STATUSES:
            matches.append(row)
    return matches


def _audit(organization_id, actor_sub, actor_role, action, delivery_id, location_id, metadata=None):
    record_audit(
        audit_table(),
        build_audit_event(
            organization_id,
            actor_sub,
            actor_role,
            action,
            "delivery",
            delivery_id,
            location_id=location_id or "",
            metadata=metadata or {},
        ),
    )


def create_delivery(organization_id, actor_sub, actor_role, body):
    allocation_id = _clean((body or {}).get("allocation_id"))
    allocation = _allocation(allocation_id, organization_id)
    location_id = _clean(allocation.get("location_id"))
    existing = _active_for_allocation(organization_id, allocation_id, location_id)
    if not existing:
        existing = _active_for_allocation(organization_id, allocation_id)
    if existing:
        raise DeliveryError(409, "An active delivery already exists for this allocation")

    quantity = allocation_quantity(allocation)
    now = utc_now()
    delivery_id = DELIVERY_ID_PREFIX + uuid.uuid4().hex[:12].upper()
    source = _clean(allocation.get("location"))
    item = {
        "delivery_id": delivery_id,
        "allocation_id": allocation_id,
        "request_id": _clean(allocation.get("request_id")),
        "resource_id": _clean(allocation.get("resource_id")),
        "resource_type": _clean(allocation.get("resource_type")),
        "organization_id": organization_id,
        "location_id": location_id,
        "source_location": source,
        "destination_location": "",
        "quantity_allocated": quantity,
        "quantity_dispatched": 0,
        "quantity_received": 0,
        "quantity_damaged": 0,
        "quantity_missing": 0,
        "quantity_handed_over": 0,
        "status": STATUS_ALLOCATED,
        "discrepancy": False,
        "receipt_confirmed": False,
        "handover_recorded": False,
        "handover_verified": False,
        "created_at": now,
        "updated_at": now,
        "version": 1,
        "events": [
            _event(actor_sub, "delivery.created", "", STATUS_ALLOCATED, "Delivery record created")
        ],
    }
    try:
        _put_new(item)
    except ClientError as error:
        code = error.response.get("Error", {}).get("Code", "")
        if code == "ConditionalCheckFailedException":
            raise DeliveryError(409, "Delivery ID already exists")
        raise
    _audit(organization_id, actor_sub, actor_role, "delivery.create", delivery_id, location_id)
    return {"message": "Delivery created", "delivery": detail_view(item)}


def list_deliveries(organization_id, query):
    query = query or {}
    location_id = _clean(query.get("location_id")) or None
    rows = query_by_organization(deliveries_table(), organization_id, location_id)
    delivery_id = _clean(query.get("delivery_id")).upper()
    allocation_id = _clean(query.get("allocation_id")).upper()
    resource_q = _clean(query.get("q")).lower()
    status = _clean(query.get("status")).upper()
    views = []
    tallies = {
        "total": 0,
        "ALLOCATED": 0,
        "READY_FOR_DISPATCH": 0,
        "DISPATCHED": 0,
        "IN_TRANSIT": 0,
        "DELIVERED": 0,
        "PARTIALLY_DELIVERED": 0,
        "RECEIVED": 0,
        "PARTIALLY_RECEIVED": 0,
        "receipt_pending": 0,
        "handover_pending": 0,
        "completed": 0,
        "failed": 0,
        "CANCELLED": 0,
        "HANDOVER_PENDING": 0,
        "HANDOVER_COMPLETED": 0,
        "DELIVERY_FAILED": 0,
    }
    for row in rows:
        if row.get("organization_id") != organization_id:
            continue
        if location_id and row.get("location_id") != location_id:
            continue
        if delivery_id and _clean(row.get("delivery_id")).upper() != delivery_id:
            continue
        if allocation_id and _clean(row.get("allocation_id")).upper() != allocation_id:
            continue
        if status and status != "ALL" and _clean(row.get("status")).upper() != status:
            continue
        if resource_q:
            hay = " ".join(
                [
                    _clean(row.get("resource_id")),
                    _clean(row.get("resource_type")),
                    _clean(row.get("delivery_id")),
                    _clean(row.get("allocation_id")),
                ]
            ).lower()
            if resource_q not in hay:
                continue
        views.append(list_view(row))
        tallies["total"] += 1
        bucket = tally_bucket(row)
        key = bucket["status"]
        if key in tallies:
            tallies[key] += 1
        if bucket["receipt_pending"]:
            tallies["receipt_pending"] += 1
        if bucket["handover_pending"]:
            tallies["handover_pending"] += 1
        if bucket["completed"]:
            tallies["completed"] += 1
        if bucket["failed"]:
            tallies["failed"] += 1
    views.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
    return {
        "message": "Deliveries retrieved successfully",
        "deliveries": views,
        "counts": tallies,
        "record_source": "application_records",
        "note": "Counts are from stored delivery records for this organization. They are not GPS or carrier proof.",
    }


def get_delivery(organization_id, delivery_id):
    item = _load(delivery_id, organization_id)
    return {"message": "Delivery retrieved successfully", "delivery": detail_view(item)}


def get_history(organization_id, delivery_id):
    item = _load(delivery_id, organization_id)
    events = list(item.get("events") or [])
    return {
        "message": "Delivery history retrieved successfully",
        "delivery_id": item["delivery_id"],
        "events": events,
    }


def _save(item, actor_sub, actor_role, action, from_status, note=""):
    expected_version = int(item.get("version") or 1)
    expected_status = from_status
    working = dict(item)
    working.pop("_note", None)
    working["version"] = expected_version
    _append_event(working, actor_sub, action, from_status, working["status"], note)
    _put_versioned(working, expected_version, expected_status)
    _audit(
        working["organization_id"],
        actor_sub,
        actor_role,
        action,
        working["delivery_id"],
        working.get("location_id"),
    )
    return working


def update_transport(organization_id, actor_sub, actor_role, delivery_id, body):
    item = _load(delivery_id, organization_id)
    status = item["status"]
    if status not in {STATUS_ALLOCATED, STATUS_READY_FOR_DISPATCH}:
        raise DeliveryError(409, "Transport details cannot be changed after dispatch")
    body = body or {}
    from_status = status
    for field in (
        "vehicle_number",
        "transport_provider",
        "driver_name",
        "driver_contact",
        "source_location",
        "destination_location",
        "estimated_arrival",
        "transport_remarks",
    ):
        if field in body:
            item[field] = _clean(body.get(field))[:120]
    if "dispatch_quantity" in body:
        quantity = as_quantity(body.get("dispatch_quantity"), "dispatch_quantity")
        if quantity > int(item.get("quantity_allocated") or 0):
            raise DeliveryError(400, "Dispatch quantity cannot exceed allocated quantity")
        item["quantity_dispatched"] = quantity
    item["status"] = from_status
    saved = _save(item, actor_sub, actor_role, "delivery.transport_updated", from_status)
    return {"message": "Transport details recorded", "delivery": detail_view(saved)}


def mark_ready(organization_id, actor_sub, actor_role, delivery_id, body):
    item = _load(delivery_id, organization_id)
    if item["status"] != STATUS_ALLOCATED:
        raise DeliveryError(409, "Delivery is not waiting for dispatch readiness")
    body = body or {}
    if "dispatch_quantity" in body:
        quantity = as_quantity(body.get("dispatch_quantity"), "dispatch_quantity")
        if quantity > int(item.get("quantity_allocated") or 0):
            raise DeliveryError(400, "Dispatch quantity cannot exceed allocated quantity")
        item["quantity_dispatched"] = quantity
    for field in (
        "vehicle_number",
        "transport_provider",
        "driver_name",
        "driver_contact",
        "source_location",
        "destination_location",
        "estimated_arrival",
        "transport_remarks",
    ):
        if field in body:
            item[field] = _clean(body.get(field))[:120]
    dispatched = int(item.get("quantity_dispatched") or 0)
    if dispatched < 1:
        raise DeliveryError(400, "Dispatch quantity is required")
    if not _clean(item.get("source_location")) or not _clean(item.get("destination_location")):
        raise DeliveryError(400, "Source and destination locations are required")
    if not _clean(item.get("vehicle_number")) and not _clean(item.get("transport_provider")):
        raise DeliveryError(400, "Vehicle number or transport provider is required")
    from_status = item["status"]
    item["status"] = STATUS_READY_FOR_DISPATCH
    saved = _save(item, actor_sub, actor_role, "delivery.ready_for_dispatch", from_status)
    return {"message": "Delivery is ready for dispatch", "delivery": detail_view(saved)}


def dispatch_delivery(organization_id, actor_sub, actor_role, delivery_id, body):
    item = _load(delivery_id, organization_id)
    if item["status"] != STATUS_READY_FOR_DISPATCH:
        raise DeliveryError(409, "Delivery is not ready for dispatch")
    body = body or {}
    dispatched = int(item.get("quantity_dispatched") or 0)
    if "dispatch_quantity" in body:
        dispatched = as_quantity(body.get("dispatch_quantity"), "dispatch_quantity")
        if dispatched > int(item.get("quantity_allocated") or 0):
            raise DeliveryError(400, "Dispatch quantity cannot exceed allocated quantity")
        item["quantity_dispatched"] = dispatched
    if dispatched < 1:
        raise DeliveryError(400, "Dispatch quantity is required")
    from_status = item["status"]
    item["status"] = STATUS_DISPATCHED
    item["dispatch_at"] = utc_now()
    saved = _save(item, actor_sub, actor_role, "delivery.dispatched", from_status)
    try:
        from emergency_notify import notify_delivery_dispatched

        notify_delivery_dispatched(
            organization_id=organization_id,
            actor_sub=actor_sub,
            request_id=saved.get("request_id") or "",
            allocation_id=saved.get("allocation_id") or "",
            resource_id=saved.get("resource_id") or "",
        )
    except Exception:
        pass
    return {"message": "Delivery dispatched", "delivery": detail_view(saved)}


def update_status(organization_id, actor_sub, actor_role, delivery_id, body):
    item = _load(delivery_id, organization_id)
    target = _clean((body or {}).get("status")).upper()
    allowed = MANUAL_STATUS_TRANSITIONS.get(item["status"], set())
    if target not in allowed:
        raise DeliveryError(409, "Delivery status transition is not allowed")
    from_status = item["status"]
    item["status"] = target
    if target in {"DELIVERED", "PARTIALLY_DELIVERED"}:
        item["actual_arrival"] = _clean((body or {}).get("actual_arrival")) or utc_now()
        if target == "PARTIALLY_DELIVERED" and int(item.get("quantity_dispatched") or 0) >= int(
            item.get("quantity_allocated") or 0
        ):
            item["discrepancy"] = True
    note = _clean((body or {}).get("note"))
    saved = _save(item, actor_sub, actor_role, "delivery.status_updated", from_status, note)
    return {"message": "Delivery status updated", "delivery": detail_view(saved)}


def confirm_receipt(organization_id, actor_sub, actor_role, delivery_id, body):
    item = _load(delivery_id, organization_id)
    if item["status"] not in RECEIPT_FROM:
        raise DeliveryError(409, "Receipt can be confirmed only after delivery is reported")
    if item.get("receipt_confirmed") is True:
        raise DeliveryError(409, "Receipt has already been confirmed")
    body = body or {}
    dispatched = int(item.get("quantity_dispatched") or 0)
    received = as_quantity(body.get("quantity_received"), "quantity_received")
    damaged = optional_quantity(body.get("quantity_damaged"), "quantity_damaged")
    missing = optional_quantity(body.get("quantity_missing"), "quantity_missing")
    if received > dispatched:
        raise DeliveryError(400, "Quantity received cannot exceed dispatched quantity")
    if damaged > dispatched:
        raise DeliveryError(400, "Damaged quantity cannot exceed dispatched quantity")
    if missing > dispatched:
        raise DeliveryError(400, "Missing quantity cannot exceed dispatched quantity")
    if received + damaged + missing > dispatched:
        raise DeliveryError(400, "Received, damaged, and missing quantities exceed dispatched quantity")
    receiver_name = _clean(body.get("receiver_name"))
    if not receiver_name:
        raise DeliveryError(400, "Receiver name is required")
    from_status = item["status"]
    item["quantity_received"] = received
    item["quantity_damaged"] = damaged
    item["quantity_missing"] = missing
    item["receiver_name"] = receiver_name[:120]
    item["receiver_organization"] = _clean(body.get("receiver_organization"))[:120]
    item["receiver_contact"] = _clean(body.get("receiver_contact"))[:120]
    item["receipt_notes"] = _clean(body.get("receipt_notes"))[:500]
    item["receipt_at"] = utc_now()
    item["receipt_confirmed"] = True
    complete = received + damaged + missing == dispatched and missing == 0 and damaged == 0 and received == dispatched
    item["status"] = STATUS_RECEIVED if complete else STATUS_PARTIALLY_RECEIVED
    saved = _save(item, actor_sub, actor_role, "delivery.receipt_confirmed", from_status)
    try:
        from emergency_notify import notify_delivery_received

        notify_delivery_received(
            organization_id=organization_id,
            actor_sub=actor_sub,
            request_id=saved.get("request_id") or "",
            allocation_id=saved.get("allocation_id") or "",
            resource_id=saved.get("resource_id") or "",
        )
    except Exception:
        pass
    return {"message": "Receipt confirmed", "delivery": detail_view(saved)}


def record_handover(organization_id, actor_sub, actor_role, delivery_id, body):
    item = _load(delivery_id, organization_id)
    if item["status"] not in HANDOVER_FROM:
        raise DeliveryError(409, "Physical handover requires a confirmed receipt")
    if item.get("handover_recorded") is True:
        raise DeliveryError(409, "Physical handover has already been recorded")
    if item.get("receipt_confirmed") is not True:
        raise DeliveryError(409, "Physical handover requires a confirmed receipt")
    body = body or {}
    handed = as_quantity(body.get("quantity_handed_over"), "quantity_handed_over")
    received = int(item.get("quantity_received") or 0)
    if handed > received:
        raise DeliveryError(400, "Quantity handed over cannot exceed quantity received")
    sender = _clean(body.get("handover_sender"))
    receiver = _clean(body.get("handover_receiver")) or _clean(item.get("receiver_name"))
    if not sender or not receiver:
        raise DeliveryError(400, "Sender and receiver are required")
    from_status = item["status"]
    item["handover_id"] = HANDOVER_ID_PREFIX + uuid.uuid4().hex[:12].upper()
    item["quantity_handed_over"] = handed
    item["handover_sender"] = sender[:120]
    item["handover_receiver"] = receiver[:120]
    item["handover_remarks"] = _clean(body.get("handover_remarks"))[:500]
    item["handover_at"] = utc_now()
    item["handover_recorded"] = True
    item["handover_verified"] = False
    item["status"] = STATUS_HANDOVER_PENDING
    saved = _save(item, actor_sub, actor_role, "delivery.handover_recorded", from_status)
    return {
        "message": "Physical handover recorded. Verification is still required.",
        "delivery": detail_view(saved),
    }


def verify_handover(organization_id, actor_sub, actor_role, delivery_id, body):
    item = _load(delivery_id, organization_id)
    if item["status"] != STATUS_HANDOVER_PENDING:
        raise DeliveryError(409, "Handover is not waiting for verification")
    if item.get("handover_recorded") is not True:
        raise DeliveryError(409, "Physical handover has not been recorded")
    if item.get("handover_verified") is True:
        raise DeliveryError(409, "Handover has already been verified")
    if item.get("receipt_confirmed") is not True:
        raise DeliveryError(409, "Handover verification requires a confirmed receipt")
    handed = int(item.get("quantity_handed_over") or 0)
    received = int(item.get("quantity_received") or 0)
    if handed != received:
        raise DeliveryError(409, "Handover quantity does not match quantity received")
    confirmation = _clean((body or {}).get("confirmation")).lower()
    if confirmation not in {"verified", "confirm", "true", "yes"}:
        raise DeliveryError(400, "Handover verification confirmation is required")
    from_status = item["status"]
    item["handover_verified"] = True
    item["handover_verified_at"] = utc_now()
    item["handover_verified_by"] = actor_sub or ""
    item["status"] = STATUS_HANDOVER_COMPLETED
    saved = _save(item, actor_sub, actor_role, "delivery.handover_verified", from_status)
    return {
        "message": "Physical handover verified",
        "delivery": detail_view(saved),
        "note": "Verification is an application record of operator confirmation. It is not a signature, photo, or GPS proof.",
    }


