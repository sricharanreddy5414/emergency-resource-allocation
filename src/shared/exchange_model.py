"""ResourceExchanges item construction and GSI key helpers.

Phase 5B foundation only. No accept/hold/transfer/handover APIs.
Authorization still requires authorize(); GSIs are not a security boundary.
"""

import uuid
from datetime import datetime, timezone

from access import (
    AccessError,
    OPERATE_ROLES,
    READ_ROLES,
)
from exchange_state import (
    ALLOCATION_TYPE_EXCHANGE,
    normalize_offer_status,
    normalize_request_status,
)


TABLE_NAME = "ResourceExchanges"
SK_META = "META"
NETWORK_OPEN_LIST_VALUE = "OPEN"

# Sparse GSI attribute names (omit when blank / not participating).
GSI_NETWORK_LIST_KEY = "network_list_key"
GSI_REQUESTER_ORG = "requester_organization_id"
GSI_PROVIDER_ORG = "provider_organization_id"
GSI_CREATED_AT = "created_at"

INDEX_NETWORK_OPEN = "NetworkOpenRequestIndex"
INDEX_REQUESTER_ORG = "RequesterOrgIndex"
INDEX_PROVIDER_OFFER = "ProviderOrgOfferIndex"

EXCHANGE_READ_ROLES = READ_ROLES
EXCHANGE_WRITE_ROLES = OPERATE_ROLES

# Safe offer snapshot keys. Serial/asset/assignee never included here.
SAFE_RESOURCE_SNAPSHOT_KEYS = frozenset(
    {
        "name",
        "resource_type_name",
        "tracking_mode",
        "condition",
        "quantity_offered",
    }
)


def _now():
    return datetime.now(timezone.utc).isoformat()


def new_exchange_request_id():
    return "EXREQ-" + uuid.uuid4().hex[:16].upper()


def new_offer_id():
    return "EXOFF-" + uuid.uuid4().hex[:16].upper()


def meta_pk(exchange_request_id):
    request_id = str(exchange_request_id or "").strip()

    if not request_id:
        raise ValueError("exchange_request_id is required")

    if not request_id.startswith("EXREQ-"):
        request_id = "EXREQ-" + request_id

    return "EXREQ#" + request_id.replace("EXREQ#", "")


def meta_sk():
    return SK_META


def offer_sk(offer_id):
    offer = str(offer_id or "").strip()

    if not offer:
        raise ValueError("offer_id is required")

    if not offer.startswith("EXOFF-"):
        offer = "EXOFF-" + offer

    return "OFFER#" + offer


def idempotency_pk(organization_id, idempotency_key):
    org = str(organization_id or "").strip()
    key = str(idempotency_key or "").strip()

    if not org or not key:
        raise ValueError("organization_id and idempotency_key are required")

    if len(key) > 128:
        raise ValueError("idempotency_key is too long")

    return "IDEM#" + org + "#" + key


def omit_blank_index_keys(item):
    """Drop blank strings used as GSI keys. DynamoDB rejects empty key values."""
    if not isinstance(item, dict):
        raise ValueError("Item is invalid")

    cleaned = dict(item)

    for name in (GSI_NETWORK_LIST_KEY, GSI_REQUESTER_ORG, GSI_PROVIDER_ORG, GSI_CREATED_AT):
        value = cleaned.get(name)

        if value is None or (isinstance(value, str) and not value.strip()):
            cleaned.pop(name, None)

    return cleaned


def apply_network_open_index(meta_item, status):
    """Sparse NetworkOpenRequestIndex: only OPEN META carries network_list_key."""
    item = dict(meta_item)
    normalized = normalize_request_status(status)
    item["status"] = normalized

    if normalized == "OPEN":
        item[GSI_NETWORK_LIST_KEY] = NETWORK_OPEN_LIST_VALUE
    else:
        item.pop(GSI_NETWORK_LIST_KEY, None)

    return omit_blank_index_keys(item)


def build_meta_item(
    *,
    exchange_request_id,
    requester_organization_id,
    resource_type_id,
    resource_type_name,
    tracking_mode,
    destination_location_id,
    created_by,
    quantity_requested=1,
    destination_location_name="",
    destination_city="",
    destination_state="",
    notes="",
    expires_at="",
    idempotency_key="",
    status="OPEN",
    created_at=None,
):
    """Construct a request META item. Does not write DynamoDB."""
    now = created_at or _now()
    request_id = str(exchange_request_id or "").strip() or new_exchange_request_id()

    if not request_id.startswith("EXREQ-"):
        request_id = "EXREQ-" + request_id

    org = str(requester_organization_id or "").strip()

    if not org:
        raise ValueError("requester_organization_id is required")

    mode = str(tracking_mode or "INDIVIDUAL").strip().upper() or "INDIVIDUAL"
    qty = int(quantity_requested or 1)

    if qty < 1:
        raise ValueError("quantity_requested is invalid")

    item = {
        "pk": meta_pk(request_id),
        "sk": meta_sk(),
        "entity_type": "EXCHANGE_REQUEST",
        "exchange_request_id": request_id,
        "status": normalize_request_status(status),
        "requester_organization_id": org,
        "resource_type_id": str(resource_type_id or "").strip(),
        "resource_type_name": str(resource_type_name or "").strip()[:80],
        "tracking_mode": mode,
        "quantity_requested": qty,
        "destination_location_id": str(destination_location_id or "").strip(),
        "destination_location_name": str(destination_location_name or "").strip()[:80],
        "destination_city": str(destination_city or "").strip()[:60],
        "destination_state": str(destination_state or "").strip()[:60],
        "notes": str(notes or "").strip()[:300],
        "expires_at": str(expires_at or "").strip(),
        "handover_expires_at": "",
        "accepted_offer_id": "",
        "accepted_resource_id": "",
        "accepted_provider_organization_id": "",
        "version": 1,
        "created_by": str(created_by or "").strip(),
        "updated_by": str(created_by or "").strip(),
        "created_at": now,
        "updated_at": now,
        "completed_at": "",
        "confirming_actor_sub": "",
        "idempotency_key": str(idempotency_key or "").strip(),
        "allocation_type_hint": ALLOCATION_TYPE_EXCHANGE,
    }

    return apply_network_open_index(item, item["status"])


def build_offer_item(
    *,
    exchange_request_id,
    offer_id,
    provider_organization_id,
    resource_id,
    created_by,
    quantity_offered=1,
    source_location_id="",
    resource_snapshot=None,
    expires_at="",
    idempotency_key="",
    status="OPEN",
    created_at=None,
):
    """Construct an OFFER item. Does not hold or mutate Resources."""
    now = created_at or _now()
    request_id = str(exchange_request_id or "").strip()

    if not request_id.startswith("EXREQ-"):
        request_id = "EXREQ-" + request_id

    oid = str(offer_id or "").strip() or new_offer_id()

    if not oid.startswith("EXOFF-"):
        oid = "EXOFF-" + oid

    provider = str(provider_organization_id or "").strip()
    rid = str(resource_id or "").strip()

    if not provider or not rid:
        raise ValueError("provider_organization_id and resource_id are required")

    qty = int(quantity_offered or 1)

    if qty < 1:
        raise ValueError("quantity_offered is invalid")

    snapshot = {}

    for key, value in (resource_snapshot or {}).items():
        if key in SAFE_RESOURCE_SNAPSHOT_KEYS and value not in (None, ""):
            snapshot[key] = value

    snapshot["quantity_offered"] = qty

    item = {
        "pk": meta_pk(request_id),
        "sk": offer_sk(oid),
        "entity_type": "EXCHANGE_OFFER",
        "exchange_request_id": request_id,
        "offer_id": oid,
        "status": normalize_offer_status(status),
        "provider_organization_id": provider,
        "resource_id": rid,
        "quantity_offered": qty,
        "source_location_id": str(source_location_id or "").strip(),
        "resource_snapshot": snapshot,
        "expires_at": str(expires_at or "").strip(),
        "created_by": str(created_by or "").strip(),
        "updated_by": str(created_by or "").strip(),
        "created_at": now,
        "updated_at": now,
        "idempotency_key": str(idempotency_key or "").strip(),
    }

    # Offers never carry network_list_key (request browse index is META-only).
    item.pop(GSI_NETWORK_LIST_KEY, None)

    return omit_blank_index_keys(item)


def build_idempotency_item(
    *,
    organization_id,
    idempotency_key,
    operation,
    result_ref,
    created_at=None,
):
    """Foundation idempotency record for later create/accept flows."""
    op = str(operation or "").strip().upper()

    if op not in {
        "CREATE_REQUEST",
        "CREATE_OFFER",
        "ACCEPT_OFFER",
        "TRANSFER_START",
        "HANDOVER_CONFIRM",
        "CANCEL",
    }:
        raise ValueError("idempotency operation is invalid")

    now = created_at or _now()

    return omit_blank_index_keys(
        {
            "pk": idempotency_pk(organization_id, idempotency_key),
            "sk": op,
            "entity_type": "EXCHANGE_IDEMPOTENCY",
            "organization_id": str(organization_id).strip(),
            "idempotency_key": str(idempotency_key).strip(),
            "operation": op,
            "result_ref": dict(result_ref or {}),
            "created_at": now,
        }
    )


def network_request_projection(meta, *, viewer_organization_id=None):
    """Fields another org may see for an OPEN network request."""
    if not isinstance(meta, dict):
        return None

    if normalize_request_status(meta.get("status")) != "OPEN":
        return None

    if meta.get(GSI_NETWORK_LIST_KEY) != NETWORK_OPEN_LIST_VALUE and meta.get("status") != "OPEN":
        return None

    requester = str(meta.get("requester_organization_id") or "").strip()
    viewer = str(viewer_organization_id or "").strip()

    if viewer and requester and viewer == requester:
        return None

    return {
        "exchange_request_id": meta.get("exchange_request_id") or "",
        "status": "OPEN",
        "resource_type_name": meta.get("resource_type_name") or "",
        "tracking_mode": meta.get("tracking_mode") or "INDIVIDUAL",
        "quantity_requested": meta.get("quantity_requested") or 1,
        "destination_city": meta.get("destination_city") or "",
        "destination_state": meta.get("destination_state") or "",
        "expires_at": meta.get("expires_at") or "",
        "created_at": meta.get("created_at") or "",
        # Display name filled by later API from Organizations; never leak internal ids beyond request id.
        "requester_organization_display_name": meta.get("requester_organization_display_name") or "",
    }


def assert_requester_organization(membership, meta):
    """Caller membership org must own the request. GSIs are not authz."""
    org = (membership or {}).get("organization_id")
    owner = (meta or {}).get("requester_organization_id")

    if not org or not owner or org != owner:
        raise AccessError(404, "Record not found")

    return membership


def assert_provider_organization(membership, offer):
    """Caller membership org must own the offer."""
    org = (membership or {}).get("organization_id")
    owner = (offer or {}).get("provider_organization_id")

    if not org or not owner or org != owner:
        raise AccessError(404, "Record not found")

    return membership


def exchange_allocation_id(exchange_request_id):
    """Reserved id shape for Phase 5E holds. Not written in Phase 5B."""
    request_id = str(exchange_request_id or "").strip()

    if not request_id.startswith("EXREQ-"):
        request_id = "EXREQ-" + request_id

    return "EXCHANGE-" + request_id
