"""Phase 8B non-pilot live notification smoke.

Does NOT touch pilot ORG-D13B30D99127. Cancels leftover OPEN requests.
"""

from __future__ import annotations

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
PROVIDER_RESOURCE = "EXCHANGE-5G1-C"
REQUESTER_TYPE = "RT-2CFD71822FEC"


def fail(msg):
    raise SystemExit(msg)


def note(step, detail=None):
    print(json.dumps({"step": step, "detail": detail}, default=str))


def invoke(function, event, out_name):
    out = ROOT / "dist" / out_name
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
        fail(f"invoke error {function}: {body}")
    return body


def exchange_api(method, path, sub, org, body=None):
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
    payload = dict(body or {})
    payload.setdefault("organization_id", org)
    if method in {"POST", "PUT", "PATCH"}:
        event["body"] = json.dumps(payload)
    parts = path.strip("/").split("/")
    if len(parts) >= 3 and parts[2].startswith("EXREQ-"):
        event["pathParameters"]["exchange_request_id"] = parts[2]
    if len(parts) >= 5 and parts[4].startswith("EXOFF-"):
        event["pathParameters"]["offer_id"] = parts[4]
    raw = invoke("erap-exchange", event, "invoke-exchange-8b.json")
    return raw.get("statusCode"), json.loads(raw.get("body") or "{}")


def notifications_api(method, path, sub, org, body=None):
    event = {
        "httpMethod": method,
        "path": path,
        "resource": path,
        "headers": {"Content-Type": "application/json", "Origin": ALLOWED_ORIGIN},
        "queryStringParameters": {"organization_id": org},
        "pathParameters": {},
        "requestContext": {"authorizer": {"claims": {"sub": sub, "token_use": "id"}}},
        "body": json.dumps({"organization_id": org, **(body or {})}) if method == "POST" else None,
        "isBase64Encoded": False,
    }
    if "/read" in path and method == "POST" and "{notification_id}" not in path:
        # path already includes id
        pass
    raw = invoke("erap-notifications", event, "invoke-notifications-8b.json")
    return raw.get("statusCode"), json.loads(raw.get("body") or "{}")


def ddb_query_inbox(org, sub):
    result = aws(
        [
            "dynamodb",
            "query",
            "--table-name",
            "Notifications",
            "--key-condition-expression",
            "pk = :pk AND begins_with(sk, :sk)",
            "--expression-attribute-values",
            json.dumps({":pk": {"S": f"INBOX#{org}#{sub}"}, ":sk": {"S": "AT#"}}),
        ],
        region=REGION,
    ) or {}
    return result.get("Items") or []


def main():
    if REQUESTER_ORG == PILOT or PROVIDER_ORG == PILOT:
        fail("pilot configured")

    # Create request as requester
    status, body = exchange_api(
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
            "notes": "Phase 8B notification smoke",
            "idempotency_key": "8b-req-" + uuid.uuid4().hex[:8],
        },
    )
    if status != 201:
        fail(f"create request {status} {body}")
    rid = body["request"]["exchange_request_id"]
    note("request_created", rid)

    before = notifications_api("GET", "/notifications/unread-count", REQUESTER_SUB, REQUESTER_ORG)
    note("unread_before", before)

    status, body = exchange_api(
        "POST",
        f"/exchange/requests/{rid}/offers",
        PROVIDER_SUB,
        PROVIDER_ORG,
        {
            "resource_id": PROVIDER_RESOURCE,
            "provider_location_id": PROVIDER_LOC,
            "quantity_offered": 1,
            "idempotency_key": "8b-off-" + uuid.uuid4().hex[:8],
        },
    )
    if status != 201:
        exchange_api("POST", f"/exchange/requests/{rid}/cancel", REQUESTER_SUB, REQUESTER_ORG, {})
        fail(f"create offer {status} {body}")
    oid = body["offer"]["offer_id"]
    note("offer_created", oid)

    # Idempotent offer path not re-run; check inbox for requester recipients excluding provider actor
    inbox = ddb_query_inbox(REQUESTER_ORG, REQUESTER_SUB)
    # REQUESTER_SUB may be OWNER - if they are actor? Actor is provider - requester should get inbox if role eligible
    after = notifications_api("GET", "/notifications", REQUESTER_SUB, REQUESTER_ORG)
    note("list_after_offer", {"status": after[0], "count": len((after[1] or {}).get("notifications") or [])})
    if after[0] != 200:
        exchange_api("POST", f"/exchange/requests/{rid}/cancel", REQUESTER_SUB, REQUESTER_ORG, {})
        fail(f"list notifications failed {after}")

    items = (after[1] or {}).get("notifications") or []
    matching = [n for n in items if n.get("event_code") == "exchange.offer.received" and rid in (n.get("href") or {}).get("exchange_request_id", "")]
    if not matching:
        # requester actor exclusion only applies to actor; provider was actor so requester users should see it
        # If REQUESTER_SUB has MEMBER role they'd be excluded - these smoke users are OPERATE-capable historically
        note("inbox_ddb_count", len(inbox))
        exchange_api("POST", f"/exchange/requests/{rid}/cancel", REQUESTER_SUB, REQUESTER_ORG, {})
        fail("expected offer.received notification for requester")

    nid = matching[0]["notification_id"]
    count = notifications_api("GET", "/notifications/unread-count", REQUESTER_SUB, REQUESTER_ORG)
    note("unread_after", count)
    if count[0] != 200 or int((count[1] or {}).get("unread_count") or 0) < 1:
        exchange_api("POST", f"/exchange/requests/{rid}/cancel", REQUESTER_SUB, REQUESTER_ORG, {})
        fail(f"unread count expected >=1 got {count}")

    read = notifications_api("POST", f"/notifications/{nid}/read", REQUESTER_SUB, REQUESTER_ORG, {})
    note("mark_read", {"status": read[0], "body": read[1]})
    if read[0] != 200:
        exchange_api("POST", f"/exchange/requests/{rid}/cancel", REQUESTER_SUB, REQUESTER_ORG, {})
        fail(f"mark read failed {read}")

    # duplicate mark read
    read2 = notifications_api("POST", f"/notifications/{nid}/read", REQUESTER_SUB, REQUESTER_ORG, {})
    if read2[0] != 200:
        fail(f"idempotent mark read failed {read2}")

    # cancel request cleanup
    cancel = exchange_api("POST", f"/exchange/requests/{rid}/cancel", REQUESTER_SUB, REQUESTER_ORG, {})
    note("cancelled", cancel)

    # tenant isolation: provider cannot read requester notification id
    iso = notifications_api("POST", f"/notifications/{nid}/read", PROVIDER_SUB, PROVIDER_ORG, {})
    note("cross_tenant", iso)
    if iso[0] != 404:
        fail(f"expected 404 cross-tenant mark-read got {iso}")

    note("done", {"pilot_touched": False, "request_id": rid})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
