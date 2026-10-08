"""P9-06 holder pagination and P9-07 ResourceReleased for automatic release."""

import importlib.util
import inspect
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from botocore.exceptions import ClientError

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


auto_release = load_module("auto_release_p9d", "src/auto_release/handler.py")
resource_handler = load_module("resource_handler_p9d", "src/resource/handler.py")
reservation_expiry = load_module("reservation_expiry_p9d", "src/shared/reservation_expiry.py")

ORG = "ORG-A"
OTHER = "ORG-B"
NOW = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
DUE = (NOW - timedelta(hours=2)).isoformat()
FRESH = NOW.isoformat()


class Rows:
    def __init__(self, rows=None):
        self.rows = [dict(row) for row in (rows or [])]
        self.puts = []

    def get_item(self, Key):
        for row in self.rows:
            if all(row.get(key) == value for key, value in Key.items()):
                return {"Item": dict(row)}
        return {}

    def put_item(self, Item, ConditionExpression=None):
        self.puts.append(dict(Item))
        self.rows.append(dict(Item))

    def query(self, **kwargs):
        return {"Items": [dict(row) for row in self.rows]}


class AllocationPages:
    """Query double that returns real DynamoDB pages for the holder index."""

    def __init__(self, rows, foreign=None, empty_tail=False, fail_org=None):
        self.rows = [dict(row) for row in rows]
        self.foreign = dict(foreign) if foreign else None
        self.empty_tail = empty_tail
        self.fail_org = fail_org
        self.queries = []
        self.scans = 0

    def scan(self, **kwargs):
        self.scans += 1
        raise AssertionError("scan is not allowed")

    def query(self, **kwargs):
        self.queries.append(dict(kwargs))
        if kwargs.get("IndexName") == "AllocationStatusIndex":
            values = kwargs["ExpressionAttributeValues"]
            status = values[":allocated"]
            cutoff = values[":cutoff"]
            matched = [
                dict(row)
                for row in self.rows
                if row.get("status") == status and str(row.get("allocated_at", "")) < cutoff
            ]
            return {"Items": matched}

        assert kwargs.get("IndexName") == "OrganizationLocationIndex"
        assert "Limit" in kwargs
        org = kwargs["ExpressionAttributeValues"][":organization_id"]
        if self.fail_org and org == self.fail_org:
            raise ClientError(
                {"Error": {"Code": "InternalServerError", "Message": "Bearer secret-token"}},
                "Query",
            )
        if self.empty_tail:
            if kwargs.get("ExclusiveStartKey"):
                return {"Items": []}
            current = [dict(row) for row in self.rows if row.get("organization_id") == org]
            return {"Items": current, "LastEvaluatedKey": {"index": 1}}

        matched = [dict(row) for row in self.rows if row.get("organization_id") == org]
        start = kwargs.get("ExclusiveStartKey", {}).get("index", 0)
        limit = kwargs["Limit"]
        page = matched[start : start + limit]
        if self.foreign:
            page.append(dict(self.foreign))
        result = {"Items": page}
        if start + limit < len(matched):
            result["LastEvaluatedKey"] = {"index": start + limit}
        return result


class Events:
    def __init__(self, fail=None, failed_count=0):
        self.entries = []
        self.fail = fail
        self.failed_count = failed_count

    def put_events(self, **kwargs):
        self.entries.extend(kwargs.get("Entries") or [])
        if self.fail:
            raise self.fail
        return {"FailedEntryCount": self.failed_count}


def _resource(resource_id="R1", organization_id=ORG, **extra):
    item = {
        "resource_id": resource_id,
        "organization_id": organization_id,
        "Available": False,
        "operational_status": "ALLOCATED",
        "location_id": "LOC1",
    }
    item.update(extra)
    return item


def _allocation(allocation_id="ALLOC-Q1", resource_id="R1", request_id="Q1", organization_id=ORG, **extra):
    item = {
        "allocation_id": allocation_id,
        "request_id": request_id,
        "resource_id": resource_id,
        "organization_id": organization_id,
        "location_id": "LOC1",
        "status": "ALLOCATED",
        "allocated_at": DUE,
    }
    item.update(extra)
    return item


def _request(request_id="Q1", organization_id=ORG, **extra):
    item = {"request_id": request_id, "organization_id": organization_id, "Status": "ALLOCATED"}
    item.update(extra)
    return item


def _filler(index, organization_id=ORG):
    return _allocation(
        allocation_id="FILL-%s" % index,
        resource_id="OTHER-%s" % index,
        request_id="QF-%s" % index,
        organization_id=organization_id,
    )


def _wire(monkeypatch, resources, allocations, requests, events=None):
    history = Rows()
    audit = Rows()
    events = events or Events()
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
    monkeypatch.setattr(auto_release, "events_client", lambda: events)
    monkeypatch.setattr(resource_handler, "resources_table", lambda: resources)
    monkeypatch.setattr(resource_handler, "allocations_table", lambda: allocations)
    monkeypatch.setattr(resource_handler, "requests_table", lambda: requests)
    monkeypatch.setattr(resource_handler, "history_table", lambda: history)
    monkeypatch.setattr(resource_handler, "audit_table", lambda: audit)
    monkeypatch.setattr(resource_handler, "events_client", lambda: events)
    monkeypatch.setattr(
        access,
        "list_memberships",
        lambda *args, **kwargs: [
            {"organization_id": ORG, "name": ORG, "role": "OPERATOR", "status": "ACTIVE"}
        ],
    )
    return history, audit, seen, events


def _store(resources, allocations, requests, history, audit):
    return {
        "resources": resources,
        "allocations": allocations,
        "requests": requests,
        "history": history,
        "audit": audit,
    }


def _holder_queries(allocations):
    return [query for query in allocations.queries if query.get("IndexName") == "OrganizationLocationIndex"]


def _detail(entry):
    assert entry["Source"] == "emergency.resource.allocation"
    assert entry["DetailType"] == "ResourceReleased"
    assert entry["EventBusName"] == "default"
    return json.loads(entry["Detail"])


def test_holder_on_first_page_stops_and_keeps_resource(monkeypatch):
    holder = _allocation(allocation_id="ALLOC-Q2", request_id="Q2")
    allocations = AllocationPages([_allocation(), holder, *[_filler(i) for i in range(150)]])
    resources = Rows([_resource()])
    requests = Rows([_request(), _request(request_id="Q2")])
    history, audit, seen, events = _wire(monkeypatch, resources, allocations, requests)
    store = _store(resources, allocations, requests, history, audit)

    decision = auto_release.release_allocation(store, dict(allocations.rows[0]), NOW)

    assert decision["free_resource"] is False
    assert len(_holder_queries(allocations)) == 1
    assert "ExclusiveStartKey" not in _holder_queries(allocations)[0]
    assert _holder_queries(allocations)[0]["Limit"] == auto_release.HOLDER_PAGE_SIZE
    assert [item["Update"]["TableName"] for item in seen[0]] == ["Allocations", "EmergencyRequests"]
    assert resources.rows[0]["Available"] is False
    assert allocations.scans == 0
    assert len(events.entries) == 1


def test_holder_on_later_page_is_found_without_freeing_resource(monkeypatch):
    current = _allocation()
    fillers = [_filler(i) for i in range(auto_release.HOLDER_PAGE_SIZE)]
    holder = _allocation(allocation_id="ALLOC-LATER", request_id="Q-LATER")
    allocations = AllocationPages([current, *fillers, holder])
    resources = Rows([_resource()])
    requests = Rows([_request(), _request(request_id="Q-LATER")])
    history, audit, seen, events = _wire(monkeypatch, resources, allocations, requests)
    store = _store(resources, allocations, requests, history, audit)

    decision = auto_release.release_allocation(store, dict(current), NOW)

    pages = _holder_queries(allocations)
    assert len(pages) >= 2
    assert pages[1]["ExclusiveStartKey"] == {"index": auto_release.HOLDER_PAGE_SIZE}
    assert decision["action"] == "release"
    assert decision["free_resource"] is False
    assert resources.rows[0]["Available"] is False
    assert resources.rows[0]["operational_status"] == "ALLOCATED"
    assert allocations.rows[-1]["status"] == "ALLOCATED"
    assert allocations.scans == 0
    assert len(events.entries) == 1


def test_multiple_pages_and_empty_tail_do_not_invent_a_holder(monkeypatch):
    allocations = AllocationPages([_allocation(), *[_filler(i) for i in range(3)]], empty_tail=True)
    monkeypatch.setattr(auto_release, "HOLDER_PAGE_SIZE", 2)
    resources = Rows([_resource()])
    requests = Rows([_request()])
    history, audit, seen, _events = _wire(monkeypatch, resources, allocations, requests)
    store = _store(resources, allocations, requests, history, audit)

    decision = auto_release.release_allocation(store, dict(allocations.rows[0]), NOW)

    pages = _holder_queries(allocations)
    assert len(pages) == 2
    assert pages[1]["ExclusiveStartKey"] == {"index": 1}
    assert decision["free_resource"] is True
    assert resources.rows[0]["Available"] is True
    assert seen


def test_no_holder_after_all_pages_frees_resource(monkeypatch):
    allocations = AllocationPages([_allocation(), *[_filler(i) for i in range(auto_release.HOLDER_PAGE_SIZE + 5)]])
    resources = Rows([_resource()])
    requests = Rows([_request()])
    history, audit, seen, events = _wire(monkeypatch, resources, allocations, requests)
    store = _store(resources, allocations, requests, history, audit)

    decision = auto_release.release_allocation(store, dict(allocations.rows[0]), NOW)

    assert len(_holder_queries(allocations)) == 2
    assert decision["free_resource"] is True
    assert resources.rows[0]["Available"] is True
    assert resources.rows[0]["operational_status"] == "AVAILABLE"
    assert len(events.entries) == 1
    assert allocations.scans == 0


def test_incomplete_holder_lookup_does_not_release(monkeypatch):
    allocations = AllocationPages([_allocation(), *[_filler(i) for i in range(5)]])
    monkeypatch.setattr(auto_release, "HOLDER_PAGE_SIZE", 1)
    monkeypatch.setattr(auto_release, "HOLDER_PAGE_LIMIT", 2)
    resources = Rows([_resource()])
    requests = Rows([_request()])
    history, audit, seen, events = _wire(monkeypatch, resources, allocations, requests)
    store = _store(resources, allocations, requests, history, audit)

    decision = auto_release.release_allocation(store, dict(allocations.rows[0]), NOW)

    assert decision["action"] == "skip"
    assert decision["reason"] == "holder lookup incomplete"
    assert seen == []
    assert events.entries == []
    assert resources.rows[0]["Available"] is False
    assert allocations.rows[0]["status"] == "ALLOCATED"
    assert len(_holder_queries(allocations)) == 2


def test_cross_tenant_holder_cannot_change_release(monkeypatch):
    foreign = _allocation(
        allocation_id="ALLOC-FOREIGN",
        request_id="Q-FOREIGN",
        resource_id="R-FOREIGN",
        organization_id=OTHER,
    )
    allocations = AllocationPages([_allocation(), foreign], foreign=foreign)
    resources = Rows([_resource(), _resource(resource_id="R-FOREIGN", organization_id=OTHER)])
    requests = Rows([_request()])
    history, audit, _seen, _events = _wire(monkeypatch, resources, allocations, requests)
    store = _store(resources, allocations, requests, history, audit)

    decision = auto_release.release_allocation(store, dict(allocations.rows[0]), NOW)
    stored = next(row for row in allocations.rows if row["organization_id"] == OTHER)

    assert _holder_queries(allocations)[0]["ExpressionAttributeValues"][":organization_id"] == ORG
    assert decision["free_resource"] is True
    assert resources.rows[0]["Available"] is True
    assert resources.rows[1]["Available"] is False
    assert stored["status"] == "ALLOCATED"
    assert ".scan(" not in inspect.getsource(auto_release.lookup_other_holders)
    assert ".scan(" not in Path(ROOT / "src" / "auto_release" / "handler.py").read_text(encoding="utf-8")


def test_due_request_releases_and_fresh_request_does_not(monkeypatch):
    due = _allocation()
    fresh = _allocation(allocation_id="ALLOC-FRESH", request_id="Q-FRESH", resource_id="R-FRESH", allocated_at=FRESH)
    allocations = AllocationPages([due, fresh])
    resources = Rows([_resource(), _resource(resource_id="R-FRESH")])
    requests = Rows([_request(), _request(request_id="Q-FRESH")])
    history, audit, _seen, events = _wire(monkeypatch, resources, allocations, requests)
    store = _store(resources, allocations, requests, history, audit)

    result = auto_release.lambda_handler({}, None, store=store, now=NOW)
    body = json.loads(result["body"])

    assert body["released_count"] == 1
    assert body["released"] == [{"allocation_id": "ALLOC-Q1"}]
    assert allocations.rows[0]["status"] == "RELEASED"
    assert allocations.rows[1]["status"] == "ALLOCATED"
    assert resources.rows[1]["Available"] is False
    assert len(events.entries) == 1
    assert _detail(events.entries[0]) == {
        "resource_id": "R1",
        "allocation_id": "ALLOC-Q1",
        "request_id": "Q1",
        "organization_id": ORG,
        "status": "RELEASED",
    }


def test_replay_and_failed_transaction_do_not_emit_another_event(monkeypatch):
    allocations = AllocationPages([_allocation()])
    resources = Rows([_resource()])
    requests = Rows([_request()])
    history, audit, _seen, events = _wire(monkeypatch, resources, allocations, requests)
    store = _store(resources, allocations, requests, history, audit)
    stale = dict(allocations.rows[0])

    first = auto_release.lambda_handler({}, None, store=store, now=NOW)
    second = auto_release.lambda_handler({}, None, store=store, now=NOW)
    replay = auto_release.release_allocation(store, stale, NOW)

    assert json.loads(first["body"])["released_count"] == 1
    assert json.loads(second["body"])["released_count"] == 0
    assert replay["action"] == "skip"
    assert len(events.entries) == 1

    conflict_allocations = AllocationPages([_allocation(allocation_id="ALLOC-Q9", request_id="Q9")])
    conflict_resources = Rows([_resource()])
    conflict_requests = Rows([_request(request_id="Q9", Status="CANCELLED")])
    conflict_events = Events()
    history, audit, _seen, events = _wire(
        monkeypatch, conflict_resources, conflict_allocations, conflict_requests, conflict_events
    )
    conflict_requests.get_item = lambda Key: {"Item": _request(request_id="Q9", Status="ALLOCATED")}
    store = _store(conflict_resources, conflict_allocations, conflict_requests, history, audit)

    decision = auto_release.release_allocation(store, dict(conflict_allocations.rows[0]), NOW)

    assert decision["action"] == "skip"
    assert decision["reason"] == "request state changed"
    assert conflict_events.entries == []
    assert conflict_allocations.rows[0]["status"] == "ALLOCATED"
    assert conflict_resources.rows[0]["Available"] is False


def test_event_publish_failure_does_not_repeat_or_undo_release(monkeypatch, capsys):
    allocations = AllocationPages([_allocation()])
    resources = Rows([_resource()])
    requests = Rows([_request()])
    events = Events(fail=RuntimeError("Bearer secret-token"))
    history, audit, _seen, events = _wire(monkeypatch, resources, allocations, requests, events)
    store = _store(resources, allocations, requests, history, audit)

    first = auto_release.lambda_handler({}, None, store=store, now=NOW)
    logged = capsys.readouterr().out
    second = auto_release.lambda_handler({}, None, store=store, now=NOW)

    assert json.loads(first["body"])["released_count"] == 1
    assert allocations.rows[0]["status"] == "RELEASED"
    assert resources.rows[0]["Available"] is True
    assert len(events.entries) == 1
    assert json.loads(second["body"])["released_count"] == 0
    assert len(events.entries) == 1
    assert "Bearer" not in logged
    assert "secret-token" not in logged


def test_failed_entry_count_is_one_attempt(monkeypatch):
    allocations = AllocationPages([_allocation()])
    resources = Rows([_resource()])
    requests = Rows([_request()])
    events = Events(failed_count=1)
    history, _audit, _seen, events = _wire(monkeypatch, resources, allocations, requests, events)
    store = _store(resources, allocations, requests, history, Rows())

    auto_release.lambda_handler({}, None, store=store, now=NOW)
    auto_release.lambda_handler({}, None, store=store, now=NOW)

    assert len(events.entries) == 1
    assert allocations.rows[0]["status"] == "RELEASED"


def test_one_failed_lookup_does_not_release_another_record(monkeypatch, capsys):
    bad = _allocation(allocation_id="ALLOC-BAD", request_id="Q-BAD", resource_id="R-BAD", organization_id="ORG-BAD")
    good = _allocation()
    allocations = AllocationPages([bad, good], fail_org="ORG-BAD")
    resources = Rows([_resource(resource_id="R-BAD", organization_id="ORG-BAD"), _resource()])
    requests = Rows([_request(request_id="Q-BAD", organization_id="ORG-BAD"), _request()])
    history, audit, _seen, events = _wire(monkeypatch, resources, allocations, requests)
    store = _store(resources, allocations, requests, history, audit)

    result = auto_release.lambda_handler({}, None, store=store, now=NOW)
    logged = capsys.readouterr().out
    body = json.loads(result["body"])

    assert body["released"] == [{"allocation_id": "ALLOC-Q1"}]
    assert allocations.rows[0]["status"] == "ALLOCATED"
    assert resources.rows[0]["Available"] is False
    assert allocations.rows[1]["status"] == "RELEASED"
    assert len(events.entries) == 1
    assert _detail(events.entries[0])["organization_id"] == ORG
    assert "Bearer" not in logged
    assert "secret-token" not in result["body"]


def test_malformed_record_does_not_block_the_next_release(monkeypatch):
    class Boom(Rows):
        def get_item(self, Key):
            if Key.get("resource_id") == "R-BAD":
                raise RuntimeError("malformed resource")
            return super().get_item(Key)

    bad = _allocation(allocation_id="ALLOC-BAD", request_id="Q-BAD", resource_id="R-BAD")
    good = _allocation(allocation_id="ALLOC-GOOD", request_id="Q-GOOD", resource_id="R-GOOD")
    allocations = AllocationPages([bad, good])
    resources = Boom([_resource(resource_id="R-GOOD")])
    requests = Rows([_request(request_id="Q-BAD"), _request(request_id="Q-GOOD")])
    history, audit, _seen, events = _wire(monkeypatch, resources, allocations, requests)
    store = _store(resources, allocations, requests, history, audit)

    result = auto_release.lambda_handler({}, None, store=store, now=NOW)

    assert json.loads(result["body"])["released"] == [{"allocation_id": "ALLOC-GOOD"}]
    assert allocations.rows[0]["status"] == "ALLOCATED"
    assert allocations.rows[1]["status"] == "RELEASED"
    assert len(events.entries) == 1


def test_manual_release_event_shape_is_unchanged(monkeypatch):
    allocations = Rows([_allocation()])
    resources = Rows([_resource()])
    requests = Rows([_request()])
    _history, _audit, _seen, events = _wire(monkeypatch, resources, allocations, requests)
    event = {
        "httpMethod": "POST",
        "path": "/allocate/resources/release",
        "queryStringParameters": {"organization_id": ORG},
        "requestContext": {"authorizer": {"claims": {"sub": "operator-sub"}}},
        "body": json.dumps({"resource_id": "R1"}),
    }

    result = resource_handler.lambda_handler(event, None)

    assert result["statusCode"] == 200
    assert _detail(events.entries[0]) == {
        "resource_id": "R1",
        "allocation_id": "ALLOC-Q1",
        "request_id": "Q1",
        "organization_id": ORG,
        "status": "RELEASED",
    }
    manual = inspect.getsource(resource_handler.release_resource)
    assert manual.count('"DetailType": "ResourceReleased"') == 1
    assert "lookup_other_holders" not in manual
    release = Path(ROOT / "src" / "shared" / "emergency_release.py").read_text(encoding="utf-8")
    assert "SET #status = :released, released_at = :released_at" in release
    assert "SET Available = :available, operational_status = :op_available" in release
    assert reservation_expiry.BATCH_LIMIT == 25
    assert "clock" not in inspect.getsource(reservation_expiry.query_due_reservations)
    assert auto_release.RELEASE_AFTER_MINUTES == 30
