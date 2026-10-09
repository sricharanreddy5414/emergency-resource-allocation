"""RM-13B: emergency claim commits resource, allocation, and request together."""

import copy
import importlib.util
import inspect
import json
import sys
from pathlib import Path

from boto3.dynamodb.types import TypeSerializer
from botocore.exceptions import ClientError

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


allocation = load_module("allocation_service_rm13b", "src/allocation/service.py")

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

    def query(self, **kwargs):
        return {"Items": [dict(item) for item in self.items]}

    def get_item(self, Key):
        for item in self.items:
            if all(item.get(key) == value for key, value in Key.items()):
                return {"Item": dict(item)}
        return {}

    def put_item(self, Item, ConditionExpression=None):
        self.puts.append(dict(Item))
        self.items.append(dict(Item))

    def update_item(self, **kwargs):
        raise AssertionError("emergency claim must not write outside the transaction")


class Events:
    def __init__(self):
        self.calls = []

    def put_events(self, **kwargs):
        self.calls.append(kwargs)


def location():
    return {
        "organization_id": ORG,
        "location_id": "LOC-A",
        "name": "North",
        "status": "ACTIVE",
    }


def request_row(request_id="Q1", status="PENDING", organization_id=ORG, priority=1):
    return {
        "request_id": request_id,
        "organization_id": organization_id,
        "location_id": "LOC-A",
        "Status": status,
        "ResourceType": "ICU_BED",
        "Priority": priority,
    }


def resource_row(resource_id="R1", organization_id=ORG, available=True):
    return {
        "resource_id": resource_id,
        "organization_id": organization_id,
        "location_id": "LOC-A",
        "Type": "ICU_BED",
        "Available": available,
        "operational_status": "AVAILABLE" if available else "ALLOCATED",
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


def claim_body(request_id="Q1"):
    return {
        "request_id": request_id,
        "resource_type": "ICU_BED",
        "location_id": "LOC-A",
        "priority": 1,
        "organization_id": ORG,
        "confirm": True,
        "resource_id": "R1",
    }


class World:
    def __init__(self, resources, requests, allocations=None):
        self.resources = Store(resources)
        self.requests = Store(requests)
        self.allocations = Store(allocations or [])
        self.history = Store()
        self.audit = Store()
        self.events = Events()
        self.transact_calls = []

    def bind(self, monkeypatch):
        use_memberships(monkeypatch)
        monkeypatch.setattr(allocation, "locations_table", lambda: Store([location()]))
        monkeypatch.setattr(allocation, "resources_table", lambda: self.resources)
        monkeypatch.setattr(allocation, "requests_table", lambda: self.requests)
        monkeypatch.setattr(allocation, "allocations_table", lambda: self.allocations)
        monkeypatch.setattr(allocation, "history_table", lambda: self.history)
        monkeypatch.setattr(allocation, "audit_table", lambda: self.audit)
        monkeypatch.setattr(allocation, "events_client", lambda: self.events)
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

    def allocate(self, request_id="Q1"):
        result = allocation.lambda_handler(event(claim_body(request_id)), None)
        return result["statusCode"], json.loads(result["body"])


def test_resource_condition_failure_writes_nothing(monkeypatch):
    world = World([resource_row(available=False)], [request_row()])
    seen = copy.deepcopy(world.resources.items)
    world.resources.query = lambda **kwargs: {
        "Items": [dict(resource_row(available=True))]
    }
    world.bind(monkeypatch)

    status, body = world.allocate()

    assert status == 404
    assert body["message"] == "No suitable resource available"
    assert world.resources.items == seen
    assert world.allocations.items == []
    assert world.requests.items[0]["Status"] == "PENDING"
    assert world.history.puts == []
    assert world.audit.puts == []
    assert world.events.calls == []


def test_allocation_condition_failure_leaves_resource_and_request(monkeypatch):
    existing = {
        "allocation_id": "ALLOC-Q1",
        "request_id": "Q1",
        "resource_id": "OTHER",
        "organization_id": ORG,
        "status": "ALLOCATED",
    }
    world = World([resource_row()], [request_row()], [existing])
    world.bind(monkeypatch)

    status, body = world.allocate()

    assert status == 409
    assert body["message"] == "Request is not eligible for allocation"
    assert world.resources.items[0]["Available"] is True
    assert world.resources.items[0]["operational_status"] == "AVAILABLE"
    assert world.requests.items[0]["Status"] == "PENDING"
    assert world.allocations.items == [existing]
    assert world.history.puts == []


def test_request_condition_failure_creates_no_allocation(monkeypatch):
    world = World([resource_row()], [request_row(status="RELEASED")])
    world.requests.get_item = lambda Key: {"Item": dict(request_row(status="PENDING"))}
    world.requests.query = lambda **kwargs: {"Items": [dict(request_row(status="PENDING"))]}
    world.bind(monkeypatch)

    status, body = world.allocate()

    assert status == 409
    assert body["message"] == "Request is not eligible for allocation"
    assert world.resources.items[0]["Available"] is True
    assert world.resources.items[0]["operational_status"] == "AVAILABLE"
    assert world.allocations.items == []
    assert world.requests.items[0]["Status"] == "RELEASED"
    assert world.history.puts == []


def test_successful_claim_commits_all_three(monkeypatch):
    world = World([resource_row()], [request_row()])
    world.bind(monkeypatch)

    status, body = world.allocate()

    assert status == 200
    assert body["allocation_id"] == "ALLOC-Q1"
    assert body["status"] == "ALLOCATED"
    assert world.resources.items[0]["Available"] is False
    assert world.resources.items[0]["operational_status"] == "ALLOCATED"
    assert world.requests.items[0]["Status"] == "ALLOCATED"
    assert len(world.allocations.items) == 1
    created = world.allocations.items[0]
    assert created["allocation_id"] == "ALLOC-Q1"
    assert created["request_id"] == "Q1"
    assert created["resource_id"] == "R1"
    assert created["organization_id"] == ORG
    assert created["status"] == "ALLOCATED"
    assert created["location_id"] == "LOC-A"
    assert world.history.puts[0]["reason"] == "RESOURCE_ALLOCATED"
    assert world.history.puts[0]["allocation_id"] == "ALLOC-Q1"
    assert world.audit.puts[0]["action"] == "allocation.create"
    assert len(world.events.calls) == 1
    assert [next(iter(item)) for item in world.transact_calls[0]] == ["Update", "Put", "Update"]


def test_retry_after_failed_transaction_has_no_partial_state(monkeypatch):
    existing = {
        "allocation_id": "ALLOC-Q1",
        "request_id": "Q1",
        "organization_id": ORG,
        "status": "ALLOCATED",
    }
    world = World([resource_row()], [request_row()], [existing])
    world.bind(monkeypatch)

    failed, _body = world.allocate()
    assert failed == 409
    assert world.resources.items[0]["Available"] is True
    assert world.requests.items[0]["Status"] == "PENDING"

    world.allocations.items.clear()
    status, body = world.allocate()

    assert status == 200
    assert body["allocation_id"] == "ALLOC-Q1"
    assert len(world.allocations.items) == 1
    assert world.resources.items[0]["operational_status"] == "ALLOCATED"
    assert world.requests.items[0]["Status"] == "ALLOCATED"
    assert len(world.history.puts) == 1


def test_concurrent_claim_allows_one_allocation(monkeypatch):
    world = World(
        [resource_row()],
        [request_row("Q1", priority=1), request_row("Q2", priority=2)],
    )
    world.bind(monkeypatch)

    first, first_body = world.allocate("Q1")
    second, second_body = world.allocate("Q1")

    assert first == 200
    assert first_body["resource_id"] == "R1"
    assert second == 409
    assert second_body["message"] == "Request is not eligible for allocation"
    assert len(world.allocations.items) == 1
    assert world.resources.items[0]["operational_status"] == "ALLOCATED"

    rival = World([resource_row()], [request_row("Q1", priority=1), request_row("Q2", priority=2)])
    rival.bind(monkeypatch)
    status, body = rival.allocate("Q2")

    assert status == 200
    assert body["request_id"] == "Q2"
    assert len(rival.allocations.items) == 1
    assert rival.allocations.items[0]["request_id"] == "Q2"
    assert rival.requests.items[0]["Status"] == "PENDING"
    assert rival.requests.items[1]["Status"] == "ALLOCATED"


def test_claim_stays_inside_the_organization(monkeypatch):
    foreign_request = World([resource_row()], [request_row(organization_id=OTHER)])
    foreign_request.bind(monkeypatch)
    status, body = foreign_request.allocate()
    assert status == 404
    assert body["message"] == "Record not found"
    assert foreign_request.allocations.items == []
    assert foreign_request.resources.items[0]["Available"] is True

    foreign_resource = World([resource_row(organization_id=OTHER)], [request_row()])
    before = copy.deepcopy(foreign_resource.resources.items)
    foreign_resource.bind(monkeypatch)
    status, body = foreign_resource.allocate()
    assert status == 404
    assert foreign_resource.resources.items == before
    assert foreign_resource.allocations.items == []
    assert foreign_resource.requests.items[0]["Status"] == "PENDING"

    rows = [resource_row(organization_id=OTHER)]
    requests = [request_row()]
    allocations = []
    try:
        apply_transact(
            {"Resources": rows, "Allocations": allocations, "EmergencyRequests": requests},
            allocation._claim_transact_items("R1", ORG, {"allocation_id": "ALLOC-Q1", "organization_id": ORG, "status": "ALLOCATED", "request_id": "Q1", "resource_id": "R1"}, "Q1"),
        )
    except ClientError as error:
        assert error.response["Error"]["Code"] == "TransactionCanceledException"
    else:
        raise AssertionError("cross-tenant claim must fail")
    assert rows[0]["organization_id"] == OTHER
    assert rows[0]["Available"] is True
    assert allocations == []
    assert requests[0]["Status"] == "PENDING"


class _HighLevelResourceClient:
    def __init__(self):
        self.calls = []

    def transact_write_items(self, TransactItems):
        self.calls.append(TransactItems)
        serializer = TypeSerializer()
        for step in TransactItems:
            body = step.get("Update") or step.get("Put") or {}
            values = list((body.get("Key") or {}).values())
            item = body.get("Item") or {}
            if "allocation_id" in item:
                values.append(item["allocation_id"])
            if "request_id" in item:
                values.append(item["request_id"])
            for value in values:
                if isinstance(value, dict) and "M" in serializer.serialize(value):
                    raise ClientError(
                        {
                            "Error": {"Code": "TransactionCanceledException", "Message": "Transaction cancelled"},
                            "CancellationReasons": [
                                {"Code": "ValidationError", "Message": "The provided key element does not match the schema"},
                                {"Code": "None"},
                                {"Code": "None"},
                            ],
                        },
                        "TransactWriteItems",
                    )


def _string_key(value):
    assert list(value) == ["S"]
    assert isinstance(value["S"], str) and value["S"]
    return value["S"]


def test_emergency_claim_uses_the_low_level_client_once(monkeypatch):
    commit_source = inspect.getsource(allocation._commit_emergency_claim)
    client_source = inspect.getsource(allocation._dynamodb_client)
    assert "_transact_write" in commit_source
    assert "meta" not in commit_source
    assert 'boto3.client("dynamodb")' in client_source
    assert "_dynamodb_client()" in inspect.getsource(allocation._transact_write)

    high = _HighLevelResourceClient()
    world = World([resource_row()], [request_row()])
    world.resources.meta = type("Meta", (), {"client": high})()
    recorded = []

    def _transact(items):
        recorded.append(copy.deepcopy(items))
        apply_transact(
            {
                "Resources": world.resources.items,
                "Allocations": world.allocations.items,
                "EmergencyRequests": world.requests.items,
            },
            items,
        )

    world.bind(monkeypatch)
    monkeypatch.setattr(allocation, "_transact_write", _transact)

    status, _body = world.allocate()

    assert status == 200
    assert high.calls == []
    assert len(recorded) == 1
    resource_update, allocation_put, request_update = recorded[0]
    assert resource_update["Update"]["TableName"] == "Resources"
    assert resource_update["Update"]["UpdateExpression"] == "SET #a = :false, operational_status = :op_allocated"
    assert resource_update["Update"]["ConditionExpression"] == EMERGENCY_CLAIM_CONDITION
    assert _string_key(resource_update["Update"]["Key"]["resource_id"]) == "R1"
    assert allocation_put["Put"]["TableName"] == "Allocations"
    assert allocation_put["Put"]["ConditionExpression"] == "attribute_not_exists(allocation_id)"
    assert _string_key(allocation_put["Put"]["Item"]["allocation_id"]) == "ALLOC-Q1"
    assert request_update["Update"]["TableName"] == "EmergencyRequests"
    assert request_update["Update"]["UpdateExpression"] == "SET #s = :status"
    assert request_update["Update"]["ConditionExpression"] == "#s = :pending AND organization_id = :organization_id"
    assert _string_key(request_update["Update"]["Key"]["request_id"]) == "Q1"
