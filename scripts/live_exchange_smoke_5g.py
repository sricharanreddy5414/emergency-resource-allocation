"""Phase 5G non-pilot live smoke for Resource Exchange.

Uses Lambda invoke with Cognito-shaped claims for two known non-pilot OWNERS.
Does NOT touch pilot ORG-D13B30D99127. Does NOT store tokens/secrets.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from aws_cli import aws
from lambda_manifest import ALLOWED_ORIGIN, API_BASE, REGION, ROOT

PILOT = "ORG-D13B30D99127"
REQUESTER_ORG = "ORG-A66B0A1E4F96"
PROVIDER_ORG = "ORG-17D0E2939B2D"
REQUESTER_SUB = "209c191c-20b1-7004-5c66-52d5e481bab3"
PROVIDER_SUB = "107ce98c-b0f1-70de-6a8e-b1a13db54dbe"
REQUESTER_LOC = "LOC-277C27A72D69"
PROVIDER_LOC = "LOC-5104F30100B6"
PROVIDER_TYPE = "RT-7F8A42456594"
SMOKE_RESOURCE = "EXCHANGE-LIVE-SMOKE-001"
SMOKE_RESOURCE_B = "EXCHANGE-LIVE-SMOKE-002"
TYPE_NAME = "Smoke Test Equipment"

REPORT = {
    "pilot_touched": False,
    "steps": [],
    "failures": [],
}


def meta_pk(exchange_request_id):
    request_id = str(exchange_request_id or "").strip()
    if not request_id.startswith("EXREQ-"):
        request_id = "EXREQ-" + request_id
    return "EXREQ#" + request_id.replace("EXREQ#", "")


def meta_sk():
    return "META"


def offer_sk(offer_id):
    offer = str(offer_id or "").strip()
    if not offer.startswith("EXOFF-"):
        offer = "EXOFF-" + offer
    return "OFFER#" + offer


def note(step, detail=None):
    REPORT["steps"].append({"step": step, "detail": detail})
    print(json.dumps({"step": step, "detail": detail}, default=str))


def fail(message):
    REPORT["failures"].append(message)
    raise SystemExit(message)


def invoke(function, event, qualifier="live"):
    raw = json.dumps(event)
    result = aws(
        [
            "lambda",
            "invoke",
            "--function-name",
            function,
            "--qualifier",
            qualifier,
            "--payload",
            raw,
            "--cli-binary-format",
            "raw-in-base64-out",
            str(ROOT / "dist" / f"invoke-{function}.json"),
        ],
        region=REGION,
    )
    body = json.loads((ROOT / "dist" / f"invoke-{function}.json").read_text(encoding="utf-8"))
    if result.get("FunctionError"):
        fail(f"{function} error: {body}")
    return body


def api_event(method, path, user_sub, organization_id, body=None, query=None):
    event = {
        "httpMethod": method,
        "path": path,
        "resource": path,
        "headers": {
            "Content-Type": "application/json",
            "Origin": ALLOWED_ORIGIN,
        },
        "queryStringParameters": query,
        "pathParameters": {},
        "requestContext": {"authorizer": {"claims": {"sub": user_sub}}},
        "body": json.dumps(body) if body is not None else None,
        "isBase64Encoded": False,
    }
    # path params for exchange ids
    parts = path.strip("/").split("/")
    if len(parts) >= 3 and parts[0] == "exchange" and parts[1] == "requests" and parts[2].startswith("EXREQ-"):
        event["pathParameters"]["exchange_request_id"] = parts[2]
    if len(parts) >= 5 and parts[3] == "offers" and parts[4].startswith("EXOFF-"):
        event["pathParameters"]["offer_id"] = parts[4]
    if organization_id:
        if method in {"POST", "PUT", "PATCH"} and isinstance(body, dict):
            body = dict(body)
            body.setdefault("organization_id", organization_id)
            event["body"] = json.dumps(body)
        query = dict(query or {})
        query.setdefault("organization_id", organization_id)
        event["queryStringParameters"] = query
    return event


def exchange(method, path, user_sub, organization_id, body=None, query=None):
    return invoke(
        "erap-exchange",
        api_event(method, path, user_sub, organization_id, body=body, query=query),
    )


def parse(resp):
    status = resp.get("statusCode")
    body = resp.get("body") or "{}"
    try:
        payload = json.loads(body) if isinstance(body, str) else body
    except json.JSONDecodeError:
        payload = {"raw": body}
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
    if not result:
        return None
    return result.get("Item")


def ddb_query(table, index_name, key_condition, values):
    args = [
        "dynamodb",
        "query",
        "--table-name",
        table,
        "--key-condition-expression",
        key_condition,
        "--expression-attribute-values",
        json.dumps(values),
    ]
    if index_name:
        args.extend(["--index-name", index_name])
    return aws(args, region=REGION).get("Items") or []


def from_av(item):
    if not item:
        return None
    out = {}
    for key, value in item.items():
        if "S" in value:
            out[key] = value["S"]
        elif "BOOL" in value:
            out[key] = value["BOOL"]
        elif "N" in value:
            out[key] = value["N"]
        elif "NULL" in value:
            out[key] = None
    return out


def ensure_requester_type():
    # Create matching type name on requester org via catalog Lambda.
    event = api_event(
        "POST",
        "/resource-types",
        REQUESTER_SUB,
        REQUESTER_ORG,
        body={
            "organization_id": REQUESTER_ORG,
            "name": TYPE_NAME,
            "category": "Testing",
            "description": "Phase 5G exchange smoke type",
            "visibility_default": "PRIVATE",
        },
    )
    resp = invoke("erap-catalog", event)
    status, payload = parse(resp)
    if status == 201:
        type_id = payload.get("type", {}).get("resource_type_id")
        note("requester_type_created", type_id)
        return type_id
    if status == 409:
        # list and find
        list_event = api_event(
            "GET",
            "/resource-types",
            REQUESTER_SUB,
            REQUESTER_ORG,
            query={"organization_id": REQUESTER_ORG, "limit": "50"},
        )
        status2, payload2 = parse(invoke("erap-catalog", list_event))
        if status2 != 200:
            fail(f"list types failed {status2} {payload2}")
        for item in payload2.get("types") or []:
            if str(item.get("name", "")).strip().lower() == TYPE_NAME.lower():
                note("requester_type_existing", item.get("resource_type_id"))
                return item.get("resource_type_id")
        fail(f"type conflict but not found: {payload2}")
    fail(f"create type failed {status} {payload}")


def ensure_provider_resource(resource_id):
    existing = from_av(
        ddb_get("Resources", {"resource_id": {"S": resource_id}})
    )
    if existing:
        if existing.get("organization_id") == PILOT:
            fail("smoke resource unexpectedly on pilot")
        if (
            existing.get("organization_id") == PROVIDER_ORG
            and existing.get("operational_status") == "AVAILABLE"
            and existing.get("Available") is True
        ):
            note("provider_resource_existing", resource_id)
            return existing
        fail(f"resource {resource_id} exists in unsafe state: {existing}")

    event = api_event(
        "POST",
        "/allocate/resources",
        PROVIDER_SUB,
        PROVIDER_ORG,
        body={
            "organization_id": PROVIDER_ORG,
            "resource_id": resource_id,
            "resource_type_id": PROVIDER_TYPE,
            "location_id": PROVIDER_LOC,
            "name": f"Exchange Live Smoke {resource_id}",
            "tracking_mode": "INDIVIDUAL",
            "Available": True,
            "visibility": "PRIVATE",
            "condition": "GOOD",
        },
    )
    resp = invoke("get-resources", event)
    status, payload = parse(resp)
    if status != 201:
        fail(f"create resource failed {status} {payload}")
    note("provider_resource_created", resource_id)
    return payload.get("resource") or from_av(
        ddb_get("Resources", {"resource_id": {"S": resource_id}})
    )


def http_no_auth(method, path, origin=None):
    url = f"{API_BASE}{path}"
    headers = {"Content-Type": "application/json"}
    if origin:
        headers["Origin"] = origin
    req = urllib.request.Request(url, method=method, headers=headers, data=b"{}" if method == "POST" else None)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, dict(resp.headers), resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read().decode("utf-8", errors="replace")


def assert_pilot_untouched(before):
    after = from_av(ddb_get("Organizations", {"organization_id": {"S": PILOT}}))
    if after != before:
        REPORT["pilot_touched"] = True
        fail("pilot organization row changed")


def main():
    folder = ROOT / "dist"
    folder.mkdir(exist_ok=True)

    pilot_before = from_av(ddb_get("Organizations", {"organization_id": {"S": PILOT}}))
    note("pilot_snapshot", pilot_before)

    # Unauthenticated API
    status, headers, body = http_no_auth("POST", "/exchange/requests", origin=ALLOWED_ORIGIN)
    if status not in {401, 403}:
        fail(f"unauthenticated expected 401/403 got {status} {body}")
    note("unauthenticated", {"status": status, "acao": headers.get("Access-Control-Allow-Origin")})

    # OPTIONS CORS
    status, headers, body = http_no_auth("OPTIONS", "/exchange/requests", origin=ALLOWED_ORIGIN)
    note(
        "options_cors",
        {
            "status": status,
            "acao": headers.get("Access-Control-Allow-Origin"),
            "acah": headers.get("Access-Control-Allow-Headers"),
            "acam": headers.get("Access-Control-Allow-Methods"),
        },
    )

    requester_type_id = ensure_requester_type()
    ensure_provider_resource(SMOKE_RESOURCE)
    ensure_provider_resource(SMOKE_RESOURCE_B)

    # STEP 1 create NETWORK request
    create_resp = exchange(
        "POST",
        "/exchange/requests",
        REQUESTER_SUB,
        REQUESTER_ORG,
        body={
            "organization_id": REQUESTER_ORG,
            "destination_location_id": REQUESTER_LOC,
            "resource_type_id": requester_type_id,
            "tracking_mode": "INDIVIDUAL",
            "quantity_requested": 1,
            "visibility": "NETWORK",
            "notes": "Phase 5G live smoke",
            "idempotency_key": f"5g-req-{int(time.time())}",
        },
    )
    status, payload = parse(create_resp)
    if status not in {200, 201}:
        fail(f"create request failed {status} {payload}")
    request_id = (payload.get("request") or {}).get("exchange_request_id") or payload.get(
        "exchange_request_id"
    )
    if not request_id:
        fail(f"no exchange_request_id in {payload}")
    note("request_created", {"status": status, "id": request_id})

    meta = from_av(ddb_get("ResourceExchanges", {"pk": {"S": meta_pk(request_id)}, "sk": {"S": meta_sk()}}))
    if not meta or meta.get("status") != "OPEN":
        fail(f"META not OPEN: {meta}")
    if meta.get("network_list_key") != "OPEN":
        fail(f"network_list_key unexpected: {meta.get('network_list_key')}")
    resource_after_create = from_av(ddb_get("Resources", {"resource_id": {"S": SMOKE_RESOURCE}}))
    if resource_after_create.get("operational_status") != "AVAILABLE":
        fail("resource mutated on request create")
    note("step1_open_request", {"request_id": request_id, "meta_status": meta.get("status")})

    # STEP 2 provider discovers
    list_resp = exchange(
        "GET",
        "/exchange/requests",
        PROVIDER_SUB,
        PROVIDER_ORG,
        query={"organization_id": PROVIDER_ORG, "scope": "network"},
    )
    status, payload = parse(list_resp)
    if status != 200:
        fail(f"network list failed {status} {payload}")
    items = payload.get("items") or payload.get("requests") or []
    if not any(item.get("exchange_request_id") == request_id for item in items):
        time.sleep(2)
        status, payload = parse(
            exchange(
                "GET",
                "/exchange/requests",
                PROVIDER_SUB,
                PROVIDER_ORG,
                query={"organization_id": PROVIDER_ORG, "scope": "network"},
            )
        )
        items = payload.get("items") or payload.get("requests") or []
        if not any(item.get("exchange_request_id") == request_id for item in items):
            fail(f"provider cannot see network request {request_id} payload={payload}")
    note("step2_provider_discovers", True)

    # Public discovery must not show NETWORK exchange metadata
    pub = invoke(
        "erap-public-resources",
        {
            "httpMethod": "GET",
            "path": "/public/resources",
            "headers": {"Origin": ALLOWED_ORIGIN},
            "queryStringParameters": {"limit": "20"},
            "requestContext": {},
            "body": None,
        },
    )
    status, payload = parse(pub)
    blob = json.dumps(payload)
    if request_id in blob or "NETWORK" in blob and "EXREQ" in blob:
        # only fail if exchange request id leaked
        if request_id in blob:
            fail("public discovery leaked exchange request")
    note("network_not_public", {"public_status": status})

    # STEP 3 offer
    offer_resp = exchange(
        "POST",
        f"/exchange/requests/{request_id}/offers",
        PROVIDER_SUB,
        PROVIDER_ORG,
        body={
            "organization_id": PROVIDER_ORG,
            "resource_id": SMOKE_RESOURCE,
            "provider_location_id": PROVIDER_LOC,
            "quantity_offered": 1,
            "notes": "5G smoke offer",
            "idempotency_key": f"5g-offer-{int(time.time())}",
        },
    )
    status, payload = parse(offer_resp)
    if status not in {200, 201}:
        fail(f"create offer failed {status} {payload}")
    offer_id = (
        payload.get("offer_id")
        or payload.get("offer", {}).get("offer_id")
        or payload.get("item", {}).get("offer_id")
    )
    if not offer_id:
        fail(f"no offer_id in {payload}")
    resource = from_av(ddb_get("Resources", {"resource_id": {"S": SMOKE_RESOURCE}}))
    if resource.get("operational_status") != "AVAILABLE" or resource.get("Available") is not True:
        fail(f"resource held on offer create: {resource}")
    note("step3_offer", {"offer_id": offer_id, "resource_status": resource.get("operational_status")})

    # Tenant isolation: requester cannot start transfer; unrelated checks via provider accept denial later
    bad = exchange(
        "POST",
        f"/exchange/requests/{request_id}/offers/{offer_id}/accept",
        PROVIDER_SUB,
        PROVIDER_ORG,
        body={"organization_id": PROVIDER_ORG},
    )
    status, payload = parse(bad)
    if status not in {403, 404}:
        fail(f"provider accept should be denied got {status} {payload}")
    note("provider_cannot_accept", status)

    # STEP 4 accept
    accept_resp = exchange(
        "POST",
        f"/exchange/requests/{request_id}/offers/{offer_id}/accept",
        REQUESTER_SUB,
        REQUESTER_ORG,
        body={"organization_id": REQUESTER_ORG},
    )
    status, payload = parse(accept_resp)
    if status not in {200, 201}:
        fail(f"accept failed {status} {payload}")
    allocation_id = (
        payload.get("allocation_id")
        or payload.get("allocation", {}).get("allocation_id")
        or payload.get("request", {}).get("allocation_id")
    )
    resource = from_av(ddb_get("Resources", {"resource_id": {"S": SMOKE_RESOURCE}}))
    if resource.get("operational_status") != "ALLOCATED" or resource.get("Available") is not False:
        fail(f"accept did not hold resource: {resource}")
    if resource.get("organization_id") != PROVIDER_ORG:
        fail("ownership changed on accept")
    note("step4_accept", {"allocation_id": allocation_id, "resource": resource.get("operational_status")})

    meta = from_av(ddb_get("ResourceExchanges", {"pk": {"S": meta_pk(request_id)}, "sk": {"S": meta_sk()}}))
    offer = from_av(
        ddb_get("ResourceExchanges", {"pk": {"S": meta_pk(request_id)}, "sk": {"S": offer_sk(offer_id)}})
    )
    if meta.get("status") != "ACCEPTED" or offer.get("status") != "ACCEPTED":
        fail(f"accept state wrong meta={meta} offer={offer}")
    allocation_id = allocation_id or meta.get("allocation_id") or offer.get("allocation_id")
    allocation = from_av(ddb_get("Allocations", {"allocation_id": {"S": allocation_id}})) if allocation_id else None
    if not allocation or allocation.get("allocation_type") != "EXCHANGE" or allocation.get("status") != "OPEN":
        fail(f"allocation wrong: {allocation}")
    note("step4_ddb", {"meta": meta.get("status"), "offer": offer.get("status"), "allocation_id": allocation_id})

    # Competing accept on second resource path: create second request/offer and dual-accept same? 
    # Spec: two competing accept attempts on same offer/request
    def accept_once():
        return parse(
            exchange(
                "POST",
                f"/exchange/requests/{request_id}/offers/{offer_id}/accept",
                REQUESTER_SUB,
                REQUESTER_ORG,
                body={"organization_id": REQUESTER_ORG},
            )
        )

    # Already accepted — second accept should be idempotent or 409
    status_b, payload_b = accept_once()
    note("accept_idempotent_or_conflict", {"status": status_b, "payload": payload_b})

    # STEP 5 transfer start
    bad_transfer = exchange(
        "POST",
        f"/exchange/requests/{request_id}/transfer/start",
        REQUESTER_SUB,
        REQUESTER_ORG,
        body={"organization_id": REQUESTER_ORG},
    )
    status, payload = parse(bad_transfer)
    if status not in {403, 404}:
        fail(f"requester transfer start should fail got {status}")
    note("requester_cannot_transfer_start", status)

    transfer_resp = exchange(
        "POST",
        f"/exchange/requests/{request_id}/transfer/start",
        PROVIDER_SUB,
        PROVIDER_ORG,
        body={"organization_id": PROVIDER_ORG},
    )
    status, payload = parse(transfer_resp)
    if status not in {200, 201}:
        fail(f"transfer start failed {status} {payload}")
    meta = from_av(ddb_get("ResourceExchanges", {"pk": {"S": meta_pk(request_id)}, "sk": {"S": meta_sk()}}))
    resource = from_av(ddb_get("Resources", {"resource_id": {"S": SMOKE_RESOURCE}}))
    if meta.get("status") != "TRANSFER_PENDING":
        fail(f"expected TRANSFER_PENDING got {meta.get('status')}")
    if resource.get("organization_id") != PROVIDER_ORG:
        fail("ownership changed on transfer start")
    note("step5_transfer_pending", meta.get("status"))

    # STEP 6 handover confirm
    bad_handover = exchange(
        "POST",
        f"/exchange/requests/{request_id}/handover/confirm",
        PROVIDER_SUB,
        PROVIDER_ORG,
        body={"organization_id": PROVIDER_ORG, "destination_location_id": REQUESTER_LOC},
    )
    status, payload = parse(bad_handover)
    if status not in {403, 404}:
        fail(f"provider handover should fail got {status} {payload}")
    note("provider_cannot_handover", status)

    # wrong destination (provider location) as requester
    bad_dest = exchange(
        "POST",
        f"/exchange/requests/{request_id}/handover/confirm",
        REQUESTER_SUB,
        REQUESTER_ORG,
        body={"organization_id": REQUESTER_ORG, "destination_location_id": PROVIDER_LOC},
    )
    status, payload = parse(bad_dest)
    if status not in {400, 403, 404, 409}:
        fail(f"provider-owned destination should reject got {status} {payload}")
    note("handover_wrong_destination", status)

    def handover_once():
        return parse(
            exchange(
                "POST",
                f"/exchange/requests/{request_id}/handover/confirm",
                REQUESTER_SUB,
                REQUESTER_ORG,
                body={
                    "organization_id": REQUESTER_ORG,
                    "destination_location_id": REQUESTER_LOC,
                },
            )
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: handover_once(), range(2)))
    successes = [r for r in results if r[0] in {200, 201}]
    conflicts = [r for r in results if r[0] in {409, 200, 201}]
    if not successes:
        fail(f"handover concurrency failed: {results}")
    note("step6_handover_concurrency", [{"status": s, "body": b} for s, b in results])

    meta = from_av(ddb_get("ResourceExchanges", {"pk": {"S": meta_pk(request_id)}, "sk": {"S": meta_sk()}}))
    offer = from_av(
        ddb_get("ResourceExchanges", {"pk": {"S": meta_pk(request_id)}, "sk": {"S": offer_sk(offer_id)}})
    )
    resource = from_av(ddb_get("Resources", {"resource_id": {"S": SMOKE_RESOURCE}}))
    allocation = from_av(ddb_get("Allocations", {"allocation_id": {"S": allocation_id}}))

    if meta.get("status") != "COMPLETED":
        fail(f"expected COMPLETED got {meta.get('status')}")
    if offer.get("status") != "ACCEPTED":
        fail(f"offer should remain ACCEPTED got {offer.get('status')}")
    if resource.get("organization_id") != REQUESTER_ORG:
        fail(f"ownership not transferred: {resource}")
    if resource.get("location_id") != REQUESTER_LOC:
        fail(f"location not transferred: {resource}")
    if resource.get("operational_status") != "AVAILABLE" or resource.get("Available") is not True:
        fail(f"resource not AVAILABLE after handover: {resource}")
    if resource.get("visibility") != "PRIVATE":
        fail(f"visibility not PRIVATE: {resource}")
    if allocation.get("status") != "RELEASED":
        fail(f"allocation not RELEASED: {allocation}")
    note(
        "step6_complete",
        {
            "request": meta.get("status"),
            "offer": offer.get("status"),
            "resource_org": resource.get("organization_id"),
            "location_id": resource.get("location_id"),
            "visibility": resource.get("visibility"),
            "allocation": allocation.get("status"),
        },
    )

    # Provider can no longer mutate transferred resource via get-resources update
    mutate = invoke(
        "get-resources",
        api_event(
            "PUT",
            "/allocate/resources",
            PROVIDER_SUB,
            PROVIDER_ORG,
            body={
                "organization_id": PROVIDER_ORG,
                "resource_id": SMOKE_RESOURCE,
                "name": "should-fail",
            },
        ),
    )
    status, payload = parse(mutate)
    if status not in {403, 404}:
        fail(f"provider mutate after transfer should fail got {status} {payload}")
    note("provider_cannot_mutate_transferred", status)

    # Requester can see resource
    get_r = invoke(
        "get-resources",
        api_event(
            "GET",
            "/allocate/resources",
            REQUESTER_SUB,
            REQUESTER_ORG,
            query={"organization_id": REQUESTER_ORG, "resource_id": SMOKE_RESOURCE},
        ),
    )
    status, payload = parse(get_r)
    note("requester_sees_resource", {"status": status})

    # Concurrency accept with second resource: full mini flow
    create2 = exchange(
        "POST",
        "/exchange/requests",
        REQUESTER_SUB,
        REQUESTER_ORG,
        body={
            "organization_id": REQUESTER_ORG,
            "destination_location_id": REQUESTER_LOC,
            "resource_type_id": requester_type_id,
            "tracking_mode": "INDIVIDUAL",
            "quantity_requested": 1,
            "visibility": "NETWORK",
            "notes": "Phase 5G concurrency",
            "idempotency_key": f"5g-req2-{int(time.time())}",
        },
    )
    status, payload = parse(create2)
    if status not in {200, 201}:
        fail(f"concurrency request failed {status} {payload}")
    request2 = (
        payload.get("exchange_request_id")
        or payload.get("request", {}).get("exchange_request_id")
    )
    if not request2:
        for key in ("request", "exchange_request", "data"):
            if isinstance(payload.get(key), dict) and payload[key].get("exchange_request_id"):
                request2 = payload[key]["exchange_request_id"]
                break
    offer2_resp = exchange(
        "POST",
        f"/exchange/requests/{request2}/offers",
        PROVIDER_SUB,
        PROVIDER_ORG,
        body={
            "organization_id": PROVIDER_ORG,
            "resource_id": SMOKE_RESOURCE_B,
            "provider_location_id": PROVIDER_LOC,
            "quantity_offered": 1,
            "idempotency_key": f"5g-offer2-{int(time.time())}",
        },
    )
    status, payload = parse(offer2_resp)
    if status not in {200, 201}:
        fail(f"concurrency offer failed {status} {payload}")
    offer2 = payload.get("offer_id") or payload.get("offer", {}).get("offer_id")

    def accept2():
        return parse(
            exchange(
                "POST",
                f"/exchange/requests/{request2}/offers/{offer2}/accept",
                REQUESTER_SUB,
                REQUESTER_ORG,
                body={"organization_id": REQUESTER_ORG},
            )
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        accept_results = list(pool.map(lambda _: accept2(), range(2)))
    ok = [r for r in accept_results if r[0] in {200, 201}]
    conflict = [r for r in accept_results if r[0] == 409]
    note("accept_concurrency", [{"status": s} for s, _ in accept_results])
    if len(ok) < 1:
        fail(f"no successful accept in concurrency: {accept_results}")
    # Complete second exchange to leave safe state
    meta2 = from_av(ddb_get("ResourceExchanges", {"pk": {"S": meta_pk(request2)}, "sk": {"S": meta_sk()}}))
    if meta2.get("status") == "ACCEPTED":
        parse(
            exchange(
                "POST",
                f"/exchange/requests/{request2}/transfer/start",
                PROVIDER_SUB,
                PROVIDER_ORG,
                body={"organization_id": PROVIDER_ORG},
            )
        )
        parse(
            exchange(
                "POST",
                f"/exchange/requests/{request2}/handover/confirm",
                REQUESTER_SUB,
                REQUESTER_ORG,
                body={
                    "organization_id": REQUESTER_ORG,
                    "destination_location_id": REQUESTER_LOC,
                },
            )
        )
    resource_b = from_av(ddb_get("Resources", {"resource_id": {"S": SMOKE_RESOURCE_B}}))
    note("smoke_b_final", resource_b)

    # MEMBER write denial: fabricate claims for nonexistent member → 403 membership
    member_deny = exchange(
        "POST",
        "/exchange/requests",
        "00000000-0000-0000-0000-000000000099",
        REQUESTER_ORG,
        body={
            "organization_id": REQUESTER_ORG,
            "destination_location_id": REQUESTER_LOC,
            "resource_type_id": requester_type_id,
            "tracking_mode": "INDIVIDUAL",
            "quantity_requested": 1,
            "visibility": "NETWORK",
        },
    )
    status, payload = parse(member_deny)
    if status not in {403, 401}:
        fail(f"non-member write expected 403 got {status}")
    note("non_member_write_denied", status)

    assert_pilot_untouched(pilot_before)

    # Final safety: no OPEN EXCHANGE allocations for smoke resources
    for rid, aid_hint in ((SMOKE_RESOURCE, allocation_id), (SMOKE_RESOURCE_B, meta2.get("allocation_id") if meta2 else None)):
        res = from_av(ddb_get("Resources", {"resource_id": {"S": rid}}))
        if res.get("operational_status") != "AVAILABLE" or res.get("Available") is not True:
            fail(f"smoke resource unsafe final state {rid}: {res}")
        if res.get("organization_id") != REQUESTER_ORG:
            fail(f"smoke resource not requester-owned {rid}")
        if res.get("visibility") != "PRIVATE":
            fail(f"smoke resource not PRIVATE {rid}")

    REPORT.update(
        {
            "requester_org": REQUESTER_ORG,
            "provider_org": PROVIDER_ORG,
            "smoke_resource": SMOKE_RESOURCE,
            "smoke_resource_b": SMOKE_RESOURCE_B,
            "request_id": request_id,
            "offer_id": offer_id,
            "allocation_id": allocation_id,
            "request2_id": request2,
            "offer2_id": offer2,
            "workflow": "COMPLETED",
            "pilot_touched": False,
        }
    )
    (folder / "exchange-live-smoke.json").write_text(json.dumps(REPORT, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"ok": True, "report": str(folder / "exchange-live-smoke.json")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
