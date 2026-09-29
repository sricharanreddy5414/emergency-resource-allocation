"""Phase 7A non-pilot live lifecycle smoke.

Does NOT touch pilot ORG-D13B30D99127. Leaves no EXCHANGE OPEN holds.
"""

from __future__ import annotations

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
PROVIDER_TYPE = "RT-7F8A42456594"
RESOURCE = "EXCHANGE-5G1-C"
TYPE_NAME = "Smoke Test Equipment"

REPORT = {"pilot_touched": False, "steps": [], "failures": []}


def note(step, detail=None):
    REPORT["steps"].append({"step": step, "detail": detail})
    print(json.dumps({"step": step, "detail": detail}, default=str))


def fail(msg):
    REPORT["failures"].append(msg)
    raise SystemExit(msg)


def invoke(event):
    out = ROOT / "dist" / "invoke-erap-exchange-7a.json"
    result = aws(
        [
            "lambda",
            "invoke",
            "--function-name",
            "erap-exchange",
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
        fail(f"invoke error: {body}")
    return body


def api(method, path, sub, org, body=None):
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
    raw = invoke(event)
    status = raw.get("statusCode")
    parsed = json.loads(raw.get("body") or "{}")
    return status, parsed


def ddb_get_resource(resource_id):
    result = aws(
        [
            "dynamodb",
            "get-item",
            "--table-name",
            "Resources",
            "--key",
            json.dumps({"resource_id": {"S": resource_id}}),
        ],
        region=REGION,
    ) or {}
    item = result.get("Item") or {}
    out = {}
    for key, value in item.items():
        if "S" in value:
            out[key] = value["S"]
        elif "BOOL" in value:
            out[key] = value["BOOL"]
        elif "N" in value:
            out[key] = value["N"]
        else:
            out[key] = list(value.values())[0]
    return out


def ensure_resource():
    existing = ddb_get_resource(RESOURCE)
    if not existing.get("resource_id"):
        fail(f"missing smoke resource {RESOURCE}")
    if existing.get("organization_id") != PROVIDER_ORG:
        fail("lifecycle resource not owned by provider")
    # restore availability if leftover hold
    if str(existing.get("operational_status", "")).upper() == "ALLOCATED":
        aws(
            [
                "dynamodb",
                "update-item",
                "--table-name",
                "Resources",
                "--key",
                json.dumps({"resource_id": {"S": RESOURCE}}),
                "--update-expression",
                "SET operational_status=:a, Available=:t",
                "--expression-attribute-values",
                json.dumps({":a": {"S": "AVAILABLE"}, ":t": {"BOOL": True}}),
            ],
            region=REGION,
        )
        note("resource_restored_available", RESOURCE)
    note("resource_ready", existing.get("resource_id"))


def create_request():
    status, body = api(
        "POST",
        "/exchange/requests",
        REQUESTER_SUB,
        REQUESTER_ORG,
        {
            "destination_location_id": REQUESTER_LOC,
            "resource_type_id": "RT-2CFD71822FEC",
            "tracking_mode": "INDIVIDUAL",
            "quantity_requested": 1,
            "visibility": "NETWORK",
            "notes": "Phase 7A lifecycle smoke",
            "idempotency_key": "7a-req-" + uuid.uuid4().hex[:8],
        },
    )
    if status != 201:
        fail(f"create request failed: {status} {body}")
    return body["request"]["exchange_request_id"]


def create_offer(request_id):
    status, body = api(
        "POST",
        f"/exchange/requests/{request_id}/offers",
        PROVIDER_SUB,
        PROVIDER_ORG,
        {
            "resource_id": RESOURCE,
            "provider_location_id": PROVIDER_LOC,
            "quantity_offered": 1,
            "idempotency_key": "7a-off-" + uuid.uuid4().hex[:8],
        },
    )
    if status != 201:
        fail(f"create offer failed: {status} {body}")
    return body["offer"]["offer_id"]


def main():
    if REQUESTER_ORG == PILOT or PROVIDER_ORG == PILOT:
        fail("pilot org configured")
    ensure_resource()

    # 1) OPEN cancel
    rid = create_request()
    oid = create_offer(rid)
    status, body = api("POST", f"/exchange/requests/{rid}/cancel", REQUESTER_SUB, REQUESTER_ORG, {})
    note("open_cancel", {"status": status, "body": body})
    if status != 200 or body.get("request", {}).get("status") != "CANCELLED":
        fail("open cancel failed")
    if ddb_get_resource(RESOURCE).get("Available") not in {True, "true", "True"}:
        # Available may be bool in dynamodb json
        avail = ddb_get_resource(RESOURCE)
        note("resource_after_open_cancel", avail)

    # 2) reject
    rid = create_request()
    oid = create_offer(rid)
    status, body = api(
        "POST",
        f"/exchange/requests/{rid}/offers/{oid}/reject",
        REQUESTER_SUB,
        REQUESTER_ORG,
        {},
    )
    note("reject", {"status": status, "body": body})
    if status != 200 or body.get("offer", {}).get("status") != "REJECTED":
        fail("reject failed")
    api("POST", f"/exchange/requests/{rid}/cancel", REQUESTER_SUB, REQUESTER_ORG, {})

    # 3) withdraw
    rid = create_request()
    oid = create_offer(rid)
    status, body = api(
        "POST",
        f"/exchange/requests/{rid}/offers/{oid}/withdraw",
        PROVIDER_SUB,
        PROVIDER_ORG,
        {},
    )
    note("withdraw", {"status": status, "body": body})
    if status != 200 or body.get("offer", {}).get("status") != "WITHDRAWN":
        fail("withdraw failed")
    api("POST", f"/exchange/requests/{rid}/cancel", REQUESTER_SUB, REQUESTER_ORG, {})

    # 4) accept then cancel (hold release)
    rid = create_request()
    oid = create_offer(rid)
    status, body = api(
        "POST",
        f"/exchange/requests/{rid}/offers/{oid}/accept",
        REQUESTER_SUB,
        REQUESTER_ORG,
        {},
    )
    note("accept", {"status": status})
    if status != 200:
        fail(f"accept failed: {body}")
    resource = ddb_get_resource(RESOURCE)
    if str(resource.get("operational_status", "")).upper() != "ALLOCATED":
        fail(f"resource not held after accept: {resource}")
    status, body = api("POST", f"/exchange/requests/{rid}/cancel", REQUESTER_SUB, REQUESTER_ORG, {})
    note("accepted_cancel", {"status": status, "body": body})
    if status != 200 or body.get("request", {}).get("status") != "CANCELLED":
        fail("accepted cancel failed")
    resource = ddb_get_resource(RESOURCE)
    if str(resource.get("operational_status", "")).upper() != "AVAILABLE":
        fail(f"hold not released: {resource}")
    if resource.get("organization_id") != PROVIDER_ORG:
        fail("ownership changed on cancel")

    # 5) cancel orphan OPEN test requests via API (supported path)
    for orphan in (
        "EXREQ-4613FD1C9B5E4D8C",
        "EXREQ-407D741B563E45FD",
        "EXREQ-95A14B7D026A48E7",
    ):
        status, body = api(
            "POST",
            f"/exchange/requests/{orphan}/cancel",
            REQUESTER_SUB,
            REQUESTER_ORG,
            {},
        )
        note("orphan_cancel", {"id": orphan, "status": status, "message": body.get("message")})
        if status not in {200, 409}:
            fail(f"orphan cancel unexpected {orphan}: {status} {body}")

    # invoke expiry worker once (idempotent)
    out = ROOT / "dist" / "invoke-erap-exchange-expiry.json"
    aws(
        [
            "lambda",
            "invoke",
            "--function-name",
            "erap-exchange-expiry",
            "--qualifier",
            "live",
            "--payload",
            "{}",
            "--cli-binary-format",
            "raw-in-base64-out",
            str(out),
        ],
        region=REGION,
    )
    note("expiry_invoke", json.loads(out.read_text(encoding="utf-8")))

    final = ddb_get_resource(RESOURCE)
    if str(final.get("operational_status", "")).upper() != "AVAILABLE":
        fail(f"final resource not available: {final}")
    note("done", {"pilot_touched": False, "resource": final.get("resource_id")})
    print(json.dumps(REPORT, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
