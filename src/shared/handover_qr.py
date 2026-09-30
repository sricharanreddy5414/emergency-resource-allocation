"""One-time QR handover sessions on ResourceExchanges.

The raw token is returned once from issue and never stored. Lookup uses
SHA-256. Completion still goes through the existing handover transaction.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from botocore.exceptions import ClientError

from access import AccessError
from audit import build_audit_event, record_audit
from exchange_model import meta_pk

QR_PREFIX = "erap-hq.v1."
QR_TTL_MINUTES = 15
QR_ISSUE_CAP = 30
QR_RETAIN_SECONDS = 7 * 24 * 3600
SESSION_SK = "SESSION"
POINTER_SK = "HANDOVER#QR"
ENTITY_SESSION = "HANDOVER_QR_SESSION"
ENTITY_POINTER = "HANDOVER_QR_POINTER"
INVALID_QR = "Handover QR is not valid"
_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{43,80}")


def log_qr(result, session_id="", exchange_request_id=""):
    """Structured metric line. Never includes the raw token or its hash."""
    print(
        json.dumps(
            {
                "qr_result": result,
                "session_id": session_id or "",
                "exchange_request_id": exchange_request_id or "",
            },
            default=str,
        )
    )


def new_token():
    """256-bit token from the OS CSPRNG. token_urlsafe(32) is 32 random bytes."""
    return secrets.token_urlsafe(32)


def token_digest(token):
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()


def parse_presented_token(body):
    """Return the secret segment or None. Accepts only the versioned payload."""
    if not isinstance(body, dict):
        return None
    text = str(body.get("token") or "").strip()
    if not text.startswith(QR_PREFIX):
        return None
    token = text[len(QR_PREFIX) :]
    if not _TOKEN_RE.fullmatch(token):
        return None
    return token


def digests_match(presented_token, stored_hash):
    if not presented_token or not stored_hash:
        return False
    return hmac.compare_digest(token_digest(presented_token), str(stored_hash))


def effective_expiry(issued, handover_expires_at, *, minutes=QR_TTL_MINUTES):
    """QR lifetime is min(issued+minutes, handover deadline). None if already due."""
    if issued.tzinfo is None:
        issued = issued.replace(tzinfo=timezone.utc)
    issued = issued.astimezone(timezone.utc)
    qr_end = issued + timedelta(minutes=minutes)
    handover_end = _parse(handover_expires_at)
    expires = qr_end
    if handover_end is not None and handover_end < expires:
        expires = handover_end
    if expires <= issued:
        return None
    return expires


def session_pk(token_hash):
    return "HQRS#" + str(token_hash)


def session_key(token_hash):
    return {"pk": session_pk(token_hash), "sk": SESSION_SK}


def pointer_key(exchange_request_id):
    return {"pk": meta_pk(exchange_request_id), "sk": POINTER_SK}


def issue_qr(service, exchange_request_id, organization_id, actor_sub, actor_role, membership):
    """Provider mints or rotates the single active session. Does not change Exchange state."""
    if actor_role not in _write_roles():
        raise AccessError(403, "You are not allowed to perform this action")

    service._require_active_organization(organization_id)
    meta = service._get_meta(exchange_request_id)
    request_id = meta["exchange_request_id"]
    status = str(meta.get("status", "")).upper()

    if status == "COMPLETED":
        raise service.ExchangeOperationError(409, "Exchange request already completed")
    if status != "TRANSFER_PENDING":
        if status == "ACCEPTED":
            raise service.ExchangeOperationError(
                409, "Exchange request is not awaiting handover confirmation"
            )
        raise service.ExchangeOperationError(409, "Exchange request is not accepted")
    if meta.get("accepted_provider_organization_id") != organization_id:
        raise AccessError(404, "Record not found")

    offer, allocation = service._accepted_context(meta)
    from exchange_model import assert_provider_organization

    assert_provider_organization(membership, offer)
    _assert_hold_open(service, meta, offer, allocation)
    resource = _load_provider_resource(service, meta, offer, organization_id)
    mode = _mode(service, resource, meta)
    held = _held(offer, allocation, mode)
    _assert_resource_eligible(service, resource, organization_id, meta, held, mode)

    pointer = _get_pointer(service, request_id)
    count = int(pointer.get("generation_count") or 0) if pointer else 0
    if count >= QR_ISSUE_CAP:
        log_qr("generation_limited", "", request_id)
        raise service.ExchangeOperationError(
            429,
            "Handover QR generation limit reached",
            code="QR_GENERATION_LIMIT",
        )

    now = _clock(service)
    expires = effective_expiry(now, meta.get("handover_expires_at"))
    if expires is None:
        raise service.ExchangeOperationError(409, "Handover window has ended")

    token = new_token()
    digest = token_digest(token)
    session_id = "HQRS-" + uuid.uuid4().hex[:16].upper()
    expires_at = expires.isoformat()
    issued_at = now.isoformat()
    old_session = None
    if pointer and pointer.get("active_token_hash"):
        old_session = _get_session_by_hash(service, pointer.get("active_token_hash"))

    table_name = _table_name(service)
    ttl_epoch = int(expires.timestamp()) + QR_RETAIN_SECONDS
    session_item = {
        "pk": session_pk(digest),
        "sk": SESSION_SK,
        "entity_type": ENTITY_SESSION,
        "session_id": session_id,
        "token_hash": digest,
        "exchange_request_id": request_id,
        "status": "ACTIVE",
        "expires_at": expires_at,
        "issued_at": issued_at,
        "issued_by": actor_sub,
        "provider_organization_id": organization_id,
        "requester_organization_id": meta.get("requester_organization_id") or "",
        "offer_id": offer.get("offer_id") or "",
        "resource_id": str(meta.get("accepted_resource_id") or offer.get("resource_id") or ""),
        "tracking_mode": mode,
        "quantity": held if mode == "QUANTITY" else None,
        "qr_ttl_epoch": ttl_epoch,
    }
    session_item = {key: value for key, value in session_item.items() if value is not None}

    transact = []
    if old_session and str(old_session.get("status", "")).upper() == "ACTIVE":
        transact.append(
            _status_update(
                service,
                table_name,
                session_key(old_session.get("token_hash") or pointer.get("active_token_hash")),
                terminal="REVOKED",
                now_iso=issued_at,
                expires_at=old_session.get("expires_at") or "",
                stamp_attr="revoked_at",
            )
        )
    transact.append(
        {
            "Put": {
                "TableName": table_name,
                "Item": service._serialize_map(session_item),
                "ConditionExpression": "attribute_not_exists(pk)",
            }
        }
    )
    if pointer:
        transact.append(
            {
                "Update": {
                    "TableName": table_name,
                    "Key": service._serialize_map(pointer_key(request_id)),
                    "UpdateExpression": (
                        "SET active_token_hash = :hash, session_id = :session, #status = :active_status, "
                        "expires_at = :expires, generation_count = generation_count + :one, "
                        "version = version + :one, updated_at = :now, replaced_session_id = :old_session"
                    ),
                    "ConditionExpression": "version = :expected AND generation_count < :cap",
                    "ExpressionAttributeNames": {"#status": "status"},
                    "ExpressionAttributeValues": service._serialize_map(
                        {
                            ":hash": digest,
                            ":session": session_id,
                            ":active_status": "ACTIVE",
                            ":expires": expires_at,
                            ":one": 1,
                            ":now": issued_at,
                            ":old_session": (old_session or {}).get("session_id") or "",
                            ":expected": int(pointer.get("version") or 0),
                            ":cap": QR_ISSUE_CAP,
                        }
                    ),
                }
            }
        )
    else:
        pointer_item = {
            "pk": pointer_key(request_id)["pk"],
            "sk": POINTER_SK,
            "entity_type": ENTITY_POINTER,
            "active_token_hash": digest,
            "session_id": session_id,
            "status": "ACTIVE",
            "expires_at": expires_at,
            "generation_count": 1,
            "version": 1,
            "exchange_request_id": request_id,
            "updated_at": issued_at,
        }
        transact.append(
            {
                "Put": {
                    "TableName": table_name,
                    "Item": service._serialize_map(pointer_item),
                    "ConditionExpression": "attribute_not_exists(pk)",
                }
            }
        )

    try:
        service._transact_write(transact)
    except ClientError as error:
        code = error.response["Error"]["Code"]
        if code in {"TransactionCanceledException", "ConditionalCheckFailedException"}:
            latest = _get_pointer(service, request_id)
            latest_count = int(latest.get("generation_count") or 0) if latest else 0
            if latest_count >= QR_ISSUE_CAP:
                log_qr("generation_limited", "", request_id)
                raise service.ExchangeOperationError(
                    429,
                    "Handover QR generation limit reached",
                    code="QR_GENERATION_LIMIT",
                ) from error
            raise service.ExchangeOperationError(409, "Exchange state conflict") from error
        raise

    generation = count + 1
    record_audit(
        service.audit_table(),
        build_audit_event(
            organization_id,
            actor_sub,
            actor_role,
            "exchange.handover_qr_issued",
            "handover_qr",
            session_id,
            metadata={
                "session_id": session_id,
                "exchange_request_id": request_id,
                "expires_at": expires_at,
                "generation_count": generation,
                "replaced_session_id": (old_session or {}).get("session_id") or "",
            },
        ),
    )
    if old_session and str(old_session.get("status", "")).upper() == "ACTIVE":
        _audit_revoked(
            service,
            organization_id,
            actor_sub,
            actor_role,
            old_session,
            generation_count=count,
        )
    log_qr("issued", session_id, request_id)
    return {
        "message": "Handover QR issued",
        "session_id": session_id,
        "expires_at": expires_at,
        "qr_payload": QR_PREFIX + token,
        "exchange_request_id": request_id,
    }


def preview_qr(service, body, organization_id, actor_sub, actor_role, membership):
    """Read-only preview for the requester. Does not consume the session."""
    del actor_sub, actor_role, membership
    service._require_active_organization(organization_id)
    session, meta = _requester_session(service, body, organization_id, kind="preview_rejected")
    if _session_expired(session, _clock(service)):
        _mark_expired(service, session)
        log_qr("expired", session.get("session_id") or "", session.get("exchange_request_id") or "")
        raise service.ExchangeOperationError(409, "Handover QR has expired")
    if str(session.get("status", "")).upper() != "ACTIVE":
        log_qr("preview_rejected")
        raise AccessError(404, INVALID_QR)
    if str(meta.get("status", "")).upper() != "TRANSFER_PENDING":
        log_qr("preview_rejected")
        raise AccessError(404, INVALID_QR)

    offer, allocation = service._accepted_context(meta)
    _assert_hold_open(service, meta, offer, allocation)
    provider_org = str(meta.get("accepted_provider_organization_id") or "").strip()
    resource = _load_provider_resource(service, meta, offer, provider_org)
    mode = _mode(service, resource, meta)
    held = _held(offer, allocation, mode)
    _assert_resource_eligible(service, resource, provider_org, meta, held, mode)
    if str(session.get("resource_id") or "") != str(meta.get("accepted_resource_id") or ""):
        raise service.ExchangeOperationError(409, "Offered resource is no longer eligible")

    log_qr("preview_ok", session.get("session_id") or "", meta.get("exchange_request_id") or "")
    return {
        "session_id": session.get("session_id"),
        "expires_at": session.get("expires_at"),
        "exchange_request_id": meta.get("exchange_request_id"),
        "tracking_mode": mode,
        "resource_type_name": meta.get("resource_type_name") or "",
        "quantity": held if mode == "QUANTITY" else None,
        "destination_location_id": meta.get("destination_location_id") or "",
        "destination_mode": "PENDING_CONFIRM" if mode == "QUANTITY" else None,
    }


def confirm_qr(service, body, organization_id, actor_sub, actor_role, membership):
    """Validate the token, then run the existing handover with the session consume."""
    service._require_active_organization(organization_id)
    token = parse_presented_token(body)
    session = _load_presented(service, token) if token else None
    if not session or not digests_match(token, session.get("token_hash")):
        log_qr("confirm_rejected")
        raise AccessError(404, INVALID_QR)
    if session.get("requester_organization_id") != organization_id:
        log_qr("confirm_rejected")
        raise AccessError(404, INVALID_QR)

    meta = service._get_meta(session.get("exchange_request_id"))
    status = str(meta.get("status", "")).upper()
    session_status = str(session.get("status", "")).upper()
    if status == "COMPLETED" or session_status == "CONSUMED":
        if status == "COMPLETED":
            result = service.confirm_handover(
                meta["exchange_request_id"],
                {},
                organization_id,
                actor_sub,
                actor_role,
                membership,
            )
            result["session_id"] = session.get("session_id")
            result["qr_consumed"] = False
            return result
        log_qr("confirm_rejected")
        raise AccessError(404, INVALID_QR)

    if _session_expired(session, _clock(service)):
        _mark_expired(service, session)
        log_qr("expired", session.get("session_id") or "", meta.get("exchange_request_id") or "")
        raise service.ExchangeOperationError(409, "Handover QR has expired")
    if session_status != "ACTIVE":
        log_qr("confirm_rejected")
        raise AccessError(404, INVALID_QR)
    if status != "TRANSFER_PENDING":
        raise service.ExchangeOperationError(409, "Exchange request is not awaiting handover confirmation")

    cleaned = _locked_body(service, body, meta, session)
    result = service.confirm_handover(
        meta["exchange_request_id"],
        cleaned,
        organization_id,
        actor_sub,
        actor_role,
        membership,
        qr_session=session,
    )
    result["session_id"] = session.get("session_id")
    result["qr_consumed"] = result.get("message") == "Handover completed"
    if result["qr_consumed"]:
        log_qr("consumed", session.get("session_id") or "", meta.get("exchange_request_id") or "")
    else:
        log_qr("confirm_conflict", session.get("session_id") or "", meta.get("exchange_request_id") or "")
    return result


def bind_for_handover(service, request_id, qr_session, now_iso):
    """Extra TransactWrite items for QR consume (QR path) or revoke (manual path)."""
    if qr_session is not None:
        items = _terminal_items(service, qr_session, now_iso, "CONSUMED", stamp_attr="consumed_at")
        return (
            items,
            {"handover_channel": "QR", "session_id": qr_session.get("session_id") or ""},
            None,
        )
    pointer, session = _load_active_pair(service, request_id)
    if not session:
        return [], {"handover_channel": "MANUAL"}, None
    items = _terminal_items(service, session, now_iso, "REVOKED", stamp_attr="revoked_at")
    return (
        items,
        {"handover_channel": "MANUAL", "session_id": session.get("session_id") or ""},
        session,
    )


def revoke_active_for_request(service, request_id, now_iso):
    """Items that revoke the active session inside cancel or expiry. Empty when none."""
    _pointer, session = _load_active_pair(service, request_id)
    if not session:
        return [], None
    return _terminal_items(service, session, now_iso, "REVOKED", stamp_attr="revoked_at"), session


def audit_revoked(service, organization_id, actor_sub, actor_role, session, generation_count=None):
    _audit_revoked(
        service,
        organization_id,
        actor_sub,
        actor_role,
        session,
        generation_count=generation_count,
    )


def merge_channel(metadata, audit_extra):
    merged = dict(metadata or {})
    extra = audit_extra or {}
    merged["handover_channel"] = extra.get("handover_channel") or "MANUAL"
    if extra.get("session_id"):
        merged["session_id"] = extra["session_id"]
    return merged


def _requester_session(service, body, organization_id, *, kind):
    token = parse_presented_token(body)
    session = _load_presented(service, token) if token else None
    if not session or not digests_match(token, session.get("token_hash")):
        log_qr(kind)
        raise AccessError(404, INVALID_QR)
    if session.get("requester_organization_id") != organization_id:
        log_qr(kind)
        raise AccessError(404, INVALID_QR)
    meta = service._get_meta(session.get("exchange_request_id"))
    return session, meta


def _load_presented(service, token):
    if not token:
        return None
    return _get_session_by_hash(service, token_digest(token))


def _locked_body(service, body, meta, session):
    supplied = str((body or {}).get("destination_location_id") or "").strip()
    locked = str(meta.get("destination_location_id") or "").strip()
    if supplied and supplied != locked:
        raise service.ExchangeOperationError(400, "Destination location cannot be changed for QR handover")
    mode = str(session.get("tracking_mode") or meta.get("tracking_mode") or "").upper()
    cleaned = dict(body or {})
    cleaned["destination_location_id"] = locked
    if mode != "QUANTITY" and str(cleaned.get("destination_resource_id") or "").strip():
        raise service.ExchangeOperationError(400, "Destination resource cannot be changed for QR handover")
    return cleaned


def _terminal_items(service, session, now_iso, terminal, *, stamp_attr):
    table_name = _table_name(service)
    request_id = session.get("exchange_request_id")
    token_hash = session.get("token_hash") or ""
    ttl = _epoch(_parse(now_iso) or datetime.now(timezone.utc)) + QR_RETAIN_SECONDS
    session_update = _status_update(
        service,
        table_name,
        session_key(token_hash),
        terminal=terminal,
        now_iso=now_iso,
        expires_at=session.get("expires_at") or "",
        stamp_attr=stamp_attr,
        ttl_epoch=ttl,
        require_unexpired=terminal == "CONSUMED",
    )
    pointer_update = {
        "Update": {
            "TableName": table_name,
            "Key": service._serialize_map(pointer_key(request_id)),
            "UpdateExpression": (
                "SET #status = :next, qr_ttl_epoch = :ttl, updated_at = :now, closed_at = :now "
                "REMOVE active_token_hash"
            ),
            "ConditionExpression": "#status = :active AND session_id = :sid",
            "ExpressionAttributeNames": {"#status": "status"},
            "ExpressionAttributeValues": service._serialize_map(
                {
                    ":next": terminal,
                    ":ttl": ttl,
                    ":now": now_iso,
                    ":active": "ACTIVE",
                    ":sid": session.get("session_id") or "",
                }
            ),
        }
    }
    return [session_update, pointer_update]


def _status_update(
    service,
    table_name,
    key,
    *,
    terminal,
    now_iso,
    expires_at,
    stamp_attr,
    ttl_epoch=None,
    require_unexpired=False,
):
    ttl = ttl_epoch
    if ttl is None:
        parsed = _parse(now_iso) or datetime.now(timezone.utc)
        ttl = _epoch(parsed) + QR_RETAIN_SECONDS
    condition = "#status = :active AND expires_at = :expires"
    values = {
        ":next": terminal,
        ":ttl": ttl,
        ":now": now_iso,
        ":active": "ACTIVE",
        ":expires": expires_at,
    }
    if require_unexpired:
        condition += " AND expires_at > :now"
    return {
        "Update": {
            "TableName": table_name,
            "Key": service._serialize_map(key),
            "UpdateExpression": (
                f"SET #status = :next, qr_ttl_epoch = :ttl, updated_at = :now, {stamp_attr} = :now"
            ),
            "ConditionExpression": condition,
            "ExpressionAttributeNames": {"#status": "status"},
            "ExpressionAttributeValues": service._serialize_map(values),
        }
    }


def _load_active_pair(service, request_id):
    pointer = _get_pointer(service, request_id)
    if not pointer or str(pointer.get("status", "")).upper() != "ACTIVE":
        return pointer, None
    if not pointer.get("active_token_hash"):
        return pointer, None
    session = _get_session_by_hash(service, pointer.get("active_token_hash"))
    if not session or str(session.get("status", "")).upper() != "ACTIVE":
        return pointer, None
    return pointer, session


def _get_pointer(service, request_id):
    item = (
        service.exchanges_table()
        .get_item(Key=pointer_key(request_id))
        .get("Item")
    )
    if not item or item.get("entity_type") != ENTITY_POINTER:
        return None
    return item


def _get_session_by_hash(service, token_hash):
    if not token_hash:
        return None
    item = (
        service.exchanges_table()
        .get_item(Key=session_key(token_hash))
        .get("Item")
    )
    if not item or item.get("entity_type") != ENTITY_SESSION:
        return None
    return item


def _mark_expired(service, session):
    now = _clock(service).isoformat()
    try:
        service.exchanges_table().update_item(
            Key=session_key(session.get("token_hash")),
            UpdateExpression="SET #status = :expired, updated_at = :now",
            ConditionExpression="#status = :active AND expires_at = :expires",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":expired": "EXPIRED",
                ":active": "ACTIVE",
                ":expires": session.get("expires_at") or "",
                ":now": now,
            },
        )
    except ClientError:
        return


def _session_expired(session, now):
    status = str(session.get("status", "")).upper()
    if status == "EXPIRED":
        return True
    if status != "ACTIVE":
        return False
    due = _parse(session.get("expires_at"))
    if due is None:
        return True
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc) >= due


def _assert_hold_open(service, meta, offer, allocation):
    from exchange_state import ALLOCATION_TYPE_EXCHANGE, EXCHANGE_ALLOCATION_STATUS_OPEN

    if str(offer.get("status", "")).upper() != "ACCEPTED":
        raise service.ExchangeOperationError(409, "Accepted offer is not in ACCEPTED state")
    if (
        allocation.get("allocation_type") != ALLOCATION_TYPE_EXCHANGE
        or str(allocation.get("status", "")).upper() != EXCHANGE_ALLOCATION_STATUS_OPEN
        or allocation.get("exchange_request_id") != meta.get("exchange_request_id")
    ):
        raise service.ExchangeOperationError(409, "Exchange allocation is not open")


def _load_provider_resource(service, meta, offer, provider_org):
    resource_id = str(meta.get("accepted_resource_id") or offer.get("resource_id") or "").strip()
    resource = service.resources_table().get_item(Key={"resource_id": resource_id}).get("Item")
    if not resource or resource.get("organization_id") != provider_org:
        raise service.ExchangeOperationError(409, "Offered resource is no longer eligible")
    return resource


def _assert_resource_eligible(service, resource, provider_org, meta, held, mode):
    resource_id = str(meta.get("accepted_resource_id") or resource.get("resource_id") or "")
    if mode == "QUANTITY":
        from quantity_handover import assert_source_eligible

        assert_source_eligible(
            resource,
            provider_org,
            resource_id,
            held,
            error_cls=service.ExchangeOperationError,
        )
        return
    if str(resource.get("operational_status") or "").upper() != "ALLOCATED":
        raise service.ExchangeOperationError(409, "Offered resource is no longer eligible")


def _held(offer, allocation, mode):
    if mode != "QUANTITY":
        return None
    from quantity_handover import held_quantity

    return held_quantity(offer, allocation)


def _mode(service, resource, meta):
    from resource_state import normalize_tracking_mode

    del service
    return normalize_tracking_mode(resource.get("tracking_mode") or meta.get("tracking_mode"))


def _audit_revoked(service, organization_id, actor_sub, actor_role, session, generation_count=None):
    metadata = {
        "session_id": session.get("session_id") or "",
        "exchange_request_id": session.get("exchange_request_id") or "",
        "expires_at": session.get("expires_at") or "",
    }
    if generation_count is not None:
        metadata["generation_count"] = int(generation_count)
    record_audit(
        service.audit_table(),
        build_audit_event(
            organization_id,
            actor_sub,
            actor_role,
            "exchange.handover_qr_revoked",
            "handover_qr",
            session.get("session_id") or "",
            metadata=metadata,
        ),
    )


def _table_name(service):
    table = service.exchanges_table()
    return getattr(table, "table_name", None) or getattr(table, "name", "ResourceExchanges")


def _clock(service):
    parsed = _parse(service._now_iso())
    return parsed or datetime.now(timezone.utc)


def _parse(value):
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _epoch(moment):
    return int(moment.timestamp())


def _write_roles():
    from exchange_model import EXCHANGE_WRITE_ROLES

    return EXCHANGE_WRITE_ROLES
