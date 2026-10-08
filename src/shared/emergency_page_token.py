"""Signed continuation tokens for emergency request and allocation lists.

The token is stateless. HMAC-SHA256 covers the payload. The signing key
comes from Secrets Manager, never from source. Authorization stays in
authorize(); this token only resumes a query the server already chose.

Request and allocation lists use different endpoint names and reject each
other's tokens. The cursor is the index key needed to resume the query.
"""

import base64
import hashlib
import hmac
import json
import os
from datetime import datetime, timezone

from access import AccessError


REQUEST_ENDPOINT = "request.list"
ALLOCATION_ENDPOINT = "allocation.list"
ENDPOINTS = {REQUEST_ENDPOINT, ALLOCATION_ENDPOINT}
VERSION = 1
TTL_SECONDS = 900
CLOCK_SKEW_SECONDS = 60
SECRET_ID = "erap/emergency/page-token"
PAGE_SIZE_MAX = 100
CURSOR_KEYS = {
    REQUEST_ENDPOINT: ("organization_id", "location_id", "request_id"),
    ALLOCATION_ENDPOINT: ("organization_id", "location_id", "allocation_id"),
}
_PAYLOAD_KEYS = {"v", "e", "o", "loc", "l", "c", "iat", "exp"}
_cached_secret = None


def _b64encode(raw):
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(text):
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode((text + pad).encode("ascii"))


def _reject():
    raise AccessError(400, "Invalid page token")


def _unavailable():
    raise AccessError(500, "Unable to process request")


def _exact_int(value):
    if isinstance(value, bool) or not isinstance(value, int):
        _reject()
    return value


def _signature_matches(key, body, supplied):
    expected = hmac.new(key.encode("utf-8"), body, hashlib.sha256).digest()
    if len(expected) != len(supplied):
        return False
    return hmac.compare_digest(expected, supplied)


def parse_secret_material(raw):
    """Return the current key and any previous keys kept for rotation."""
    text = str(raw or "").strip()
    if text.startswith("{"):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            _unavailable()
        current = parsed.get("current") if isinstance(parsed, dict) else None
        previous = parsed.get("previous") if isinstance(parsed, dict) else None
        if not isinstance(current, str) or len(current) < 32:
            _unavailable()
        older = [previous] if isinstance(previous, str) and len(previous) >= 32 else []
        return current, older
    if len(text) < 32:
        _unavailable()
    return text, []


def load_emergency_page_secret(client=None, secret_id=None):
    """Load the current and previous keys. The values are not logged."""
    global _cached_secret
    supplied_client = client is not None
    if not supplied_client and _cached_secret is not None:
        return _cached_secret
    secret_id = secret_id or os.environ.get("ERAP_EMERGENCY_PAGE_TOKEN_SECRET_ID") or SECRET_ID
    if client is None:
        import boto3

        client = boto3.client(
            "secretsmanager",
            region_name=os.environ.get("AWS_REGION", "eu-north-1"),
        )
    try:
        response = client.get_secret_value(SecretId=secret_id)
        material = parse_secret_material(response.get("SecretString") or "")
    except AccessError:
        raise
    except Exception:
        _unavailable()
    if not supplied_client:
        _cached_secret = material
    return material


def reset_secret_cache():
    global _cached_secret
    _cached_secret = None


def _bound_text(value):
    if not isinstance(value, str) or value != value.strip() or len(value) > 200:
        _reject()
    return value


def _cursor(value, organization_id, endpoint):
    keys = CURSOR_KEYS.get(endpoint)
    if keys is None or not isinstance(value, dict) or set(value) != set(keys):
        _reject()
    cleaned = {}
    for key in keys:
        item = value.get(key)
        if not isinstance(item, str) or not item or len(item) > 200:
            _reject()
        cleaned[key] = item
    if cleaned["organization_id"] != str(organization_id):
        _reject()
    return cleaned


def sign_emergency_page_token(
    cursor,
    *,
    endpoint,
    organization_id,
    location_id,
    limit,
    secret,
    now=None,
):
    if not cursor:
        return None
    if endpoint not in ENDPOINTS:
        _reject()
    moment = now or datetime.now(timezone.utc)
    issued = int(moment.timestamp())
    payload = {
        "v": VERSION,
        "e": endpoint,
        "o": _bound_text(str(organization_id)),
        "loc": _bound_text(str(location_id)),
        "l": _exact_int(int(limit)),
        "c": _cursor(cursor, organization_id, endpoint),
        "iat": issued,
        "exp": issued + TTL_SECONDS,
    }
    if payload["o"] == "" or not 1 <= payload["l"] <= PAGE_SIZE_MAX:
        _reject()
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    signature = hmac.new(str(secret).encode("utf-8"), body, hashlib.sha256).digest()
    return _b64encode(body) + "." + _b64encode(signature)


def read_emergency_page_token(
    token,
    *,
    endpoint,
    organization_id,
    location_id,
    limit,
    secrets,
    now=None,
):
    """Return the DynamoDB cursor. A missing token is the first page.

    An empty supplied token is rejected. Signature is checked before any
    payload field is trusted.
    """
    if token is None:
        return None
    if endpoint not in ENDPOINTS:
        _reject()
    text = str(token).strip()
    if text == "" or len(text) > 2000 or text.count(".") != 1:
        _reject()
    encoded_body, encoded_signature = text.split(".", 1)
    try:
        body = _b64decode(encoded_body)
        supplied = _b64decode(encoded_signature)
    except Exception:
        _reject()
    keys = [item for item in secrets if isinstance(item, str) and item]
    if not keys or not any(_signature_matches(key, body, supplied) for key in keys):
        _reject()
    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception:
        _reject()
    if not isinstance(payload, dict) or set(payload) != _PAYLOAD_KEYS:
        _reject()
    if payload.get("v") != VERSION or payload.get("e") != endpoint:
        _reject()
    if payload.get("o") != str(organization_id):
        _reject()
    if payload.get("loc") != str(location_id):
        _reject()
    if _exact_int(payload.get("l")) != int(limit):
        _reject()
    moment = now or datetime.now(timezone.utc)
    now_epoch = int(moment.timestamp())
    issued = _exact_int(payload.get("iat"))
    expires = _exact_int(payload.get("exp"))
    if issued > now_epoch + CLOCK_SKEW_SECONDS or expires <= now_epoch:
        _reject()
    if expires - issued != TTL_SECONDS:
        _reject()
    return _cursor(payload.get("c"), organization_id, endpoint)
