"""Phase 4 advanced resource lifecycle tests."""

import copy
import importlib.util
import sys
from pathlib import Path

import pytest
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [
    str(ROOT / "src"),
    str(ROOT / "src" / "shared"),
    str(ROOT / "src" / "organization"),
]

from resource_state import (
    ALLOWED_RESOURCE_TRANSITIONS,
    ResourceStateError,
    emergency_matchable,
    lifecycle_fields_from_body,
    metadata_fields_from_body,
    validate_transition,
)


def load_module(name, relative):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


lifecycle = load_module("lifecycle_ops_test", "src/shared/lifecycle_operations.py")
public_api = load_module("public_lifecycle_test", "src/public/handler.py")

ORG = "ORG-A"
USER = "user-a"


def _conditional_failed():
    return ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")


class Table:
    def __init__(self, items=None):
        self.name = "Resources"
        self.items = {item["resource_id"]: copy.deepcopy(item) for item in (items or [])}
        self.allocation_items = {}
        self.history = []
        self.meta = type("Meta", (), {"client": self})()

    def get_item(self, Key):
        if "resource_id" in Key:
            item = self.items.get(Key["resource_id"])
            return {"Item": copy.deepcopy(item)} if item else {}
        item = self.allocation_items.get(Key["allocation_id"])
        return {"Item": copy.deepcopy(item)} if item else {}

    def update_item(self, **kwargs):
        key = kwargs["Key"]["resource_id"]
        item = self.items[key]
        expr = kwargs.get("UpdateExpression", "")
        values = kwargs.get("ExpressionAttributeValues", {})
        condition = kwargs.get("ConditionExpression", "")

        if item.get("organization_id") != values.get(":organization_id"):
            raise _conditional_failed()

        if "Available = :true" in condition and not item.get("Available"):
            raise _conditional_failed()

        if "operational_status = :current" in condition:
            current = item.get("operational_status") or ("AVAILABLE" if item.get("Available") else "ALLOCATED")
            if current != values.get(":current"):
                raise _conditional_failed()

        if "operational_status <> :retired" in condition and item.get("operational_status") == "RETIRED":
            raise _conditional_failed()

        if "SET" in expr:
            set_part = expr.split("REMOVE")[0].replace("SET", "").strip()
            for chunk in set_part.split(","):
                chunk = chunk.strip()
                if not chunk or "=" not in chunk:
                    continue
                left, right = [part.strip() for part in chunk.split("=", 1)]
                if right.startswith(":"):
                    item[left] = values[right]

        if "REMOVE" in expr:
            for name in expr.split("REMOVE", 1)[1].split(","):
                item.pop(name.strip(), None)

    def put_item(self, Item, ConditionExpression=None):
        if Item.get("history_id"):
            self.history.append(copy.deepcopy(Item))
            return
        if "allocation_id" in Item:
            self.allocation_items[Item["allocation_id"]] = copy.deepcopy(Item)


class AuditCapture:
    def __init__(self):
        self.events = []

    def put_item(self, Item):
        self.events.append(copy.deepcopy(Item))


def store(resource, allocations=None):
    table = Table([resource])
    if allocations:
        for item in allocations:
            table.allocation_items[item["allocation_id"]] = dict(item)
    allocations_table = type(
        "Alloc",
        (),
        {"name": "Allocations", "allocation_items": table.allocation_items, "meta": table.meta},
    )()
    allocations_table.get_item = lambda Key: table.get_item(Key)
    return {
        "resources": table,
        "allocations": allocations_table,
        "history": table,
        "audit": AuditCapture(),
    }


def individual(resource_id="R1", status="AVAILABLE", available=True, **extra):
    item = {
        "resource_id": resource_id,
        "organization_id": ORG,
        "Available": available,
        "operational_status": status,
        "tracking_mode": "INDIVIDUAL",
        "location_id": "LOC1",
        "Type": "Kit",
        "Location": "HQ",
    }
    item.update(extra)
    return item


@pytest.mark.parametrize(
    "current,target",
    [
        (current, target)
        for current, targets in ALLOWED_RESOURCE_TRANSITIONS.items()
        for target in targets
    ],
)
def test_allowed_transitions(current, target):
    validate_transition(current, target)


@pytest.mark.parametrize(
    "current,target",
    [
        ("RETIRED", "AVAILABLE"),
        ("RETIRED", "ALLOCATED"),
        ("RETIRED", "IN_USE"),
        ("RETIRED", "MAINTENANCE"),
        ("MAINTENANCE", "ALLOCATED"),
        ("DAMAGED", "ALLOCATED"),
        ("DAMAGED", "IN_USE"),
        ("RESERVED", "RETIRED"),
        ("ALLOCATED", "RETIRED"),
        ("IN_USE", "RETIRED"),
        ("ALLOCATED", "MAINTENANCE"),
        ("ALLOCATED", "DAMAGED"),
    ],
)
def test_blocked_transitions(current, target):
    with pytest.raises(ResourceStateError):
        validate_transition(current, target)


def test_put_blocks_lifecycle_fields():
    assert "operational_status" in lifecycle_fields_from_body({"operational_status": "RETIRED"})
    assert "assigned_to" in lifecycle_fields_from_body({"assigned_to": "u1"})


def test_metadata_fields():
    fields = metadata_fields_from_body(
        {"condition": "good", "serial_number": "S1", "asset_tag": "A1", "department": "Ops"}
    )
    assert fields["condition"] == "GOOD"
    assert fields["serial_number"] == "S1"


def test_maintenance_and_complete():
    tables = store(individual())
    lifecycle.start_maintenance({"notes": "service"}, ORG, USER, "OPERATOR", tables["resources"].items["R1"], tables)
    assert tables["resources"].items["R1"]["operational_status"] == "MAINTENANCE"
    assert tables["resources"].items["R1"]["Available"] is False
    lifecycle.complete_maintenance({}, ORG, USER, "OPERATOR", tables["resources"].items["R1"], tables)
    assert tables["resources"].items["R1"]["operational_status"] == "AVAILABLE"
    assert tables["resources"].items["R1"]["Available"] is True
    assert any(event["action"] == "resource.maintenance_start" for event in tables["audit"].events)
    assert any(entry["reason"] == "RESOURCE_MAINTENANCE_STARTED" for entry in tables["history"].history)


def test_damage_and_recover():
    tables = store(individual())
    lifecycle.mark_damaged({"reason": "crack"}, ORG, USER, "OPERATOR", tables["resources"].items["R1"], tables)
    assert tables["resources"].items["R1"]["operational_status"] == "DAMAGED"
    lifecycle.recover_damage({}, ORG, USER, "OPERATOR", tables["resources"].items["R1"], tables)
    assert tables["resources"].items["R1"]["operational_status"] == "AVAILABLE"


def test_retire_blocks_active_everyday():
    resource = individual(status="AVAILABLE")
    tables = store(
        resource,
        allocations=[
            {
                "allocation_id": "EVERYDAY-1",
                "resource_id": "R1",
                "organization_id": ORG,
                "allocation_type": "EVERYDAY",
                "status": "OPEN",
            }
        ],
    )
    with pytest.raises(lifecycle.LifecycleOperationError) as error:
        lifecycle.retire_resource({}, ORG, USER, "OPERATOR", resource, tables)
    assert error.value.status_code == 409


def test_retire_blocks_emergency():
    resource = individual(status="AVAILABLE")
    tables = store(
        resource,
        allocations=[
            {
                "allocation_id": "ALLOC-1",
                "resource_id": "R1",
                "organization_id": ORG,
                "status": "ALLOCATED",
                "request_id": "Q1",
            }
        ],
    )
    with pytest.raises(lifecycle.LifecycleOperationError):
        lifecycle.start_maintenance({}, ORG, USER, "OPERATOR", resource, tables)


def test_retire_success():
    tables = store(individual())
    lifecycle.retire_resource({}, ORG, USER, "OPERATOR", tables["resources"].items["R1"], tables)
    assert tables["resources"].items["R1"]["operational_status"] == "RETIRED"
    assert tables["resources"].items["R1"]["Available"] is False


def test_assign_and_unassign():
    tables = store(individual())
    lifecycle.assign_resource(
        {"assigned_to": "member-1", "department": "EMS"},
        ORG,
        USER,
        "OPERATOR",
        tables["resources"].items["R1"],
        tables,
    )
    assert tables["resources"].items["R1"]["assigned_to"] == "member-1"
    lifecycle.unassign_resource({"clear_all": True}, ORG, USER, "OPERATOR", tables["resources"].items["R1"], tables)
    assert "assigned_to" not in tables["resources"].items["R1"]
    assert any(event["action"] == "resource.assign" for event in tables["audit"].events)
    assert not any(entry.get("reason") == "RESOURCE_ASSIGNED" for entry in tables["history"].history)


def test_in_use_and_return():
    tables = store(individual(status="ALLOCATED", available=False))
    lifecycle.mark_in_use({}, ORG, USER, "OPERATOR", tables["resources"].items["R1"], tables)
    assert tables["resources"].items["R1"]["operational_status"] == "IN_USE"
    lifecycle.return_to_available({}, ORG, USER, "OPERATOR", tables["resources"].items["R1"], tables)
    assert tables["resources"].items["R1"]["operational_status"] == "AVAILABLE"


def test_concurrent_maintenance():
    tables = store(individual())
    lifecycle.start_maintenance({}, ORG, USER, "OPERATOR", tables["resources"].items["R1"], tables)
    with pytest.raises(lifecycle.LifecycleOperationError) as error:
        lifecycle.start_maintenance({}, ORG, USER, "OPERATOR", tables["resources"].items["R1"], tables)
    assert error.value.status_code == 409
    assert len([event for event in tables["audit"].events if event["action"] == "resource.maintenance_start"]) == 1


def test_quantity_retire_requires_idle_pool():
    resource = {
        "resource_id": "Q1",
        "organization_id": ORG,
        "tracking_mode": "QUANTITY",
        "Available": False,
        "operational_status": "AVAILABLE",
        "quantity_total": 10,
        "quantity_available": 7,
        "quantity_reserved": 0,
        "quantity_allocated": 3,
        "location_id": "LOC1",
        "Type": "Supplies",
        "Location": "HQ",
    }
    tables = store(resource)
    with pytest.raises(lifecycle.LifecycleOperationError):
        lifecycle.retire_resource({}, ORG, USER, "OPERATOR", resource, tables)


def test_emergency_matchable_lifecycle_states():
    assert emergency_matchable({"Available": True}) is True
    assert emergency_matchable({"Available": True, "operational_status": "AVAILABLE"}) is True
    for status in ("RESERVED", "ALLOCATED", "IN_USE", "MAINTENANCE", "DAMAGED", "RETIRED"):
        assert emergency_matchable({"Available": True, "operational_status": status}) is False
    assert emergency_matchable({"Available": False, "operational_status": "MAINTENANCE"}) is False


def test_public_view_hides_assignment_and_serial():
    view = public_api.public_view(
        {
            "visibility_key": "PUBLIC",
            "visibility": "PUBLIC",
            "public_type_name": "Kit",
            "public_name": "Public",
            "public_city": "City",
            "organization_id": ORG,
            "serial_number": "SECRET",
            "assigned_to": "user",
            "department": "Ops",
            "operational_status": "MAINTENANCE",
        }
    )
    payload = str(view)
    assert "SECRET" not in payload
    assert "assigned_to" not in payload
    assert "department" not in payload
    assert "operational_status" not in payload
