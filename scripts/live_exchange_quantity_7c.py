"""Phase 7C non-pilot live quantity ownership transfer smoke.

Does NOT touch pilot ORG-D13B30D99127. Cleans EXCHANGE OPEN holds.
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
PROVIDER_POOL = "EXCHANGE-7C-QTY-PROVIDER"
REQUESTER_TYPE = "RT-2CFD71822FEC"
TYPE_NAME = "Smoke Test Equipment"


def fail(msg):
    raise SystemExit(msg)


def note(step, detail=None):
    print(json.dumps({"step": step, "detail": detail}, default=str))


def invoke(event):
    out = ROOT / "dist" / "invoke-erap-exchange-7c.json"
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
    return raw.get("statusCode"), json.loads(raw.get("body") or "{}")


def ddb_get(resource_id):
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
            out[key] = int(value["N"]) if str(value["N"]).isdigit() or str(value["N"]).lstrip("-").isdigit() else value["N"]
        else:
            out[key] = list(value.values())[0]
    return out


def ensure_provider_pool():
    existing = ddb_get(PROVIDER_POOL)
    if existing.get("resource_id"):
        if existing.get("organization_id") != PROVIDER_ORG:
            fail("provider pool wrong org")
        # reset counters to known baseline for smoke
        aws(
            [
                "dynamodb",
                "update-item",
                "--table-name",
                "Resources",
                "--key",
                json.dumps({"resource_id": {"S": PROVIDER_POOL}}),
                "--update-expression",
                "SET quantity_total=:t, quantity_available=:a, quantity_reserved=:z, quantity_allocated=:z, operational_status=:op, tracking_mode=:tm",
                "--expression-attribute-values",
                json.dumps(
                    {
                        ":t": {"N": "20"},
                        ":a": {"N": "20"},
                        ":z": {"N": "0"},
                        ":op": {"S": "AVAILABLE"},
                        ":tm": {"S": "QUANTITY"},
                    }
                ),
            ],
            region=REGION,
        )
        note("provider_pool_reset", PROVIDER_POOL)
        return
    item = {
        "resource_id": {"S": PROVIDER_POOL},
        "organization_id": {"S": PROVIDER_ORG},
        "location_id": {"S": PROVIDER_LOC},
        "Location": {"S": "RM2 Test Location"},
        "name": {"S": "7C Quantity Provider Pool"},
        "Type": {"S": TYPE_NAME},
        "resource_type_id": {"S": "RT-7F8A42456594"},
        "Available": {"BOOL": False},
        "operational_status": {"S": "AVAILABLE"},
        "tracking_mode": {"S": "QUANTITY"},
        "quantity_total": {"N": "20"},
        "quantity_available": {"N": "20"},
        "quantity_reserved": {"N": "0"},
        "quantity_allocated": {"N": "0"},
        "visibility": {"S": "PRIVATE"},
        "status": {"S": "ACTIVE"},
    }
    aws(["dynamodb", "put-item", "--table-name", "Resources", "--item", json.dumps(item)], region=REGION)
    note("provider_pool_created", PROVIDER_POOL)


def flow(quantity, destination_resource_id=None, suffix=""):
    status, body = api(
        "POST",
        "/exchange/requests",
        REQUESTER_SUB,
        REQUESTER_ORG,
        {
            "destination_location_id": REQUESTER_LOC,
            "resource_type_id": REQUESTER_TYPE,
            "tracking_mode": "QUANTITY",
            "quantity_requested": quantity,
            "visibility": "NETWORK",
            "notes": "Phase 7C quantity smoke",
            "idempotency_key": "7c-req-" + uuid.uuid4().hex[:8] + suffix,
        },
    )
    if status != 201:
        fail(f"create request {status} {body}")
    rid = body["request"]["exchange_request_id"]
    status, body = api(
        "POST",
        f"/exchange/requests/{rid}/offers",
        PROVIDER_SUB,
        PROVIDER_ORG,
        {
            "resource_id": PROVIDER_POOL,
            "provider_location_id": PROVIDER_LOC,
            "quantity_offered": quantity,
            "idempotency_key": "7c-off-" + uuid.uuid4().hex[:8] + suffix,
        },
    )
    if status != 201:
        fail(f"create offer {status} {body}")
    oid = body["offer"]["offer_id"]
    status, body = api("POST", f"/exchange/requests/{rid}/offers/{oid}/accept", REQUESTER_SUB, REQUESTER_ORG, {})
    if status != 200:
        fail(f"accept {status} {body}")
    status, body = api("POST", f"/exchange/requests/{rid}/transfer/start", PROVIDER_SUB, PROVIDER_ORG, {})
    if status != 200:
        fail(f"start {status} {body}")
    confirm_body = {"quantity": quantity, "destination_location_id": REQUESTER_LOC}
    if destination_resource_id:
        confirm_body["destination_resource_id"] = destination_resource_id
    status, body = api(
        "POST",
        f"/exchange/requests/{rid}/handover/confirm",
        REQUESTER_SUB,
        REQUESTER_ORG,
        confirm_body,
    )
    if status != 200:
        fail(f"confirm {status} {body}")
    return rid, body


def main():
    if REQUESTER_ORG == PILOT or PROVIDER_ORG == PILOT:
        fail("pilot configured")
    ensure_provider_pool()

    # 1) create destination pool via omit destination_resource_id
    rid, body = flow(5, suffix="create")
    note("create_path", body.get("transfer"))
    dest = body["transfer"]["destination_resource_id"]
    dest_item = ddb_get(dest)
    if dest_item.get("visibility") != "PRIVATE":
        fail(f"dest not private: {dest_item}")
    if dest_item.get("quantity_total") != 5 or dest_item.get("organization_id") != REQUESTER_ORG:
        fail(f"dest counters wrong: {dest_item}")

    # 2) merge into that pool
    before = ddb_get(PROVIDER_POOL)
    rid2, body2 = flow(3, destination_resource_id=dest, suffix="merge")
    note("merge_path", body2.get("transfer"))
    dest_item = ddb_get(dest)
    if dest_item.get("quantity_total") != 8:
        fail(f"merge total expected 8 got {dest_item}")
    after = ddb_get(PROVIDER_POOL)
    if after.get("quantity_total") != before.get("quantity_total") - 3:
        fail(f"provider total not reduced: {before} -> {after}")
    if after.get("quantity_allocated") != 0:
        fail(f"provider still allocated: {after}")

    # 3) idempotent retry
    status, again = api(
        "POST",
        f"/exchange/requests/{rid2}/handover/confirm",
        REQUESTER_SUB,
        REQUESTER_ORG,
        {"quantity": 3, "destination_location_id": REQUESTER_LOC, "destination_resource_id": dest},
    )
    note("retry", {"status": status, "message": again.get("message")})
    if status != 200:
        fail(f"retry failed {status} {again}")
    if ddb_get(dest).get("quantity_total") != 8:
        fail("retry duplicated quantity")

    # 4) insufficient accept conflict
    status, body = api(
        "POST",
        "/exchange/requests",
        REQUESTER_SUB,
        REQUESTER_ORG,
        {
            "destination_location_id": REQUESTER_LOC,
            "resource_type_id": REQUESTER_TYPE,
            "tracking_mode": "QUANTITY",
            "quantity_requested": 1000,
            "visibility": "NETWORK",
            "idempotency_key": "7c-big-" + uuid.uuid4().hex[:8],
        },
    )
    rid3 = body["request"]["exchange_request_id"]
    status, body = api(
        "POST",
        f"/exchange/requests/{rid3}/offers",
        PROVIDER_SUB,
        PROVIDER_ORG,
        {
            "resource_id": PROVIDER_POOL,
            "provider_location_id": PROVIDER_LOC,
            "quantity_offered": 1000,
            "idempotency_key": "7c-bigoff-" + uuid.uuid4().hex[:8],
        },
    )
    # offer may fail eligibility or accept fails
    if status == 201:
        oid3 = body["offer"]["offer_id"]
        status, body = api(
            "POST",
            f"/exchange/requests/{rid3}/offers/{oid3}/accept",
            REQUESTER_SUB,
            REQUESTER_ORG,
            {},
        )
        note("insufficient_accept", {"status": status, "message": body.get("message")})
        if status not in {409, 400}:
            fail(f"expected insufficient conflict got {status}")
        api("POST", f"/exchange/requests/{rid3}/cancel", REQUESTER_SUB, REQUESTER_ORG, {})
    else:
        note("insufficient_offer", {"status": status, "message": body.get("message")})
        api("POST", f"/exchange/requests/{rid3}/cancel", REQUESTER_SUB, REQUESTER_ORG, {})

    final = ddb_get(PROVIDER_POOL)
    if final.get("quantity_allocated", 0) != 0:
        fail(f"leftover allocation: {final}")
    if str(final.get("operational_status", "")).upper() != "AVAILABLE":
        fail(f"provider not available: {final}")
    note("done", {"provider": final, "destination": ddb_get(dest), "pilot_touched": False})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
