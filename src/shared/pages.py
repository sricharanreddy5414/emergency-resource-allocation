"""Opaque list continuation tokens."""

import base64
import json

from access import AccessError


def encode_token(key):
    if not key:
        return None

    raw = json.dumps(key, separators=(",", ":"), default=str).encode("utf-8")

    if len(raw) > 500:
        raise AccessError(400, "Invalid page token")

    return base64.urlsafe_b64encode(raw).decode("ascii")


def decode_token(token, allowed_keys):
    if token is None or token == "":
        return None

    text = str(token).strip()

    if len(text) > 800:
        raise AccessError(400, "Invalid page token")

    try:
        parsed = json.loads(base64.urlsafe_b64decode(text.encode("ascii")).decode("utf-8"))
    except Exception:
        raise AccessError(400, "Invalid page token")

    if not isinstance(parsed, dict) or set(parsed) != set(allowed_keys):
        raise AccessError(400, "Invalid page token")

    for key in allowed_keys:
        value = parsed.get(key)

        if not isinstance(value, str) or not value or len(value) > 200:
            raise AccessError(400, "Invalid page token")

    return parsed
