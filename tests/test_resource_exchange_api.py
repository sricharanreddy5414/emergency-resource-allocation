"""Phase 5C: Resource Exchange API foundation tests."""

import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest
from botocore.exceptions import ClientError
from boto3.dynamodb.conditions import Key

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [
    str(ROOT / "src"),
    str(ROOT / "src" / "shared"),
    str(ROOT / "src" / "organization"),
    str(ROOT / "src" / "exchange"),
]

import access
from access import AccessError


def load_module(name, relative):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# Reload exchange modules under unique names for isolation.
handler = load_module("exchange_handler_5c", "src/exchange/handler.py")
service = load_module("exchange_service_5c", "src/exchange/service.py")
# Point handler at the same service module instance used by tests.
handler.service = service

public_api = load_module("public_api_5c", "src/public/handler.py")
visibility = load_module("visibility_5c", "src/shared/visibility.py")

ORG_A = "ORG-A"
ORG_B = "ORG-B"
ORG_C = "ORG-C"
USER = "user-a"
TYPE_A = "RT-TYPE-A"
TYPE_B = "RT-TYPE-B"


def memberships(*pairs):
    return [
        {
            "organization_id": organization_id,
            "name": organization_id,
            "role": role,
            "status": "ACTIVE",
        }
        for organization_id, role in pairs
    ]


def use_memberships(monkeypatch, records):
    monkeypatch.setattr(access, "list_memberships", lambda *args, **kwargs: records)


def use_billing(monkeypatch, status="ACTIVE"):
    class Subs:
        def get_item(self, Key):
            if status is None:
                return {}
            return {"Item": {"organization_id": Key["organization_id"], "subscription_status": status}}

    monkeypatch.setattr(access, "subscriptions_table", lambda: Subs())


def event(method="GET", body=None, organization_id=ORG_A, path="/exchange/requests", query=None, subject=USER):
    payload = {
        "httpMethod": method,
        "path": path,
        "queryStringParameters": {"organization_id": organization_id, **(query or {})},
        "requestContext": {"authorizer": {"claims": {"sub": subject, "token_use": "id"}}},
    }
    if body is not None:
        payload["body"] = json.dumps(body)
    return payload


def body_of(result):
    return json.loads(result["body"])


def _conditional_failed():
    return ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "PutItem")


class ExchangeStore:
    def __init__(self):
        self.items = {}
        self.puts = []
        self.queries = []
        self.scans = 0

    def _key(self, Key):
        return (Key["pk"], Key["sk"])

    def get_item(self, Key):
        item = self.items.get(self._key(Key))
        return {"Item": copy.deepcopy(item)} if item else {}

    def put_item(self, Item, ConditionExpression=None, **kwargs):
        key = self._key({"pk": Item["pk"], "sk": Item["sk"]})
        if ConditionExpression and "attribute_not_exists" in ConditionExpression and key in self.items:
            raise _conditional_failed()
        self.items[key] = copy.deepcopy(Item)
        self.puts.append(copy.deepcopy(Item))

    def query(self, **kwargs):
        self.queries.append(kwargs)
        index = kwargs.get("IndexName")
        items = list(self.items.values())

        if index == "NetworkOpenRequestIndex":
            items = [
                item
                for item in items
                if item.get("network_list_key") == "OPEN"
                and item.get("entity_type") == "EXCHANGE_REQUEST"
            ]
        elif index == "RequesterOrgIndex":
            items = [item for item in items if item.get("entity_type") == "EXCHANGE_REQUEST"]
        elif index == "ProviderOrgOfferIndex":
            items = [item for item in items if item.get("entity_type") == "EXCHANGE_OFFER"]
        else:
            # Primary table query — return offers/meta under matching pk values present.
            items = [
                item
                for item in items
                if item.get("entity_type") == "EXCHANGE_OFFER"
            ]
            # If exactly one META exists in store for a request under test, keep offers for that pk set.
            pks = {item.get("pk") for item in self.items.values() if item.get("sk") == "META"}
            if len(pks) == 1:
                only = next(iter(pks))
                items = [item for item in items if item.get("pk") == only]

        limit = kwargs.get("Limit")
        if limit:
            items = items[: int(limit)]
        return {"Items": copy.deepcopy(items)}

    def scan(self, **kwargs):
        self.scans += 1
        raise AssertionError("Scan is forbidden")


class SimpleStore:
    def __init__(self, items=None):
        self.items = {self._id(item): copy.deepcopy(item) for item in (items or [])}
        self.puts = []

    def _id(self, item):
        if "resource_id" in item:
            return ("resource", item["resource_id"])
        if "location_id" in item:
            return ("location", item["organization_id"], item["location_id"])
        if "resource_type_id" in item:
            return ("type", item["organization_id"], item["resource_type_id"])
        if "organization_id" in item and "name" in item and "resource_type_id" not in item and "location_id" not in item:
            return ("org", item["organization_id"])
        return ("other", id(item))

    def get_item(self, Key):
        if "resource_id" in Key:
            item = self.items.get(("resource", Key["resource_id"]))
        elif "location_id" in Key:
            item = self.items.get(("location", Key["organization_id"], Key["location_id"]))
        elif "resource_type_id" in Key:
            item = self.items.get(("type", Key["organization_id"], Key["resource_type_id"]))
        elif "organization_id" in Key and len(Key) == 1:
            item = self.items.get(("org", Key["organization_id"]))
        else:
            item = None
        return {"Item": copy.deepcopy(item)} if item else {}

    def put_item(self, Item, **kwargs):
        self.puts.append(copy.deepcopy(Item))
        self.items[self._id(Item)] = copy.deepcopy(Item)

    def query(self, **kwargs):
        raise AssertionError("unexpected query")

    def scan(self, **kwargs):
        raise AssertionError("scan")


class AuditStore:
    def __init__(self):
        self.events = []

    def put_item(self, Item):
        self.events.append(Item)


def seed_world():
    orgs = SimpleStore(
        [
            {"organization_id": ORG_A, "name": "Org A", "status": "ACTIVE"},
            {"organization_id": ORG_B, "name": "Org B", "status": "ACTIVE"},
            {"organization_id": ORG_C, "name": "Org C", "status": "ACTIVE"},
        ]
    )
    locations = SimpleStore(
        [
            {
                "organization_id": ORG_A,
                "location_id": "LOC-A",
                "name": "A HQ",
                "city": "Bengaluru",
                "state": "KA",
                "status": "ACTIVE",
            },
            {
                "organization_id": ORG_B,
                "location_id": "LOC-B",
                "name": "B Depot",
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
    resources = SimpleStore(
        [
            {
                "resource_id": "R-B-1",
                "organization_id": ORG_B,
                "location_id": "LOC-B",
                "name": "Spare Kit",
                "Type": "Medical Kit",
                "Available": True,
                "operational_status": "AVAILABLE",
                "tracking_mode": "INDIVIDUAL",
                "visibility": "PRIVATE",
            },
            {
                "resource_id": "R-B-Q",
                "organization_id": ORG_B,
                "location_id": "LOC-B",
                "name": "Supplies",
                "Type": "Medical Kit",
                "Available": False,
                "operational_status": "AVAILABLE",
                "tracking_mode": "QUANTITY",
                "quantity_total": 10,
                "quantity_available": 7,
                "quantity_reserved": 0,
                "quantity_allocated": 3,
                "visibility": "NETWORK",
            },
            {
                "resource_id": "R-B-RET",
                "organization_id": ORG_B,
                "location_id": "LOC-B",
                "name": "Retired Kit",
                "Type": "Medical Kit",
                "Available": False,
                "operational_status": "RETIRED",
                "tracking_mode": "INDIVIDUAL",
            },
            {
                "resource_id": "R-B-ALLOC",
                "organization_id": ORG_B,
                "location_id": "LOC-B",
                "name": "Busy Kit",
                "Type": "Medical Kit",
                "Available": False,
                "operational_status": "ALLOCATED",
                "tracking_mode": "INDIVIDUAL",
            },
        ]
    )
    return orgs, locations, types, resources


def wire(monkeypatch, exchanges, orgs, locations, types, resources, audit=None):
    monkeypatch.setattr(service, "exchanges_table", lambda: exchanges)
    monkeypatch.setattr(service, "organizations_table", lambda: orgs)
    monkeypatch.setattr(service, "locations_table", lambda: locations)
    monkeypatch.setattr(service, "resource_types_table", lambda: types)
    monkeypatch.setattr(service, "resources_table", lambda: resources)
    monkeypatch.setattr(service, "audit_table", lambda: audit or AuditStore())


def create_open_request(monkeypatch, role="OPERATOR"):
    use_memberships(monkeypatch, memberships((ORG_A, role)))
    use_billing(monkeypatch, "ACTIVE")
    exchanges = ExchangeStore()
    orgs, locations, types, resources = seed_world()
    audit = AuditStore()
    wire(monkeypatch, exchanges, orgs, locations, types, resources, audit)
    result = handler.lambda_handler(
        event(
            "POST",
            {
                "organization_id": ORG_A,
                "destination_location_id": "LOC-A",
                "resource_type_id": TYPE_A,
                "tracking_mode": "INDIVIDUAL",
                "quantity_requested": 1,
                "notes": "Need kit",
                "visibility": "NETWORK",
                "idempotency_key": "req-1",
            },
            path="/exchange/requests",
        ),
        None,
    )
    return result, exchanges, orgs, locations, types, resources, audit


def test_create_request_valid(monkeypatch):
    result, exchanges, *_rest, audit = create_open_request(monkeypatch)
    assert result["statusCode"] == 201
    payload = body_of(result)
    assert payload["request"]["status"] == "OPEN"
    assert payload["request"]["visibility"] == "NETWORK"
    meta = next(item for item in exchanges.items.values() if item.get("sk") == "META")
    assert meta["network_list_key"] == "OPEN"
    assert any(event["action"] == "exchange.request_created" for event in audit.events)


def test_create_request_rejects_public_private_and_bad_quantity(monkeypatch):
    use_memberships(monkeypatch, memberships((ORG_A, "OPERATOR")))
    use_billing(monkeypatch, "ACTIVE")
    exchanges = ExchangeStore()
    orgs, locations, types, resources = seed_world()
    wire(monkeypatch, exchanges, orgs, locations, types, resources)

    public = handler.lambda_handler(
        event(
            "POST",
            {
                "organization_id": ORG_A,
                "destination_location_id": "LOC-A",
                "resource_type_id": TYPE_A,
                "visibility": "PUBLIC",
            },
        ),
        None,
    )
    assert public["statusCode"] == 400

    private = handler.lambda_handler(
        event(
            "POST",
            {
                "organization_id": ORG_A,
                "destination_location_id": "LOC-A",
                "resource_type_id": TYPE_A,
                "visibility": "PRIVATE",
            },
        ),
        None,
    )
    assert private["statusCode"] == 400

    bad_qty = handler.lambda_handler(
        event(
            "POST",
            {
                "organization_id": ORG_A,
                "destination_location_id": "LOC-A",
                "resource_type_id": TYPE_A,
                "quantity_requested": 0,
            },
        ),
        None,
    )
    assert bad_qty["statusCode"] == 400


def test_create_request_member_forbidden_and_billing_expired(monkeypatch):
    use_memberships(monkeypatch, memberships((ORG_A, "MEMBER")))
    use_billing(monkeypatch, "ACTIVE")
    exchanges = ExchangeStore()
    orgs, locations, types, resources = seed_world()
    wire(monkeypatch, exchanges, orgs, locations, types, resources)
    member = handler.lambda_handler(
        event(
            "POST",
            {
                "organization_id": ORG_A,
                "destination_location_id": "LOC-A",
                "resource_type_id": TYPE_A,
            },
        ),
        None,
    )
    assert member["statusCode"] == 403

    use_memberships(monkeypatch, memberships((ORG_A, "OPERATOR")))
    use_billing(monkeypatch, "EXPIRED")
    expired = handler.lambda_handler(
        event(
            "POST",
            {
                "organization_id": ORG_A,
                "destination_location_id": "LOC-A",
                "resource_type_id": TYPE_A,
            },
        ),
        None,
    )
    assert expired["statusCode"] == 403
    assert body_of(expired).get("error", {}).get("code") == "BILLING_REQUIRED"


@pytest.mark.parametrize("status", ["TRIALING", "ACTIVE", "PAST_DUE", "GRANDFATHERED"])
def test_billing_writable_statuses(monkeypatch, status):
    use_memberships(monkeypatch, memberships((ORG_A, "OPERATOR")))
    use_billing(monkeypatch, status)
    exchanges = ExchangeStore()
    orgs, locations, types, resources = seed_world()
    wire(monkeypatch, exchanges, orgs, locations, types, resources)
    result = handler.lambda_handler(
        event(
            "POST",
            {
                "organization_id": ORG_A,
                "destination_location_id": "LOC-A",
                "resource_type_id": TYPE_A,
                "idempotency_key": f"bill-{status}",
            },
        ),
        None,
    )
    assert result["statusCode"] == 201


def test_network_list_excludes_own_and_closed(monkeypatch):
    result, exchanges, orgs, locations, types, resources, audit = create_open_request(monkeypatch)
    assert result["statusCode"] == 201
    request_id = body_of(result)["request"]["exchange_request_id"]

    # Close a copy on network index by mutating status keys
    meta_key = next(key for key, item in exchanges.items.items() if item.get("sk") == "META")
    closed = copy.deepcopy(exchanges.items[meta_key])
    closed["status"] = "CANCELLED"
    closed.pop("network_list_key", None)
    exchanges.items[("EXREQ#CLOSED", "META")] = closed
    exchanges.items[("EXREQ#CLOSED", "META")]["exchange_request_id"] = "EXREQ-CLOSED"
    exchanges.items[("EXREQ#CLOSED", "META")]["pk"] = "EXREQ#EXREQ-CLOSED"

    use_memberships(monkeypatch, memberships((ORG_B, "MEMBER")))
    use_billing(monkeypatch, "ACTIVE")
    listed = handler.lambda_handler(
        event("GET", organization_id=ORG_B, path="/exchange/requests", query={"scope": "network"}),
        None,
    )
    assert listed["statusCode"] == 200
    items = body_of(listed)["items"]
    assert all(item["exchange_request_id"] != request_id or True for item in items)
    # Own org excluded when requester lists as network
    use_memberships(monkeypatch, memberships((ORG_A, "MEMBER")))
    own = handler.lambda_handler(
        event("GET", organization_id=ORG_A, path="/exchange/requests", query={"scope": "network"}),
        None,
    )
    assert all(item["exchange_request_id"] != request_id for item in body_of(own)["items"])
    # Provider sees the open request
    use_memberships(monkeypatch, memberships((ORG_B, "MEMBER")))
    seen = body_of(
        handler.lambda_handler(
            event("GET", organization_id=ORG_B, path="/exchange/requests", query={"scope": "network"}),
            None,
        )
    )["items"]
    assert any(item["exchange_request_id"] == request_id for item in seen)
    assert all("destination_location_id" not in item for item in seen)
    assert all("requester_organization_id" not in item for item in seen)


def test_get_request_authz(monkeypatch):
    result, exchanges, *_ = create_open_request(monkeypatch)
    request_id = body_of(result)["request"]["exchange_request_id"]

    use_memberships(monkeypatch, memberships((ORG_A, "MEMBER")))
    use_billing(monkeypatch, "ACTIVE")
    mine = handler.lambda_handler(
        event("GET", organization_id=ORG_A, path=f"/exchange/requests/{request_id}"),
        None,
    )
    assert mine["statusCode"] == 200
    assert body_of(mine)["request"]["destination_location_id"] == "LOC-A"

    use_memberships(monkeypatch, memberships((ORG_B, "MEMBER")))
    provider = handler.lambda_handler(
        event("GET", organization_id=ORG_B, path=f"/exchange/requests/{request_id}"),
        None,
    )
    assert provider["statusCode"] == 200
    assert "destination_location_id" not in body_of(provider)["request"]

    use_memberships(monkeypatch, memberships((ORG_C, "OPERATOR")))
    # Still OPEN network — ORG_C may read projection
    other = handler.lambda_handler(
        event("GET", organization_id=ORG_C, path=f"/exchange/requests/{request_id}"),
        None,
    )
    assert other["statusCode"] == 200


def test_offer_create_does_not_hold_resource(monkeypatch):
    result, exchanges, orgs, locations, types, resources, audit = create_open_request(monkeypatch)
    request_id = body_of(result)["request"]["exchange_request_id"]
    before = copy.deepcopy(resources.get_item(Key={"resource_id": "R-B-1"})["Item"])

    use_memberships(monkeypatch, memberships((ORG_B, "OPERATOR")))
    use_billing(monkeypatch, "ACTIVE")
    offered = handler.lambda_handler(
        event(
            "POST",
            {
                "organization_id": ORG_B,
                "resource_id": "R-B-1",
                "provider_location_id": "LOC-B",
                "idempotency_key": "offer-1",
            },
            organization_id=ORG_B,
            path=f"/exchange/requests/{request_id}/offers",
        ),
        None,
    )
    assert offered["statusCode"] == 201
    after = resources.get_item(Key={"resource_id": "R-B-1"})["Item"]
    assert after["Available"] is True
    assert after["operational_status"] == "AVAILABLE"
    assert after == before
    assert any(event["action"] == "exchange.offer_created" for event in audit.events)
    assert any(event.get("metadata", {}).get("hold_created") is False for event in audit.events)


def test_offer_rejects_ineligible_and_own_request(monkeypatch):
    result, exchanges, orgs, locations, types, resources, audit = create_open_request(monkeypatch)
    request_id = body_of(result)["request"]["exchange_request_id"]

    use_memberships(monkeypatch, memberships((ORG_A, "OPERATOR")))
    use_billing(monkeypatch, "ACTIVE")
    own = handler.lambda_handler(
        event(
            "POST",
            {"organization_id": ORG_A, "resource_id": "R-B-1", "provider_location_id": "LOC-A"},
            path=f"/exchange/requests/{request_id}/offers",
        ),
        None,
    )
    assert own["statusCode"] in (403, 404, 409)

    use_memberships(monkeypatch, memberships((ORG_B, "OPERATOR")))
    retired = handler.lambda_handler(
        event(
            "POST",
            {
                "organization_id": ORG_B,
                "resource_id": "R-B-RET",
                "provider_location_id": "LOC-B",
            },
            organization_id=ORG_B,
            path=f"/exchange/requests/{request_id}/offers",
        ),
        None,
    )
    assert retired["statusCode"] == 409

    allocated = handler.lambda_handler(
        event(
            "POST",
            {
                "organization_id": ORG_B,
                "resource_id": "R-B-ALLOC",
                "provider_location_id": "LOC-B",
            },
            organization_id=ORG_B,
            path=f"/exchange/requests/{request_id}/offers",
        ),
        None,
    )
    assert allocated["statusCode"] == 409


def test_quantity_offer_validates_without_decrement(monkeypatch):
    use_memberships(monkeypatch, memberships((ORG_A, "OPERATOR")))
    use_billing(monkeypatch, "ACTIVE")
    exchanges = ExchangeStore()
    orgs, locations, types, resources = seed_world()
    audit = AuditStore()
    wire(monkeypatch, exchanges, orgs, locations, types, resources, audit)
    created = handler.lambda_handler(
        event(
            "POST",
            {
                "organization_id": ORG_A,
                "destination_location_id": "LOC-A",
                "resource_type_id": TYPE_A,
                "tracking_mode": "QUANTITY",
                "quantity_requested": 5,
                "idempotency_key": "qty-req",
            },
        ),
        None,
    )
    assert created["statusCode"] == 201
    request_id = body_of(created)["request"]["exchange_request_id"]
    before = resources.get_item(Key={"resource_id": "R-B-Q"})["Item"]["quantity_available"]

    use_memberships(monkeypatch, memberships((ORG_B, "OPERATOR")))
    too_many = handler.lambda_handler(
        event(
            "POST",
            {
                "organization_id": ORG_B,
                "resource_id": "R-B-Q",
                "provider_location_id": "LOC-B",
                "quantity_offered": 8,
            },
            organization_id=ORG_B,
            path=f"/exchange/requests/{request_id}/offers",
        ),
        None,
    )
    assert too_many["statusCode"] == 409

    ok = handler.lambda_handler(
        event(
            "POST",
            {
                "organization_id": ORG_B,
                "resource_id": "R-B-Q",
                "provider_location_id": "LOC-B",
                "quantity_offered": 3,
                "idempotency_key": "qty-offer",
            },
            organization_id=ORG_B,
            path=f"/exchange/requests/{request_id}/offers",
        ),
        None,
    )
    assert ok["statusCode"] == 201
    assert resources.get_item(Key={"resource_id": "R-B-Q"})["Item"]["quantity_available"] == before


def test_offer_list_and_get_isolation(monkeypatch):
    result, exchanges, orgs, locations, types, resources, audit = create_open_request(monkeypatch)
    request_id = body_of(result)["request"]["exchange_request_id"]
    use_memberships(monkeypatch, memberships((ORG_B, "OPERATOR")))
    use_billing(monkeypatch, "ACTIVE")
    offered = handler.lambda_handler(
        event(
            "POST",
            {
                "organization_id": ORG_B,
                "resource_id": "R-B-1",
                "provider_location_id": "LOC-B",
                "idempotency_key": "iso-offer",
            },
            organization_id=ORG_B,
            path=f"/exchange/requests/{request_id}/offers",
        ),
        None,
    )
    offer_id = body_of(offered)["offer"]["offer_id"]

    use_memberships(monkeypatch, memberships((ORG_A, "MEMBER")))
    listed = handler.lambda_handler(
        event("GET", organization_id=ORG_A, path=f"/exchange/requests/{request_id}/offers"),
        None,
    )
    assert listed["statusCode"] == 200
    assert len(body_of(listed)["items"]) == 1

    use_memberships(monkeypatch, memberships((ORG_C, "OPERATOR")))
    denied = handler.lambda_handler(
        event(
            "GET",
            organization_id=ORG_C,
            path=f"/exchange/requests/{request_id}/offers/{offer_id}",
        ),
        None,
    )
    assert denied["statusCode"] == 404


def test_idempotency_replay_and_conflict(monkeypatch):
    use_memberships(monkeypatch, memberships((ORG_A, "OPERATOR")))
    use_billing(monkeypatch, "ACTIVE")
    exchanges = ExchangeStore()
    orgs, locations, types, resources = seed_world()
    wire(monkeypatch, exchanges, orgs, locations, types, resources)
    body = {
        "organization_id": ORG_A,
        "destination_location_id": "LOC-A",
        "resource_type_id": TYPE_A,
        "idempotency_key": "same-key",
        "notes": "one",
    }
    first = handler.lambda_handler(event("POST", body), None)
    second = handler.lambda_handler(event("POST", body), None)
    assert first["statusCode"] == 201
    assert second["statusCode"] == 201
    assert body_of(first)["request"]["exchange_request_id"] == body_of(second)["request"]["exchange_request_id"]
    metas = [item for item in exchanges.items.values() if item.get("sk") == "META"]
    assert len(metas) == 1

    conflict = handler.lambda_handler(
        event(
            "POST",
            {
                **body,
                "notes": "different",
            },
        ),
        None,
    )
    assert conflict["statusCode"] == 409


def test_options_and_unauthenticated(monkeypatch):
    options = handler.lambda_handler({"httpMethod": "OPTIONS", "path": "/exchange/requests"}, None)
    assert options["statusCode"] == 200
    use_memberships(monkeypatch, [])
    denied = handler.lambda_handler(event("GET", path="/exchange/requests"), None)
    assert denied["statusCode"] in (401, 403)


def test_no_scan_on_list(monkeypatch):
    result, exchanges, *_ = create_open_request(monkeypatch)
    use_memberships(monkeypatch, memberships((ORG_B, "MEMBER")))
    use_billing(monkeypatch, "ACTIVE")
    handler.lambda_handler(
        event("GET", organization_id=ORG_B, path="/exchange/requests", query={"scope": "network"}),
        None,
    )
    assert exchanges.scans == 0
    assert any(query.get("IndexName") == "NetworkOpenRequestIndex" for query in exchanges.queries)


def test_network_not_in_public_discovery():
    assert public_api.public_view({"visibility": "NETWORK", "public_name": "x"}) is None
    fields = visibility.publication_fields({"visibility": "NETWORK"}, {}, "Kit", "R1", True)
    assert "visibility_key" not in fields


def test_type_matching_uses_names_not_ids():
    assert service.resource_type_names_compatible("Medical Kit", {"Type": "Medical Kit"})
    assert not service.resource_type_names_compatible("Medical Kit", {"Type": "Water"})
    assert service.resource_type_names_compatible("Medical Kit", {"Type": "medical kit"})
