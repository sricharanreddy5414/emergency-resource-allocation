"""RM-08: ResourceStatusHistory stores the authenticated actor and does not return it."""

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from api_views import RESOURCE_HISTORY_FIELDS, resource_history_view

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [
    str(ROOT / "src" / "shared"),
    str(ROOT / "src" / "organization"),
    str(ROOT / "src"),
]


def load_module(name, relative):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


everyday = load_module("everyday_rm08", "src/shared/everyday_operations.py")
lifecycle = load_module("lifecycle_rm08", "src/shared/lifecycle_operations.py")
resource_handler = load_module("resource_handler_rm08", "src/resource/handler.py")
auto_release = load_module("auto_release_rm08", "src/auto_release/handler.py")

ORG = "ORG-A"
OTHER = "ORG-B"
ACTOR = "cognito-operator-a"
FORGED = "cognito-someone-else"
NOW = datetime(2026, 10, 8, 8, 0, tzinfo=timezone.utc)


class History:
    def __init__(self):
        self.rows = []

    def put_item(self, Item, ConditionExpression=None):
        self.rows.append(dict(Item))


class Audit:
    def put_item(self, Item, ConditionExpression=None):
        return None


class ResourceTable:
    def __init__(self, item):
        self.item = dict(item)

    def update_item(self, **kwargs):
        return {}

    def get_item(self, Key):
        if self.item.get("resource_id") == Key.get("resource_id"):
            return {"Item": dict(self.item)}
        return {}


def resource(**extra):
    item = {
        "resource_id": "R1",
        "organization_id": ORG,
        "location_id": "LOC1",
        "Type": "Kit",
        "Location": "HQ",
        "Available": True,
        "operational_status": "AVAILABLE",
        "tracking_mode": "INDIVIDUAL",
    }
    item.update(extra)
    return item


def tables_for(item):
    history = History()
    return {
        "resources": ResourceTable(item),
        "allocations": ResourceTable({}),
        "history": history,
        "audit": Audit(),
    }, history


def forged_body():
    return {
        "actor_sub": FORGED,
        "user_sub": FORGED,
        "created_by": FORGED,
        "organization_id": OTHER,
        "email": "forged@example.com",
    }


def test_reserve_history_uses_authenticated_actor():
    tables, history = tables_for(resource())
    everyday.reserve_individual(forged_body(), ORG, ACTOR, "OPERATOR", tables["resources"].item, tables)
    assert history.rows[-1]["actor_sub"] == ACTOR
    assert history.rows[-1]["organization_id"] == ORG
    assert FORGED not in json.dumps(history.rows[-1])


def test_reservation_release_history_uses_authenticated_actor():
    tables, history = tables_for(resource(Available=False, operational_status="RESERVED", reserved_by=ACTOR))
    everyday.release_reservation(forged_body(), ORG, ACTOR, "OPERATOR", tables["resources"].item, tables)
    assert history.rows[-1]["reason"] == "RESOURCE_RESERVATION_RELEASED"
    assert history.rows[-1]["actor_sub"] == ACTOR
    assert history.rows[-1]["organization_id"] == ORG


def test_maintenance_history_uses_authenticated_actor():
    tables, history = tables_for(resource())
    lifecycle.start_maintenance(forged_body(), ORG, ACTOR, "OPERATOR", tables["resources"].item, tables)
    assert history.rows[-1]["new_status"] == "MAINTENANCE"
    assert history.rows[-1]["actor_sub"] == ACTOR


def test_damage_history_uses_authenticated_actor():
    tables, history = tables_for(resource())
    lifecycle.mark_damaged(forged_body(), ORG, ACTOR, "OPERATOR", tables["resources"].item, tables)
    assert history.rows[-1]["new_status"] == "DAMAGED"
    assert history.rows[-1]["actor_sub"] == ACTOR


def test_retirement_history_uses_authenticated_actor():
    tables, history = tables_for(resource())
    lifecycle.retire_resource(forged_body(), ORG, ACTOR, "OPERATOR", tables["resources"].item, tables)
    assert history.rows[-1]["new_status"] == "RETIRED"
    assert history.rows[-1]["actor_sub"] == ACTOR


def test_in_use_history_uses_authenticated_actor():
    tables, history = tables_for(resource(Available=False, operational_status="ALLOCATED"))
    lifecycle.mark_in_use(forged_body(), ORG, ACTOR, "OPERATOR", tables["resources"].item, tables)
    assert history.rows[-1]["new_status"] == "IN_USE"
    assert history.rows[-1]["actor_sub"] == ACTOR


def test_everyday_allocation_and_return_use_authenticated_actor(monkeypatch):
    tables, history = tables_for(resource())
    monkeypatch.setattr(everyday, "_commit_everyday_allocation", lambda *args, **kwargs: None)
    everyday.everyday_allocate_individual(forged_body(), ORG, ACTOR, "OPERATOR", tables["resources"].item, tables)
    assert history.rows[-1]["reason"] == "EVERYDAY_RESOURCE_ALLOCATED"
    assert history.rows[-1]["actor_sub"] == ACTOR

    allocation = {
        "allocation_id": "EVERYDAY-R1",
        "organization_id": ORG,
        "resource_id": "R1",
        "allocation_type": "EVERYDAY",
        "status": "OPEN",
        "quantity": 1,
    }
    tables["allocations"] = type("Alloc", (), {})()
    tables["allocations"].get_item = lambda Key: {"Item": dict(allocation)}
    tables["allocations"].update_item = lambda **kwargs: None
    tables["resources"].item["operational_status"] = "ALLOCATED"
    tables["resources"].item["Available"] = False
    everyday.everyday_return(
        {"allocation_id": "EVERYDAY-R1", **forged_body()},
        ORG,
        ACTOR,
        "OPERATOR",
        tables["resources"].item,
        tables,
    )
    assert history.rows[-1]["reason"] == "EVERYDAY_RESOURCE_RETURNED"
    assert history.rows[-1]["actor_sub"] == ACTOR
    assert FORGED not in json.dumps(history.rows[-1])


def test_emergency_release_records_authenticated_actor(monkeypatch):
    history = History()
    monkeypatch.setattr(resource_handler, "allocations_table", lambda: object())
    monkeypatch.setattr(resource_handler, "history_table", lambda: history)
    monkeypatch.setattr(resource_handler, "audit_table", lambda: Audit())
    monkeypatch.setattr(resource_handler, "commit_emergency_release", lambda **kwargs: None)
    monkeypatch.setattr(resource_handler, "events_client", lambda: type("Events", (), {"put_events": lambda self, **kwargs: None})())
    monkeypatch.setattr(
        resource_handler,
        "resources_table",
        lambda: ResourceTable(resource(Available=False, operational_status="ALLOCATED")),
    )
    monkeypatch.setattr(
        resource_handler,
        "requests_table",
        lambda: type("Requests", (), {"get_item": lambda self, Key: {"Item": {"request_id": "Q1", "organization_id": ORG, "Status": "ALLOCATED"}}})(),
    )
    monkeypatch.setattr(
        resource_handler,
        "query_by_organization",
        lambda *args, **kwargs: [
            {
                "allocation_id": "A1",
                "resource_id": "R1",
                "organization_id": ORG,
                "status": "ALLOCATED",
                "request_id": "Q1",
                "allocated_at": "2026-10-08T00:00:00+00:00",
                "location_id": "LOC1",
            }
        ],
    )

    result = resource_handler.release_resource(forged_body() | {"resource_id": "R1"}, ORG, ACTOR, "OPERATOR")

    assert result["statusCode"] == 200
    assert history.rows[-1]["reason"] == "RESOURCE_RELEASED"
    assert history.rows[-1]["actor_sub"] == ACTOR
    assert history.rows[-1]["organization_id"] == ORG
    assert FORGED not in json.dumps(history.rows[-1])


def test_client_actor_sub_is_ignored():
    tables, history = tables_for(resource())
    everyday.reserve_individual({"actor_sub": FORGED}, ORG, ACTOR, "OPERATOR", tables["resources"].item, tables)
    assert history.rows[-1]["actor_sub"] == ACTOR


def test_client_user_sub_is_ignored():
    tables, history = tables_for(resource())
    everyday.reserve_individual({"user_sub": FORGED}, ORG, ACTOR, "OPERATOR", tables["resources"].item, tables)
    assert history.rows[-1]["actor_sub"] == ACTOR


def test_client_created_by_is_ignored():
    tables, history = tables_for(resource())
    everyday.reserve_individual({"created_by": FORGED}, ORG, ACTOR, "OPERATOR", tables["resources"].item, tables)
    assert history.rows[-1]["actor_sub"] == ACTOR
    assert "created_by" not in history.rows[-1]


def test_cross_tenant_body_cannot_claim_another_organization():
    tables, history = tables_for(resource())
    everyday.reserve_individual(
        {"organization_id": OTHER, "actor_sub": FORGED},
        ORG,
        ACTOR,
        "OPERATOR",
        tables["resources"].item,
        tables,
    )
    assert history.rows[-1]["organization_id"] == ORG
    assert history.rows[-1]["actor_sub"] == ACTOR


def test_automatic_release_uses_the_system_actor(monkeypatch):
    history = History()
    allocation = {
        "allocation_id": "A1",
        "request_id": "Q1",
        "resource_id": "R1",
        "organization_id": ORG,
        "location_id": "LOC1",
        "status": "ALLOCATED",
        "allocated_at": (NOW - timedelta(hours=2)).isoformat(),
        "created_by": ACTOR,
    }
    store = {
        "resources": ResourceTable(resource(Available=False, operational_status="ALLOCATED")),
        "requests": type("Requests", (), {"get_item": lambda self, Key: {"Item": {"request_id": "Q1", "organization_id": ORG, "Status": "ALLOCATED"}}})(),
        "allocations": type("Alloc", (), {"query": lambda self, **kwargs: {"Items": [allocation]}})(),
        "history": history,
        "audit": Audit(),
    }
    monkeypatch.setattr(auto_release, "commit_emergency_release", lambda **kwargs: None)

    decision = auto_release.release_allocation(store, allocation, NOW)

    assert decision["action"] == "release"
    assert history.rows[-1]["reason"] == "AUTOMATIC_RESOURCE_RELEASE"
    assert history.rows[-1]["actor_sub"] == "system"
    assert history.rows[-1]["actor_sub"] != ACTOR


def test_old_history_without_actor_remains_readable(monkeypatch):
    old = {
        "history_id": "HIST-OLD",
        "resource_id": "R1",
        "organization_id": ORG,
        "previous_status": "AVAILABLE",
        "new_status": "ALLOCATED",
        "reason": "RESOURCE_ALLOCATED",
        "changed_at": "2026-01-01T00:00:00+00:00",
    }
    monkeypatch.setattr(resource_handler, "resources_table", lambda: ResourceTable(resource()))
    monkeypatch.setattr(
        resource_handler,
        "history_table",
        lambda: type("Hist", (), {"query": lambda self, **kwargs: {"Items": [old]}})(),
    )

    result = resource_handler.resource_history(
        {"queryStringParameters": {"resource_id": "R1"}},
        ORG,
    )
    body = json.loads(result["body"])

    assert result["statusCode"] == 200
    assert body[0]["previous_status"] == "AVAILABLE"
    assert body[0]["new_status"] == "ALLOCATED"
    assert "actor_sub" not in body[0]


def test_history_response_stores_actor_but_does_not_return_it(monkeypatch):
    stored = {
        "history_id": "HIST-NEW",
        "resource_id": "R1",
        "organization_id": ORG,
        "previous_status": "AVAILABLE",
        "new_status": "RESERVED",
        "reason": "RESOURCE_RESERVED",
        "changed_at": "2026-10-08T00:00:00+00:00",
        "actor_sub": ACTOR,
        "internal_secret_field": "should-never-leak",
    }
    monkeypatch.setattr(resource_handler, "resources_table", lambda: ResourceTable(resource()))
    monkeypatch.setattr(
        resource_handler,
        "history_table",
        lambda: type("Hist", (), {"query": lambda self, **kwargs: {"Items": [stored]}})(),
    )

    result = resource_handler.resource_history(
        {"queryStringParameters": {"resource_id": "R1"}},
        ORG,
    )
    body = json.loads(result["body"])

    assert "actor_sub" not in RESOURCE_HISTORY_FIELDS
    assert resource_history_view(stored) == body[0]
    assert body[0]["reason"] == "RESOURCE_RESERVED"
    assert "actor_sub" not in body[0]
    assert ACTOR not in result["body"]
    assert "should-never-leak" not in result["body"]
