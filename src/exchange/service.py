"""Resource Exchange API operations (Phase 5C/5D/5E).

Phase 5C: create/list requests and offers (offer create does not hold).
Phase 5D: atomic offer acceptance creates EXCHANGE allocation hold.
Phase 5E: provider starts transfer; requester confirms handover (ownership/location).
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timezone

from botocore.exceptions import ClientError
from boto3.dynamodb.types import TypeSerializer

from access import AccessError, require_location, require_owned
from audit import build_audit_event, record_audit
from exchange_model import (
    EXCHANGE_WRITE_ROLES,
    GSI_CREATED_AT,
    GSI_NETWORK_LIST_KEY,
    GSI_PROVIDER_ORG,
    GSI_REQUESTER_ORG,
    INDEX_NETWORK_OPEN,
    INDEX_PROVIDER_OFFER,
    INDEX_REQUESTER_ORG,
    NETWORK_OPEN_LIST_VALUE,
    TABLE_NAME,
    assert_provider_organization,
    assert_requester_organization,
    build_idempotency_item,
    build_meta_item,
    build_offer_item,
    exchange_allocation_id,
    meta_pk,
    meta_sk,
    network_request_projection,
    new_exchange_request_id,
    new_offer_id,
    offer_sk,
)
from exchange_state import (
    ALLOCATION_TYPE_EXCHANGE,
    EXCHANGE_ALLOCATION_STATUS_OPEN,
    EXCHANGE_ALLOCATION_STATUS_RELEASED,
)
from pages import decode_token, encode_token
from resource_state import (
    EMERGENCY_CLAIM_CONDITION,
    ResourceStateError,
    available_flag,
    effective_operational_status,
    normalize_tracking_mode,
    quantity_snapshot,
)
from visibility import PRIVATE_INDEX_ATTRIBUTES

# Captured at import so later tests that rebind sys.modules["service"]
# cannot point QR helpers at a different, unpatched module.
_THIS = sys.modules[__name__]

# Core accept transaction: META + accepted offer + resource + allocation = 4.
# Competing SUPERSEDED updates run post-commit (conditional) so a sibling
# withdraw/reject race cannot cancel the hold, and DynamoDB's 100-item
# TransactWrite limit cannot truncate the invariant.
MAX_SUPERSEDE_CLEANUP_ROUNDS = 8
MAX_SUPERSEDE_BATCH_HINT = 40  # documented batch size for follow-up cleanup loops

class ExchangeOperationError(Exception):
    def __init__(self, status_code, message, code=None):
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.code = code


def exchanges_table():
    import boto3

    return boto3.resource("dynamodb").Table(
        os.environ.get("EXCHANGES_TABLE", TABLE_NAME)
    )


def resources_table():
    import boto3

    return boto3.resource("dynamodb").Table(
        os.environ.get("RESOURCES_TABLE", "Resources")
    )


def locations_table():
    import boto3

    return boto3.resource("dynamodb").Table(
        os.environ.get("LOCATIONS_TABLE", "Locations")
    )


def resource_types_table():
    import boto3

    return boto3.resource("dynamodb").Table(
        os.environ.get("RESOURCE_TYPES_TABLE", "ResourceTypes")
    )


def organizations_table():
    import boto3

    return boto3.resource("dynamodb").Table(
        os.environ.get("ORGANIZATIONS_TABLE", "Organizations")
    )


def audit_table():
    import boto3

    return boto3.resource("dynamodb").Table(
        os.environ.get("AUDIT_TABLE", "AuditEvents")
    )


def allocations_table():
    import boto3

    return boto3.resource("dynamodb").Table(
        os.environ.get("ALLOCATIONS_TABLE", "Allocations")
    )


def history_table():
    import boto3

    return boto3.resource("dynamodb").Table(
        os.environ.get("HISTORY_TABLE", "ResourceStatusHistory")
    )


def _dynamodb_client():
    import boto3

    return boto3.client("dynamodb", region_name=os.environ.get("AWS_REGION", "eu-north-1"))


def _fingerprint(payload):
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _require_active_organization(organization_id):
    item = organizations_table().get_item(Key={"organization_id": organization_id}).get("Item")

    if not item or item.get("status") != "ACTIVE":
        raise AccessError(403, "Organization access denied")

    return item


def _active_resource_type(organization_id, resource_type_id):
    type_id = str(resource_type_id or "").strip()

    if not type_id:
        raise ExchangeOperationError(400, "resource_type_id is required")

    item = resource_types_table().get_item(
        Key={"organization_id": organization_id, "resource_type_id": type_id}
    ).get("Item")

    if not item or item.get("organization_id") != organization_id or item.get("status") != "ACTIVE":
        raise ExchangeOperationError(400, "Resource type is invalid")

    return item


def _get_meta(exchange_request_id, *, apply_expiry=True):
    request_id = str(exchange_request_id or "").strip()

    if not request_id:
        raise ExchangeOperationError(400, "exchange_request_id is required")

    if not request_id.startswith("EXREQ-"):
        request_id = "EXREQ-" + request_id

    item = exchanges_table().get_item(Key={"pk": meta_pk(request_id), "sk": meta_sk()}).get("Item")

    if not item or item.get("entity_type") != "EXCHANGE_REQUEST":
        raise AccessError(404, "Record not found")

    if apply_expiry:
        from lifecycle import apply_lazy_expiry

        item = apply_lazy_expiry(item) or item

    return item


def _get_offer(exchange_request_id, offer_id):
    request_id = str(exchange_request_id or "").strip()
    oid = str(offer_id or "").strip()

    if not request_id or not oid:
        raise ExchangeOperationError(400, "exchange_request_id and offer_id are required")

    if not request_id.startswith("EXREQ-"):
        request_id = "EXREQ-" + request_id

    if not oid.startswith("EXOFF-"):
        oid = "EXOFF-" + oid

    item = exchanges_table().get_item(Key={"pk": meta_pk(request_id), "sk": offer_sk(oid)}).get("Item")

    if not item or item.get("entity_type") != "EXCHANGE_OFFER":
        raise AccessError(404, "Record not found")

    if item.get("exchange_request_id") != request_id:
        raise AccessError(404, "Record not found")

    return item


def requester_view(meta):
    return {
        "exchange_request_id": meta.get("exchange_request_id"),
        "status": meta.get("status"),
        "requester_organization_id": meta.get("requester_organization_id"),
        "resource_type_id": meta.get("resource_type_id"),
        "resource_type_name": meta.get("resource_type_name"),
        "tracking_mode": meta.get("tracking_mode"),
        "quantity_requested": meta.get("quantity_requested"),
        "destination_location_id": meta.get("destination_location_id"),
        "destination_location_name": meta.get("destination_location_name"),
        "destination_city": meta.get("destination_city"),
        "destination_state": meta.get("destination_state"),
        "notes": meta.get("notes"),
        "expires_at": meta.get("expires_at") or None,
        "handover_expires_at": meta.get("handover_expires_at") or None,
        "accepted_offer_id": meta.get("accepted_offer_id"),
        "accepted_resource_id": meta.get("accepted_resource_id"),
        "accepted_provider_organization_id": meta.get("accepted_provider_organization_id"),
        "completed_at": meta.get("completed_at"),
        "confirming_actor_sub": meta.get("confirming_actor_sub"),
        "transfer_started_at": meta.get("transfer_started_at"),
        "completed_destination_resource_id": meta.get("completed_destination_resource_id"),
        "quantity_transferred": meta.get("quantity_transferred"),
        "created_at": meta.get("created_at"),
        "updated_at": meta.get("updated_at"),
        "visibility": "NETWORK",
    }


def offer_view(offer, *, for_requester=False):
    snapshot = dict(offer.get("resource_snapshot") or {})
    view = {
        "offer_id": offer.get("offer_id"),
        "exchange_request_id": offer.get("exchange_request_id"),
        "status": offer.get("status"),
        "provider_organization_id": offer.get("provider_organization_id") if for_requester else None,
        "resource_id": offer.get("resource_id") if for_requester else None,
        "quantity_offered": offer.get("quantity_offered"),
        "source_location_id": offer.get("source_location_id") if for_requester else None,
        "resource_snapshot": snapshot,
        "notes": offer.get("notes") or "",
        "created_at": offer.get("created_at"),
        "updated_at": offer.get("updated_at"),
    }

    if not for_requester:
        view["provider_organization_id"] = offer.get("provider_organization_id")
        view["resource_id"] = offer.get("resource_id")
        view["source_location_id"] = offer.get("source_location_id")

    return {key: value for key, value in view.items() if value is not None}


def provider_offer_view(offer):
    return offer_view(offer, for_requester=False)


def requester_offer_view(offer):
    """Requester sees safe provider/resource fields needed to evaluate offers."""
    snapshot = dict(offer.get("resource_snapshot") or {})
    return {
        "offer_id": offer.get("offer_id"),
        "exchange_request_id": offer.get("exchange_request_id"),
        "status": offer.get("status"),
        "quantity_offered": offer.get("quantity_offered"),
        "resource_snapshot": snapshot,
        "notes": offer.get("notes") or "",
        "created_at": offer.get("created_at"),
        "provider_organization_display_name": offer.get("provider_organization_display_name") or "",
        # Intentional: requester needs resource_id to evaluate; serial/asset never in snapshot.
        "resource_id": offer.get("resource_id"),
        "source_location_id": offer.get("source_location_id"),
        "provider_organization_id": offer.get("provider_organization_id"),
    }


def resource_type_names_compatible(request_type_name, resource):
    """Phase 5C matching rule: compare type *names*, never cross-org type ids.

    Requester stores its local resource_type_id + name. Provider resource Type/name
    must match the request resource_type_name case-insensitively.
    """
    wanted = str(request_type_name or "").strip().upper()
    have = str(resource.get("Type") or resource.get("name") or "").strip().upper()
    return bool(wanted) and wanted == have


def resource_eligible_to_offer(resource):
    """Offer eligibility only. Does not hold or mutate the resource."""
    if not isinstance(resource, dict):
        return False

    status = effective_operational_status(resource)

    if status != "AVAILABLE":
        return False

    mode = normalize_tracking_mode(resource.get("tracking_mode"))

    if mode == "INDIVIDUAL":
        return available_flag(resource.get("Available"))

    try:
        snapshot = quantity_snapshot(resource)
    except ResourceStateError:
        return False
    return snapshot is not None and snapshot["quantity_available"] > 0


def create_exchange_request(body, organization_id, actor_sub, actor_role):
    if actor_role not in EXCHANGE_WRITE_ROLES:
        raise AccessError(403, "You are not allowed to perform this action")

    _require_active_organization(organization_id)

    visibility = str((body or {}).get("visibility") or "NETWORK").strip().upper()

    if visibility != "NETWORK":
        raise ExchangeOperationError(400, "Exchange requests must use NETWORK visibility")

    location_id = str((body or {}).get("destination_location_id") or (body or {}).get("requester_location_id") or "").strip()
    location = require_location(locations_table(), organization_id, location_id)
    resource_type = _active_resource_type(organization_id, (body or {}).get("resource_type_id"))

    tracking_mode = str((body or {}).get("tracking_mode") or "INDIVIDUAL").strip().upper() or "INDIVIDUAL"

    if tracking_mode not in {"INDIVIDUAL", "QUANTITY"}:
        raise ExchangeOperationError(400, "tracking_mode is invalid")

    raw_qty = (body or {}).get("quantity_requested")
    if raw_qty is None:
        raw_qty = (body or {}).get("requested_quantity")
    if raw_qty is None:
        quantity = 1
    else:
        try:
            quantity = int(raw_qty)
        except (TypeError, ValueError):
            raise ExchangeOperationError(400, "quantity_requested is invalid") from None

    if quantity < 1:
        raise ExchangeOperationError(400, "quantity_requested is invalid")

    if tracking_mode == "INDIVIDUAL" and quantity != 1:
        raise ExchangeOperationError(400, "Individual exchange requests require quantity_requested = 1")

    notes = str((body or {}).get("notes") or (body or {}).get("description") or "").strip()

    if len(notes) > 300:
        raise ExchangeOperationError(400, "notes is too long")

    client_expires_at = str((body or {}).get("expires_at") or "").strip()
    expires_at = client_expires_at
    if not expires_at:
        from lifecycle import default_request_expires_at

        expires_at = default_request_expires_at()
    idempotency_key = str((body or {}).get("idempotency_key") or "").strip()

    fingerprint_payload = {
        "destination_location_id": location_id,
        "resource_type_id": resource_type["resource_type_id"],
        "tracking_mode": tracking_mode,
        "quantity_requested": quantity,
        "notes": notes,
        "expires_at": client_expires_at,
        "visibility": "NETWORK",
    }
    fingerprint = _fingerprint(fingerprint_payload)

    if idempotency_key:
        existing = _load_idempotency(organization_id, idempotency_key, "CREATE_REQUEST")

        if existing:
            if existing.get("result_ref", {}).get("fingerprint") != fingerprint:
                raise ExchangeOperationError(409, "Idempotency key conflict")

            meta = _get_meta(existing["result_ref"]["exchange_request_id"])
            return requester_view(meta)

    request_id = new_exchange_request_id()
    meta = build_meta_item(
        exchange_request_id=request_id,
        requester_organization_id=organization_id,
        resource_type_id=resource_type["resource_type_id"],
        resource_type_name=resource_type.get("name") or "",
        tracking_mode=tracking_mode,
        destination_location_id=location["location_id"],
        destination_location_name=location.get("name") or "",
        destination_city=str(location.get("city") or "").strip()[:60],
        destination_state=str(location.get("state") or "").strip()[:60],
        notes=notes,
        expires_at=expires_at,
        idempotency_key=idempotency_key,
        created_by=actor_sub,
        quantity_requested=quantity,
        status="OPEN",
    )

    try:
        exchanges_table().put_item(
            Item=meta,
            ConditionExpression="attribute_not_exists(pk)",
        )
    except ClientError as error:
        if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise ExchangeOperationError(409, "Exchange request already exists") from error
        raise

    if idempotency_key:
        _put_idempotency(
            organization_id,
            idempotency_key,
            "CREATE_REQUEST",
            {
                "fingerprint": fingerprint,
                "exchange_request_id": request_id,
            },
        )

    record_audit(
        audit_table(),
        build_audit_event(
            organization_id,
            actor_sub,
            actor_role,
            "exchange.request_created",
            "exchange_request",
            request_id,
            location_id=location["location_id"],
            metadata={
                "resource_type_id": resource_type["resource_type_id"],
                "tracking_mode": tracking_mode,
                "quantity_requested": quantity,
            },
        ),
    )
    return requester_view(meta)


def _network_page_secrets():
    from network_page_token import load_network_page_secret

    current, previous = load_network_page_secret()
    return [current, *previous]


def list_network_requests(organization_id, query):
    from network_page_token import read_network_page_token, sign_network_page_token

    _require_active_organization(organization_id)
    limit = _page_limit(query)
    supplied = (query or {}).get("page_token")
    secrets = _network_page_secrets() if supplied else None
    start = None
    if supplied:
        start = read_network_page_token(
            supplied,
            organization_id=organization_id,
            limit=limit,
            secrets=secrets,
        )

    from boto3.dynamodb.conditions import Key

    kwargs = {
        "IndexName": INDEX_NETWORK_OPEN,
        "KeyConditionExpression": Key(GSI_NETWORK_LIST_KEY).eq(NETWORK_OPEN_LIST_VALUE),
        "Limit": limit,
    }

    if start:
        kwargs["ExclusiveStartKey"] = start

    result = exchanges_table().query(**kwargs)
    items = []

    for item in result.get("Items") or []:
        if item.get("entity_type") != "EXCHANGE_REQUEST":
            continue

        if item.get("status") != "OPEN":
            continue

        if item.get("requester_organization_id") == organization_id:
            continue

        projection = network_request_projection(item, viewer_organization_id=organization_id)

        if projection:
            items.append(projection)

    next_key = result.get("LastEvaluatedKey")
    next_token = None
    if next_key:
        if secrets is None:
            secrets = _network_page_secrets()
        next_token = sign_network_page_token(
            next_key,
            organization_id=organization_id,
            limit=limit,
            secret=secrets[0],
        )
    return {
        "items": items,
        "next_token": next_token,
    }


def list_my_requests(organization_id, query):
    _require_active_organization(organization_id)
    limit = _page_limit(query)
    start = decode_token(
        (query or {}).get("page_token"),
        [GSI_REQUESTER_ORG, GSI_CREATED_AT, "pk", "sk"],
    )

    if start and start.get(GSI_REQUESTER_ORG) != organization_id:
        raise AccessError(400, "Invalid page token")

    from boto3.dynamodb.conditions import Key

    kwargs = {
        "IndexName": INDEX_REQUESTER_ORG,
        "KeyConditionExpression": Key(GSI_REQUESTER_ORG).eq(organization_id),
        "Limit": limit,
    }

    if start:
        kwargs["ExclusiveStartKey"] = start

    result = exchanges_table().query(**kwargs)
    items = [
        requester_view(item)
        for item in (result.get("Items") or [])
        if item.get("entity_type") == "EXCHANGE_REQUEST"
        and item.get("requester_organization_id") == organization_id
    ]
    return {
        "items": items,
        "next_token": encode_token(result.get("LastEvaluatedKey")),
    }


def get_exchange_request(exchange_request_id, organization_id, membership):
    _require_active_organization(organization_id)
    meta = _get_meta(exchange_request_id)

    if meta.get("requester_organization_id") == organization_id:
        assert_requester_organization(membership, meta)
        return requester_view(meta)

    # Accepted-offer provider may view participant exchange after accept.
    if meta.get("accepted_provider_organization_id") == organization_id:
        status = str(meta.get("status", "")).upper()
        if status in {"ACCEPTED", "TRANSFER_PENDING", "COMPLETED", "CANCELLED", "EXPIRED"}:
            return requester_view(meta)

    if meta.get("status") == "OPEN" and meta.get(GSI_NETWORK_LIST_KEY) == NETWORK_OPEN_LIST_VALUE:
        projection = network_request_projection(meta, viewer_organization_id=organization_id)

        if projection:
            return projection

    raise AccessError(404, "Record not found")


def create_offer(exchange_request_id, body, organization_id, actor_sub, actor_role):
    """Create an OPEN offer. Does NOT hold, reserve, allocate, or mutate the resource."""
    if actor_role not in EXCHANGE_WRITE_ROLES:
        raise AccessError(403, "You are not allowed to perform this action")

    _require_active_organization(organization_id)
    meta = _get_meta(exchange_request_id)

    if meta.get("status") != "OPEN":
        raise ExchangeOperationError(409, "Exchange request is not open")

    if meta.get(GSI_NETWORK_LIST_KEY) != NETWORK_OPEN_LIST_VALUE:
        raise ExchangeOperationError(409, "Exchange request is not network-open")

    if meta.get("requester_organization_id") == organization_id:
        raise ExchangeOperationError(409, "Cannot offer on your own exchange request")

    resource_id = str((body or {}).get("resource_id") or "").strip()

    if not resource_id:
        raise ExchangeOperationError(400, "resource_id is required")

    resource = resources_table().get_item(Key={"resource_id": resource_id}).get("Item")
    require_owned(resource, organization_id)

    location_id = str(
        (body or {}).get("provider_location_id")
        or (body or {}).get("source_location_id")
        or resource.get("location_id")
        or ""
    ).strip()
    location = require_location(locations_table(), organization_id, location_id)

    if resource.get("location_id") != location["location_id"]:
        raise ExchangeOperationError(409, "Resource location does not match provider location")

    if not resource_eligible_to_offer(resource):
        raise ExchangeOperationError(409, "Resource is not eligible to offer")

    if not resource_type_names_compatible(meta.get("resource_type_name"), resource):
        raise ExchangeOperationError(409, "Resource type does not match the exchange request")

    mode = normalize_tracking_mode(resource.get("tracking_mode"))
    request_mode = str(meta.get("tracking_mode") or "INDIVIDUAL").upper()

    if mode != request_mode:
        raise ExchangeOperationError(409, "Resource tracking mode does not match the exchange request")

    raw_qty = (body or {}).get("quantity_offered")
    if raw_qty is None:
        raw_qty = (body or {}).get("offered_quantity")
    if raw_qty is None:
        quantity = 1
    else:
        try:
            quantity = int(raw_qty)
        except (TypeError, ValueError):
            raise ExchangeOperationError(400, "quantity_offered is invalid") from None

    if quantity < 1:
        raise ExchangeOperationError(400, "quantity_offered is invalid")

    if mode == "INDIVIDUAL" and quantity != 1:
        raise ExchangeOperationError(400, "Individual offers require quantity_offered = 1")

    if mode == "QUANTITY":
        available = quantity_snapshot(resource)["quantity_available"]

        if quantity > available:
            raise ExchangeOperationError(409, "Offered quantity exceeds available quantity")

        requested = int(meta.get("quantity_requested") or 1)

        if quantity > requested:
            raise ExchangeOperationError(409, "Offered quantity exceeds requested quantity")

    notes = str((body or {}).get("notes") or (body or {}).get("message") or "").strip()

    if len(notes) > 300:
        raise ExchangeOperationError(400, "notes is too long")

    offer_expires = str((body or {}).get("expires_at") or "").strip()
    request_expires = str(meta.get("expires_at") or "").strip()
    if not offer_expires:
        offer_expires = request_expires
    elif request_expires and offer_expires > request_expires:
        raise ExchangeOperationError(400, "Offer expiry cannot exceed request expiry")

    idempotency_key = str((body or {}).get("idempotency_key") or "").strip()
    fingerprint_payload = {
        "exchange_request_id": meta["exchange_request_id"],
        "resource_id": resource_id,
        "quantity_offered": quantity,
        "source_location_id": location["location_id"],
        "notes": notes,
        "expires_at": offer_expires,
    }
    fingerprint = _fingerprint(fingerprint_payload)

    if idempotency_key:
        existing = _load_idempotency(organization_id, idempotency_key, "CREATE_OFFER")

        if existing:
            if existing.get("result_ref", {}).get("fingerprint") != fingerprint:
                raise ExchangeOperationError(409, "Idempotency key conflict")

            offer = _get_offer(
                existing["result_ref"]["exchange_request_id"],
                existing["result_ref"]["offer_id"],
            )
            return provider_offer_view(offer)

    offer_id = new_offer_id()
    snapshot = {
        "name": resource.get("name") or resource.get("Type") or "",
        "resource_type_name": resource.get("Type") or "",
        "tracking_mode": mode,
        "condition": resource.get("condition") or "",
        "quantity_offered": quantity,
    }
    offer = build_offer_item(
        exchange_request_id=meta["exchange_request_id"],
        offer_id=offer_id,
        provider_organization_id=organization_id,
        resource_id=resource_id,
        created_by=actor_sub,
        quantity_offered=quantity,
        source_location_id=location["location_id"],
        resource_snapshot=snapshot,
        expires_at=offer_expires,
        idempotency_key=idempotency_key,
        status="OPEN",
    )
    offer["notes"] = notes

    # CRITICAL: no Resources update, no Allocations put, no quantity decrement.
    try:
        exchanges_table().put_item(
            Item=offer,
            ConditionExpression="attribute_not_exists(pk) AND attribute_not_exists(sk)",
        )
    except ClientError as error:
        if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise ExchangeOperationError(409, "Offer already exists") from error
        raise

    if idempotency_key:
        _put_idempotency(
            organization_id,
            idempotency_key,
            "CREATE_OFFER",
            {
                "fingerprint": fingerprint,
                "exchange_request_id": meta["exchange_request_id"],
                "offer_id": offer_id,
            },
        )

    record_audit(
        audit_table(),
        build_audit_event(
            organization_id,
            actor_sub,
            actor_role,
            "exchange.offer_created",
            "exchange_offer",
            offer_id,
            location_id=location["location_id"],
            metadata={
                "exchange_request_id": meta["exchange_request_id"],
                "resource_id": resource_id,
                "quantity_offered": quantity,
                "hold_created": False,
            },
        ),
    )
    try:
        from exchange_notify import notify_offer_received

        notify_offer_received(meta, offer, actor_sub)
    except Exception:
        pass
    return provider_offer_view(offer)


def list_offers_for_request(exchange_request_id, organization_id, membership):
    _require_active_organization(organization_id)
    meta = _get_meta(exchange_request_id)

    is_requester = meta.get("requester_organization_id") == organization_id

    if is_requester:
        assert_requester_organization(membership, meta)
    else:
        # Provider may only list their own offers for this request.
        pass

    from boto3.dynamodb.conditions import Key

    result = exchanges_table().query(
        KeyConditionExpression=Key("pk").eq(meta_pk(meta["exchange_request_id"]))
        & Key("sk").begins_with("OFFER#"),
    )

    items = []

    for item in result.get("Items") or []:
        if item.get("entity_type") != "EXCHANGE_OFFER":
            continue

        if is_requester:
            items.append(requester_offer_view(item))
        elif item.get("provider_organization_id") == organization_id:
            items.append(provider_offer_view(item))

    if not is_requester and not items:
        # Unrelated org: hide existence of offers/request detail beyond network projection rules.
        if meta.get("requester_organization_id") != organization_id:
            # Allow empty list only if they could see the OPEN request; otherwise 404.
            if meta.get("status") != "OPEN":
                raise AccessError(404, "Record not found")

    return {"items": items}


def get_offer(exchange_request_id, offer_id, organization_id, membership):
    _require_active_organization(organization_id)
    meta = _get_meta(exchange_request_id)
    offer = _get_offer(exchange_request_id, offer_id)

    if meta.get("requester_organization_id") == organization_id:
        assert_requester_organization(membership, meta)
        return requester_offer_view(offer)

    if offer.get("provider_organization_id") == organization_id:
        assert_provider_organization(membership, offer)
        return provider_offer_view(offer)

    raise AccessError(404, "Record not found")


def list_my_offers(organization_id, query):
    _require_active_organization(organization_id)
    limit = _page_limit(query)
    start = decode_token(
        (query or {}).get("page_token"),
        [GSI_PROVIDER_ORG, GSI_CREATED_AT, "pk", "sk"],
    )

    if start and start.get(GSI_PROVIDER_ORG) != organization_id:
        raise AccessError(400, "Invalid page token")

    from boto3.dynamodb.conditions import Key

    kwargs = {
        "IndexName": INDEX_PROVIDER_OFFER,
        "KeyConditionExpression": Key(GSI_PROVIDER_ORG).eq(organization_id),
        "Limit": limit,
    }

    if start:
        kwargs["ExclusiveStartKey"] = start

    result = exchanges_table().query(**kwargs)
    items = [
        provider_offer_view(item)
        for item in (result.get("Items") or [])
        if item.get("entity_type") == "EXCHANGE_OFFER"
        and item.get("provider_organization_id") == organization_id
    ]
    return {
        "items": items,
        "next_token": encode_token(result.get("LastEvaluatedKey")),
    }


def _page_limit(query):
    try:
        limit = int((query or {}).get("limit") or 25)
    except (TypeError, ValueError):
        raise AccessError(400, "Page size is invalid") from None

    if limit < 1 or limit > 50:
        raise AccessError(400, "Page size is invalid")

    return limit


def _load_idempotency(organization_id, idempotency_key, operation):
    from exchange_model import idempotency_pk

    return exchanges_table().get_item(
        Key={"pk": idempotency_pk(organization_id, idempotency_key), "sk": operation}
    ).get("Item")


def _put_idempotency(organization_id, idempotency_key, operation, result_ref):
    item = build_idempotency_item(
        organization_id=organization_id,
        idempotency_key=idempotency_key,
        operation=operation,
        result_ref=result_ref,
    )

    try:
        exchanges_table().put_item(
            Item=item,
            ConditionExpression="attribute_not_exists(pk)",
        )
    except ClientError as error:
        if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
            existing = _load_idempotency(organization_id, idempotency_key, operation)

            if existing and existing.get("result_ref", {}).get("fingerprint") != result_ref.get("fingerprint"):
                raise ExchangeOperationError(409, "Idempotency key conflict") from error

            return existing

        raise

    return item


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _serialize_map(values):
    serializer = TypeSerializer()
    encoded = {}

    for key, value in values.items():
        if value is None:
            continue
        encoded[key] = serializer.serialize(value)

    return encoded


def _list_open_offers(exchange_request_id):
    from boto3.dynamodb.conditions import Key

    result = exchanges_table().query(
        KeyConditionExpression=Key("pk").eq(meta_pk(exchange_request_id))
        & Key("sk").begins_with("OFFER#"),
    )
    return [
        item
        for item in (result.get("Items") or [])
        if item.get("entity_type") == "EXCHANGE_OFFER"
        and str(item.get("status", "")).upper() == "OPEN"
    ]


def _supersede_competing_open_offers(request_id, accepted_offer_id, now=None, *, meta=None, actor_sub=""):
    """Conditionally SUPERSEDE every remaining OPEN sibling offer.

    Runs outside the core accept transaction so:
    - hold consistency never depends on sibling count
    - already non-OPEN siblings are left unchanged (condition fails → skip)
    - transient follow-up failures are healed by idempotent accept retry
    """
    stamp = now or _now_iso()
    accepted = str(accepted_offer_id or "").strip()
    request_meta = meta or _get_meta(request_id)

    for _ in range(MAX_SUPERSEDE_CLEANUP_ROUNDS):
        competing = [
            item
            for item in _list_open_offers(request_id)
            if item.get("offer_id") != accepted
        ]
        if not competing:
            return True

        for other in competing:
            try:
                exchanges_table().update_item(
                    Key={"pk": meta_pk(request_id), "sk": offer_sk(other["offer_id"])},
                    UpdateExpression=(
                        "SET #status = :superseded, updated_at = :now "
                        "REMOVE expiry_due_key, expiry_due_at"
                    ),
                    ConditionExpression="#status = :open",
                    ExpressionAttributeNames={"#status": "status"},
                    ExpressionAttributeValues={
                        ":superseded": "SUPERSEDED",
                        ":open": "OPEN",
                        ":now": stamp,
                    },
                )
                try:
                    from exchange_notify import notify_offer_superseded

                    notify_offer_superseded(request_meta, other, actor_sub)
                except Exception:
                    pass
            except ClientError:
                # ConditionalCheckFailed (already non-OPEN) or transient — continue.
                continue

    remaining = [
        item
        for item in _list_open_offers(request_id)
        if item.get("offer_id") != accepted
    ]
    return not remaining


def accept_offer(exchange_request_id, offer_id, organization_id, actor_sub, actor_role, membership):
    """Atomically accept an offer and hold the resource. Does NOT transfer ownership."""
    if actor_role not in EXCHANGE_WRITE_ROLES:
        raise AccessError(403, "You are not allowed to perform this action")

    _require_active_organization(organization_id)
    meta = _get_meta(exchange_request_id)
    assert_requester_organization(membership, meta)

    oid = str(offer_id or "").strip()
    if oid and not oid.startswith("EXOFF-"):
        oid = "EXOFF-" + oid

    if str(meta.get("status", "")).upper() == "ACCEPTED":
        if meta.get("accepted_offer_id") == oid:
            offer = _get_offer(exchange_request_id, oid)
            allocation_id = exchange_allocation_id(offer["offer_id"])
            allocation = allocations_table().get_item(Key={"allocation_id": allocation_id}).get("Item") or {}
            # Heal any competing OPEN leftovers from a prior interrupted cleanup.
            _supersede_competing_open_offers(
                exchange_request_id, oid, meta=meta, actor_sub=actor_sub
            )
            try:
                from exchange_notify import notify_offer_accepted

                notify_offer_accepted(meta, offer, actor_sub)
            except Exception:
                pass
            return _acceptance_response(meta, offer, allocation)
        raise ExchangeOperationError(409, "Exchange request already accepted")

    if str(meta.get("status", "")).upper() != "OPEN":
        raise ExchangeOperationError(409, "Exchange request is not open")

    offer = _get_offer(exchange_request_id, oid)

    if str(offer.get("status", "")).upper() != "OPEN":
        raise ExchangeOperationError(409, "Offer is not open")

    if offer.get("provider_organization_id") == organization_id:
        raise ExchangeOperationError(403, "Cannot accept your own organization's offer")

    provider_org = str(offer.get("provider_organization_id") or "").strip()
    resource_id = str(offer.get("resource_id") or "").strip()
    resource = resources_table().get_item(Key={"resource_id": resource_id}).get("Item")

    if not resource or resource.get("organization_id") != provider_org:
        raise ExchangeOperationError(409, "Offered resource is no longer eligible")

    if str(resource.get("location_id") or "") != str(offer.get("source_location_id") or ""):
        raise ExchangeOperationError(409, "Offered resource location no longer matches")

    if not resource_eligible_to_offer(resource):
        raise ExchangeOperationError(409, "Offered resource is no longer eligible")

    if not resource_type_names_compatible(meta.get("resource_type_name"), resource):
        raise ExchangeOperationError(409, "Resource type does not match the exchange request")

    mode = normalize_tracking_mode(resource.get("tracking_mode"))
    request_mode = str(meta.get("tracking_mode") or "INDIVIDUAL").upper()

    if mode != request_mode:
        raise ExchangeOperationError(409, "Resource tracking mode does not match the exchange request")

    quantity = int(offer.get("quantity_offered") or 1)

    if mode == "QUANTITY":
        try:
            available = quantity_snapshot(resource)["quantity_available"]
        except ResourceStateError as error:
            raise ExchangeOperationError(409, "Offered resource is no longer eligible") from error
        if quantity > available:
            raise ExchangeOperationError(409, "Offered quantity is no longer available")

    now = _now_iso()
    from lifecycle import default_handover_expires_at

    handover_due = default_handover_expires_at()
    oid = offer["offer_id"]
    allocation_id = exchange_allocation_id(oid)
    request_id = meta["exchange_request_id"]
    exchanges_name = getattr(exchanges_table(), "table_name", None) or getattr(
        exchanges_table(), "name", TABLE_NAME
    )
    resources_name = getattr(resources_table(), "table_name", None) or getattr(
        resources_table(), "name", "Resources"
    )
    allocations_name = getattr(allocations_table(), "table_name", None) or getattr(
        allocations_table(), "name", "Allocations"
    )
    allocation = {
        "allocation_id": allocation_id,
        "allocation_type": ALLOCATION_TYPE_EXCHANGE,
        "status": EXCHANGE_ALLOCATION_STATUS_OPEN,
        "resource_id": resource_id,
        "organization_id": provider_org,
        "provider_organization_id": provider_org,
        "requester_organization_id": organization_id,
        "exchange_request_id": request_id,
        "offer_id": oid,
        "quantity": quantity,
        "location_id": resource.get("location_id") or "",
        "location": resource.get("Location") or "",
        "resource_type": resource.get("Type") or meta.get("resource_type_name") or "",
        "allocated_at": now,
        "updated_at": now,
        "accepted_by": actor_sub,
    }

    # Core atomic hold only. Competing SUPERSEDED is post-commit cleanup.
    transact_items = [
        {
            "Update": {
                "TableName": exchanges_name,
                "Key": _serialize_map({"pk": meta_pk(request_id), "sk": meta_sk()}),
                "UpdateExpression": (
                    "SET #status = :accepted, accepted_offer_id = :offer_id, "
                    "accepted_resource_id = :resource_id, "
                    "accepted_provider_organization_id = :provider, "
                    "handover_expires_at = :handover_due, "
                    "expiry_due_key = :due_key, expiry_due_at = :handover_due, "
                    "updated_at = :now, updated_by = :actor "
                    "REMOVE network_list_key"
                ),
                "ConditionExpression": "#status = :open",
                "ExpressionAttributeNames": {"#status": "status"},
                "ExpressionAttributeValues": _serialize_map(
                    {
                        ":accepted": "ACCEPTED",
                        ":open": "OPEN",
                        ":offer_id": oid,
                        ":resource_id": resource_id,
                        ":provider": provider_org,
                        ":handover_due": handover_due,
                        ":due_key": "DUE",
                        ":now": now,
                        ":actor": actor_sub,
                    }
                ),
            }
        },
        {
            "Update": {
                "TableName": exchanges_name,
                "Key": _serialize_map({"pk": meta_pk(request_id), "sk": offer_sk(oid)}),
                "UpdateExpression": (
                    "SET #status = :accepted, updated_at = :now, updated_by = :actor "
                    "REMOVE expiry_due_key, expiry_due_at"
                ),
                "ConditionExpression": "#status = :open",
                "ExpressionAttributeNames": {"#status": "status"},
                "ExpressionAttributeValues": _serialize_map(
                    {
                        ":accepted": "ACCEPTED",
                        ":open": "OPEN",
                        ":now": now,
                        ":actor": actor_sub,
                    }
                ),
            }
        },
    ]

    if mode == "INDIVIDUAL":
        transact_items.append(
            {
                "Update": {
                    "TableName": resources_name,
                    "Key": _serialize_map({"resource_id": resource_id}),
                    "UpdateExpression": "SET #a = :false, operational_status = :op_allocated, updated_at = :now",
                    "ConditionExpression": EMERGENCY_CLAIM_CONDITION,
                    "ExpressionAttributeNames": {"#a": "Available"},
                    "ExpressionAttributeValues": _serialize_map(
                        {
                            ":false": False,
                            ":true": True,
                            ":organization_id": provider_org,
                            ":op_available": "AVAILABLE",
                            ":indiv": "INDIVIDUAL",
                            ":op_allocated": "ALLOCATED",
                            ":now": now,
                        }
                    ),
                }
            }
        )
    else:
        transact_items.append(
            {
                "Update": {
                    "TableName": resources_name,
                    "Key": _serialize_map({"resource_id": resource_id}),
                    "UpdateExpression": (
                        "SET quantity_available = quantity_available - :qty, "
                        "quantity_allocated = quantity_allocated + :qty, updated_at = :now"
                    ),
                    "ConditionExpression": (
                        "organization_id = :organization_id AND tracking_mode = :quantity "
                        "AND quantity_available >= :qty"
                    ),
                    "ExpressionAttributeValues": _serialize_map(
                        {
                            ":qty": quantity,
                            ":organization_id": provider_org,
                            ":quantity": "QUANTITY",
                            ":now": now,
                        }
                    ),
                }
            }
        )

    transact_items.append(
        {
            "Put": {
                "TableName": allocations_name,
                "Item": _serialize_map(allocation),
                "ConditionExpression": "attribute_not_exists(allocation_id)",
            }
        }
    )

    try:
        _transact_write(transact_items)
    except ClientError as error:
        code = error.response["Error"]["Code"]
        if code in {"TransactionCanceledException", "ConditionalCheckFailedException"}:
            latest = _get_meta(request_id)
            if (
                str(latest.get("status", "")).upper() == "ACCEPTED"
                and latest.get("accepted_offer_id") == oid
            ):
                allocation_row = allocations_table().get_item(
                    Key={"allocation_id": allocation_id}
                ).get("Item") or allocation
                _supersede_competing_open_offers(
                    request_id, oid, now, meta=latest, actor_sub=actor_sub
                )
                try:
                    from exchange_notify import notify_offer_accepted

                    notify_offer_accepted(latest, _get_offer(request_id, oid), actor_sub)
                except Exception:
                    pass
                return _acceptance_response(latest, _get_offer(request_id, oid), allocation_row)
            raise ExchangeOperationError(409, "Resource state conflict") from error
        raise

    _supersede_competing_open_offers(
        request_id, oid, now, meta=_get_meta(request_id), actor_sub=actor_sub
    )

    history_reason_item = {
        "history_id": "HIST-EXCHANGE-" + oid + "-" + resource_id,
        "resource_id": resource_id,
        "organization_id": provider_org,
        "location_id": resource.get("location_id") or "",
        "resource_type": resource.get("Type") or "",
        "location": resource.get("Location") or "",
        "previous_status": "AVAILABLE",
        "new_status": "ALLOCATED" if mode == "INDIVIDUAL" else "AVAILABLE",
        "changed_at": now,
        "reason": "RESOURCE_EXCHANGE_ALLOCATED",
        "allocation_id": allocation_id,
        "exchange_request_id": request_id,
        "offer_id": oid,
    }
    if mode == "QUANTITY":
        history_reason_item["quantity"] = quantity
        history_reason_item["history_id"] = "HIST-EXCHANGE-QTY-" + oid + "-" + resource_id

    try:
        history_table().put_item(
            Item=history_reason_item,
            ConditionExpression="attribute_not_exists(history_id)",
        )
    except ClientError:
        pass

    record_audit(
        audit_table(),
        build_audit_event(
            organization_id,
            actor_sub,
            actor_role,
            "exchange.offer_accepted",
            "exchange_offer",
            oid,
            location_id=meta.get("destination_location_id") or "",
            metadata={
                "exchange_request_id": request_id,
                "offer_id": oid,
                "resource_id": resource_id,
                "provider_organization_id": provider_org,
                "requester_organization_id": organization_id,
                "quantity": quantity,
                "ownership_transferred": False,
            },
        ),
    )
    record_audit(
        audit_table(),
        build_audit_event(
            provider_org,
            actor_sub,
            actor_role,
            "resource.exchange_allocated",
            "allocation",
            allocation_id,
            location_id=resource.get("location_id") or "",
            metadata={
                "exchange_request_id": request_id,
                "offer_id": oid,
                "resource_id": resource_id,
                "requester_organization_id": organization_id,
                "quantity": quantity,
            },
        ),
    )

    latest_meta = _get_meta(request_id)
    latest_offer = _get_offer(request_id, oid)
    try:
        from exchange_notify import notify_offer_accepted

        notify_offer_accepted(latest_meta, latest_offer, actor_sub)
    except Exception:
        pass

    return _acceptance_response(latest_meta, latest_offer, allocation)


def _accepted_context(meta):
    """Load accepted offer + OPEN/RELEASED allocation for an accepted exchange."""
    request_id = meta["exchange_request_id"]
    oid = str(meta.get("accepted_offer_id") or "").strip()
    if not oid:
        raise ExchangeOperationError(409, "Exchange request has no accepted offer")
    offer = _get_offer(request_id, oid)
    allocation_id = exchange_allocation_id(oid)
    allocation = allocations_table().get_item(Key={"allocation_id": allocation_id}).get("Item")
    if not allocation:
        raise ExchangeOperationError(409, "Exchange allocation is missing")
    return offer, allocation


def _reject_quantity_handover(meta, offer, resource):
    """Deprecated Phase 5E gate — retained only so old imports do not break tests."""
    del meta, offer, resource
    return None


def start_transfer(exchange_request_id, organization_id, actor_sub, actor_role, membership):
    """Provider marks ACCEPTED → TRANSFER_PENDING. No ownership/location change."""
    if actor_role not in EXCHANGE_WRITE_ROLES:
        raise AccessError(403, "You are not allowed to perform this action")

    _require_active_organization(organization_id)
    meta = _get_meta(exchange_request_id)
    status = str(meta.get("status", "")).upper()

    if status == "TRANSFER_PENDING":
        if meta.get("accepted_provider_organization_id") != organization_id:
            raise AccessError(404, "Record not found")
        offer, allocation = _accepted_context(meta)
        assert_provider_organization(membership, offer)
        try:
            from exchange_notify import notify_transfer_started

            notify_transfer_started(meta, offer, actor_sub)
        except Exception:
            pass
        return {
            "message": "Transfer already started",
            "request": requester_view(meta),
            "offer": provider_offer_view(offer),
            "allocation": _allocation_view(allocation),
            "ownership_transferred": False,
            "location_transferred": False,
        }

    if status == "COMPLETED":
        raise ExchangeOperationError(409, "Exchange request already completed")

    if status != "ACCEPTED":
        raise ExchangeOperationError(409, "Exchange request is not accepted")

    if meta.get("accepted_provider_organization_id") != organization_id:
        raise AccessError(404, "Record not found")

    offer, allocation = _accepted_context(meta)
    assert_provider_organization(membership, offer)

    if str(offer.get("status", "")).upper() != "ACCEPTED":
        raise ExchangeOperationError(409, "Accepted offer is not in ACCEPTED state")

    if (
        allocation.get("allocation_type") != ALLOCATION_TYPE_EXCHANGE
        or str(allocation.get("status", "")).upper() != EXCHANGE_ALLOCATION_STATUS_OPEN
    ):
        raise ExchangeOperationError(409, "Exchange allocation is not open")

    resource_id = str(offer.get("resource_id") or "").strip()
    resource = resources_table().get_item(Key={"resource_id": resource_id}).get("Item")
    if not resource or resource.get("organization_id") != organization_id:
        raise ExchangeOperationError(409, "Offered resource is no longer eligible")

    mode = normalize_tracking_mode(resource.get("tracking_mode") or meta.get("tracking_mode"))
    if mode == "QUANTITY":
        from quantity_handover import assert_source_eligible, held_quantity

        held = held_quantity(offer, allocation)
        if held < 1:
            raise ExchangeOperationError(409, "Offered resource is no longer eligible")
        assert_source_eligible(
            resource, organization_id, resource_id, held, error_cls=ExchangeOperationError
        )
    elif str(resource.get("operational_status") or "").upper() != "ALLOCATED":
        raise ExchangeOperationError(409, "Offered resource is no longer eligible")

    now = _now_iso()
    request_id = meta["exchange_request_id"]
    exchanges_name = getattr(exchanges_table(), "table_name", None) or getattr(
        exchanges_table(), "name", TABLE_NAME
    )

    try:
        _transact_write(
            [
                {
                    "Update": {
                        "TableName": exchanges_name,
                        "Key": _serialize_map({"pk": meta_pk(request_id), "sk": meta_sk()}),
                        "UpdateExpression": (
                            "SET #status = :pending, transfer_started_at = :now, "
                            "transfer_started_by = :actor, updated_at = :now, updated_by = :actor"
                        ),
                        "ConditionExpression": "#status = :accepted",
                        "ExpressionAttributeNames": {"#status": "status"},
                        "ExpressionAttributeValues": _serialize_map(
                            {
                                ":pending": "TRANSFER_PENDING",
                                ":accepted": "ACCEPTED",
                                ":now": now,
                                ":actor": actor_sub,
                            }
                        ),
                    }
                }
            ]
        )
    except ClientError as error:
        code = error.response["Error"]["Code"]
        if code in {"TransactionCanceledException", "ConditionalCheckFailedException"}:
            latest = _get_meta(request_id)
            if str(latest.get("status", "")).upper() == "TRANSFER_PENDING":
                offer, allocation = _accepted_context(latest)
                return {
                    "message": "Transfer already started",
                    "request": requester_view(latest),
                    "offer": provider_offer_view(offer),
                    "allocation": _allocation_view(allocation),
                    "ownership_transferred": False,
                    "location_transferred": False,
                }
            raise ExchangeOperationError(409, "Exchange state conflict") from error
        raise

    record_audit(
        audit_table(),
        build_audit_event(
            organization_id,
            actor_sub,
            actor_role,
            "exchange.transfer_started",
            "exchange_request",
            request_id,
            location_id=resource.get("location_id") or "",
            metadata={
                "offer_id": offer["offer_id"],
                "resource_id": resource_id,
                "requester_organization_id": meta.get("requester_organization_id"),
            },
        ),
    )

    latest = _get_meta(request_id)
    try:
        from exchange_notify import notify_transfer_started

        notify_transfer_started(latest, offer, actor_sub)
    except Exception:
        pass
    return {
        "message": "Transfer started",
        "request": requester_view(latest),
        "offer": provider_offer_view(offer),
        "allocation": _allocation_view(allocation),
        "ownership_transferred": False,
        "location_transferred": False,
    }


def confirm_handover(
    exchange_request_id,
    body,
    organization_id,
    actor_sub,
    actor_role,
    membership,
    qr_session=None,
):
    """Requester confirms handover: TRANSFER_PENDING → COMPLETED + ownership/location."""
    if actor_role not in EXCHANGE_WRITE_ROLES:
        raise AccessError(403, "You are not allowed to perform this action")

    _require_active_organization(organization_id)
    meta = _get_meta(exchange_request_id)
    assert_requester_organization(membership, meta)
    status = str(meta.get("status", "")).upper()

    if status == "COMPLETED":
        offer, allocation = _accepted_context(meta)
        try:
            from exchange_notify import notify_handover_completed

            mode = str(meta.get("tracking_mode") or "").upper()
            extra = {"tracking_mode": mode or "INDIVIDUAL"}
            if mode == "QUANTITY":
                dest = (allocation or {}).get("destination_resource_id") or meta.get(
                    "completed_destination_resource_id"
                ) or ""
                extra["destination_resource_id"] = dest
                created = meta.get("completed_destination_created")
                if created is True:
                    extra["destination_mode"] = "CREATE"
                elif created is False:
                    extra["destination_mode"] = "MERGE"
                extra["quantity"] = (allocation or {}).get("quantity") or meta.get(
                    "quantity_transferred"
                )
            notify_handover_completed(meta, offer, actor_sub, **extra)
        except Exception:
            pass
        return _handover_response(meta, offer, allocation, message="Handover already completed")

    if status != "TRANSFER_PENDING":
        raise ExchangeOperationError(409, "Exchange request is not awaiting handover confirmation")

    offer, allocation = _accepted_context(meta)

    if str(offer.get("status", "")).upper() != "ACCEPTED":
        raise ExchangeOperationError(409, "Accepted offer is not in ACCEPTED state")

    if (
        allocation.get("allocation_type") != ALLOCATION_TYPE_EXCHANGE
        or str(allocation.get("status", "")).upper() != EXCHANGE_ALLOCATION_STATUS_OPEN
        or allocation.get("exchange_request_id") != meta["exchange_request_id"]
    ):
        raise ExchangeOperationError(409, "Exchange allocation is not open")

    provider_org = str(
        meta.get("accepted_provider_organization_id")
        or offer.get("provider_organization_id")
        or ""
    ).strip()
    resource_id = str(meta.get("accepted_resource_id") or offer.get("resource_id") or "").strip()
    resource = resources_table().get_item(Key={"resource_id": resource_id}).get("Item")

    if not resource or resource.get("organization_id") != provider_org:
        raise ExchangeOperationError(409, "Offered resource is no longer eligible")

    mode = normalize_tracking_mode(resource.get("tracking_mode") or meta.get("tracking_mode"))
    from handover_qr import audit_revoked, bind_for_handover, merge_channel

    if qr_session is not None:
        supplied = str((body or {}).get("destination_location_id") or "").strip()
        locked = str(meta.get("destination_location_id") or "").strip()
        if supplied and supplied != locked:
            raise ExchangeOperationError(
                400, "Destination location cannot be changed for QR handover"
            )
        if str(qr_session.get("resource_id") or "") != resource_id:
            raise ExchangeOperationError(409, "Offered resource is no longer eligible")
        if str(qr_session.get("offer_id") or "") != str(offer.get("offer_id") or ""):
            raise ExchangeOperationError(409, "Accepted offer is not in ACCEPTED state")
        body = dict(body or {})
        body["destination_location_id"] = locked
        if mode != "QUANTITY" and str(body.get("destination_resource_id") or "").strip():
            raise ExchangeOperationError(
                400, "Destination resource cannot be changed for QR handover"
            )
        if mode == "QUANTITY":
            from quantity_handover import held_quantity

            try:
                session_qty = int(qr_session.get("quantity"))
            except (TypeError, ValueError):
                session_qty = -1
            if session_qty != held_quantity(offer, allocation):
                raise ExchangeOperationError(409, "Quantity must match the accepted exchange hold")

    extra_items, audit_extra, revoked_session = bind_for_handover(
        _THIS,
        meta["exchange_request_id"],
        qr_session,
        _now_iso(),
    )
    if mode == "QUANTITY":
        from quantity_handover import confirm_quantity_handover

        result = confirm_quantity_handover(
            service=_THIS,
            meta=meta,
            offer=offer,
            allocation=allocation,
            resource=resource,
            body=body or {},
            organization_id=organization_id,
            actor_sub=actor_sub,
            actor_role=actor_role,
            extra_transact_items=extra_items,
            audit_extra=audit_extra,
        )
        if revoked_session and result.get("message") == "Handover completed":
            audit_revoked(
                _THIS,
                organization_id,
                actor_sub,
                actor_role,
                revoked_session,
            )
        return result

    if str(resource.get("operational_status") or "").upper() != "ALLOCATED":
        raise ExchangeOperationError(409, "Resource is not exchange-held")

    if available_flag(resource.get("Available")):
        raise ExchangeOperationError(409, "Resource is not exchange-held")

    dest_id = str(
        (body or {}).get("destination_location_id")
        or meta.get("destination_location_id")
        or ""
    ).strip()
    destination = require_location(locations_table(), organization_id, dest_id)

    now = _now_iso()
    request_id = meta["exchange_request_id"]
    oid = offer["offer_id"]
    allocation_id = allocation["allocation_id"]
    previous_location_id = str(resource.get("location_id") or "")
    previous_location_name = str(resource.get("Location") or "")
    new_location_id = destination["location_id"]
    new_location_name = str(destination.get("name") or "")

    exchanges_name = getattr(exchanges_table(), "table_name", None) or getattr(
        exchanges_table(), "name", TABLE_NAME
    )
    resources_name = getattr(resources_table(), "table_name", None) or getattr(
        resources_table(), "name", "Resources"
    )
    allocations_name = getattr(allocations_table(), "table_name", None) or getattr(
        allocations_table(), "name", "Allocations"
    )

    remove_public = ", ".join(PRIVATE_INDEX_ATTRIBUTES)
    resource_update = (
        "SET organization_id = :requester, location_id = :loc_id, #loc = :loc_name, "
        "#a = :true, operational_status = :available, visibility = :private, "
        "updated_at = :now "
        f"REMOVE {remove_public}"
    )

    transact_items = [
        {
            "Update": {
                "TableName": exchanges_name,
                "Key": _serialize_map({"pk": meta_pk(request_id), "sk": meta_sk()}),
                "UpdateExpression": (
                    "SET #status = :completed, completed_at = :now, confirming_actor_sub = :actor, "
                    "completed_destination_location_id = :loc_id, "
                    "previous_owner_organization_id = :provider, "
                    "previous_location_id = :prev_loc, "
                    "updated_at = :now, updated_by = :actor"
                ),
                "ConditionExpression": "#status = :pending",
                "ExpressionAttributeNames": {"#status": "status"},
                "ExpressionAttributeValues": _serialize_map(
                    {
                        ":completed": "COMPLETED",
                        ":pending": "TRANSFER_PENDING",
                        ":now": now,
                        ":actor": actor_sub,
                        ":loc_id": new_location_id,
                        ":provider": provider_org,
                        ":prev_loc": previous_location_id,
                    }
                ),
            }
        },
        {
            "Update": {
                "TableName": resources_name,
                "Key": _serialize_map({"resource_id": resource_id}),
                "UpdateExpression": resource_update,
                "ConditionExpression": (
                    "organization_id = :provider AND operational_status = :allocated "
                    "AND #a = :false"
                ),
                "ExpressionAttributeNames": {
                    "#a": "Available",
                    "#loc": "Location",
                },
                "ExpressionAttributeValues": _serialize_map(
                    {
                        ":requester": organization_id,
                        ":provider": provider_org,
                        ":loc_id": new_location_id,
                        ":loc_name": new_location_name,
                        ":true": True,
                        ":false": False,
                        ":available": "AVAILABLE",
                        ":allocated": "ALLOCATED",
                        ":private": "PRIVATE",
                        ":now": now,
                    }
                ),
            }
        },
        {
            "Update": {
                "TableName": allocations_name,
                "Key": _serialize_map({"allocation_id": allocation_id}),
                "UpdateExpression": (
                    "SET #status = :released, released_at = :now, updated_at = :now, "
                    "completed_by = :actor"
                ),
                "ConditionExpression": (
                    "attribute_exists(allocation_id) AND #status = :open "
                    "AND allocation_type = :exchange"
                ),
                "ExpressionAttributeNames": {"#status": "status"},
                "ExpressionAttributeValues": _serialize_map(
                    {
                        ":released": EXCHANGE_ALLOCATION_STATUS_RELEASED,
                        ":open": EXCHANGE_ALLOCATION_STATUS_OPEN,
                        ":exchange": ALLOCATION_TYPE_EXCHANGE,
                        ":now": now,
                        ":actor": actor_sub,
                    }
                ),
            }
        },
    ]
    transact_items.extend(extra_items)

    try:
        _transact_write(transact_items)
    except ClientError as error:
        code = error.response["Error"]["Code"]
        if code in {"TransactionCanceledException", "ConditionalCheckFailedException"}:
            latest = _get_meta(request_id)
            if str(latest.get("status", "")).upper() == "COMPLETED":
                offer_row, allocation_row = _accepted_context(latest)
                return _handover_response(
                    latest, offer_row, allocation_row, message="Handover already completed"
                )
            raise ExchangeOperationError(409, "Handover state conflict") from error
        raise

    history_item = {
        "history_id": "HIST-EXCHANGE-XFER-" + oid + "-" + resource_id,
        "resource_id": resource_id,
        "organization_id": organization_id,
        "location_id": new_location_id,
        "resource_type": resource.get("Type") or meta.get("resource_type_name") or "",
        "location": new_location_name,
        "previous_status": "ALLOCATED",
        "new_status": "AVAILABLE",
        "changed_at": now,
        "reason": "RESOURCE_EXCHANGE_TRANSFERRED",
        "allocation_id": allocation_id,
        "exchange_request_id": request_id,
        "offer_id": oid,
        "previous_organization_id": provider_org,
        "new_organization_id": organization_id,
        "previous_location_id": previous_location_id,
        "new_location_id": new_location_id,
    }
    try:
        history_table().put_item(
            Item=history_item,
            ConditionExpression="attribute_not_exists(history_id)",
        )
    except ClientError:
        pass

    record_audit(
        audit_table(),
        build_audit_event(
            organization_id,
            actor_sub,
            actor_role,
            "exchange.handover_confirmed",
            "exchange_request",
            request_id,
            location_id=new_location_id,
            metadata=merge_channel(
                {
                    "offer_id": oid,
                    "resource_id": resource_id,
                    "provider_organization_id": provider_org,
                    "requester_organization_id": organization_id,
                    "previous_location_id": previous_location_id,
                    "new_location_id": new_location_id,
                },
                audit_extra,
            ),
        ),
    )
    record_audit(
        audit_table(),
        build_audit_event(
            organization_id,
            actor_sub,
            actor_role,
            "resource.ownership_transferred",
            "resource",
            resource_id,
            location_id=new_location_id,
            metadata=merge_channel(
                {
                    "exchange_request_id": request_id,
                    "offer_id": oid,
                    "provider_organization_id": provider_org,
                    "previous_location_id": previous_location_id,
                    "new_location_id": new_location_id,
                },
                audit_extra,
            ),
        ),
    )
    if revoked_session:
        audit_revoked(
            _THIS,
            organization_id,
            actor_sub,
            actor_role,
            revoked_session,
        )

    latest = _get_meta(request_id)
    try:
        from exchange_notify import notify_handover_completed

        notify_handover_completed(
            latest,
            offer,
            actor_sub,
            tracking_mode="INDIVIDUAL",
        )
    except Exception:
        pass
    allocation_row = allocations_table().get_item(Key={"allocation_id": allocation_id}).get("Item") or {
        **allocation,
        "status": EXCHANGE_ALLOCATION_STATUS_RELEASED,
    }
    return _handover_response(latest, offer, allocation_row, message="Handover completed")


def _allocation_view(allocation):
    return {
        "allocation_id": allocation.get("allocation_id"),
        "allocation_type": allocation.get("allocation_type"),
        "status": allocation.get("status"),
        "resource_id": allocation.get("resource_id"),
        "quantity": allocation.get("quantity"),
        "exchange_request_id": allocation.get("exchange_request_id"),
        "offer_id": allocation.get("offer_id"),
        "provider_organization_id": allocation.get("provider_organization_id"),
        "requester_organization_id": allocation.get("requester_organization_id"),
        "destination_resource_id": allocation.get("destination_resource_id"),
        "destination_location_id": allocation.get("destination_location_id"),
    }


def _handover_response(meta, offer, allocation, *, message, transfer=None):
    payload = {
        "message": message,
        "request": requester_view(meta),
        "offer": requester_offer_view(offer),
        "allocation": _allocation_view(allocation),
        "ownership_transferred": str(meta.get("status", "")).upper() == "COMPLETED",
        "location_transferred": str(meta.get("status", "")).upper() == "COMPLETED",
    }
    if transfer is not None:
        payload["transfer"] = transfer
    elif str(meta.get("status", "")).upper() == "COMPLETED" and meta.get(
        "completed_destination_resource_id"
    ):
        payload["transfer"] = {
            "tracking_mode": "QUANTITY",
            "quantity": meta.get("quantity_transferred"),
            "source_resource_id": meta.get("accepted_resource_id"),
            "destination_resource_id": meta.get("completed_destination_resource_id"),
            "destination_created": None,
            "ownership_transferred": True,
        }
    return payload


def _acceptance_response(meta, offer, allocation):
    return {
        "message": "Offer accepted",
        "request": requester_view(meta),
        "offer": requester_offer_view(offer),
        "allocation": {
            "allocation_id": allocation.get("allocation_id"),
            "allocation_type": allocation.get("allocation_type"),
            "status": allocation.get("status"),
            "resource_id": allocation.get("resource_id"),
            "quantity": allocation.get("quantity"),
            "exchange_request_id": allocation.get("exchange_request_id"),
            "offer_id": allocation.get("offer_id"),
            "provider_organization_id": allocation.get("provider_organization_id"),
            "requester_organization_id": allocation.get("requester_organization_id"),
        },
        "ownership_transferred": False,
        "location_transferred": False,
    }


def _transact_write(transact_items):
    """Execute TransactWriteItems. Tests may monkeypatch this helper."""
    _dynamodb_client().transact_write_items(TransactItems=transact_items)


def issue_handover_qr(exchange_request_id, organization_id, actor_sub, actor_role, membership):
    from handover_qr import issue_qr

    return issue_qr(
        _THIS,
        exchange_request_id,
        organization_id,
        actor_sub,
        actor_role,
        membership,
    )


def preview_handover_qr(body, organization_id, actor_sub, actor_role, membership):
    from handover_qr import preview_qr

    return preview_qr(
        _THIS,
        body,
        organization_id,
        actor_sub,
        actor_role,
        membership,
    )


def confirm_handover_qr(body, organization_id, actor_sub, actor_role, membership):
    from handover_qr import confirm_qr

    return confirm_qr(
        _THIS,
        body,
        organization_id,
        actor_sub,
        actor_role,
        membership,
    )


# Phase 7A lifecycle recovery surface (cancel / reject / withdraw / expire).
from lifecycle import (  # noqa: E402
    cancel_exchange_request,
    reject_offer,
    run_expiry,
    withdraw_offer,
)
