import json
import uuid
from datetime import datetime, timezone


def build_audit_event(
    organization_id,
    actor_sub,
    actor_role,
    action,
    entity_type,
    entity_id,
    result="SUCCESS",
    location_id="",
    metadata=None,
):
    timestamp = datetime.now(timezone.utc).isoformat()
    event_id = timestamp + "#" + uuid.uuid4().hex[:12]

    return {
        "event_id": event_id,
        "timestamp": timestamp,
        "organization_id": organization_id or "",
        "location_id": location_id or "",
        "actor_sub": actor_sub or "",
        "actor_role": actor_role or "",
        "action": action,
        "entity_type": entity_type,
        "entity_id": entity_id or "",
        "result": result,
        "metadata": metadata or {},
    }


def record_audit(table, event):
    """Best-effort audit. A logging failure must not undo the operation."""
    safe = dict(event)
    metadata = safe.get("metadata") or {}
    safe["metadata"] = {
        key: value
        for key, value in metadata.items()
        if key not in {
            "token",
            "qr_payload",
            "token_hash",
            "active_token_hash",
            "authorization",
            "password",
            "secret",
        }
    }

    try:
        if table is not None:
            table.put_item(Item=safe)
    except Exception as error:
        from observability import log_event

        log_event(
            "ERROR",
            "audit",
            "write",
            "failed",
            error_code=error.__class__.__name__,
            organization_id=safe.get("organization_id") or "",
        )

    from observability import log_event

    logged = dict(safe)
    actor = logged.pop("actor_sub", "")
    entity_id = str(logged.get("entity_id") or "")
    identifiers = {}
    if entity_id.startswith("EXREQ-"):
        identifiers["exchange_request_id"] = entity_id
    elif entity_id.startswith("EXOFF-"):
        identifiers["exchange_offer_id"] = entity_id
    elif logged.get("entity_type") == "resource":
        identifiers["resource_id"] = entity_id
    log_event(
        "INFO",
        "audit",
        logged.get("action") or "audit",
        str(logged.get("result") or "SUCCESS").lower(),
        organization_id=logged.get("organization_id") or "",
        actor_sub=actor,
        entity_type=logged.get("entity_type") or "",
        entity_id=entity_id,
        **identifiers,
    )
    return safe
