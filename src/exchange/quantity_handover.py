"""Phase 7C quantity ownership transfer (Option B).

Consumes an EXCHANGE quantity hold into a requester destination pool
(merge into explicit destination_resource_id, or create PRIVATE pool).
"""

from __future__ import annotations

import re
import uuid

from botocore.exceptions import ClientError

from access import AccessError, require_location
from audit import build_audit_event, record_audit
from exchange_model import TABLE_NAME, meta_pk, meta_sk
from exchange_state import (
    ALLOCATION_TYPE_EXCHANGE,
    EXCHANGE_ALLOCATION_STATUS_OPEN,
    EXCHANGE_ALLOCATION_STATUS_RELEASED,
)
from resource_state import ResourceStateError, normalize_tracking_mode, quantity_snapshot
from visibility import PRIVATE_INDEX_ATTRIBUTES


class QuantityHandoverError(Exception):
    def __init__(self, status_code, message, code=None):
        self.status_code = status_code
        self.message = message
        self.code = code
        super().__init__(message)


def held_quantity(offer, allocation):
    try:
        if allocation.get("quantity") is not None:
            return int(allocation.get("quantity"))
        return int(offer.get("quantity_offered") or 0)
    except (TypeError, ValueError):
        return 0


def parse_required_transfer_quantity(body, held, *, error_cls):
    if not isinstance(body, dict) or "quantity" not in body or body.get("quantity") is None:
        raise error_cls(400, "quantity is required")
    raw = body.get("quantity")
    try:
        if isinstance(raw, bool):
            raise TypeError("bool")
        if isinstance(raw, float) and not float(raw).is_integer():
            raise error_cls(400, "quantity is invalid")
        quantity = int(raw)
    except (TypeError, ValueError) as error:
        raise error_cls(400, "quantity is invalid") from error
    if quantity <= 0:
        raise error_cls(400, "quantity is invalid")
    if quantity != held:
        raise error_cls(409, "Quantity must match the accepted exchange hold")
    return quantity


def new_quantity_destination_resource_id():
    return "EXQTY-" + uuid.uuid4().hex[:16].upper()


def assert_source_eligible(resource, provider_org, held_resource_id, n, *, error_cls):
    if not resource or resource.get("organization_id") != provider_org:
        raise error_cls(409, "Offered resource is no longer eligible")
    if str(resource.get("resource_id") or "") != str(held_resource_id or ""):
        raise error_cls(409, "Offered resource is no longer eligible")
    if normalize_tracking_mode(resource.get("tracking_mode")) != "QUANTITY":
        raise error_cls(409, "Offered resource is no longer eligible")
    status = str(resource.get("operational_status") or "").upper()
    if status in {"RETIRED", "DAMAGED", "MAINTENANCE"}:
        raise error_cls(409, "Offered resource is no longer eligible")
    try:
        snap = quantity_snapshot(resource)
    except ResourceStateError as error:
        raise error_cls(409, "Offered resource is no longer eligible") from error
    if snap["quantity_allocated"] < n or snap["quantity_total"] < n:
        raise error_cls(409, "Offered quantity is no longer available")


def assert_destination_merge_eligible(
    destination,
    organization_id,
    dest_location_id,
    meta,
    *,
    type_compatible,
    error_cls,
    access_error_cls,
):
    if not destination or destination.get("organization_id") != organization_id:
        raise access_error_cls(404, "Record not found")
    if normalize_tracking_mode(destination.get("tracking_mode")) != "QUANTITY":
        raise error_cls(409, "Destination resource is not eligible")
    if str(destination.get("location_id") or "") != str(dest_location_id or ""):
        raise error_cls(409, "Destination resource location does not match")
    status = str(destination.get("operational_status") or "").upper()
    if status in {"RETIRED", "DAMAGED", "MAINTENANCE"}:
        raise error_cls(409, "Destination resource is not eligible")
    if status and status != "AVAILABLE":
        raise error_cls(409, "Destination resource is not eligible")
    if not type_compatible(meta.get("resource_type_name"), destination):
        raise error_cls(409, "Destination resource type does not match the exchange request")
    try:
        quantity_snapshot(destination)
    except ResourceStateError as error:
        raise error_cls(409, "Destination resource is not eligible") from error


def build_destination_pool(*, resource_id, organization_id, destination, meta, offer, quantity, now):
    type_name = str(meta.get("resource_type_name") or "").strip() or "Quantity resource"
    snapshot = offer.get("resource_snapshot") if isinstance(offer.get("resource_snapshot"), dict) else {}
    name = str(snapshot.get("name") or type_name).strip()[:80] or type_name
    return {
        "resource_id": resource_id,
        "organization_id": organization_id,
        "location_id": destination["location_id"],
        "Location": str(destination.get("name") or ""),
        "name": name,
        "Type": type_name,
        "resource_type_id": str(meta.get("resource_type_id") or ""),
        "tracking_mode": "QUANTITY",
        "quantity_total": quantity,
        "quantity_available": quantity,
        "quantity_reserved": 0,
        "quantity_allocated": 0,
        "Available": False,
        "operational_status": "AVAILABLE",
        "visibility": "PRIVATE",
        "status": "ACTIVE",
        "attributes": {},
        "created_at": now,
        "updated_at": now,
        "created_via": "EXCHANGE_QUANTITY_HANDOVER",
        "source_exchange_request_id": meta.get("exchange_request_id"),
        "source_offer_id": offer.get("offer_id"),
    }


def confirm_quantity_handover(
    *,
    service,
    meta,
    offer,
    allocation,
    resource,
    body,
    organization_id,
    actor_sub,
    actor_role,
):
    """Atomic quantity handover. service provides tables/helpers/error types."""
    error_cls = service.ExchangeOperationError
    provider_org = str(
        meta.get("accepted_provider_organization_id")
        or offer.get("provider_organization_id")
        or ""
    ).strip()
    source_resource_id = str(meta.get("accepted_resource_id") or offer.get("resource_id") or "").strip()
    held = held_quantity(offer, allocation)
    if held < 1:
        raise error_cls(409, "Exchange allocation quantity is invalid")
    quantity = parse_required_transfer_quantity(body, held, error_cls=error_cls)
    assert_source_eligible(resource, provider_org, source_resource_id, quantity, error_cls=error_cls)

    if str(resource.get("resource_id") or "") != str(offer.get("resource_id") or ""):
        raise error_cls(409, "Offered resource is no longer eligible")

    dest_location_id = str(
        (body or {}).get("destination_location_id") or meta.get("destination_location_id") or ""
    ).strip()
    destination = require_location(service.locations_table(), organization_id, dest_location_id)

    destination_resource_id = str((body or {}).get("destination_resource_id") or "").strip()
    destination_created = False
    destination_item = None
    now = service._now_iso()

    if destination_resource_id:
        destination_item = service.resources_table().get_item(
            Key={"resource_id": destination_resource_id}
        ).get("Item")
        assert_destination_merge_eligible(
            destination_item,
            organization_id,
            destination["location_id"],
            meta,
            type_compatible=service.resource_type_names_compatible,
            error_cls=error_cls,
            access_error_cls=AccessError,
        )
    else:
        destination_resource_id = new_quantity_destination_resource_id()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", destination_resource_id):
            raise error_cls(500, "Failed to allocate destination resource id")
        destination_created = True
        destination_item = build_destination_pool(
            resource_id=destination_resource_id,
            organization_id=organization_id,
            destination=destination,
            meta=meta,
            offer=offer,
            quantity=quantity,
            now=now,
        )

    request_id = meta["exchange_request_id"]
    oid = offer["offer_id"]
    allocation_id = allocation["allocation_id"]
    previous_location_id = str(resource.get("location_id") or "")
    new_location_id = destination["location_id"]

    exchanges_name = getattr(service.exchanges_table(), "table_name", None) or getattr(
        service.exchanges_table(), "name", TABLE_NAME
    )
    resources_name = getattr(service.resources_table(), "table_name", None) or getattr(
        service.resources_table(), "name", "Resources"
    )
    allocations_name = getattr(service.allocations_table(), "table_name", None) or getattr(
        service.allocations_table(), "name", "Allocations"
    )
    remove_public = ", ".join(PRIVATE_INDEX_ATTRIBUTES)

    meta_update = {
        "Update": {
            "TableName": exchanges_name,
            "Key": service._serialize_map({"pk": meta_pk(request_id), "sk": meta_sk()}),
            "UpdateExpression": (
                "SET #status = :completed, completed_at = :now, confirming_actor_sub = :actor, "
                "completed_destination_location_id = :loc_id, "
                "completed_destination_resource_id = :dest_resource, "
                "previous_owner_organization_id = :provider, "
                "previous_location_id = :prev_loc, "
                "quantity_transferred = :qty, "
                "updated_at = :now, updated_by = :actor "
                "REMOVE expiry_due_key, expiry_due_at"
            ),
            "ConditionExpression": "#status = :pending",
            "ExpressionAttributeNames": {"#status": "status"},
            "ExpressionAttributeValues": service._serialize_map(
                {
                    ":completed": "COMPLETED",
                    ":pending": "TRANSFER_PENDING",
                    ":now": now,
                    ":actor": actor_sub,
                    ":loc_id": new_location_id,
                    ":dest_resource": destination_resource_id,
                    ":provider": provider_org,
                    ":prev_loc": previous_location_id,
                    ":qty": quantity,
                }
            ),
        }
    }

    # Phase 7B locked counters: consume hold once via allocated-=n and total-=n.
    provider_update = {
        "Update": {
            "TableName": resources_name,
            "Key": service._serialize_map({"resource_id": source_resource_id}),
            "UpdateExpression": (
                "SET quantity_allocated = quantity_allocated - :qty, "
                "quantity_total = quantity_total - :qty, updated_at = :now"
            ),
            "ConditionExpression": (
                "organization_id = :provider AND tracking_mode = :quantity "
                "AND quantity_allocated >= :qty AND quantity_total >= :qty"
            ),
            "ExpressionAttributeValues": service._serialize_map(
                {
                    ":qty": quantity,
                    ":provider": provider_org,
                    ":quantity": "QUANTITY",
                    ":now": now,
                }
            ),
        }
    }

    if destination_created:
        destination_item["updated_at"] = now
        destination_item["created_at"] = now
        destination_write = {
            "Put": {
                "TableName": resources_name,
                "Item": service._serialize_map(destination_item),
                "ConditionExpression": "attribute_not_exists(resource_id)",
            }
        }
    else:
        destination_write = {
            "Update": {
                "TableName": resources_name,
                "Key": service._serialize_map({"resource_id": destination_resource_id}),
                "UpdateExpression": (
                    "SET quantity_total = quantity_total + :qty, "
                    "quantity_available = quantity_available + :qty, "
                    "visibility = :private, updated_at = :now "
                    f"REMOVE {remove_public}"
                ),
                "ConditionExpression": (
                    "organization_id = :requester AND tracking_mode = :quantity "
                    "AND location_id = :loc_id AND operational_status = :available"
                ),
                "ExpressionAttributeValues": service._serialize_map(
                    {
                        ":qty": quantity,
                        ":requester": organization_id,
                        ":quantity": "QUANTITY",
                        ":loc_id": new_location_id,
                        ":available": "AVAILABLE",
                        ":private": "PRIVATE",
                        ":now": now,
                    }
                ),
            }
        }

    allocation_update = {
        "Update": {
            "TableName": allocations_name,
            "Key": service._serialize_map({"allocation_id": allocation_id}),
            "UpdateExpression": (
                "SET #status = :released, released_at = :now, updated_at = :now, "
                "completed_by = :actor, destination_resource_id = :dest_resource, "
                "destination_location_id = :loc_id"
            ),
            "ConditionExpression": (
                "attribute_exists(allocation_id) AND #status = :open "
                "AND allocation_type = :exchange"
            ),
            "ExpressionAttributeNames": {"#status": "status"},
            "ExpressionAttributeValues": service._serialize_map(
                {
                    ":released": EXCHANGE_ALLOCATION_STATUS_RELEASED,
                    ":open": EXCHANGE_ALLOCATION_STATUS_OPEN,
                    ":exchange": ALLOCATION_TYPE_EXCHANGE,
                    ":now": now,
                    ":actor": actor_sub,
                    ":dest_resource": destination_resource_id,
                    ":loc_id": new_location_id,
                }
            ),
        }
    }

    try:
        service._transact_write([meta_update, provider_update, destination_write, allocation_update])
    except ClientError as error:
        code = error.response["Error"]["Code"]
        if code in {"TransactionCanceledException", "ConditionalCheckFailedException"}:
            latest = service._get_meta(request_id)
            if str(latest.get("status", "")).upper() == "COMPLETED":
                offer_row, allocation_row = service._accepted_context(latest)
                return service._handover_response(
                    latest, offer_row, allocation_row, message="Handover already completed"
                )
            raise error_cls(409, "Handover state conflict") from error
        raise

    type_name = resource.get("Type") or meta.get("resource_type_name") or ""
    for history_item in (
        {
            "history_id": "HIST-EXCHANGE-XFER-SRC-" + oid + "-" + source_resource_id,
            "resource_id": source_resource_id,
            "organization_id": provider_org,
            "location_id": previous_location_id,
            "resource_type": type_name,
            "previous_status": "AVAILABLE",
            "new_status": "AVAILABLE",
            "changed_at": now,
            "reason": "RESOURCE_EXCHANGE_TRANSFERRED",
            "allocation_id": allocation_id,
            "exchange_request_id": request_id,
            "offer_id": oid,
            "quantity": quantity,
            "direction": "SOURCE",
            "counterpart_resource_id": destination_resource_id,
            "previous_organization_id": provider_org,
            "new_organization_id": organization_id,
        },
        {
            "history_id": "HIST-EXCHANGE-XFER-DST-" + oid + "-" + destination_resource_id,
            "resource_id": destination_resource_id,
            "organization_id": organization_id,
            "location_id": new_location_id,
            "resource_type": type_name,
            "previous_status": "AVAILABLE",
            "new_status": "AVAILABLE",
            "changed_at": now,
            "reason": "RESOURCE_EXCHANGE_TRANSFERRED",
            "allocation_id": allocation_id,
            "exchange_request_id": request_id,
            "offer_id": oid,
            "quantity": quantity,
            "direction": "DESTINATION",
            "counterpart_resource_id": source_resource_id,
            "destination_created": destination_created,
            "previous_organization_id": provider_org,
            "new_organization_id": organization_id,
        },
    ):
        try:
            service.history_table().put_item(
                Item=history_item,
                ConditionExpression="attribute_not_exists(history_id)",
            )
        except ClientError:
            pass

    record_audit(
        service.audit_table(),
        build_audit_event(
            organization_id,
            actor_sub,
            actor_role,
            "exchange.handover_confirmed",
            "exchange_request",
            request_id,
            location_id=new_location_id,
            metadata={
                "offer_id": oid,
                "tracking_mode": "QUANTITY",
                "quantity": quantity,
                "source_resource_id": source_resource_id,
                "destination_resource_id": destination_resource_id,
                "destination_created": destination_created,
                "provider_organization_id": provider_org,
                "requester_organization_id": organization_id,
            },
        ),
    )
    record_audit(
        service.audit_table(),
        build_audit_event(
            organization_id,
            actor_sub,
            actor_role,
            "resource.ownership_transferred",
            "resource",
            destination_resource_id,
            location_id=new_location_id,
            metadata={
                "exchange_request_id": request_id,
                "offer_id": oid,
                "tracking_mode": "QUANTITY",
                "quantity": quantity,
                "source_resource_id": source_resource_id,
                "destination_resource_id": destination_resource_id,
                "destination_created": destination_created,
                "provider_organization_id": provider_org,
                "requester_organization_id": organization_id,
            },
        ),
    )

    latest = service._get_meta(request_id)
    allocation_row = service.allocations_table().get_item(Key={"allocation_id": allocation_id}).get(
        "Item"
    ) or {
        **allocation,
        "status": EXCHANGE_ALLOCATION_STATUS_RELEASED,
        "destination_resource_id": destination_resource_id,
    }
    return service._handover_response(
        latest,
        offer,
        allocation_row,
        message="Handover completed",
        transfer={
            "tracking_mode": "QUANTITY",
            "quantity": quantity,
            "source_resource_id": source_resource_id,
            "destination_resource_id": destination_resource_id,
            "destination_created": destination_created,
            "ownership_transferred": True,
        },
    )
