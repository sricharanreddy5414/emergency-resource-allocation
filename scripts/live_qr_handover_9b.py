"""Phase 9B non-pilot live QR handover smoke.

Creates one disposable individual resource through the resources API,
completes one QR handover, and retries the same token. Does not print
the raw QR token. Does not touch pilot ORG-D13B30D99127.
"""

from __future__ import annotations

import hashlib
import json
import time
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
PROVIDER_TYPE = "RT-7F8A42456594"


def fail(message):
    raise SystemExit(message)


def note(step, detail=None):
    print(json.dumps({"step": step, "detail": detail}, default=str))


def invoke(function, event, out_name):
    out = ROOT / "dist" / out_name
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
    if result.get("FunctionError"):
        fail(f"invoke error {function}: {body.get('errorType') or body.get('errorMessage')}")
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
    parts = path.strip("/").split("/")
    if len(parts) >= 3 and parts[2].startswith("EXREQ-"):
        event["pathParameters"]["exchange_request_id"] = parts[2]
    if len(parts) >= 5 and parts[4].startswith("EXOFF-"):
        event["pathParameters"]["offer_id"] = parts[4]
    return event


def call(function, method, path, sub, org, body=None):
    raw = invoke(function, api_event(method, path, sub, org, body), "invoke-qr-9b.json")
    status = raw.get("statusCode")
    try:
        payload = json.loads(raw.get("body") or "{}")
    except json.JSONDecodeError:
        payload = {"raw": "unparsed"}
    return status, payload


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


def assert_not_pilot(org):
    if org == PILOT:
        fail("pilot organization is forbidden")


def main():
    assert_not_pilot(REQUESTER_ORG)
    assert_not_pilot(PROVIDER_ORG)
    resource_id = "QR9B" + uuid.uuid4().hex[:8].upper()
    created = call(
        "get-resources",
        "POST",
        "/allocate/resources",
        PROVIDER_SUB,
        PROVIDER_ORG,
        {
            "resource_id": resource_id,
            "resource_type_id": PROVIDER_TYPE,
            "location_id": PROVIDER_LOC,
            "name": "QR 9B handover",
            "tracking_mode": "INDIVIDUAL",
            "Available": True,
            "visibility": "PRIVATE",
        },
    )
    note("resource_created", {"status": created[0], "resource_id": resource_id})
    if created[0] not in {200, 201}:
        fail(f"resource create failed {created[0]}")

    opened = call(
        "erap-exchange",
        "POST",
        "/exchange/requests",
        REQUESTER_SUB,
        REQUESTER_ORG,
        {
            "destination_location_id": REQUESTER_LOC,
            "resource_type_id": REQUESTER_TYPE,
            "tracking_mode": "INDIVIDUAL",
            "quantity_requested": 1,
            "idempotency_key": "qr9b-" + resource_id,
        },
    )
    if opened[0] != 201:
        fail(f"request create failed {opened[0]} {opened[1].get('message')}")
    request_id = opened[1]["request"]["exchange_request_id"]
    note("request", request_id)

    offered = call(
        "erap-exchange",
        "POST",
        f"/exchange/requests/{request_id}/offers",
        PROVIDER_SUB,
        PROVIDER_ORG,
        {
            "resource_id": resource_id,
            "provider_location_id": PROVIDER_LOC,
            "quantity_offered": 1,
            "idempotency_key": "qr9b-off-" + resource_id,
        },
    )
    if offered[0] != 201:
        fail(f"offer failed {offered[0]} {offered[1].get('message')}")
    offer_id = offered[1]["offer"]["offer_id"]

    accepted = call(
        "erap-exchange",
        "POST",
        f"/exchange/requests/{request_id}/offers/{offer_id}/accept",
        REQUESTER_SUB,
        REQUESTER_ORG,
        {},
    )
    if accepted[0] != 200:
        fail(f"accept failed {accepted[0]} {accepted[1].get('message')}")

    started = call(
        "erap-exchange",
        "POST",
        f"/exchange/requests/{request_id}/transfer/start",
        PROVIDER_SUB,
        PROVIDER_ORG,
        {},
    )
    if started[0] != 200:
        fail(f"transfer start failed {started[0]} {started[1].get('message')}")

    issued = call(
        "erap-exchange",
        "POST",
        f"/exchange/requests/{request_id}/handover/qr",
        PROVIDER_SUB,
        PROVIDER_ORG,
        {},
    )
    if issued[0] != 200:
        fail(f"qr issue failed {issued[0]} {issued[1].get('message')}")
    payload = issued[1].get("qr_payload") or ""
    session_id = issued[1].get("session_id")
    if not payload.startswith("erap-hq.v1.") or request_id in payload or "token" in issued[1]:
        fail("qr issue response was not an opaque payload")
    note("issued", {"session_id": session_id, "expires_at": issued[1].get("expires_at")})

    rotated = call(
        "erap-exchange",
        "POST",
        f"/exchange/requests/{request_id}/handover/qr",
        PROVIDER_SUB,
        PROVIDER_ORG,
        {},
    )
    if rotated[0] != 200:
        fail(f"qr rotate failed {rotated[0]} {rotated[1].get('message')}")
    new_payload = rotated[1].get("qr_payload") or ""
    if new_payload == payload:
        fail("rotation returned the same payload")
    stale = call(
        "erap-exchange",
        "POST",
        "/exchange/handover/qr/preview",
        REQUESTER_SUB,
        REQUESTER_ORG,
        {"token": payload},
    )
    note("stale_preview", stale[0])
    if stale[0] != 404:
        fail(f"revoked qr preview expected 404 got {stale[0]}")

    provider_preview = call(
        "erap-exchange",
        "POST",
        "/exchange/handover/qr/preview",
        PROVIDER_SUB,
        PROVIDER_ORG,
        {"token": new_payload},
    )
    note("provider_preview", provider_preview[0])
    if provider_preview[0] != 404 or request_id in json.dumps(provider_preview[1]):
        fail("provider preview leaked or was accepted")

    garbage = call(
        "erap-exchange",
        "POST",
        "/exchange/handover/qr/preview",
        REQUESTER_SUB,
        REQUESTER_ORG,
        {"token": "erap-hq.v1." + "a" * 43},
    )
    if garbage[0] != 404:
        fail(f"garbage preview expected 404 got {garbage[0]}")

    preview = call(
        "erap-exchange",
        "POST",
        "/exchange/handover/qr/preview",
        REQUESTER_SUB,
        REQUESTER_ORG,
        {"token": new_payload},
    )
    if preview[0] != 200:
        fail(f"preview failed {preview[0]} {preview[1].get('message')}")
    if preview[1].get("exchange_request_id") != request_id or preview[1].get("tracking_mode") != "INDIVIDUAL":
        fail(f"preview shape unexpected keys={sorted(preview[1])}")
    if any(key in preview[1] for key in ("issued_by", "token_hash", "user_sub", "qr_payload")):
        fail("preview leaked private fields")
    note("preview", {"session_id": preview[1].get("session_id"), "tracking_mode": preview[1].get("tracking_mode")})

    confirmed = call(
        "erap-exchange",
        "POST",
        "/exchange/handover/qr/confirm",
        REQUESTER_SUB,
        REQUESTER_ORG,
        {"token": new_payload},
    )
    if confirmed[0] != 200 or confirmed[1].get("request", {}).get("status") != "COMPLETED":
        fail(f"confirm failed {confirmed[0]} {confirmed[1].get('message')}")
    if confirmed[1].get("qr_consumed") is not True:
        fail("confirm did not report qr_consumed")
    if confirmed[1].get("allocation", {}).get("status") != "RELEASED":
        fail("allocation was not released")
    note("confirmed", {"session_id": confirmed[1].get("session_id"), "status": "COMPLETED"})

    replay = call(
        "erap-exchange",
        "POST",
        "/exchange/handover/qr/confirm",
        REQUESTER_SUB,
        REQUESTER_ORG,
        {"token": new_payload},
    )
    if replay[0] != 200 or replay[1].get("message") != "Handover already completed":
        fail(f"replay expected already completed got {replay[0]} {replay[1].get('message')}")
    if replay[1].get("qr_consumed") is not False:
        fail("replay consumed the qr again")

    token = new_payload.split("erap-hq.v1.", 1)[1]
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    session = ddb_get("ResourceExchanges", {"pk": {"S": "HQRS#" + digest}, "sk": {"S": "SESSION"}})
    if session.get("status", {}).get("S") != "CONSUMED":
        fail(f"session status {session.get('status')}")
    if "qr_payload" in session or any(token in json.dumps(value) for value in session.values()):
        fail("raw token was stored")
    meta = ddb_get(
        "ResourceExchanges",
        {"pk": {"S": "EXREQ#" + request_id}, "sk": {"S": "META"}},
    )
    if meta.get("status", {}).get("S") != "COMPLETED":
        fail("meta not completed")
    if "qr_ttl_epoch" in meta:
        fail("META has qr ttl")
    resource = ddb_get("Resources", {"resource_id": {"S": resource_id}})
    if resource.get("organization_id", {}).get("S") != REQUESTER_ORG:
        fail("ownership did not transfer")
    if resource.get("visibility", {}).get("S") != "PRIVATE":
        fail("destination visibility was not PRIVATE")
    if resource.get("location_id", {}).get("S") != REQUESTER_LOC:
        fail("location did not transfer")

    notes = call("erap-notifications", "GET", "/notifications", PROVIDER_SUB, PROVIDER_ORG)
    found = False
    if notes[0] == 200:
        for item in notes[1].get("notifications") or []:
            href = item.get("href") or {}
            if href.get("exchange_request_id") == request_id and item.get("event_code") == "exchange.handover.completed":
                found = True
    note("provider_notification", found)
    if not found:
        fail("handover.completed notification was not listed for the provider")

    start_ms = int((time.time() - 600) * 1000)
    logs = aws(
        [
            "logs",
            "filter-log-events",
            "--log-group-name",
            "/aws/lambda/erap-exchange",
            "--start-time",
            str(start_ms),
            "--filter-pattern",
            "erap-hq",
        ],
        region=REGION,
    )
    events = (logs or {}).get("events") or []
    note("token_log_matches", len(events))
    if events:
        fail("raw qr payload appeared in exchange logs")

    note(
        "done",
        {
            "pilot_touched": False,
            "request_id": request_id,
            "resource_id": resource_id,
            "session_id": rotated[1].get("session_id"),
            "resource_now_owned_by": REQUESTER_ORG,
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
