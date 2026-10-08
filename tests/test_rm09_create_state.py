"""RM-09: resource creation owns the initial lifecycle state."""

import importlib.util
import json
import sys
from pathlib import Path

from api_views import RESOURCE_FIELDS

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


resource_handler = load_module("resource_handler_rm09", "src/resource/handler.py")
everyday = load_module("everyday_rm09", "src/shared/everyday_operations.py")

ORG = "ORG-A"
OTHER = "ORG-B"
ACTOR = "cognito-operator-a"


class PutTable:
    def __init__(self):
        self.puts = []

    def put_item(self, Item, ConditionExpression=None):
        self.puts.append(dict(Item))


class Audit:
    def __init__(self):
        self.events = []

    def put_item(self, Item, ConditionExpression=None):
        self.events.append(dict(Item))


class Lookup:
    def __init__(self, item):
        self.item = item

    def get_item(self, Key):
        return {"Item": dict(self.item)}


def wire(monkeypatch, audit=None):
    resources = PutTable()
    history = PutTable()
    monkeypatch.setattr(resource_handler, "resources_table", lambda: resources)
    monkeypatch.setattr(resource_handler, "history_table", lambda: history)
    monkeypatch.setattr(resource_handler, "audit_table", lambda: audit or Audit())
    monkeypatch.setattr(
        resource_handler,
        "locations_table",
        lambda: Lookup(
            {
                "organization_id": ORG,
                "location_id": "LOC1",
                "name": "HQ",
                "status": "ACTIVE",
                "city": "City",
                "state": "State",
            }
        ),
    )
    monkeypatch.setattr(
        resource_handler,
        "resource_types_table",
        lambda: Lookup(
            {
                "organization_id": ORG,
                "resource_type_id": "TYPE1",
                "name": "Kit",
                "status": "ACTIVE",
                "attributes_schema": {
                    "fields": [{"key": "capacity", "type": "number", "required": False}]
                },
            }
        ),
    )
    return resources, history


def create(monkeypatch, body):
    resources, history = wire(monkeypatch)
    result = resource_handler.register_resource(body, ORG, ACTOR, "OPERATOR")
    return result, resources, history


def base_body(**extra):
    body = {
        "resource_id": "R-NEW",
        "resource_type_id": "TYPE1",
        "location_id": "LOC1",
        "name": "North kit",
        "attributes": {"capacity": 2},
        "visibility": "PRIVATE",
    }
    body.update(extra)
    return body


def stored(resources):
    assert resources.puts
    return resources.puts[0]


def test_individual_create_without_state_fields_is_available(monkeypatch):
    result, resources, history = create(monkeypatch, base_body())
    item = stored(resources)
    assert result["statusCode"] == 201
    assert item["operational_status"] == "AVAILABLE"
    assert item["Available"] is True
    assert item["tracking_mode"] == "INDIVIDUAL"
    assert history.puts == []


def test_client_cannot_combine_available_with_maintenance(monkeypatch):
    result, resources, _history = create(
        monkeypatch,
        base_body(Available=True, operational_status="MAINTENANCE"),
    )
    item = stored(resources)
    assert result["statusCode"] == 201
    assert item["Available"] is True
    assert item["operational_status"] == "AVAILABLE"


def test_client_cannot_combine_unavailable_with_available_status(monkeypatch):
    result, resources, _history = create(
        monkeypatch,
        base_body(Available=False, operational_status="AVAILABLE"),
    )
    item = stored(resources)
    assert result["statusCode"] == 201
    assert item["Available"] is True
    assert item["operational_status"] == "AVAILABLE"


def test_client_cannot_create_maintenance(monkeypatch):
    result, resources, _history = create(monkeypatch, base_body(operational_status="MAINTENANCE"))
    item = stored(resources)
    assert result["statusCode"] == 201
    assert item["operational_status"] == "AVAILABLE"
    assert item["Available"] is True


def test_client_cannot_create_retired(monkeypatch):
    result, resources, _history = create(
        monkeypatch,
        base_body(operational_status="RETIRED", Available=True),
    )
    item = stored(resources)
    assert result["statusCode"] == 201
    assert item["operational_status"] == "AVAILABLE"
    assert item["Available"] is True
    assert "RETIRED" not in json.dumps(item["operational_status"])


def test_client_cannot_supply_reserved_by(monkeypatch):
    result, resources, _history = create(monkeypatch, base_body(reserved_by="cognito-other"))
    item = stored(resources)
    assert result["statusCode"] == 201
    assert "reserved_by" not in item
    assert "cognito-other" not in json.dumps(item)


def test_client_cannot_supply_reserved_at(monkeypatch):
    result, resources, _history = create(monkeypatch, base_body(reserved_at="2020-01-01T00:00:00+00:00"))
    item = stored(resources)
    assert result["statusCode"] == 201
    assert "reserved_at" not in item


def test_client_organization_and_assignment_do_not_stick(monkeypatch):
    result, resources, _history = create(
        monkeypatch,
        base_body(
            organization_id=OTHER,
            assigned_to="member-other",
            actor_sub="forged-actor",
        ),
    )
    item = stored(resources)
    assert result["statusCode"] == 201
    assert item["organization_id"] == ORG
    assert "assigned_to" not in item
    assert "actor_sub" not in item


def test_legitimate_create_fields_remain(monkeypatch):
    result, resources, _history = create(
        monkeypatch,
        base_body(name="Renamed kit", visibility="NETWORK"),
    )
    item = stored(resources)
    body = json.loads(result["body"])["resource"]
    assert item["name"] == "Renamed kit"
    assert item["Type"] == "Kit"
    assert item["resource_type_id"] == "TYPE1"
    assert item["location_id"] == "LOC1"
    assert item["Location"] == "HQ"
    assert item["attributes"] == {"capacity": 2}
    assert item["visibility"] == "NETWORK"
    assert body["name"] == "Renamed kit"
    assert body["visibility"] == "NETWORK"


def test_quantity_create_keeps_counter_invariant(monkeypatch):
    result, resources, _history = create(
        monkeypatch,
        base_body(
            tracking_mode="QUANTITY",
            quantity_total=8,
            quantity_available=1,
            quantity_reserved=3,
            quantity_allocated=4,
            Available=True,
            operational_status="RETIRED",
        ),
    )
    item = stored(resources)
    assert result["statusCode"] == 201
    assert item["tracking_mode"] == "QUANTITY"
    assert item["operational_status"] == "AVAILABLE"
    assert item["Available"] is False
    assert item["quantity_total"] == 8
    assert item["quantity_available"] == 8
    assert item["quantity_reserved"] == 0
    assert item["quantity_allocated"] == 0
    assert item["quantity_available"] + item["quantity_reserved"] + item["quantity_allocated"] == item["quantity_total"]


def test_quantity_create_still_requires_a_total(monkeypatch):
    result, resources, _history = create(monkeypatch, base_body(tracking_mode="QUANTITY"))
    assert result["statusCode"] == 400
    assert resources.puts == []


def test_create_response_stays_on_the_resource_allowlist(monkeypatch):
    result, resources, _history = create(
        monkeypatch,
        base_body(reserved_by="secret-sub", internal_secret_field="should-never-leak"),
    )
    body = json.loads(result["body"])["resource"]
    assert set(body) <= set(RESOURCE_FIELDS)
    assert body["resource_id"] == "R-NEW"
    assert body["Available"] is True
    assert body["operational_status"] == "AVAILABLE"
    assert "reserved_by" not in body
    assert "should-never-leak" not in result["body"]
    assert "secret-sub" not in result["body"]
    assert stored(resources)["organization_id"] == ORG


def test_create_does_not_write_history_and_audits_the_authenticated_actor(monkeypatch):
    audit = Audit()
    resources, history = wire(monkeypatch, audit)
    result = resource_handler.register_resource(
        base_body(actor_sub="forged-actor", user_sub="forged-actor"),
        ORG,
        ACTOR,
        "OPERATOR",
    )
    assert result["statusCode"] == 201
    assert history.puts == []
    assert audit.events[-1]["actor_sub"] == ACTOR
    assert audit.events[-1]["organization_id"] == ORG


def test_created_individual_can_reserve_and_return(monkeypatch):
    result, resources, _history = create(monkeypatch, base_body())
    item = stored(resources)
    assert result["statusCode"] == 201

    class Mutable:
        def __init__(self, current):
            self.item = dict(current)
            self.history = []

        def update_item(self, **kwargs):
            values = kwargs.get("ExpressionAttributeValues") or {}
            expression = kwargs.get("UpdateExpression") or ""
            if ":reserved" in values and "operational_status = :reserved" in expression:
                self.item["operational_status"] = "RESERVED"
                self.item["Available"] = False
                self.item["reserved_by"] = values[":actor"]
            elif ":available" in values and "operational_status = :available" in expression:
                self.item["operational_status"] = "AVAILABLE"
                self.item["Available"] = True
                self.item.pop("reserved_by", None)
                self.item.pop("reserved_at", None)

        def put_item(self, Item, ConditionExpression=None):
            if Item.get("history_id"):
                self.history.append(Item)

        def get_item(self, Key):
            return {"Item": dict(self.item)}

        def transact_write_items(self, TransactItems):
            for step in TransactItems:
                update = step["Update"]
                if "resource_id" not in update["Key"]:
                    continue
                expression = update.get("UpdateExpression") or ""
                values = {
                    key: next(iter(value.values()))
                    for key, value in update["ExpressionAttributeValues"].items()
                }
                if "operational_status = :available" in expression:
                    self.item["operational_status"] = values[":available"]
                    self.item["Available"] = values[":true"]

    table = Mutable(item)
    table.meta = type("Meta", (), {"client": table})()
    tables = {
        "resources": table,
        "allocations": type("Alloc", (), {"get_item": lambda self, Key: {"Item": {}}, "update_item": lambda self, **kwargs: None})(),
        "history": table,
        "audit": Audit(),
    }
    everyday.reserve_individual({}, ORG, ACTOR, "OPERATOR", table.item, tables)
    assert table.item["operational_status"] == "RESERVED"
    assert table.item["reserved_by"] == ACTOR
    assert table.history[-1]["actor_sub"] == ACTOR

    everyday.release_reservation({}, ORG, ACTOR, "OPERATOR", table.item, tables)
    assert table.item["operational_status"] == "AVAILABLE"
    assert table.item["Available"] is True

    commit_allocation = everyday._commit_everyday_allocation
    monkeypatch.setattr(everyday, "_commit_everyday_allocation", lambda *args, **kwargs: None)
    everyday.everyday_allocate_individual({}, ORG, ACTOR, "OPERATOR", table.item, tables)
    monkeypatch.setattr(everyday, "_commit_everyday_allocation", commit_allocation)
    assert table.history[-1]["reason"] == "EVERYDAY_RESOURCE_ALLOCATED"
    assert table.history[-1]["actor_sub"] == ACTOR

    table.item["operational_status"] = "ALLOCATED"
    table.item["Available"] = False
    tables["allocations"].get_item = lambda Key: {
        "Item": {
            "allocation_id": "EVERYDAY-R1",
            "organization_id": ORG,
            "resource_id": "R-NEW",
            "allocation_type": "EVERYDAY",
            "status": "OPEN",
            "quantity": 1,
        }
    }
    everyday.everyday_return(
        {"allocation_id": "EVERYDAY-R1"},
        ORG,
        ACTOR,
        "OPERATOR",
        table.item,
        tables,
    )
    assert table.item["operational_status"] == "AVAILABLE"
    assert table.item["Available"] is True
    assert table.history[-1]["reason"] == "EVERYDAY_RESOURCE_RETURNED"
    assert table.history[-1]["actor_sub"] == ACTOR
