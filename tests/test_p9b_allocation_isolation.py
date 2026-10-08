"""P9-02: POST /allocate targets only the submitted request."""

import copy
import importlib.util
import inspect
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [
    str(ROOT / "src" / "shared"),
    str(ROOT / "src" / "organization"),
]

import access
from resource_state import EMERGENCY_CLAIM_CONDITION
from transact_memory import apply_transact


def load_module(name, relative):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


allocation = load_module("allocation_service_p9b", "src/allocation/service.py")

ORG = "ORG-A"
OTHER = "ORG-B"
USER = "operator-sub"


def use_memberships(monkeypatch):
    monkeypatch.setattr(
        access,
        "list_memberships",
        lambda *args, **kwargs: [
            {
                "organization_id": ORG,
                "name": ORG,
                "role": "OPERATOR",
                "status": "ACTIVE",
            }
        ],
    )


class Store:
    def __init__(self, items=None):
        self.items = [dict(item) for item in (items or [])]
        self.puts = []
        self.queries = 0

    def query(self, **kwargs):
        self.queries += 1
        return {"Items": [dict(item) for item in self.items]}

    def get_item(self, Key):
        for item in self.items:
            if all(item.get(key) == value for key, value in Key.items()):
                return {"Item": dict(item)}
        return {}

    def put_item(self, Item, ConditionExpression=None):
        self.puts.append(dict(Item))
        self.items.append(dict(Item))


class Events:
    def put_events(self, **kwargs):
        return {}


def location():
    return {
        "organization_id": ORG,
        "location_id": "LOC-A",
        "name": "North",
        "status": "ACTIVE",
    }


def request_row(request_id="Q-A", status="PENDING", organization_id=ORG, priority=1, resource_type="ICU_BED"):
    return {
        "request_id": request_id,
        "organization_id": organization_id,
        "location_id": "LOC-A",
        "Status": status,
        "ResourceType": resource_type,
        "Priority": priority,
    }


def resource_row(resource_id="R1", resource_type="ICU_BED"):
    return {
        "resource_id": resource_id,
        "organization_id": ORG,
        "location_id": "LOC-A",
        "Type": resource_type,
        "Available": True,
        "operational_status": "AVAILABLE",
        "tracking_mode": "INDIVIDUAL",
    }


def event(body):
    return {
        "httpMethod": "POST",
        "path": "/allocate",
        "queryStringParameters": {"organization_id": ORG},
        "requestContext": {"authorizer": {"claims": {"sub": USER}}},
        "body": json.dumps(body),
    }


def claim_body(request_id="Q-A", **extra):
    body = {
        "request_id": request_id,
        "resource_type": "ICU_BED",
        "location_id": "LOC-A",
        "priority": 1,
        "organization_id": ORG,
    }
    body.update(extra)
    return body


class World:
    def __init__(self, resources, requests):
        self.resources = Store(resources)
        self.requests = Store(requests)
        self.allocations = Store()
        self.history = Store()
        self.audit = Store()
        self.transact_calls = []

    def bind(self, monkeypatch):
        use_memberships(monkeypatch)
        monkeypatch.setattr(allocation, "locations_table", lambda: Store([location()]))
        monkeypatch.setattr(allocation, "resources_table", lambda: self.resources)
        monkeypatch.setattr(allocation, "requests_table", lambda: self.requests)
        monkeypatch.setattr(allocation, "allocations_table", lambda: self.allocations)
        monkeypatch.setattr(allocation, "history_table", lambda: self.history)
        monkeypatch.setattr(allocation, "audit_table", lambda: self.audit)
        monkeypatch.setattr(allocation, "events_client", lambda: Events())
        monkeypatch.setattr(allocation, "request_types_table", lambda: Store())

        def _transact(items):
            self.transact_calls.append(copy.deepcopy(items))
            apply_transact(
                {
                    "Resources": self.resources.items,
                    "Allocations": self.allocations.items,
                    "EmergencyRequests": self.requests.items,
                },
                items,
            )

        monkeypatch.setattr(allocation, "_transact_write", _transact)

    def allocate(self, body):
        result = allocation.lambda_handler(event(body), None)
        return result["statusCode"], json.loads(result["body"])


def test_allocate_reads_only_the_submitted_request():
    source = inspect.getsource(allocation.allocate)
    assert "query_by_organization" not in source
    assert "requests_table().put_item" not in source
    assert "sort_requests_by_priority" not in source
    assert "_claim_transact_items" in source


def test_valid_pending_request_allocates_once(monkeypatch):
    world = World([resource_row()], [request_row(), request_row("Q-B", priority=1)])
    world.bind(monkeypatch)

    status, body = world.allocate(claim_body())

    assert status == 200
    assert body["request_id"] == "Q-A"
    assert body["allocation_id"] == "ALLOC-Q-A"
    assert body["status"] == "ALLOCATED"
    assert world.requests.queries == 0
    assert world.requests.puts == []
    assert [item["request_id"] for item in world.allocations.items] == ["Q-A"]
    assert world.requests.items[0]["Status"] == "ALLOCATED"
    assert world.requests.items[1]["Status"] == "PENDING"
    assert len(world.transact_calls) == 1
    resource_update, allocation_put, request_update = world.transact_calls[0]
    assert resource_update["Update"]["ConditionExpression"] == EMERGENCY_CLAIM_CONDITION
    assert allocation_put["Put"]["ConditionExpression"] == "attribute_not_exists(allocation_id)"
    assert request_update["Update"]["ConditionExpression"] == "#s = :pending AND organization_id = :organization_id"
    assert request_update["Update"]["Key"]["request_id"]["S"] == "Q-A"


def test_missing_request_id_is_rejected(monkeypatch):
    world = World([resource_row()], [request_row()])
    before = copy.deepcopy(world.requests.items)
    world.bind(monkeypatch)

    status, body = world.allocate(claim_body(request_id=""))

    assert status == 400
    assert body["message"] == "request_id, resource_type and location_id are required"
    assert world.requests.items == before
    assert world.requests.puts == []
    assert world.allocations.items == []
    assert world.transact_calls == []
    assert world.resources.items[0]["Available"] is True


def test_nonexistent_request_is_not_created(monkeypatch):
    world = World([resource_row()], [request_row("Q-B")])
    world.bind(monkeypatch)

    status, body = world.allocate(claim_body("Q-MISSING"))

    assert status == 404
    assert body["message"] == "Record not found"
    assert [item["request_id"] for item in world.requests.items] == ["Q-B"]
    assert world.requests.puts == []
    assert world.requests.items[0]["Status"] == "PENDING"
    assert world.allocations.items == []
    assert world.transact_calls == []
    assert world.resources.items[0]["Available"] is True


def test_other_organization_request_cannot_be_allocated(monkeypatch):
    world = World([resource_row()], [request_row(organization_id=OTHER)])
    world.bind(monkeypatch)

    status, body = world.allocate(claim_body())

    assert status == 404
    assert body["message"] == "Record not found"
    assert world.requests.items[0]["organization_id"] == OTHER
    assert world.requests.items[0]["Status"] == "PENDING"
    assert world.allocations.items == []
    assert world.resources.items[0]["Available"] is True


def test_allocating_one_pending_request_leaves_the_other_pending(monkeypatch):
    world = World(
        [resource_row()],
        [request_row("Q-A", priority=5), request_row("Q-B", priority=1)],
    )
    world.bind(monkeypatch)

    status, body = world.allocate(claim_body("Q-A"))

    assert status == 200
    assert body["request_id"] == "Q-A"
    assert world.requests.queries == 0
    assert [item["request_id"] for item in world.allocations.items] == ["Q-A"]
    assert world.requests.items[0]["Status"] == "ALLOCATED"
    assert world.requests.items[1]["Status"] == "PENDING"
    assert world.requests.items[1]["Priority"] == 1


def test_no_match_leaves_every_request_unchanged(monkeypatch):
    world = World(
        [resource_row(resource_type="GENERAL_BED")],
        [request_row("Q-A"), request_row("Q-B", priority=1)],
    )
    before_requests = copy.deepcopy(world.requests.items)
    before_resources = copy.deepcopy(world.resources.items)
    world.bind(monkeypatch)

    status, body = world.allocate(claim_body("Q-A"))

    assert status == 404
    assert body["message"] == "No suitable resource available"
    assert world.requests.items == before_requests
    assert world.resources.items == before_resources
    assert world.allocations.items == []
    assert world.transact_calls == []


def test_repeated_allocation_does_not_claim_another_resource(monkeypatch):
    world = World([resource_row(), resource_row("R2")], [request_row()])
    world.bind(monkeypatch)

    first, first_body = world.allocate(claim_body())
    second, second_body = world.allocate(claim_body())

    assert first == 200
    assert first_body["allocation_id"] == "ALLOC-Q-A"
    assert second == 409
    assert second_body["message"] == "Request is not eligible for allocation"
    assert len(world.transact_calls) == 1
    assert [item["allocation_id"] for item in world.allocations.items] == ["ALLOC-Q-A"]
    assert world.resources.items[0]["operational_status"] == "ALLOCATED"
    assert world.resources.items[1]["Available"] is True
    assert world.requests.items[0]["Status"] == "ALLOCATED"


def test_released_request_is_not_allocated(monkeypatch):
    world = World([resource_row()], [request_row(status="RELEASED"), request_row("Q-B")])
    world.bind(monkeypatch)

    status, body = world.allocate(claim_body())

    assert status == 409
    assert body["message"] == "Request is not eligible for allocation"
    assert world.transact_calls == []
    assert world.requests.items[0]["Status"] == "RELEASED"
    assert world.requests.items[1]["Status"] == "PENDING"
    assert world.resources.items[0]["Available"] is True


def test_request_conflict_inside_the_transaction_writes_nothing(monkeypatch):
    world = World([resource_row()], [request_row()])
    world.requests.get_item = lambda Key: {"Item": dict(request_row())}
    world.bind(monkeypatch)
    world.requests.items[0]["Status"] = "ALLOCATED"

    status, body = world.allocate(claim_body())

    assert status == 409
    assert body["message"] == "Request is not eligible for allocation"
    assert world.allocations.items == []
    assert world.resources.items[0]["Available"] is True
    assert world.requests.items[0]["Status"] == "ALLOCATED"
