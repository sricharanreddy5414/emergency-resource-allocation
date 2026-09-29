"""Resource Exchange API operations (Phase 5C).

Creates/lists requests and offers only. No accept, hold, transfer, or handover.
OFFER CREATE does not mutate Resources or Allocations.
"""

from __future__ import annotations

import hashlib
import json
import os

from botocore.exceptions import ClientError

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
    meta_pk,
    meta_sk,
    network_request_projection,
    new_exchange_request_id,
    new_offer_id,
    offer_sk,
)
from pages import decode_token, encode_token
from resource_state import (
    available_flag,
    effective_operational_status,
    normalize_tracking_mode,
    quantity_snapshot,
)


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


def _get_meta(exchange_request_id):
    request_id = str(exchange_request_id or "").strip()

    if not request_id:
        raise ExchangeOperationError(400, "exchange_request_id is required")

    if not request_id.startswith("EXREQ-"):
        request_id = "EXREQ-" + request_id

    item = exchanges_table().get_item(Key={"pk": meta_pk(request_id), "sk": meta_sk()}).get("Item")

    if not item or item.get("entity_type") != "EXCHANGE_REQUEST":
        raise AccessError(404, "Record not found")

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

    snapshot = quantity_snapshot(resource)
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

    expires_at = str((body or {}).get("expires_at") or "").strip()
    idempotency_key = str((body or {}).get("idempotency_key") or "").strip()

    fingerprint_payload = {
        "destination_location_id": location_id,
        "resource_type_id": resource_type["resource_type_id"],
        "tracking_mode": tracking_mode,
        "quantity_requested": quantity,
        "notes": notes,
        "expires_at": expires_at,
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


def list_network_requests(organization_id, query):
    _require_active_organization(organization_id)
    limit = _page_limit(query)
    start = decode_token(
        (query or {}).get("page_token"),
        [GSI_NETWORK_LIST_KEY, GSI_CREATED_AT, "pk", "sk"],
    )

    if start and start.get(GSI_NETWORK_LIST_KEY) != NETWORK_OPEN_LIST_VALUE:
        raise AccessError(400, "Invalid page token")

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

    return {
        "items": items,
        "next_token": encode_token(result.get("LastEvaluatedKey")),
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

    idempotency_key = str((body or {}).get("idempotency_key") or "").strip()
    fingerprint_payload = {
        "exchange_request_id": meta["exchange_request_id"],
        "resource_id": resource_id,
        "quantity_offered": quantity,
        "source_location_id": location["location_id"],
        "notes": notes,
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
