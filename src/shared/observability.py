"""Request correlation and safe operational logs. Tokens are never logged.

Actor identifiers in logs are SHA-256 of the UTF-8 Cognito sub, truncated to
16 hex characters. The hash is deterministic and not reversible. The raw sub
is not written to CloudWatch.
"""

import hashlib
import json
import re
import secrets
import time
from contextvars import ContextVar


_meta = ContextVar("erap_request_meta", default=None)
MAX_BODY_BYTES = 8192
_CODES = {
    400: "INVALID_REQUEST",
    401: "UNAUTHENTICATED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    500: "REQUEST_FAILED",
}


_REDACTED_KEYS = {
    "authorization",
    "cookie",
    "password",
    "mfa",
    "otp",
    "secret",
    "webhook_secret",
    "signature",
    "token",
    "page_token",
    "next_token",
    "qr_payload",
    "qr_token",
    "token_hash",
    "active_token_hash",
    "access_token",
    "refresh_token",
    "id_token",
    "card",
    "cvv",
    "email",
    "phone",
    "body",
    "headers",
    "environment",
}
_ID_RE = re.compile(r"\b(EXREQ-[A-Za-z0-9_-]+|EXOFF-[A-Za-z0-9_-]+)\b")


def actor_hash(actor_sub):
    text = str(actor_sub or "").strip()
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _sensitive_text(value):
    text = str(value or "")
    if "bearer " in text.lower():
        return True
    parts = text.split(".")
    return len(parts) == 3 and all(len(part) > 8 for part in parts)


def begin_request(event):
    event = event or {}
    context = event.get("requestContext") or {}
    request_id = str(context.get("requestId") or "").strip() or secrets.token_hex(8)
    method = str(event.get("httpMethod") or context.get("http", {}).get("method") or "").upper()
    path = str(event.get("path") or event.get("rawPath") or "")
    meta = {
        "request_id": request_id[:80],
        "route": f"{method} {path}"[:160],
        "started": time.monotonic(),
    }
    _meta.set(meta)
    return request_id


def current_request_id():
    meta = _meta.get()
    return meta["request_id"] if meta else ""


def note_context(*, organization_id="", actor_sub=""):
    meta = dict(_meta.get() or {})
    if organization_id:
        meta["organization_id"] = str(organization_id)
    if actor_sub:
        meta["actor_sub_hash"] = actor_hash(actor_sub)
    _meta.set(meta)


def log_event(level, service, operation, outcome, **fields):
    """One JSON line. Secret-shaped fields and raw actor subs are dropped."""
    allowed_level = str(level or "INFO").upper()
    if allowed_level not in {"INFO", "WARNING", "ERROR"}:
        allowed_level = "INFO"
    meta = _meta.get() or {}
    payload = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "level": allowed_level,
        "service": str(service or ""),
        "operation": str(operation or ""),
        "outcome": str(outcome or ""),
        "correlation_id": meta.get("request_id", ""),
    }
    if meta.get("organization_id") and "organization_id" not in fields:
        payload["organization_id"] = meta.get("organization_id")
    if meta.get("actor_sub_hash") and "actor_sub_hash" not in fields:
        payload["actor_sub_hash"] = meta.get("actor_sub_hash")
    for key, value in fields.items():
        name = str(key)
        if name.lower() in _REDACTED_KEYS or name in {"actor_sub", "user_sub"}:
            if name in {"actor_sub", "user_sub"}:
                payload["actor_sub_hash"] = actor_hash(value)
            continue
        if isinstance(value, str) and _sensitive_text(value):
            continue
        if value is None or value == "":
            continue
        payload[name] = value
    print(json.dumps(payload, default=str))
    return payload


def load_object(raw, is_base64=False):
    if raw is None or raw == "":
        return {}

    if is_base64 and isinstance(raw, str):
        import base64

        raw = base64.b64decode(raw).decode("utf-8")

    if isinstance(raw, str):
        if len(raw.encode("utf-8")) > MAX_BODY_BYTES:
            raise ValueError("Request is too large")
        parsed = json.loads(raw)
    elif isinstance(raw, dict):
        parsed = raw
    else:
        raise ValueError("JSON body must be an object")

    if not isinstance(parsed, dict):
        raise ValueError("JSON body must be an object")

    if _depth(parsed) > 5:
        raise ValueError("Request is invalid")

    if len(json.dumps(parsed).encode("utf-8")) > MAX_BODY_BYTES:
        raise ValueError("Request is too large")

    return parsed


def error_body(status_code, body):
    if status_code < 400 or not isinstance(body, dict):
        return body

    message = str(body.get("message") or "Request failed")
    code = str(body.get("code") or _CODES.get(status_code, "REQUEST_FAILED"))
    return {
        "message": message,
        "error": {
            "code": code,
            "message": message,
            "request_id": current_request_id(),
        },
    }


def log_result(status_code, operation="", organization_id="", entity_type="", entity_id="", error_code=""):
    meta = _meta.get() or {}
    route = str(meta.get("route") or "")
    if route.startswith("OPTIONS"):
        return
    started = meta.get("started")
    duration_ms = int((time.monotonic() - started) * 1000) if started else 0
    found = _ID_RE.findall(route)
    exchange_request_id = next((item for item in found if item.startswith("EXREQ-")), "")
    exchange_offer_id = next((item for item in found if item.startswith("EXOFF-")), "")
    level = "INFO"
    outcome = "ok"
    if status_code >= 500:
        level = "ERROR"
        outcome = "failed"
    elif status_code >= 400:
        level = "WARNING"
        outcome = "denied"
    log_event(
        level,
        "api",
        operation or route,
        outcome,
        request_id=meta.get("request_id", ""),
        correlation_id=meta.get("request_id", ""),
        route=route,
        result=status_code,
        duration_ms=duration_ms,
        organization_id=organization_id or meta.get("organization_id", ""),
        entity_type=entity_type,
        entity_id=entity_id,
        error_code=error_code,
        exchange_request_id=exchange_request_id,
        exchange_offer_id=exchange_offer_id,
    )


def _depth(value, level=1):
    if level > 5:
        return level

    if isinstance(value, dict):
        return max([level, *(_depth(item, level + 1) for item in value.values())], default=level)

    if isinstance(value, list):
        return max([level, *(_depth(item, level + 1) for item in value)], default=level)

    return level
