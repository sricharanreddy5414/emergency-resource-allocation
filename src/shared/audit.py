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
        print("Audit write skipped:", error.__class__.__name__)

    print("AUDIT", json.dumps(safe, default=str))
    return safe
