"""Phase 9B non-pilot live QR handover smoke.

Creates one disposable individual resource through the resources API, completes
one QR handover, and retries the same code. Does not print the raw token.
Does not touch pilot ORG-D13B30D99127. Does not scan DynamoDB.
"""

from __future__ import annotations

import hashlib
import json
import uuid

from aws_cli import aws
from lambda_manifest import ALLOWED_ORIGIN, REGION, ROOT

PILOT = "ORG-D13B30D99127"
REQUESTER_ORG = "ORG-A66B0A1E4F96"
PROVIDER_ORG = "ORG-17D0E2939B2D"
REQUESTER_SUB = "209c191c-20b1-7004-5c66-52d5e481bab3"
PROVIDER_SUB = "107ce98c-b0f1-70de-6a8e-b1a13db54dbe"
REQUESTER_LOC = "LOC-277C27A72D69"
PROVIDER_LOC = "LOC-5104F30100B6"
REQUESTER_TYPE = "RT-2CFD71822FEC"
KNOWN_PROVIDER_RESOURCE = "EXCHANGE-5G1-C"


def fail(message):
    raise SystemExit(message)


def note(step, detail=None):
    print(json.dumps({"step": step, "detail": detail}, default=str))


def invoke(function, event):
    out = ROOT / "dist" / "invoke-qr-9b.json"
    out.parent.mkdir(exist_ok=True)
    result = aws(
        [
            "lambda",
            "invoke",
            "--function-name",
            function,
            "--qualifier",
            "live",
            "--payload",
            json.dumps(event),
            "--cli-binary-format",
            "raw-in-base64-out",
            str(out),
        ],
        region=REGION,
    )
    body = json.loads(out.read_text(encoding="utf-8"))
    stored = json.loads(json.dumps(body))
    inner_text = stored.get("body") if isinstance(stored, dict) else None
    if isinstance(inner_text, str):
        try:
            inner = json.loads(inner_text or "{}")
        except json.JSONDecodeError:
            inner = None
        if isinstance(inner, dict):
            inner.pop("qr_payload", None)
            inner.pop("token", None)
            stored["body"] = json.dumps(inner)
            out.write_text(json.dumps(stored), encoding="utf-8")
    if result.get("FunctionError"):
        fail(f"invoke error {function}")
    return body


def api_event(method, path, sub, org, body=None):
    event = {
        "httpMethod": method,
        "path": path,
        "resource": path,
        "headers": {"Content-Type": "application/json", "Origin": ALLOWED_ORIGIN},
        "queryStringParameters": {"organization_id": org},
        "pathParameters": {},
        "requestContext": {"authorizer": {"claims": {"sub": sub, "token_use": "id"}}},
        "body": None,
        "isBase64Encoded": False,
    }
    if method in {"POST", "PUT", "PATCH"}:
        payload = dict(body or {})
        payload.setdefault("organization_id", org)
        event["body"] = json.dumps(payload)
    return event


def call(function, method, path, sub, org, body=None):
    raw = invoke(function, api_event(method, path, sub, org, body))
    status = raw.get("statusCode")
    try:
        payload = json.loads(raw.get("body") or "{}")
    except json.JSONDecodeError:
        payload = {}
    if isinstance(payload, dict):
        payload.pop("qr_payload", None)
        payload.pop("token", None)
    return status, payload, raw


def exchange(method, path, sub, org, body=None):
    status, payload, raw = call("erap-exchange", method, path, sub, org, body)
    return status, payload, raw


def ddb_get(table, key):
    result = aws(
        [
            "dynamodb",
            "get-item",
            "--table-name",
            table,
            "--key",
            json.dumps(key),
        ],
        region=REGION,
    )
    return (result or {}).get("Item") or {}


def attr(item, name):
    value = item.get(name) or {}
    if "S" in value:
        return value["S"]
    if "BOOL" in value:
        return value["BOOL"]
    if "N" in value:
        return value["N"]
    return None


def main():
    if PILOT in {REQUESTER_ORG, PROVIDER_ORG}:
        fail("pilot configured")

    known = ddb_get("Resources", {"resource_id": {"S": KNOWN_PROVIDER_RESOURCE}})
    provider_type = attr(known, "resource_type_id")
    if attr(known, "organization_id") == PILOT or not provider_type:
        fail("could not read non-pilot provider type")

    resource_id = "QR9B-" + uuid.uuid4().hex[:10].upper()
    status, payload, _raw = call(
        "get-resources",
        "POST",
        "/allocate/resources",
        PROVIDER_SUB,
        PROVIDER_ORG,
        {
            "resource_id": resource_id,
            "resource_type_id": provider_type,
            "location_id": PROVIDER_LOC,
            "name": "QR 9B handover",
            "tracking_mode": "INDIVIDUAL",
            "Available": True,
            "visibility": "PRIVATE",
        },
    )
    if status != 201:
        fail(f"create resource {status} {payload}")
    note("resource_created", resource_id)

    status, body, _raw = exchange(
        "POST",
        "/exchange/requests",
        REQUESTER_SUB,
        REQUESTER_ORG,
        {
            "destination_location_id": REQUESTER_LOC,
            "resource_type_id": REQUESTER_TYPE,
            "tracking_mode": "INDIVIDUAL",
            "quantity_requested": 1,
            "visibility": "NETWORK",
            "idempotency_key": "9b-req-" + uuid.uuid4().hex[:8],
        },
    )
    if status != 201:
        fail(f"create request {status} {body}")
    request_id = body["request"]["exchange_request_id"]
    note("request_created", request_id)

    def cancel():
        exchange("POST", f"/exchange/requests/{request_id}/cancel", REQUESTER_SUB, REQUESTER_ORG, {})

    status, body, _raw = exchange(
        "POST",
        f"/exchange/requests/{request_id}/offers",
        PROVIDER_SUB,
        PROVIDER_ORG,
        {
            "resource_id": resource_id,
            "provider_location_id": PROVIDER_LOC,
            "quantity_offered": 1,
            "idempotency_key": "9b-off-" + uuid.uuid4().hex[:8],
        },
    )
    if status != 201:
        cancel()
        fail(f"create offer {status} {body}")
    offer_id = body["offer"]["offer_id"]

    status, body, _raw = exchange(
        "POST",
        f"/exchange/requests/{request_id}/offers/{offer_id}/accept",
        REQUESTER_SUB,
        REQUESTER_ORG,
        {},
    )
    if status != 200:
        cancel()
        fail(f"accept {status} {body}")

    status, body, _raw = exchange(
        "POST",
        f"/exchange/requests/{request_id}/transfer/start",
        PROVIDER_SUB,
        PROVIDER_ORG,
        {},
    )
    if status != 200:
        cancel()
        fail(f"transfer start {status} {body}")
    note("transfer_pending", request_id)

    status, issued, raw = exchange(
        "POST",
        f"/exchange/requests/{request_id}/handover/qr",
        PROVIDER_SUB,
        PROVIDER_ORG,
        {},
    )
    full = json.loads(raw.get("body") or "{}")
    payload = str(full.get("qr_payload") or "")
    if status != 200 or not payload.startswith("erap-hq.v1.") or request_id in payload:
        cancel()
        fail(f"issue failed {status}")
    if "token" in full:
        cancel()
        fail("raw token returned separately")
    note("qr_issued", {"session_id": issued.get("session_id"), "expires_at": full.get("expires_at")})

    status, preview, _raw = exchange(
        "POST",
        "/exchange/handover/qr/preview",
        REQUESTER_SUB,
        REQUESTER_ORG,
        {"token": payload},
    )
    if status != 200 or preview.get("exchange_request_id") != request_id:
        cancel()
        fail(f"preview failed {status} {preview}")
    if preview.get("tracking_mode") != "INDIVIDUAL" or preview.get("quantity") not in {None, 0}:
        cancel()
        fail(f"preview shape {preview}")
    note("preview_ok", preview.get("session_id"))

    status, denied, _raw = exchange(
        "POST",
        "/exchange/handover/qr/preview",
        PROVIDER_SUB,
        PROVIDER_ORG,
        {"token": payload},
    )
    if status != 404 or request_id in json.dumps(denied):
        cancel()
        fail(f"provider preview should be opaque 404, got {status}")
    note("provider_preview_denied", status)

    status, _again, raw2 = exchange(
        "POST",
        f"/exchange/requests/{request_id}/handover/qr",
        PROVIDER_SUB,
        PROVIDER_ORG,
        {},
    )
    rotated = json.loads(raw2.get("body") or "{}")
    rotated_payload = str(rotated.get("qr_payload") or "")
    if status != 200 or rotated_payload == payload:
        cancel()
        fail("rotation failed")
    status, stale, _raw = exchange(
        "POST",
        "/exchange/handover/qr/preview",
        REQUESTER_SUB,
        REQUESTER_ORG,
        {"token": payload},
    )
    if status != 404:
        cancel()
        fail(f"revoked token still previewed {status}")
    note("rotated", rotated.get("session_id"))

    status, garbage, _raw = exchange(
        "POST",
        "/exchange/handover/qr/preview",
        REQUESTER_SUB,
        REQUESTER_ORG,
        {"token": "erap-hq.v1." + "a" * 43},
    )
    if status != 404:
        cancel()
        fail(f"garbage token {status} {garbage}")

    status, done, _raw = exchange(
        "POST",
        "/exchange/handover/qr/confirm",
        REQUESTER_SUB,
        REQUESTER_ORG,
        {"token": rotated_payload},
    )
    if status != 200 or done.get("qr_consumed") is not True:
        fail(f"confirm failed {status} {done}")
    if done.get("request", {}).get("status") != "COMPLETED":
        fail(f"exchange not completed {done.get('request', {}).get('status')}")
    if done.get("allocation", {}).get("status") != "RELEASED":
        fail("allocation not released")
    note("confirmed", done.get("session_id"))

    resource = ddb_get("Resources", {"resource_id": {"S": resource_id}})
    if attr(resource, "organization_id") != REQUESTER_ORG:
        fail("ownership did not move to requester")
    if attr(resource, "location_id") != REQUESTER_LOC:
        fail("location did not move")
    if attr(resource, "visibility") != "PRIVATE":
        fail("destination visibility is not PRIVATE")
    note("ownership", {"resource_id": resource_id, "organization_id": REQUESTER_ORG})

    secret = rotated_payload[len("erap-hq.v1.") :]
    digest = hashlib.sha256(secret.encode("utf-8")).hexdigest()
    session = ddb_get(
        "ResourceExchanges",
        {"pk": {"S": "HQRS#" + digest}, "sk": {"S": "SESSION"}},
    )
    if attr(session, "status") != "CONSUMED":
        fail("session was not consumed")
    if secret in json.dumps(session):
        fail("raw token stored on session")
    meta = ddb_get(
        "ResourceExchanges",
        {"pk": {"S": "EXREQ#" + request_id}, "sk": {"S": "META"}},
    )
    if "qr_ttl_epoch" in meta:
        fail("META carries QR TTL")
    note("session_consumed", attr(session, "session_id"))

    status, replay, _raw = exchange(
        "POST",
        "/exchange/handover/qr/confirm",
        REQUESTER_SUB,
        REQUESTER_ORG,
        {"token": rotated_payload},
    )
    if status != 200 or replay.get("message") != "Handover already completed":
        fail(f"replay {status} {replay}")
    if replay.get("qr_consumed") is not False:
        fail("replay reported a second consume")
    resource_again = ddb_get("Resources", {"resource_id": {"S": resource_id}})
    if attr(resource_again, "organization_id") != REQUESTER_ORG:
        fail("replay moved ownership again")
    note("replay_idempotent", request_id)

    listed = invoke(
        "erap-notifications",
        api_event("GET", "/notifications", PROVIDER_SUB, PROVIDER_ORG),
    )
    notes = json.loads(listed.get("body") or "{}").get("notifications") or []
    found = [
        item
        for item in notes
        if item.get("event_code") == "exchange.handover.completed"
        and (item.get("href") or {}).get("exchange_request_id") == request_id
    ]
    if not found:
        fail("handover.completed notification missing")
    if any(str(item.get("event_code", "")).startswith("qr.") for item in notes if request_id in json.dumps(item)):
        fail("QR-specific notification was created")
    note("notification", "handover.completed")
    note(
        "done",
        {
            "pilot_touched": False,
            "request_id": request_id,
            "resource_id": resource_id,
            "resource_left_with": REQUESTER_ORG,
            "quantity_live": "not performed",
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
