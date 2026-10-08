"""Phase 4 advanced resource lifecycle tests."""

import copy
import importlib.util
import json
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
        self.last_update = None
        self.meta = type("Meta", (), {"client": self})()

    def get_item(self, Key):
        if "resource_id" in Key:
            item = self.items.get(Key["resource_id"])
            return {"Item": copy.deepcopy(item)} if item else {}
        item = self.allocation_items.get(Key["allocation_id"])
        return {"Item": copy.deepcopy(item)} if item else {}

    def update_item(self, **kwargs):
        self.last_update = kwargs
        key = kwargs["Key"]
        expr = kwargs.get("UpdateExpression", "")
        values = kwargs.get("ExpressionAttributeValues", {})
        condition = kwargs.get("ConditionExpression", "")
        names = kwargs.get("ExpressionAttributeNames") or {}

        if "allocation_id" in key:
            alloc = self.allocation_items[key["allocation_id"]]
            if ":open" in values and alloc.get("status") != values.get(":open"):
                raise _conditional_failed()
            if names.get("#status") == "status" or "#status" in expr:
                alloc["status"] = values.get(":returned", "RETURNED")
                alloc["returned_at"] = values.get(":now")
                alloc["returned_by"] = values.get(":actor")
                alloc["updated_at"] = values.get(":now")
            return

        item = self.items[key["resource_id"]]

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
                attr = names.get(left, left)
                if right.startswith(":"):
                    item[attr] = values[right]

        if "REMOVE" in expr:
            for name in expr.split("REMOVE", 1)[1].split(","):
                attr = names.get(name.strip(), name.strip())
                item.pop(attr, None)

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
    allocations_table.update_item = table.update_item
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


def test_maintenance_complete_with_condition_uses_attribute_names():
    tables = store(individual(status="MAINTENANCE", available=False))
    lifecycle.complete_maintenance(
        {"condition": "GOOD"},
        ORG,
        USER,
        "OPERATOR",
        tables["resources"].items["R1"],
        tables,
    )
    resource = tables["resources"].items["R1"]
    assert resource["operational_status"] == "AVAILABLE"
    assert resource["condition"] == "GOOD"
    assert "#condition" not in resource
    update = tables["resources"].last_update
    assert "#condition = :condition" in update["UpdateExpression"]
    assert update["ExpressionAttributeNames"]["#condition"] == "condition"
    assert update["ExpressionAttributeValues"][":condition"] == "GOOD"


def test_damage_and_recover():
    tables = store(individual())
    lifecycle.mark_damaged({"reason": "crack"}, ORG, USER, "OPERATOR", tables["resources"].items["R1"], tables)
    assert tables["resources"].items["R1"]["operational_status"] == "DAMAGED"
    lifecycle.recover_damage({}, ORG, USER, "OPERATOR", tables["resources"].items["R1"], tables)
    assert tables["resources"].items["R1"]["operational_status"] == "AVAILABLE"


def test_damage_recover_with_condition_uses_attribute_names():
    tables = store(individual(status="DAMAGED", available=False))
    lifecycle.recover_damage(
        {"target_status": "AVAILABLE", "condition": "FAIR"},
        ORG,
        USER,
        "OPERATOR",
        tables["resources"].items["R1"],
        tables,
    )
    resource = tables["resources"].items["R1"]
    assert resource["operational_status"] == "AVAILABLE"
    assert resource["condition"] == "FAIR"
    update = tables["resources"].last_update
    assert "#condition = :condition" in update["UpdateExpression"]
    assert update["ExpressionAttributeNames"]["#condition"] == "condition"


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


def test_in_use_return_closes_open_everyday():
    tables = store(
        individual(status="IN_USE", available=False),
        allocations=[
            {
                "allocation_id": "EVERYDAY-R1-OPEN1",
                "resource_id": "R1",
                "organization_id": ORG,
                "allocation_type": "EVERYDAY",
                "status": "OPEN",
            },
            {
                "allocation_id": "ALLOC-EMERGENCY-1",
                "resource_id": "R1",
                "organization_id": ORG,
                "status": "RELEASED",
                "request_id": "Q1",
            },
        ],
    )
    lifecycle.return_to_available({}, ORG, USER, "OPERATOR", tables["resources"].items["R1"], tables)
    assert tables["resources"].items["R1"]["operational_status"] == "AVAILABLE"
    assert tables["allocations"].allocation_items["EVERYDAY-R1-OPEN1"]["status"] == "RETURNED"
    assert tables["allocations"].allocation_items["ALLOC-EMERGENCY-1"]["status"] == "RELEASED"


def test_in_use_return_blocks_active_emergency():
    tables = store(
        individual(status="IN_USE", available=False),
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
    with pytest.raises(lifecycle.LifecycleOperationError) as error:
        lifecycle.return_to_available({}, ORG, USER, "OPERATOR", tables["resources"].items["R1"], tables)
    assert error.value.status_code == 409
    assert tables["allocations"].allocation_items["ALLOC-1"]["status"] == "ALLOCATED"


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


resource_handler = load_module("resource_handler_rm01", "src/resource/handler.py")


class MetadataTable:
    """Applies the handler's UpdateItem condition instead of replacing the item."""

    def __init__(self, item):
        self.items = {item["resource_id"]: copy.deepcopy(item)}
        self.after_read = None
        self.puts = []
        self.last_update = None

    def get_item(self, Key):
        item = copy.deepcopy(self.items[Key["resource_id"]])
        hook = self.after_read
        self.after_read = None
        if hook:
            hook()
        return {"Item": item}

    def put_item(self, Item, ConditionExpression=None):
        self.puts.append(copy.deepcopy(Item))
        raise AssertionError("metadata update must not put the whole item")

    def update_item(self, **kwargs):
        self.last_update = kwargs
        item = self.items[kwargs["Key"]["resource_id"]]
        values = kwargs.get("ExpressionAttributeValues") or {}
        names = kwargs.get("ExpressionAttributeNames") or {}
        if not _condition_holds(item, kwargs.get("ConditionExpression") or "", values):
            raise _conditional_failed()
        expression = kwargs.get("UpdateExpression") or ""
        set_part, _, remove_part = expression.partition("REMOVE")
        if "SET" in set_part:
            for chunk in set_part.split("SET", 1)[1].split(","):
                left, right = [part.strip() for part in chunk.split("=", 1)]
                item[names[left]] = values[right]
        if remove_part.strip():
            for token in remove_part.split(","):
                item.pop(names[token.strip()], None)


def _condition_holds(item, condition, values):
    for clause in [part.strip() for part in condition.split(" AND ") if part.strip()]:
        if clause.startswith("attribute_not_exists(") and clause.endswith(")"):
            if clause[len("attribute_not_exists("):-1] in item:
                return False
            continue
        left, right = [part.strip() for part in clause.split("=", 1)]
        if item.get(left) != values[right]:
            return False
    return True


class _Lookup:
    def __init__(self, items):
        self.items = items

    def get_item(self, Key):
        if "resource_type_id" in Key:
            found = self.items.get((Key["organization_id"], Key["resource_type_id"]))
        else:
            found = self.items.get((Key["organization_id"], Key["location_id"]))
        return {"Item": copy.deepcopy(found)} if found else {}


def _wire_update(monkeypatch, resource):
    table = MetadataTable(resource)
    locations = _Lookup({
        (ORG, "LOC1"): {"organization_id": ORG, "location_id": "LOC1", "name": "HQ", "status": "ACTIVE", "city": "City"},
        (ORG, "LOC2"): {"organization_id": ORG, "location_id": "LOC2", "name": "Depot", "status": "ACTIVE", "city": "Town"},
    })
    types = _Lookup({
        (ORG, "TYPE1"): {"organization_id": ORG, "resource_type_id": "TYPE1", "name": "Kit", "status": "ACTIVE", "attributes_schema": {"fields": []}},
    })
    monkeypatch.setattr(resource_handler, "resources_table", lambda: table)
    monkeypatch.setattr(resource_handler, "locations_table", lambda: locations)
    monkeypatch.setattr(resource_handler, "resource_types_table", lambda: types)
    monkeypatch.setattr(resource_handler, "audit_table", lambda: AuditCapture())
    return table


def _body(result):
    return result["statusCode"], json.loads(result["body"])


def test_metadata_update_loses_to_concurrent_reserve(monkeypatch):
    table = _wire_update(monkeypatch, individual(name="Old", visibility="PRIVATE", location_id="LOC1", Location="HQ"))

    def reserve():
        item = table.items["R1"]
        item["Available"] = False
        item["operational_status"] = "RESERVED"
        item["reserved_by"] = "other-user"
        item["reserved_at"] = "2026-10-08T00:00:00+00:00"

    table.after_read = reserve
    status, body = _body(resource_handler.update_resource({"resource_id": "R1", "name": "New Name"}, ORG, USER, "OPERATOR"))
    saved = table.items["R1"]
    assert status == 409
    assert body["message"] == "Resource state conflict"
    assert saved["name"] == "Old"
    assert saved["Available"] is False
    assert saved["operational_status"] == "RESERVED"
    assert saved["reserved_by"] == "other-user"
    assert table.puts == []


def test_metadata_update_changes_name_location_and_visibility(monkeypatch):
    table = _wire_update(
        monkeypatch,
        individual(name="Old", visibility="PRIVATE", location_id="LOC1", Location="HQ", resource_type_id="TYPE1"),
    )
    status, body = _body(
        resource_handler.update_resource(
            {
                "resource_id": "R1",
                "name": "Renamed",
                "location_id": "LOC2",
                "visibility": "NETWORK",
            },
            ORG,
            USER,
            "OPERATOR",
        )
    )
    saved = table.items["R1"]
    assert status == 200
    assert body["message"] == "Resource updated"
    assert saved["name"] == "Renamed"
    assert saved["location_id"] == "LOC2"
    assert saved["Location"] == "Depot"
    assert saved["visibility"] == "NETWORK"
    assert saved["Available"] is True
    assert saved["operational_status"] == "AVAILABLE"
    assert "Available" not in table.last_update["UpdateExpression"].split("REMOVE")[0].split("SET", 1)[-1]
    assert table.puts == []


def test_name_only_update_leaves_other_fields(monkeypatch):
    table = _wire_update(
        monkeypatch,
        individual(
            name="Old",
            visibility="PUBLIC",
            location_id="LOC1",
            Location="HQ",
            tracking_mode="QUANTITY",
            quantity_total=10,
            quantity_available=6,
            quantity_reserved=1,
            quantity_allocated=3,
            public_name="Public",
        ),
    )
    status, _body_payload = _body(resource_handler.update_resource({"resource_id": "R1", "name": "Renamed"}, ORG, USER, "OPERATOR"))
    saved = table.items["R1"]
    set_clause = table.last_update["UpdateExpression"].split("REMOVE")[0]
    assert status == 200
    assert saved["name"] == "Renamed"
    assert saved["location_id"] == "LOC1"
    assert saved["visibility"] == "PUBLIC"
    assert saved["Available"] is True
    assert saved["operational_status"] == "AVAILABLE"
    assert saved["quantity_total"] == 10
    assert saved["quantity_available"] == 6
    assert saved["quantity_reserved"] == 1
    assert saved["quantity_allocated"] == 3
    assert "reserved_by" not in saved
    assert "Available =" not in set_clause
    assert "operational_status =" not in set_clause
    assert "quantity_" not in set_clause


def test_metadata_update_loses_to_concurrent_allocation(monkeypatch):
    table = _wire_update(monkeypatch, individual(name="Old", visibility="PRIVATE"))

    def allocate():
        item = table.items["R1"]
        item["Available"] = False
        item["operational_status"] = "ALLOCATED"

    table.after_read = allocate
    status, body = _body(resource_handler.update_resource({"resource_id": "R1", "name": "New Name"}, ORG, USER, "OPERATOR"))
    saved = table.items["R1"]
    assert status == 409
    assert body["message"] == "Resource state conflict"
    assert saved["name"] == "Old"
    assert saved["Available"] is False
    assert saved["operational_status"] == "ALLOCATED"
    assert table.puts == []
