"""Phase 5D: atomic offer acceptance + EXCHANGE hold tests."""

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


def load_module(name, relative):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


handler = load_module("exchange_handler_5d", "src/exchange/handler.py")
service = load_module("exchange_service_5d", "src/exchange/service.py")
handler.service = service
lifecycle = load_module("lifecycle_5d", "src/shared/lifecycle_operations.py")

ORG_A = "ORG-A"
ORG_B = "ORG-B"
ORG_C = "ORG-C"
USER = "user-a"
TYPE_A = "RT-TYPE-A"
TYPE_B = "RT-TYPE-B"
DESER = TypeDeserializer()


def memberships(*pairs):
    return [
        {"organization_id": o, "name": o, "role": r, "status": "ACTIVE"}
        for o, r in pairs
    ]


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


def event(method="GET", body=None, organization_id=ORG_A, path="/exchange/requests", query=None):
    payload = {
        "httpMethod": method,
        "path": path,
        "queryStringParameters": {"organization_id": organization_id, **(query or {})},
        "requestContext": {"authorizer": {"claims": {"sub": USER, "token_use": "id"}}},
    }
    if body is not None:
        payload["body"] = json.dumps(body)
    return payload


def body_of(result):
    return json.loads(result["body"])


def _decode(av_map):
    return {k: DESER.deserialize(v) for k, v in av_map.items()}


class ExchangeStore:
    def __init__(self):
        self.name = "ResourceExchanges"
        self.table_name = "ResourceExchanges"
        self.items = {}
        self.puts = []

    def get_item(self, Key):
        item = self.items.get((Key["pk"], Key["sk"]))
        return {"Item": copy.deepcopy(item)} if item else {}

    def put_item(self, Item, ConditionExpression=None, **kwargs):
        key = (Item["pk"], Item["sk"])
        if ConditionExpression and "attribute_not_exists" in ConditionExpression and key in self.items:
            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "PutItem")
        self.items[key] = copy.deepcopy(Item)
        self.puts.append(copy.deepcopy(Item))

    def update_item(self, **kwargs):
        key = (kwargs["Key"]["pk"], kwargs["Key"]["sk"])
        item = self.items.get(key)
        if not item:
            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")
        values = kwargs.get("ExpressionAttributeValues") or {}
        condition = kwargs.get("ConditionExpression") or ""
        if "#status = :open" in condition and item.get("status") != values.get(":open"):
            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")
        expr = kwargs.get("UpdateExpression") or ""
        if "SUPERSEDED" in str(values) or values.get(":superseded") == "SUPERSEDED":
            item["status"] = "SUPERSEDED"
        if values.get(":accepted") == "ACCEPTED":
            item["status"] = "ACCEPTED"
        if "REMOVE network_list_key" in expr:
            item.pop("network_list_key", None)
        item["updated_at"] = values.get(":now", item.get("updated_at"))
        self.items[key] = item

    def query(self, **kwargs):
        items = list(self.items.values())
        index = kwargs.get("IndexName")
        if index == "NetworkOpenRequestIndex":
            items = [i for i in items if i.get("network_list_key") == "OPEN"]
        else:
            target_pk = None
            kce = kwargs.get("KeyConditionExpression")
            if kce is not None:
                from boto3.dynamodb.conditions import ConditionExpressionBuilder

                built = ConditionExpressionBuilder().build_expression(kce)
                for value in (built.attribute_value_placeholders or {}).values():
                    if isinstance(value, str) and value.startswith("EXREQ#"):
                        target_pk = value
                        break
            items = [i for i in items if i.get("entity_type") == "EXCHANGE_OFFER"]
            if target_pk:
                items = [i for i in items if i.get("pk") == target_pk]
        return {"Items": copy.deepcopy(items)}


class ResourceStore:
    def __init__(self, items):
        self.name = "Resources"
        self.table_name = "Resources"
        self.items = {i["resource_id"]: copy.deepcopy(i) for i in items}
        self.updates = []

    def get_item(self, Key):
        item = self.items.get(Key["resource_id"])
        return {"Item": copy.deepcopy(item)} if item else {}

    def update_item(self, **kwargs):
        rid = kwargs["Key"]["resource_id"]
        item = self.items[rid]
        values = kwargs.get("ExpressionAttributeValues") or {}
        condition = kwargs.get("ConditionExpression") or ""
        self.updates.append(kwargs)
        if "Available = :true" in condition or "#a = :true" in condition:
            if not item.get("Available"):
                raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")
            if item.get("organization_id") != values.get(":organization_id"):
                raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")
            status = item.get("operational_status")
            if status is not None and str(status).upper() not in {"AVAILABLE", ""}:
                raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")
        if "quantity_available >= :qty" in condition:
            if item.get("quantity_available", 0) < values.get(":qty", 0):
                raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")
            item["quantity_available"] -= values[":qty"]
            item["quantity_allocated"] = item.get("quantity_allocated", 0) + values[":qty"]
            return
        if values.get(":op_allocated") == "ALLOCATED":
            item["Available"] = False
            item["operational_status"] = "ALLOCATED"


class AllocStore:
    def __init__(self):
        self.name = "Allocations"
        self.table_name = "Allocations"
        self.items = {}

    def get_item(self, Key):
        item = self.items.get(Key["allocation_id"])
        return {"Item": copy.deepcopy(item)} if item else {}

    def put_item(self, Item, ConditionExpression=None, **kwargs):
        aid = Item["allocation_id"]
        if ConditionExpression and aid in self.items:
            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "PutItem")
        self.items[aid] = copy.deepcopy(Item)


class SimpleStore:
    def __init__(self, items):
        self._items = list(items)

    def get_item(self, Key):
        for item in self._items:
            if all(item.get(k) == v for k, v in Key.items()):
                return {"Item": copy.deepcopy(item)}
        return {}


class AuditStore:
    def __init__(self):
        self.events = []

    def put_item(self, Item):
        self.events.append(Item)


class HistoryStore:
    def __init__(self):
        self.items = []

    def put_item(self, Item, **kwargs):
        self.items.append(Item)


def seed():
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
    resources = ResourceStore(
        [
            {
                "resource_id": "R-B-1",
                "organization_id": ORG_B,
                "location_id": "LOC-B",
                "Location": "B",
                "name": "Spare",
                "Type": "Medical Kit",
                "Available": True,
                "operational_status": "AVAILABLE",
                "tracking_mode": "INDIVIDUAL",
            },
            {
                "resource_id": "R-B-Q",
                "organization_id": ORG_B,
                "location_id": "LOC-B",
                "Location": "B",
                "name": "Pool",
                "Type": "Medical Kit",
                "Available": False,
                "operational_status": "AVAILABLE",
                "tracking_mode": "QUANTITY",
                "quantity_total": 10,
                "quantity_available": 7,
                "quantity_reserved": 0,
                "quantity_allocated": 3,
            },
            {
                "resource_id": "R-B-2",
                "organization_id": ORG_B,
                "location_id": "LOC-B",
                "Location": "B",
                "name": "Spare2",
                "Type": "Medical Kit",
                "Available": True,
                "operational_status": "AVAILABLE",
                "tracking_mode": "INDIVIDUAL",
            },
        ]
    )
    return orgs, locations, types, resources


def install_transact(monkeypatch, exchanges, resources, allocations):
    def fake_transact(transact_items):
        # Validate then apply; any condition failure aborts all.
        planned = []
        for entry in transact_items:
            if "Update" in entry:
                upd = entry["Update"]
                table = upd["TableName"]
                key = _decode(upd["Key"])
                values = _decode(upd.get("ExpressionAttributeValues") or {})
                condition = upd.get("ConditionExpression") or ""
                expr = upd.get("UpdateExpression") or ""
                if table == "ResourceExchanges":
                    item = exchanges.items.get((key["pk"], key["sk"]))
                    if not item:
                        raise ClientError(
                            {"Error": {"Code": "TransactionCanceledException"}},
                            "TransactWriteItems",
                        )
                    if "#status = :open" in condition and item.get("status") != "OPEN":
                        raise ClientError(
                            {"Error": {"Code": "TransactionCanceledException"}},
                            "TransactWriteItems",
                        )
                    planned.append(("ex_upd", key, values, expr))
                elif table == "Resources":
                    item = resources.items.get(key["resource_id"])
                    if not item:
                        raise ClientError(
                            {"Error": {"Code": "TransactionCanceledException"}},
                            "TransactWriteItems",
                        )
                    if "quantity_available >= :qty" in condition:
                        if item.get("quantity_available", 0) < values.get(":qty", 0):
                            raise ClientError(
                                {"Error": {"Code": "TransactionCanceledException"}},
                                "TransactWriteItems",
                            )
                    elif not item.get("Available") or item.get("organization_id") != values.get(
                        ":organization_id"
                    ):
                        raise ClientError(
                            {"Error": {"Code": "TransactionCanceledException"}},
                            "TransactWriteItems",
                        )
                    elif item.get("operational_status") not in (None, "AVAILABLE"):
                        raise ClientError(
                            {"Error": {"Code": "TransactionCanceledException"}},
                            "TransactWriteItems",
                        )
                    planned.append(("res_upd", key, values, expr))
            elif "Put" in entry:
                put = entry["Put"]
                item = _decode(put["Item"])
                if item["allocation_id"] in allocations.items:
                    raise ClientError(
                        {"Error": {"Code": "TransactionCanceledException"}},
                        "TransactWriteItems",
                    )
                planned.append(("alloc_put", item, None, None))

        for kind, a, b, c in planned:
            if kind == "ex_upd":
                item = exchanges.items[(a["pk"], a["sk"])]
                if b.get(":accepted") == "ACCEPTED":
                    item["status"] = "ACCEPTED"
                    if ":offer_id" in b:
                        item["accepted_offer_id"] = b[":offer_id"]
                        item["accepted_resource_id"] = b.get(":resource_id")
                        item["accepted_provider_organization_id"] = b.get(":provider")
                    item.pop("network_list_key", None)
                if b.get(":superseded") == "SUPERSEDED":
                    item["status"] = "SUPERSEDED"
            elif kind == "res_upd":
                item = resources.items[a["resource_id"]]
                if ":qty" in b and "quantity_available" in (c or ""):
                    item["quantity_available"] -= b[":qty"]
                    item["quantity_allocated"] = item.get("quantity_allocated", 0) + b[":qty"]
                else:
                    item["Available"] = False
                    item["operational_status"] = "ALLOCATED"
            elif kind == "alloc_put":
                allocations.items[a["allocation_id"]] = a

    monkeypatch.setattr(service, "_transact_write", fake_transact)


def wire(monkeypatch, exchanges, resources, allocations, orgs, locations, types, audit=None, history=None):
    monkeypatch.setattr(service, "exchanges_table", lambda: exchanges)
    monkeypatch.setattr(service, "resources_table", lambda: resources)
    monkeypatch.setattr(service, "allocations_table", lambda: allocations)
    monkeypatch.setattr(service, "organizations_table", lambda: orgs)
    monkeypatch.setattr(service, "locations_table", lambda: locations)
    monkeypatch.setattr(service, "resource_types_table", lambda: types)
    monkeypatch.setattr(service, "audit_table", lambda: audit or AuditStore())
    monkeypatch.setattr(service, "history_table", lambda: history or HistoryStore())
    install_transact(monkeypatch, exchanges, resources, allocations)


def setup_open_exchange(monkeypatch, tracking="INDIVIDUAL", quantity_requested=1, resource_id="R-B-1"):
    use_memberships(monkeypatch, memberships((ORG_A, "OPERATOR")))
    use_billing(monkeypatch, "ACTIVE")
    exchanges = ExchangeStore()
    allocations = AllocStore()
    orgs, locations, types, resources = seed()
    audit = AuditStore()
    history = HistoryStore()
    wire(monkeypatch, exchanges, resources, allocations, orgs, locations, types, audit, history)
    created = handler.lambda_handler(
        event(
            "POST",
            {
                "organization_id": ORG_A,
                "destination_location_id": "LOC-A",
                "resource_type_id": TYPE_A,
                "tracking_mode": tracking,
                "quantity_requested": quantity_requested,
                "idempotency_key": f"req-{tracking}-{resource_id}",
            },
        ),
        None,
    )
    assert created["statusCode"] == 201
    request_id = body_of(created)["request"]["exchange_request_id"]
    use_memberships(monkeypatch, memberships((ORG_B, "OPERATOR")))
    qty = 1 if tracking == "INDIVIDUAL" else min(3, quantity_requested)
    offered = handler.lambda_handler(
        event(
            "POST",
            {
                "organization_id": ORG_B,
                "resource_id": resource_id,
                "provider_location_id": "LOC-B",
                "quantity_offered": qty,
                "idempotency_key": f"off-{resource_id}",
            },
            organization_id=ORG_B,
            path=f"/exchange/requests/{request_id}/offers",
        ),
        None,
    )
    assert offered["statusCode"] == 201, body_of(offered)
    offer_id = body_of(offered)["offer"]["offer_id"]
    return request_id, offer_id, exchanges, resources, allocations, audit, history


def test_individual_accept_holds_without_ownership_transfer(monkeypatch):
    request_id, offer_id, exchanges, resources, allocations, audit, history = setup_open_exchange(
        monkeypatch
    )
    use_memberships(monkeypatch, memberships((ORG_A, "OPERATOR")))
    use_billing(monkeypatch, "ACTIVE")
    accepted = handler.lambda_handler(
        event(
            "POST",
            {"organization_id": ORG_A},
            path=f"/exchange/requests/{request_id}/offers/{offer_id}/accept",
        ),
        None,
    )
    assert accepted["statusCode"] == 200, body_of(accepted)
    payload = body_of(accepted)
    assert payload["request"]["status"] == "ACCEPTED"
    assert payload["offer"]["status"] == "ACCEPTED"
    assert payload["allocation"]["allocation_type"] == "EXCHANGE"
    assert payload["allocation"]["status"] == "OPEN"
    assert payload["ownership_transferred"] is False
    assert payload["location_transferred"] is False
    resource = resources.items["R-B-1"]
    assert resource["Available"] is False
    assert resource["operational_status"] == "ALLOCATED"
    assert resource["organization_id"] == ORG_B
    assert resource["location_id"] == "LOC-B"
    assert len(allocations.items) == 1
    assert any(e["action"] == "exchange.offer_accepted" for e in audit.events)
    assert any(h["reason"] == "RESOURCE_EXCHANGE_ALLOCATED" for h in history.items)


def test_quantity_accept_updates_counters_only(monkeypatch):
    request_id, offer_id, exchanges, resources, allocations, audit, history = setup_open_exchange(
        monkeypatch, tracking="QUANTITY", quantity_requested=5, resource_id="R-B-Q"
    )
    before_total = resources.items["R-B-Q"]["quantity_total"]
    before_avail = resources.items["R-B-Q"]["quantity_available"]
    before_alloc = resources.items["R-B-Q"]["quantity_allocated"]
    use_memberships(monkeypatch, memberships((ORG_A, "OPERATOR")))
    use_billing(monkeypatch, "ACTIVE")
    accepted = handler.lambda_handler(
        event(
            "POST",
            {"organization_id": ORG_A},
            path=f"/exchange/requests/{request_id}/offers/{offer_id}/accept",
        ),
        None,
    )
    assert accepted["statusCode"] == 200
    resource = resources.items["R-B-Q"]
    assert resource["quantity_total"] == before_total
    assert resource["quantity_available"] == before_avail - 3
    assert resource["quantity_allocated"] == before_alloc + 3
    assert resource["organization_id"] == ORG_B
    assert body_of(accepted)["allocation"]["quantity"] == 3


def test_competing_offers_superseded(monkeypatch):
    request_id, offer_id, exchanges, resources, allocations, audit, history = setup_open_exchange(
        monkeypatch
    )
    use_memberships(monkeypatch, memberships((ORG_B, "OPERATOR")))
    use_billing(monkeypatch, "ACTIVE")
    second = handler.lambda_handler(
        event(
            "POST",
            {
                "organization_id": ORG_B,
                "resource_id": "R-B-2",
                "provider_location_id": "LOC-B",
                "idempotency_key": "off-2",
            },
            organization_id=ORG_B,
            path=f"/exchange/requests/{request_id}/offers",
        ),
        None,
    )
    assert second["statusCode"] == 201
    offer2 = body_of(second)["offer"]["offer_id"]
    use_memberships(monkeypatch, memberships((ORG_A, "OPERATOR")))
    accepted = handler.lambda_handler(
        event(
            "POST",
            {"organization_id": ORG_A},
            path=f"/exchange/requests/{request_id}/offers/{offer_id}/accept",
        ),
        None,
    )
    assert accepted["statusCode"] == 200
    offers = {
        item["offer_id"]: item["status"]
        for item in exchanges.items.values()
        if item.get("entity_type") == "EXCHANGE_OFFER"
    }
    assert offers[offer_id] == "ACCEPTED"
    assert offers[offer2] == "SUPERSEDED"
    assert resources.items["R-B-2"]["Available"] is True
    assert resources.items["R-B-2"]["operational_status"] == "AVAILABLE"


def test_double_accept_same_resource_conflicts(monkeypatch):
    request_id, offer_id, exchanges, resources, allocations, audit, history = setup_open_exchange(
        monkeypatch
    )
    # Second request offering same resource
    use_memberships(monkeypatch, memberships((ORG_A, "OPERATOR")))
    use_billing(monkeypatch, "ACTIVE")
    created2 = handler.lambda_handler(
        event(
            "POST",
            {
                "organization_id": ORG_A,
                "destination_location_id": "LOC-A",
                "resource_type_id": TYPE_A,
                "idempotency_key": "req-2",
            },
        ),
        None,
    )
    request2 = body_of(created2)["request"]["exchange_request_id"]
    use_memberships(monkeypatch, memberships((ORG_B, "OPERATOR")))
    offered2 = handler.lambda_handler(
        event(
            "POST",
            {
                "organization_id": ORG_B,
                "resource_id": "R-B-1",
                "provider_location_id": "LOC-B",
                "idempotency_key": "off-same-res",
            },
            organization_id=ORG_B,
            path=f"/exchange/requests/{request2}/offers",
        ),
        None,
    )
    offer2 = body_of(offered2)["offer"]["offer_id"]

    use_memberships(monkeypatch, memberships((ORG_A, "OPERATOR")))
    first = handler.lambda_handler(
        event(
            "POST",
            {"organization_id": ORG_A},
            path=f"/exchange/requests/{request_id}/offers/{offer_id}/accept",
        ),
        None,
    )
    second = handler.lambda_handler(
        event(
            "POST",
            {"organization_id": ORG_A},
            path=f"/exchange/requests/{request2}/offers/{offer2}/accept",
        ),
        None,
    )
    assert first["statusCode"] == 200
    assert second["statusCode"] == 409
    assert len(allocations.items) == 1


def test_idempotent_accept_retry(monkeypatch):
    request_id, offer_id, exchanges, resources, allocations, audit, history = setup_open_exchange(
        monkeypatch
    )
    use_memberships(monkeypatch, memberships((ORG_A, "OPERATOR")))
    use_billing(monkeypatch, "ACTIVE")
    path = f"/exchange/requests/{request_id}/offers/{offer_id}/accept"
    first = handler.lambda_handler(event("POST", {"organization_id": ORG_A}, path=path), None)
    second = handler.lambda_handler(event("POST", {"organization_id": ORG_A}, path=path), None)
    assert first["statusCode"] == 200
    assert second["statusCode"] == 200
    assert len(allocations.items) == 1


def test_provider_and_unrelated_cannot_accept(monkeypatch):
    request_id, offer_id, exchanges, resources, allocations, audit, history = setup_open_exchange(
        monkeypatch
    )
    use_memberships(monkeypatch, memberships((ORG_B, "OPERATOR")))
    use_billing(monkeypatch, "ACTIVE")
    provider = handler.lambda_handler(
        event(
            "POST",
            {"organization_id": ORG_B},
            organization_id=ORG_B,
            path=f"/exchange/requests/{request_id}/offers/{offer_id}/accept",
        ),
        None,
    )
    assert provider["statusCode"] in (403, 404)

    use_memberships(monkeypatch, memberships((ORG_C, "OPERATOR")))
    other = handler.lambda_handler(
        event(
            "POST",
            {"organization_id": ORG_C},
            organization_id=ORG_C,
            path=f"/exchange/requests/{request_id}/offers/{offer_id}/accept",
        ),
        None,
    )
    assert other["statusCode"] == 404


def test_billing_expired_blocks_accept(monkeypatch):
    request_id, offer_id, exchanges, resources, allocations, audit, history = setup_open_exchange(
        monkeypatch
    )
    use_memberships(monkeypatch, memberships((ORG_A, "OPERATOR")))
    use_billing(monkeypatch, "EXPIRED")
    denied = handler.lambda_handler(
        event(
            "POST",
            {"organization_id": ORG_A},
            path=f"/exchange/requests/{request_id}/offers/{offer_id}/accept",
        ),
        None,
    )
    assert denied["statusCode"] == 403


def test_exchange_hold_blocks_lifecycle(monkeypatch):
    request_id, offer_id, exchanges, resources, allocations, audit, history = setup_open_exchange(
        monkeypatch
    )
    use_memberships(monkeypatch, memberships((ORG_A, "OPERATOR")))
    use_billing(monkeypatch, "ACTIVE")
    handler.lambda_handler(
        event(
            "POST",
            {"organization_id": ORG_A},
            path=f"/exchange/requests/{request_id}/offers/{offer_id}/accept",
        ),
        None,
    )
    resource = resources.items["R-B-1"]
    tables = {
        "resources": resources,
        "allocations": type(
            "A",
            (),
            {"allocation_items": allocations.items, "name": "Allocations"},
        )(),
        "history": HistoryStore(),
        "audit": AuditStore(),
    }
    tables["allocations"].allocation_items = allocations.items
    with pytest.raises(lifecycle.LifecycleOperationError) as error:
        lifecycle.start_maintenance({}, ORG_B, USER, "OPERATOR", resource, tables)
    assert error.value.status_code == 409


def test_failed_transact_leaves_no_partial_state(monkeypatch):
    request_id, offer_id, exchanges, resources, allocations, audit, history = setup_open_exchange(
        monkeypatch
    )

    def boom(items):
        raise ClientError({"Error": {"Code": "TransactionCanceledException"}}, "TransactWriteItems")

    monkeypatch.setattr(service, "_transact_write", boom)
    use_memberships(monkeypatch, memberships((ORG_A, "OPERATOR")))
    use_billing(monkeypatch, "ACTIVE")
    failed = handler.lambda_handler(
        event(
            "POST",
            {"organization_id": ORG_A},
            path=f"/exchange/requests/{request_id}/offers/{offer_id}/accept",
        ),
        None,
    )
    assert failed["statusCode"] == 409
    meta = next(i for i in exchanges.items.values() if i.get("sk") == "META")
    assert meta["status"] == "OPEN"
    assert resources.items["R-B-1"]["Available"] is True
    assert allocations.items == {}
    assert not any(e["action"] == "exchange.offer_accepted" for e in audit.events)


@pytest.mark.parametrize("status", ["TRIALING", "ACTIVE", "PAST_DUE", "GRANDFATHERED"])
def test_billing_writable_allows_accept(monkeypatch, status):
    request_id, offer_id, exchanges, resources, allocations, audit, history = setup_open_exchange(
        monkeypatch
    )
    use_memberships(monkeypatch, memberships((ORG_A, "OPERATOR")))
    use_billing(monkeypatch, status)
    accepted = handler.lambda_handler(
        event(
            "POST",
            {"organization_id": ORG_A},
            path=f"/exchange/requests/{request_id}/offers/{offer_id}/accept",
        ),
        None,
    )
    assert accepted["statusCode"] == 200


def test_billing_cancelled_blocks_accept(monkeypatch):
    request_id, offer_id, exchanges, resources, allocations, audit, history = setup_open_exchange(
        monkeypatch
    )
    use_memberships(monkeypatch, memberships((ORG_A, "OPERATOR")))
    use_billing(monkeypatch, "CANCELLED")
    denied = handler.lambda_handler(
        event(
            "POST",
            {"organization_id": ORG_A},
            path=f"/exchange/requests/{request_id}/offers/{offer_id}/accept",
        ),
        None,
    )
    assert denied["statusCode"] == 403


def test_member_cannot_accept(monkeypatch):
    request_id, offer_id, exchanges, resources, allocations, audit, history = setup_open_exchange(
        monkeypatch
    )
    use_memberships(monkeypatch, memberships((ORG_A, "MEMBER")))
    use_billing(monkeypatch, "ACTIVE")
    denied = handler.lambda_handler(
        event(
            "POST",
            {"organization_id": ORG_A},
            path=f"/exchange/requests/{request_id}/offers/{offer_id}/accept",
        ),
        None,
    )
    assert denied["statusCode"] == 403


def test_accept_fails_when_resource_already_allocated(monkeypatch):
    request_id, offer_id, exchanges, resources, allocations, audit, history = setup_open_exchange(
        monkeypatch
    )
    resources.items["R-B-1"]["Available"] = False
    resources.items["R-B-1"]["operational_status"] = "ALLOCATED"
    use_memberships(monkeypatch, memberships((ORG_A, "OPERATOR")))
    use_billing(monkeypatch, "ACTIVE")
    failed = handler.lambda_handler(
        event(
            "POST",
            {"organization_id": ORG_A},
            path=f"/exchange/requests/{request_id}/offers/{offer_id}/accept",
        ),
        None,
    )
    assert failed["statusCode"] == 409
    assert allocations.items == {}


def test_accept_fails_when_resource_reserved(monkeypatch):
    request_id, offer_id, exchanges, resources, allocations, audit, history = setup_open_exchange(
        monkeypatch
    )
    resources.items["R-B-1"]["Available"] = False
    resources.items["R-B-1"]["operational_status"] = "RESERVED"
    use_memberships(monkeypatch, memberships((ORG_A, "OPERATOR")))
    use_billing(monkeypatch, "ACTIVE")
    failed = handler.lambda_handler(
        event(
            "POST",
            {"organization_id": ORG_A},
            path=f"/exchange/requests/{request_id}/offers/{offer_id}/accept",
        ),
        None,
    )
    assert failed["statusCode"] == 409


def test_quantity_insufficient_conflicts(monkeypatch):
    request_id, offer_id, exchanges, resources, allocations, audit, history = setup_open_exchange(
        monkeypatch, tracking="QUANTITY", quantity_requested=5, resource_id="R-B-Q"
    )
    # Keep quantity invariant: available + reserved + allocated == total
    resources.items["R-B-Q"]["quantity_available"] = 1
    resources.items["R-B-Q"]["quantity_allocated"] = 9
    resources.items["R-B-Q"]["quantity_reserved"] = 0
    use_memberships(monkeypatch, memberships((ORG_A, "OPERATOR")))
    use_billing(monkeypatch, "ACTIVE")
    failed = handler.lambda_handler(
        event(
            "POST",
            {"organization_id": ORG_A},
            path=f"/exchange/requests/{request_id}/offers/{offer_id}/accept",
        ),
        None,
    )
    assert failed["statusCode"] == 409
    assert resources.items["R-B-Q"]["quantity_available"] == 1
    assert allocations.items == {}


def test_accept_uses_emergency_claim_condition(monkeypatch):
    """Exchange hold must share emergency claim predicates for race safety."""
    from resource_state import EMERGENCY_CLAIM_CONDITION

    request_id, offer_id, exchanges, resources, allocations, audit, history = setup_open_exchange(
        monkeypatch
    )
    seen = {}

    def capture(transact_items):
        for entry in transact_items:
            if "Update" in entry and entry["Update"]["TableName"] == "Resources":
                seen["condition"] = entry["Update"]["ConditionExpression"]
        raise ClientError({"Error": {"Code": "TransactionCanceledException"}}, "TransactWriteItems")

    monkeypatch.setattr(service, "_transact_write", capture)
    use_memberships(monkeypatch, memberships((ORG_A, "OPERATOR")))
    use_billing(monkeypatch, "ACTIVE")
    handler.lambda_handler(
        event(
            "POST",
            {"organization_id": ORG_A},
            path=f"/exchange/requests/{request_id}/offers/{offer_id}/accept",
        ),
        None,
    )
    assert seen["condition"] == EMERGENCY_CLAIM_CONDITION


def test_second_accept_on_same_request_conflicts(monkeypatch):
    request_id, offer_id, exchanges, resources, allocations, audit, history = setup_open_exchange(
        monkeypatch
    )
    use_memberships(monkeypatch, memberships((ORG_B, "OPERATOR")))
    use_billing(monkeypatch, "ACTIVE")
    second = handler.lambda_handler(
        event(
            "POST",
            {
                "organization_id": ORG_B,
                "resource_id": "R-B-2",
                "provider_location_id": "LOC-B",
                "idempotency_key": "off-alt",
            },
            organization_id=ORG_B,
            path=f"/exchange/requests/{request_id}/offers",
        ),
        None,
    )
    offer2 = body_of(second)["offer"]["offer_id"]
    use_memberships(monkeypatch, memberships((ORG_A, "OPERATOR")))
    first = handler.lambda_handler(
        event(
            "POST",
            {"organization_id": ORG_A},
            path=f"/exchange/requests/{request_id}/offers/{offer_id}/accept",
        ),
        None,
    )
    again = handler.lambda_handler(
        event(
            "POST",
            {"organization_id": ORG_A},
            path=f"/exchange/requests/{request_id}/offers/{offer2}/accept",
        ),
        None,
    )
    assert first["statusCode"] == 200
    assert again["statusCode"] == 409
    assert len(allocations.items) == 1
    assert resources.items["R-B-2"]["Available"] is True


def _seed_competing_offers(exchanges, request_id, count, *, skip_offer_id=None):
    """Insert OPEN competing offers directly (bypass create_offer resource uniqueness)."""
    from exchange_model import meta_pk, offer_sk

    seeded = []
    for index in range(count):
        oid = f"EXOFF-COMP{index:04d}"
        if oid == skip_offer_id:
            continue
        item = {
            "pk": meta_pk(request_id),
            "sk": offer_sk(oid),
            "entity_type": "EXCHANGE_OFFER",
            "offer_id": oid,
            "exchange_request_id": request_id,
            "status": "OPEN",
            "provider_organization_id": ORG_B,
            "resource_id": f"R-SEED-{index}",
            "source_location_id": "LOC-B",
            "quantity_offered": 1,
        }
        exchanges.items[(item["pk"], item["sk"])] = item
        seeded.append(oid)
    return seeded


def _offer_statuses(exchanges):
    return {
        item["offer_id"]: item["status"]
        for item in exchanges.items.values()
        if item.get("entity_type") == "EXCHANGE_OFFER"
    }


def test_accept_core_transaction_is_exactly_four_items(monkeypatch):
    request_id, offer_id, exchanges, resources, allocations, audit, history = setup_open_exchange(
        monkeypatch
    )
    _seed_competing_offers(exchanges, request_id, 5)
    seen = {"count": None}
    inner = service._transact_write

    def wrapped(items):
        seen["count"] = len(items)
        return inner(items)

    monkeypatch.setattr(service, "_transact_write", wrapped)
    use_memberships(monkeypatch, memberships((ORG_A, "OPERATOR")))
    use_billing(monkeypatch, "ACTIVE")
    accepted = handler.lambda_handler(
        event(
            "POST",
            {"organization_id": ORG_A},
            path=f"/exchange/requests/{request_id}/offers/{offer_id}/accept",
        ),
        None,
    )
    assert accepted["statusCode"] == 200
    assert seen["count"] == 4
    statuses = _offer_statuses(exchanges)
    assert statuses[offer_id] == "ACCEPTED"
    assert all(status == "SUPERSEDED" for oid, status in statuses.items() if oid != offer_id)


def test_forty_or_fewer_competing_offers_all_superseded(monkeypatch):
    request_id, offer_id, exchanges, resources, allocations, audit, history = setup_open_exchange(
        monkeypatch
    )
    seeded = _seed_competing_offers(exchanges, request_id, 40)
    assert len(seeded) == 40
    use_memberships(monkeypatch, memberships((ORG_A, "OPERATOR")))
    use_billing(monkeypatch, "ACTIVE")
    accepted = handler.lambda_handler(
        event(
            "POST",
            {"organization_id": ORG_A},
            path=f"/exchange/requests/{request_id}/offers/{offer_id}/accept",
        ),
        None,
    )
    assert accepted["statusCode"] == 200
    statuses = _offer_statuses(exchanges)
    assert statuses[offer_id] == "ACCEPTED"
    assert all(statuses[oid] == "SUPERSEDED" for oid in seeded)
    assert len(allocations.items) == 1


def test_more_than_forty_competing_offers_all_superseded(monkeypatch):
    request_id, offer_id, exchanges, resources, allocations, audit, history = setup_open_exchange(
        monkeypatch
    )
    seeded = _seed_competing_offers(exchanges, request_id, 55)
    use_memberships(monkeypatch, memberships((ORG_A, "OPERATOR")))
    use_billing(monkeypatch, "ACTIVE")
    accepted = handler.lambda_handler(
        event(
            "POST",
            {"organization_id": ORG_A},
            path=f"/exchange/requests/{request_id}/offers/{offer_id}/accept",
        ),
        None,
    )
    assert accepted["statusCode"] == 200
    statuses = _offer_statuses(exchanges)
    assert statuses[offer_id] == "ACCEPTED"
    assert all(statuses[oid] == "SUPERSEDED" for oid in seeded)
    meta = next(i for i in exchanges.items.values() if i.get("sk") == "META")
    assert meta["status"] == "ACCEPTED"
    assert len(allocations.items) == 1
    assert resources.items["R-B-1"]["Available"] is False


def test_supersede_followup_failure_healed_on_retry(monkeypatch):
    request_id, offer_id, exchanges, resources, allocations, audit, history = setup_open_exchange(
        monkeypatch
    )
    seeded = _seed_competing_offers(exchanges, request_id, 3)
    original_update = exchanges.update_item
    fail_budget = {"n": 2}

    def flaky_update(**kwargs):
        values = kwargs.get("ExpressionAttributeValues") or {}
        if values.get(":superseded") == "SUPERSEDED" and fail_budget["n"] > 0:
            fail_budget["n"] -= 1
            raise ClientError(
                {"Error": {"Code": "ProvisionedThroughputExceededException"}},
                "UpdateItem",
            )
        return original_update(**kwargs)

    exchanges.update_item = flaky_update
    use_memberships(monkeypatch, memberships((ORG_A, "OPERATOR")))
    use_billing(monkeypatch, "ACTIVE")
    path = f"/exchange/requests/{request_id}/offers/{offer_id}/accept"
    first = handler.lambda_handler(event("POST", {"organization_id": ORG_A}, path=path), None)
    assert first["statusCode"] == 200
    # Hold succeeded even if some supersedes flaked in the first pass.
    assert len(allocations.items) == 1
    assert resources.items["R-B-1"]["Available"] is False

    # Ensure at least one leftover OPEN if cleanup rounds exhausted mid-failure,
    # then force leftovers and heal via idempotent retry.
    for oid in seeded:
        key = next(
            k for k, v in exchanges.items.items() if v.get("offer_id") == oid
        )
        exchanges.items[key]["status"] = "OPEN"
    fail_budget["n"] = 0
    second = handler.lambda_handler(event("POST", {"organization_id": ORG_A}, path=path), None)
    assert second["statusCode"] == 200
    statuses = _offer_statuses(exchanges)
    assert statuses[offer_id] == "ACCEPTED"
    assert all(statuses[oid] == "SUPERSEDED" for oid in seeded)
    assert len(allocations.items) == 1


def test_competing_offer_cannot_be_accepted_after_request_accepted(monkeypatch):
    request_id, offer_id, exchanges, resources, allocations, audit, history = setup_open_exchange(
        monkeypatch
    )
    seeded = _seed_competing_offers(exchanges, request_id, 2)
    # Leave one competitor intentionally OPEN after accept by stubbing cleanup.
    monkeypatch.setattr(service, "_supersede_competing_open_offers", lambda *a, **k: False)
    use_memberships(monkeypatch, memberships((ORG_A, "OPERATOR")))
    use_billing(monkeypatch, "ACTIVE")
    first = handler.lambda_handler(
        event(
            "POST",
            {"organization_id": ORG_A},
            path=f"/exchange/requests/{request_id}/offers/{offer_id}/accept",
        ),
        None,
    )
    assert first["statusCode"] == 200
    competitor = seeded[0]
    assert _offer_statuses(exchanges)[competitor] == "OPEN"
    blocked = handler.lambda_handler(
        event(
            "POST",
            {"organization_id": ORG_A},
            path=f"/exchange/requests/{request_id}/offers/{competitor}/accept",
        ),
        None,
    )
    assert blocked["statusCode"] == 409
    assert len(allocations.items) == 1


def test_failed_transaction_does_not_supersede_competitors(monkeypatch):
    request_id, offer_id, exchanges, resources, allocations, audit, history = setup_open_exchange(
        monkeypatch
    )
    seeded = _seed_competing_offers(exchanges, request_id, 5)

    def boom(items):
        raise ClientError({"Error": {"Code": "TransactionCanceledException"}}, "TransactWriteItems")

    monkeypatch.setattr(service, "_transact_write", boom)
    use_memberships(monkeypatch, memberships((ORG_A, "OPERATOR")))
    use_billing(monkeypatch, "ACTIVE")
    failed = handler.lambda_handler(
        event(
            "POST",
            {"organization_id": ORG_A},
            path=f"/exchange/requests/{request_id}/offers/{offer_id}/accept",
        ),
        None,
    )
    assert failed["statusCode"] == 409
    statuses = _offer_statuses(exchanges)
    assert statuses[offer_id] == "OPEN"
    assert all(statuses[oid] == "OPEN" for oid in seeded)
    assert allocations.items == {}
    assert resources.items["R-B-1"]["Available"] is True


def test_non_open_competitor_preserved_on_accept(monkeypatch):
    request_id, offer_id, exchanges, resources, allocations, audit, history = setup_open_exchange(
        monkeypatch
    )
    seeded = _seed_competing_offers(exchanges, request_id, 2)
    withdrawn = seeded[0]
    key = next(k for k, v in exchanges.items.items() if v.get("offer_id") == withdrawn)
    exchanges.items[key]["status"] = "WITHDRAWN"
    use_memberships(monkeypatch, memberships((ORG_A, "OPERATOR")))
    use_billing(monkeypatch, "ACTIVE")
    accepted = handler.lambda_handler(
        event(
            "POST",
            {"organization_id": ORG_A},
            path=f"/exchange/requests/{request_id}/offers/{offer_id}/accept",
        ),
        None,
    )
    assert accepted["statusCode"] == 200
    statuses = _offer_statuses(exchanges)
    assert statuses[withdrawn] == "WITHDRAWN"
    assert statuses[seeded[1]] == "SUPERSEDED"
    assert statuses[offer_id] == "ACCEPTED"
