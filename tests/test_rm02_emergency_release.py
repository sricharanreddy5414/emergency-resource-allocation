"""RM-02: emergency release commits resource, allocation, and request together."""

import importlib.util
import inspect
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [
    str(ROOT / "src"),
    str(ROOT / "src" / "shared"),
    str(ROOT / "src" / "organization"),
]

import access
import emergency_release
from transact_memory import apply_transact


def load_module(name, relative):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


resource_handler = load_module("resource_handler_rm02", "src/resource/handler.py")
auto_release = load_module("auto_release_rm02", "src/auto_release/handler.py")

ORG = "ORG-A"
USER = "operator-sub"
NOW = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)


class MemoryTable:
    def __init__(self, rows=None):
        self.rows = [dict(row) for row in (rows or [])]
        self.puts = []
        self.after_get = None
        self.after_query = None

    def get_item(self, Key):
        for row in self.rows:
            if all(row.get(key) == value for key, value in Key.items()):
                snapshot = dict(row)
                if self.after_get:
                    callback = self.after_get
                    self.after_get = None
                    callback()
                return {"Item": snapshot}
        return {}

    def query(self, **kwargs):
        selected = list(self.rows)
        if kwargs.get("IndexName") == "AllocationStatusIndex":
            values = kwargs.get("ExpressionAttributeValues") or {}
            status = values.get(":allocated")
            cutoff = values.get(":cutoff")
            selected = [
                row
                for row in self.rows
                if (status is None or row.get("status") == status)
                and (cutoff is None or row.get("allocated_at", "") < cutoff)
            ]
        snapshots = [dict(row) for row in selected]
        if self.after_query:
            callback = self.after_query
            self.after_query = None
            callback()
        return {"Items": snapshots}

    def put_item(self, Item, ConditionExpression=None):
        self.puts.append(dict(Item))
        self.rows.append(dict(Item))

    def update_item(self, **kwargs):
        raise AssertionError("emergency release must use TransactWriteItems")


class AuditLog:
    def __init__(self):
        self.events = []

    def put_item(self, Item):
        self.events.append(dict(Item))


class Events:
    def put_events(self, **kwargs):
        return {}


def _resource(resource_id="R1", **extra):
    item = {
        "resource_id": resource_id,
        "organization_id": ORG,
        "Available": False,
        "operational_status": "ALLOCATED",
        "location_id": "LOC1",
        "quantity_available": 4,
    }
    item.update(extra)
    return item


def _allocation(allocation_id="ALLOC-Q1", resource_id="R1", request_id="Q1", **extra):
    item = {
        "allocation_id": allocation_id,
        "request_id": request_id,
        "resource_id": resource_id,
        "organization_id": ORG,
        "location_id": "LOC1",
        "status": "ALLOCATED",
        "allocated_at": (NOW - timedelta(hours=2)).isoformat(),
    }
    item.update(extra)
    return item


def _request(request_id="Q1", **extra):
    item = {
        "request_id": request_id,
        "organization_id": ORG,
        "Status": "ALLOCATED",
    }
    item.update(extra)
    return item


def _wire(monkeypatch, resources, allocations, requests, history=None, audit=None):
    history = history or MemoryTable()
    audit = audit or AuditLog()
    monkeypatch.setattr(resource_handler, "resources_table", lambda: resources)
    monkeypatch.setattr(resource_handler, "allocations_table", lambda: allocations)
    monkeypatch.setattr(resource_handler, "requests_table", lambda: requests)
    monkeypatch.setattr(resource_handler, "history_table", lambda: history)
    monkeypatch.setattr(resource_handler, "audit_table", lambda: audit)
    monkeypatch.setattr(resource_handler, "events_client", lambda: Events())
    monkeypatch.setattr(auto_release, "events_client", lambda: Events())
    monkeypatch.setattr(access, "list_memberships", lambda *args, **kwargs: [
        {
            "organization_id": ORG,
            "name": ORG,
            "role": "OPERATOR",
            "status": "ACTIVE",
        }
    ])
    seen = []

    def _transact(items):
        seen.append(items)
        apply_transact(
            {
                "Resources": resources.rows,
                "Allocations": allocations.rows,
                "EmergencyRequests": requests.rows,
            },
            items,
        )

    monkeypatch.setattr(emergency_release, "_transact_write", _transact)
    return history, audit, seen


def _release(resource_id="R1", headers=None):
    event = {
        "httpMethod": "POST",
        "path": "/allocate/resources/release",
        "queryStringParameters": {"organization_id": ORG},
        "requestContext": {"authorizer": {"claims": {"sub": USER}}},
        "body": json.dumps({"resource_id": resource_id}),
    }
    if headers:
        event["headers"] = headers
    return resource_handler.lambda_handler(event, None)


def _store(resources, allocations, requests, history=None, audit=None):
    return {
        "resources": resources,
        "allocations": allocations,
        "requests": requests,
        "history": history or MemoryTable(),
        "audit": audit or AuditLog(),
    }


def _assert_not_contradictory(resource):
    assert not (resource.get("Available") is True and resource.get("operational_status") == "ALLOCATED")


def _unchanged(resource, allocation, request, resource_status="ALLOCATED"):
    assert resource["Available"] is False
    assert resource["operational_status"] == resource_status
    assert allocation["status"] == "ALLOCATED"
    assert request["Status"] == "ALLOCATED"
    _assert_not_contradictory(resource)


def test_rm01_update_remains_field_scoped():
    source = inspect.getsource(resource_handler.update_resource)
    assert "put_item" not in source
    assert "_seen_state_condition" in source
    assert "update_item" in source


def test_manual_emergency_release_commits_all_three(monkeypatch):
    resources = MemoryTable([_resource()])
    allocations = MemoryTable([_allocation()])
    requests = MemoryTable([_request()])
    history, audit, seen = _wire(monkeypatch, resources, allocations, requests)

    result = _release()

    assert result["statusCode"] == 200
    body = json.loads(result["body"])
    assert body["allocation"]["status"] == "RELEASED"
    assert body["request"]["status"] == "RELEASED"
    resource = resources.rows[0]
    assert resource["Available"] is True
    assert resource["operational_status"] == "AVAILABLE"
    assert resource["quantity_available"] == 4
    assert allocations.rows[0]["status"] == "RELEASED"
    assert requests.rows[0]["Status"] == "RELEASED"
    assert len(seen) == 1
    tables = [item["Update"]["TableName"] for item in seen[0]]
    assert tables == ["Resources", "Allocations", "EmergencyRequests"]
    resource_update = seen[0][0]["Update"]
    assert "operational_status = :op_available" in resource_update["UpdateExpression"]
    assert "Available = :available" in resource_update["UpdateExpression"]
    assert "attribute_not_exists(operational_status)" in resource_update["ConditionExpression"]
    assert "operational_status = :op_allocated" in resource_update["ConditionExpression"]
    assert len(history.puts) == 1
    assert len(audit.events) == 1
    _assert_not_contradictory(resource)


def test_manual_release_rejects_concurrent_resource_change(monkeypatch, capsys):
    resources = MemoryTable([_resource()])
    allocations = MemoryTable([_allocation()])
    requests = MemoryTable([_request()])
    history, audit, seen = _wire(monkeypatch, resources, allocations, requests)

    def change_resource():
        resources.rows[0]["operational_status"] = "MAINTENANCE"

    resources.after_get = change_resource
    result = _release(headers={"Authorization": "Bearer secret-value"})
    logged = capsys.readouterr().out

    assert result["statusCode"] == 409
    assert "could not be released" in result["body"]
    assert "TransactionCanceled" not in result["body"]
    assert "Transaction cancelled" not in result["body"]
    assert "secret-value" not in result["body"]
    assert "secret-value" not in logged
    assert "Bearer" not in logged
    assert "Transaction cancelled" not in logged
    assert history.puts == []
    assert audit.events == []
    _unchanged(resources.rows[0], allocations.rows[0], requests.rows[0], "MAINTENANCE")


def test_manual_release_rejects_concurrent_allocation_change(monkeypatch):
    resources = MemoryTable([_resource()])
    allocations = MemoryTable([_allocation()])
    requests = MemoryTable([_request()])
    history, audit, _seen = _wire(monkeypatch, resources, allocations, requests)

    def change_allocation():
        allocations.rows[0]["status"] = "RETURNED"

    allocations.after_query = change_allocation
    result = _release()

    assert result["statusCode"] == 409
    assert "TransactionCanceled" not in result["body"]
    assert history.puts == []
    assert audit.events == []
    assert resources.rows[0]["Available"] is False
    assert resources.rows[0]["operational_status"] == "ALLOCATED"
    assert allocations.rows[0]["status"] == "RETURNED"
    assert requests.rows[0]["Status"] == "ALLOCATED"
    _assert_not_contradictory(resources.rows[0])


def test_manual_release_rejects_concurrent_request_change(monkeypatch):
    resources = MemoryTable([_resource()])
    allocations = MemoryTable([_allocation()])
    requests = MemoryTable([_request()])
    history, audit, _seen = _wire(monkeypatch, resources, allocations, requests)

    def change_request():
        requests.rows[0]["Status"] = "CANCELLED"

    requests.after_get = change_request
    result = _release()

    assert result["statusCode"] == 409
    assert "TransactionCanceled" not in result["body"]
    assert history.puts == []
    assert audit.events == []
    assert resources.rows[0]["operational_status"] == "ALLOCATED"
    assert allocations.rows[0]["status"] == "ALLOCATED"
    assert requests.rows[0]["Status"] == "CANCELLED"
    _assert_not_contradictory(resources.rows[0])


def test_repeated_manual_release_does_not_duplicate_history(monkeypatch):
    resources = MemoryTable([_resource()])
    allocations = MemoryTable([_allocation()])
    requests = MemoryTable([_request()])
    history, audit, seen = _wire(monkeypatch, resources, allocations, requests)

    first = _release()
    second = _release()

    assert first["statusCode"] == 200
    assert second["statusCode"] == 409
    assert "already available" in second["body"]
    assert len(seen) == 1
    assert len(history.puts) == 1
    assert len(audit.events) == 1
    assert resources.rows[0]["Available"] is True
    assert resources.rows[0]["operational_status"] == "AVAILABLE"
    assert allocations.rows[0]["status"] == "RELEASED"
    assert requests.rows[0]["Status"] == "RELEASED"
    _assert_not_contradictory(resources.rows[0])


@pytest.mark.parametrize("operational_status", ["ALLOCATED", None])
def test_automatic_release_restores_available_status(monkeypatch, operational_status):
    resource = _resource(quantity_available=2)
    if operational_status is None:
        resource.pop("operational_status")
    else:
        resource["operational_status"] = operational_status
    resources = MemoryTable(
        [
            resource,
            _resource(
                resource_id="R-FRESH",
                operational_status="ALLOCATED",
            ),
        ]
    )
    allocations = MemoryTable(
        [
            _allocation(),
            _allocation(
                allocation_id="ALLOC-FRESH",
                resource_id="R-FRESH",
                request_id="Q-FRESH",
                allocated_at=NOW.isoformat(),
            ),
        ]
    )
    requests = MemoryTable([_request(), _request(request_id="Q-FRESH")])
    history, audit, seen = _wire(monkeypatch, resources, allocations, requests)
    store = _store(resources, allocations, requests, history, audit)

    result = auto_release.lambda_handler({}, None, store=store, now=NOW)

    body = json.loads(result["body"])
    assert body["released_count"] == 1
    assert resources.rows[0]["Available"] is True
    assert resources.rows[0]["operational_status"] == "AVAILABLE"
    assert resources.rows[0]["quantity_available"] == 2
    assert allocations.rows[0]["status"] == "RELEASED"
    assert requests.rows[0]["Status"] == "RELEASED"
    assert allocations.rows[1]["status"] == "ALLOCATED"
    assert resources.rows[1]["operational_status"] == "ALLOCATED"
    assert len(history.puts) == 1
    assert len(seen) == 1
    _assert_not_contradictory(resources.rows[0])


@pytest.mark.parametrize(
    "operational_status,available",
    [
        ("MAINTENANCE", False),
        ("DAMAGED", False),
        ("RETIRED", False),
        ("RESERVED", False),
        ("IN_USE", False),
        ("DAMAGED", True),
    ],
)
def test_automatic_release_does_not_overwrite_newer_resource_state(monkeypatch, operational_status, available):
    resources = MemoryTable([_resource(operational_status=operational_status, Available=available)])
    allocations = MemoryTable([_allocation()])
    requests = MemoryTable([_request()])
    history, audit, _seen = _wire(monkeypatch, resources, allocations, requests)
    store = _store(resources, allocations, requests, history, audit)

    decision = auto_release.release_allocation(store, dict(allocations.rows[0]), NOW)

    assert decision["action"] == "skip"
    assert decision["reason"] == "resource state changed"
    assert resources.rows[0]["operational_status"] == operational_status
    assert resources.rows[0]["Available"] is available
    assert allocations.rows[0]["status"] == "ALLOCATED"
    assert requests.rows[0]["Status"] == "ALLOCATED"
    assert history.puts == []
    assert audit.events == []
    _assert_not_contradictory(resources.rows[0])


def test_automatic_release_replay_does_not_duplicate_effects(monkeypatch):
    resources = MemoryTable([_resource(quantity_available=3)])
    allocations = MemoryTable([_allocation()])
    requests = MemoryTable([_request()])
    history, audit, seen = _wire(monkeypatch, resources, allocations, requests)
    store = _store(resources, allocations, requests, history, audit)
    stale = dict(allocations.rows[0])

    first = auto_release.lambda_handler({}, None, store=store, now=NOW)
    second = auto_release.lambda_handler({}, None, store=store, now=NOW)
    replay = auto_release.release_allocation(store, stale, NOW)

    assert json.loads(first["body"])["released_count"] == 1
    assert json.loads(second["body"])["released_count"] == 0
    assert replay["action"] == "skip"
    assert replay["reason"] == "already released"
    assert len(seen) == 2
    assert len(history.puts) == 1
    assert len(audit.events) == 1
    assert resources.rows[0]["Available"] is True
    assert resources.rows[0]["operational_status"] == "AVAILABLE"
    assert resources.rows[0]["quantity_available"] == 3
    assert allocations.rows[0]["status"] == "RELEASED"
    assert requests.rows[0]["Status"] == "RELEASED"
    _assert_not_contradictory(resources.rows[0])


def test_automatic_release_keeps_resource_when_another_allocation_holds_it(monkeypatch):
    resources = MemoryTable([_resource()])
    allocations = MemoryTable(
        [
            _allocation(),
            _allocation(allocation_id="ALLOC-Q2", request_id="Q2", allocated_at=(NOW - timedelta(hours=1)).isoformat()),
        ]
    )
    requests = MemoryTable([_request(), _request(request_id="Q2")])
    history, _audit, seen = _wire(monkeypatch, resources, allocations, requests)
    store = _store(resources, allocations, requests, history)

    decision = auto_release.release_allocation(store, dict(allocations.rows[0]), NOW)

    assert decision["action"] == "release"
    assert decision["free_resource"] is False
    assert [item["Update"]["TableName"] for item in seen[0]] == ["Allocations", "EmergencyRequests"]
    assert resources.rows[0]["Available"] is False
    assert resources.rows[0]["operational_status"] == "ALLOCATED"
    assert allocations.rows[0]["status"] == "RELEASED"
    assert requests.rows[0]["Status"] == "RELEASED"
    assert allocations.rows[1]["status"] == "ALLOCATED"
    _assert_not_contradictory(resources.rows[0])


def test_automatic_release_request_conflict_leaves_allocation(monkeypatch):
    resources = MemoryTable([_resource()])
    allocations = MemoryTable([_allocation()])
    requests = MemoryTable([_request(Status="CANCELLED")])
    history, audit, _seen = _wire(monkeypatch, resources, allocations, requests)
    store = _store(resources, allocations, requests, history, audit)
    stale_request = dict(requests.rows[0])
    stale_request["Status"] = "ALLOCATED"

    def return_stale(Key):
        if Key.get("request_id") == "Q1":
            return {"Item": dict(stale_request)}
        return {}

    requests.get_item = return_stale
    decision = auto_release.release_allocation(store, dict(allocations.rows[0]), NOW)

    assert decision["action"] == "skip"
    assert decision["reason"] == "request state changed"
    assert history.puts == []
    assert audit.events == []
    assert resources.rows[0]["Available"] is False
    assert resources.rows[0]["operational_status"] == "ALLOCATED"
    assert allocations.rows[0]["status"] == "ALLOCATED"
    assert requests.rows[0]["Status"] == "CANCELLED"
    _assert_not_contradictory(resources.rows[0])
