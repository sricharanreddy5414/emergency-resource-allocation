"""Phase 7C: quantity exchange ownership transfer."""

import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest
from botocore.exceptions import ClientError
from boto3.dynamodb.types import TypeDeserializer

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [
    str(ROOT / "src"),
    str(ROOT / "src" / "shared"),
    str(ROOT / "src" / "organization"),
    str(ROOT / "src" / "exchange"),
]

import access

ORG_A = "ORG-A"
ORG_B = "ORG-B"
ORG_C = "ORG-C"
USER = "user-a"
TYPE_A = "RT-TYPE-A"
TYPE_B = "RT-TYPE-B"
DESER = TypeDeserializer()


def load_module(name, relative):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


handler = load_module("exchange_handler_7c", "src/exchange/handler.py")
service = load_module("exchange_service_7c", "src/exchange/service.py")
lifecycle = load_module("exchange_lifecycle_7c", "src/exchange/lifecycle.py")
quantity_handover = load_module("exchange_quantity_handover_7c", "src/exchange/quantity_handover.py")

# Load shared fake stores from the lifecycle suite, then rebind this suite's modules.
lifecycle_tests = load_module("exchange_lifecycle_tests_7c", "tests/test_resource_exchange_lifecycle.py")
ExchangeStore = lifecycle_tests.ExchangeStore
ResourceStore = lifecycle_tests.ResourceStore
AllocStore = lifecycle_tests.AllocStore
SimpleStore = lifecycle_tests.SimpleStore
AuditStore = lifecycle_tests.AuditStore
HistoryStore = lifecycle_tests.HistoryStore

handler.service = service
sys.modules["service"] = service
sys.modules["lifecycle"] = lifecycle
sys.modules["quantity_handover"] = quantity_handover


def memberships(*pairs):
    return [{"organization_id": o, "name": o, "role": r, "status": "ACTIVE"} for o, r in pairs]


def use_memberships(monkeypatch, records):
    monkeypatch.setattr(access, "list_memberships", lambda *a, **k: records)


def use_billing(monkeypatch, status="ACTIVE"):
    class Subs:
        def get_item(self, Key):
            if status is None:
                return {}
            return {
                "Item": {
                    "organization_id": Key["organization_id"],
                    "subscription_status": status,
                }
            }

    monkeypatch.setattr(access, "subscriptions_table", lambda: Subs())


def event(method="GET", body=None, organization_id=ORG_A, path="/exchange/requests", query=None, role_sub=USER):
    payload = {
        "httpMethod": method,
        "path": path,
        "queryStringParameters": {"organization_id": organization_id, **(query or {})},
        "requestContext": {"authorizer": {"claims": {"sub": role_sub, "token_use": "id"}}},
    }
    if body is not None:
        payload["body"] = json.dumps(body)
    return payload


def body_of(result):
    return json.loads(result["body"])


def _decode(av_map):
    return {k: DESER.deserialize(v) for k, v in av_map.items()}


def seed(provider_qty=100, dest_pool=None):
    orgs = SimpleStore(
        [
            {"organization_id": ORG_A, "status": "ACTIVE", "name": "A"},
            {"organization_id": ORG_B, "status": "ACTIVE", "name": "B"},
            {"organization_id": ORG_C, "status": "ACTIVE", "name": "C"},
        ]
    )
    locations = SimpleStore(
        [
            {
                "organization_id": ORG_A,
                "location_id": "LOC-A",
                "name": "A",
                "city": "Bengaluru",
                "state": "KA",
                "status": "ACTIVE",
            },
            {
                "organization_id": ORG_B,
                "location_id": "LOC-B",
                "name": "B",
                "city": "Mysuru",
                "state": "KA",
                "status": "ACTIVE",
            },
        ]
    )
    types = SimpleStore(
        [
            {
                "organization_id": ORG_A,
                "resource_type_id": TYPE_A,
                "name": "Medical Kit",
                "status": "ACTIVE",
            },
            {
                "organization_id": ORG_B,
                "resource_type_id": TYPE_B,
                "name": "Medical Kit",
                "status": "ACTIVE",
            },
        ]
    )
    resources = [
        {
            "resource_id": "R-QTY-B",
            "organization_id": ORG_B,
            "location_id": "LOC-B",
            "Location": "B",
            "name": "Provider Kits",
            "Type": "Medical Kit",
            "Available": False,
            "operational_status": "AVAILABLE",
            "tracking_mode": "QUANTITY",
            "quantity_total": provider_qty,
            "quantity_available": provider_qty,
            "quantity_reserved": 0,
            "quantity_allocated": 0,
            "visibility": "PRIVATE",
        }
    ]
    if dest_pool:
        resources.append(dest_pool)
    return (
        orgs,
        locations,
        types,
        ResourceStore(resources),
        ExchangeStore(),
        AllocStore(),
        AuditStore(),
        HistoryStore(),
    )


def install(monkeypatch, orgs, locations, types, resources, exchanges, allocs, audits, history):
    sys.modules["service"] = service
    sys.modules["quantity_handover"] = quantity_handover
    monkeypatch.setattr(service, "organizations_table", lambda: orgs)
    monkeypatch.setattr(service, "locations_table", lambda: locations)
    monkeypatch.setattr(service, "resource_types_table", lambda: types)
    monkeypatch.setattr(service, "resources_table", lambda: resources)
    monkeypatch.setattr(service, "exchanges_table", lambda: exchanges)
    monkeypatch.setattr(service, "allocations_table", lambda: allocs)
    monkeypatch.setattr(service, "audit_table", lambda: audits)
    monkeypatch.setattr(service, "history_table", lambda: history)

    def fake_transact(items):
        for entry in items:
            if "Update" in entry:
                upd = entry["Update"]
                key = _decode(upd["Key"])
                values = _decode(upd.get("ExpressionAttributeValues") or {})
                names = upd.get("ExpressionAttributeNames") or {}
                table = upd["TableName"]
                store = {
                    "ResourceExchanges": exchanges,
                    "Resources": resources,
                    "Allocations": allocs,
                }[table]
                store.update_item(
                    Key=key,
                    UpdateExpression=upd.get("UpdateExpression"),
                    ConditionExpression=upd.get("ConditionExpression"),
                    ExpressionAttributeValues=values,
                    ExpressionAttributeNames=names,
                )
            elif "Put" in entry:
                put = entry["Put"]
                item = _decode(put["Item"])
                table = put["TableName"]
                if table == "Allocations":
                    allocs.put_item(item, ConditionExpression=put.get("ConditionExpression"))
                elif table == "Resources":
                    resources.put_item(item, ConditionExpression=put.get("ConditionExpression"))
                else:
                    exchanges.put_item(item, ConditionExpression=put.get("ConditionExpression"))

    monkeypatch.setattr(service, "_transact_write", fake_transact)


def create_qty_pair(monkeypatch, quantity=10, provider_qty=100, dest_pool=None, idem="q1"):
    use_memberships(monkeypatch, memberships((ORG_A, "OWNER"), (ORG_B, "OWNER")))
    use_billing(monkeypatch, "ACTIVE")
    orgs, locations, types, resources, exchanges, allocs, audits, history = seed(
        provider_qty=provider_qty, dest_pool=dest_pool
    )
    install(monkeypatch, orgs, locations, types, resources, exchanges, allocs, audits, history)

    created = handler.lambda_handler(
        event(
            "POST",
            {
                "destination_location_id": "LOC-A",
                "resource_type_id": TYPE_A,
                "tracking_mode": "QUANTITY",
                "quantity_requested": quantity,
                "visibility": "NETWORK",
                "idempotency_key": f"req-{idem}",
            },
            ORG_A,
            "/exchange/requests",
        ),
        None,
    )
    assert created["statusCode"] == 201, body_of(created)
    request_id = body_of(created)["request"]["exchange_request_id"]

    offered = handler.lambda_handler(
        event(
            "POST",
            {
                "resource_id": "R-QTY-B",
                "provider_location_id": "LOC-B",
                "quantity_offered": quantity,
                "idempotency_key": f"off-{idem}",
            },
            ORG_B,
            f"/exchange/requests/{request_id}/offers",
        ),
        None,
    )
    assert offered["statusCode"] == 201, body_of(offered)
    offer_id = body_of(offered)["offer"]["offer_id"]
    return request_id, offer_id, resources, exchanges, allocs, audits, history


def accept_start(request_id, offer_id):
    accepted = handler.lambda_handler(
        event("POST", {}, ORG_A, f"/exchange/requests/{request_id}/offers/{offer_id}/accept"),
        None,
    )
    assert accepted["statusCode"] == 200, body_of(accepted)
    started = handler.lambda_handler(
        event("POST", {}, ORG_B, f"/exchange/requests/{request_id}/transfer/start"),
        None,
    )
    assert started["statusCode"] == 200, body_of(started)
    return accepted, started


def confirm(request_id, quantity, destination_resource_id=None, org=ORG_A):
    body = {"quantity": quantity, "destination_location_id": "LOC-A"}
    if destination_resource_id is not None:
        body["destination_resource_id"] = destination_resource_id
    return handler.lambda_handler(
        event("POST", body, org, f"/exchange/requests/{request_id}/handover/confirm"),
        None,
    )


def test_partial_transfer_creates_destination(monkeypatch):
    request_id, offer_id, resources, exchanges, allocs, audits, history = create_qty_pair(
        monkeypatch, quantity=10, provider_qty=100
    )
    accept_start(request_id, offer_id)
    src = resources.items["R-QTY-B"]
    assert src["quantity_available"] == 90
    assert src["quantity_allocated"] == 10
    assert src["quantity_total"] == 100

    result = confirm(request_id, 10)
    assert result["statusCode"] == 200, body_of(result)
    payload = body_of(result)
    assert payload["request"]["status"] == "COMPLETED"
    assert payload["allocation"]["status"] == "RELEASED"
    assert payload["transfer"]["quantity"] == 10
    assert payload["transfer"]["destination_created"] is True
    dest_id = payload["transfer"]["destination_resource_id"]
    dest = resources.items[dest_id]
    assert dest["organization_id"] == ORG_A
    assert dest["tracking_mode"] == "QUANTITY"
    assert dest["quantity_total"] == 10
    assert dest["quantity_available"] == 10
    assert dest["visibility"] == "PRIVATE"
    assert dest["operational_status"] == "AVAILABLE"
    assert "visibility_key" not in dest
    src = resources.items["R-QTY-B"]
    assert src["organization_id"] == ORG_B
    assert src["quantity_total"] == 90
    assert src["quantity_available"] == 90
    assert src["quantity_allocated"] == 0
    assert src["location_id"] == "LOC-B"
    assert any(e.get("action") == "resource.ownership_transferred" for e in audits.events)
    assert sum(1 for h in history.items if h.get("reason") == "RESOURCE_EXCHANGE_TRANSFERRED") == 2


def test_full_transfer(monkeypatch):
    request_id, offer_id, resources, *_ = create_qty_pair(monkeypatch, quantity=25, provider_qty=25)
    accept_start(request_id, offer_id)
    result = confirm(request_id, 25)
    assert result["statusCode"] == 200
    src = resources.items["R-QTY-B"]
    assert src["quantity_total"] == 0
    assert src["quantity_available"] == 0
    assert src["quantity_allocated"] == 0


def test_merge_into_existing_destination(monkeypatch):
    dest_pool = {
        "resource_id": "R-QTY-A",
        "organization_id": ORG_A,
        "location_id": "LOC-A",
        "Location": "A",
        "name": "Requester Kits",
        "Type": "Medical Kit",
        "Available": False,
        "operational_status": "AVAILABLE",
        "tracking_mode": "QUANTITY",
        "quantity_total": 20,
        "quantity_available": 20,
        "quantity_reserved": 0,
        "quantity_allocated": 0,
        "visibility": "NETWORK",
    }
    request_id, offer_id, resources, *_ = create_qty_pair(
        monkeypatch, quantity=10, provider_qty=100, dest_pool=dest_pool
    )
    accept_start(request_id, offer_id)
    result = confirm(request_id, 10, destination_resource_id="R-QTY-A")
    assert result["statusCode"] == 200, body_of(result)
    payload = body_of(result)
    assert payload["transfer"]["destination_created"] is False
    assert payload["transfer"]["destination_resource_id"] == "R-QTY-A"
    dest = resources.items["R-QTY-A"]
    assert dest["quantity_total"] == 30
    assert dest["quantity_available"] == 30
    assert dest["quantity_reserved"] == 0
    assert dest["quantity_allocated"] == 0
    assert dest["visibility"] == "PRIVATE"


def test_quantity_required_and_must_match_hold(monkeypatch):
    request_id, offer_id, *_ = create_qty_pair(monkeypatch, quantity=10)
    accept_start(request_id, offer_id)
    missing = handler.lambda_handler(
        event(
            "POST",
            {"destination_location_id": "LOC-A"},
            ORG_A,
            f"/exchange/requests/{request_id}/handover/confirm",
        ),
        None,
    )
    assert missing["statusCode"] == 400
    mismatch = confirm(request_id, 9)
    assert mismatch["statusCode"] == 409
    zero = confirm(request_id, 0)
    assert zero["statusCode"] == 400
    bad = handler.lambda_handler(
        event(
            "POST",
            {"quantity": 1.5, "destination_location_id": "LOC-A"},
            ORG_A,
            f"/exchange/requests/{request_id}/handover/confirm",
        ),
        None,
    )
    assert bad["statusCode"] == 400


def test_wrong_destination_org_and_type(monkeypatch):
    dest_pool = {
        "resource_id": "R-QTY-WRONG",
        "organization_id": ORG_C,
        "location_id": "LOC-A",
        "Location": "A",
        "name": "Other",
        "Type": "Medical Kit",
        "Available": False,
        "operational_status": "AVAILABLE",
        "tracking_mode": "QUANTITY",
        "quantity_total": 5,
        "quantity_available": 5,
        "quantity_reserved": 0,
        "quantity_allocated": 0,
        "visibility": "PRIVATE",
    }
    request_id, offer_id, resources, *_ = create_qty_pair(
        monkeypatch, quantity=5, dest_pool=dest_pool
    )
    # Put a requester-owned wrong-type pool
    resources.items["R-QTY-WRONGTYPE"] = {
        "resource_id": "R-QTY-WRONGTYPE",
        "organization_id": ORG_A,
        "location_id": "LOC-A",
        "Location": "A",
        "name": "Blankets",
        "Type": "Blanket",
        "Available": False,
        "operational_status": "AVAILABLE",
        "tracking_mode": "QUANTITY",
        "quantity_total": 5,
        "quantity_available": 5,
        "quantity_reserved": 0,
        "quantity_allocated": 0,
        "visibility": "PRIVATE",
    }
    accept_start(request_id, offer_id)
    assert confirm(request_id, 5, destination_resource_id="R-QTY-WRONG")["statusCode"] == 404
    assert confirm(request_id, 5, destination_resource_id="R-QTY-WRONGTYPE")["statusCode"] == 409


def test_retired_destination_rejected(monkeypatch):
    dest_pool = {
        "resource_id": "R-QTY-RET",
        "organization_id": ORG_A,
        "location_id": "LOC-A",
        "Location": "A",
        "name": "Retired",
        "Type": "Medical Kit",
        "Available": False,
        "operational_status": "RETIRED",
        "tracking_mode": "QUANTITY",
        "quantity_total": 5,
        "quantity_available": 5,
        "quantity_reserved": 0,
        "quantity_allocated": 0,
        "visibility": "PRIVATE",
    }
    request_id, offer_id, *_ = create_qty_pair(monkeypatch, quantity=5, dest_pool=dest_pool)
    accept_start(request_id, offer_id)
    assert confirm(request_id, 5, destination_resource_id="R-QTY-RET")["statusCode"] == 409


def test_completed_retry_idempotent(monkeypatch):
    request_id, offer_id, resources, *_ = create_qty_pair(monkeypatch, quantity=8)
    accept_start(request_id, offer_id)
    first = confirm(request_id, 8)
    assert first["statusCode"] == 200
    dest_id = body_of(first)["transfer"]["destination_resource_id"]
    src_total = resources.items["R-QTY-B"]["quantity_total"]
    dest_total = resources.items[dest_id]["quantity_total"]
    second = confirm(request_id, 8)
    assert second["statusCode"] == 200
    assert body_of(second)["message"] == "Handover already completed"
    assert resources.items["R-QTY-B"]["quantity_total"] == src_total
    assert resources.items[dest_id]["quantity_total"] == dest_total


def test_cancel_after_hold_before_handover(monkeypatch):
    request_id, offer_id, resources, *_ = create_qty_pair(monkeypatch, quantity=6)
    accept_start(request_id, offer_id)
    cancelled = handler.lambda_handler(
        event("POST", {}, ORG_A, f"/exchange/requests/{request_id}/cancel"),
        None,
    )
    assert cancelled["statusCode"] == 200, body_of(cancelled)
    src = resources.items["R-QTY-B"]
    assert src["quantity_total"] == 100
    assert src["quantity_available"] == 100
    assert src["quantity_allocated"] == 0
    assert confirm(request_id, 6)["statusCode"] == 409


def test_member_and_provider_cannot_confirm(monkeypatch):
    request_id, offer_id, *_ = create_qty_pair(monkeypatch, quantity=4)
    accept_start(request_id, offer_id)
    use_memberships(monkeypatch, memberships((ORG_A, "MEMBER"), (ORG_B, "OWNER")))
    assert confirm(request_id, 4)["statusCode"] == 403
    use_memberships(monkeypatch, memberships((ORG_A, "OWNER"), (ORG_B, "OWNER")))
    assert confirm(request_id, 4, org=ORG_B)["statusCode"] in {403, 404}


def test_billing_blocks_quantity_handover(monkeypatch):
    request_id, offer_id, *_ = create_qty_pair(monkeypatch, quantity=3)
    accept_start(request_id, offer_id)
    use_billing(monkeypatch, "CANCELLED")
    result = confirm(request_id, 3)
    assert result["statusCode"] == 403
    payload = body_of(result)
    assert payload.get("code") == "BILLING_REQUIRED" or payload.get("error", {}).get("code") == "BILLING_REQUIRED"


def test_concurrent_source_accepts(monkeypatch):
    """Two accepts for 6 from available=10: only one can hold."""
    use_memberships(monkeypatch, memberships((ORG_A, "OWNER"), (ORG_B, "OWNER")))
    use_billing(monkeypatch, "ACTIVE")
    orgs, locations, types, resources, exchanges, allocs, audits, history = seed(provider_qty=10)
    install(monkeypatch, orgs, locations, types, resources, exchanges, allocs, audits, history)

    def make_request(suffix):
        created = handler.lambda_handler(
            event(
                "POST",
                {
                    "destination_location_id": "LOC-A",
                    "resource_type_id": TYPE_A,
                    "tracking_mode": "QUANTITY",
                    "quantity_requested": 6,
                    "visibility": "NETWORK",
                    "idempotency_key": f"req-{suffix}",
                },
                ORG_A,
                "/exchange/requests",
            ),
            None,
        )
        request_id = body_of(created)["request"]["exchange_request_id"]
        offered = handler.lambda_handler(
            event(
                "POST",
                {
                    "resource_id": "R-QTY-B",
                    "provider_location_id": "LOC-B",
                    "quantity_offered": 6,
                    "idempotency_key": f"off-{suffix}",
                },
                ORG_B,
                f"/exchange/requests/{request_id}/offers",
            ),
            None,
        )
        return request_id, body_of(offered)["offer"]["offer_id"]

    r1, o1 = make_request("a")
    r2, o2 = make_request("b")
    a1 = handler.lambda_handler(
        event("POST", {}, ORG_A, f"/exchange/requests/{r1}/offers/{o1}/accept"), None
    )
    a2 = handler.lambda_handler(
        event("POST", {}, ORG_A, f"/exchange/requests/{r2}/offers/{o2}/accept"), None
    )
    codes = sorted([a1["statusCode"], a2["statusCode"]])
    assert codes == [200, 409]
    src = resources.items["R-QTY-B"]
    assert src["quantity_available"] == 4
    assert src["quantity_allocated"] == 6
    assert src["quantity_available"] >= 0


def test_frontend_quantity_handover_copy():
    text = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
    assert "destination_resource_id" in text or "exchangeHandoverDestination" in text
    assert "units will transfer" in text.lower() or "quantity" in text.lower()


def test_package_includes_quantity_handover():
    from scripts.lambda_manifest import EXCHANGE_PACKAGES

    assert "quantity_handover.py" in EXCHANGE_PACKAGES["erap-exchange"]
    assert "quantity_handover.py" in EXCHANGE_PACKAGES["erap-exchange-expiry"]
