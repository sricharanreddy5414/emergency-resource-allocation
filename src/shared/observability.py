"""Request correlation and safe error bodies. Tokens are never logged."""

import json
import time
import uuid
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


def begin_request(event):
    event = event or {}
    context = event.get("requestContext") or {}
    request_id = str(context.get("requestId") or "").strip() or uuid.uuid4().hex[:16]
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
    print(
        json.dumps(
            {
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "request_id": meta.get("request_id", ""),
                "route": route,
                "operation": operation or route,
                "result": status_code,
                "duration_ms": duration_ms,
                "organization_id": organization_id or "",
                "entity_type": entity_type,
                "entity_id": entity_id,
                "error_code": error_code,
            },
            default=str,
        )
    )


def _depth(value, level=1):
    if level > 5:
        return level

    if isinstance(value, dict):
        return max([level, *(_depth(item, level + 1) for item in value.values())], default=level)

    if isinstance(value, list):
        return max([level, *(_depth(item, level + 1) for item in value)], default=level)

    return level
