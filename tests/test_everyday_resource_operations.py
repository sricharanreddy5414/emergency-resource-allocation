import importlib.util
import json
import sys
from pathlib import Path

import pytest
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [
    str(ROOT / "src" / "shared"),
    str(ROOT / "src" / "organization"),
]

import access
from resource_state import emergency_matchable


def load_module(name, relative):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


everyday = load_module("everyday_ops_test", "src/shared/everyday_operations.py")
matching = load_module("matching_everyday_test", "src/shared/matching.py")
auto_release = load_module("auto_release_everyday_test", "src/auto_release/handler.py")

ORG = "ORG-A"
USER = "user-a"


class Table:
    def __init__(self, name, items=None):
        self.name = name
        self.items = {item["resource_id"]: dict(item) for item in (items or []) if item.get("resource_id")}
        self.allocation_items = {}
        self.history = []
        self.meta = type("Meta", (), {"client": self})()

    def get_item(self, Key):
        if "resource_id" in Key:
            item = self.items.get(Key["resource_id"])
            return {"Item": dict(item)} if item else {}

        item = self.allocation_items.get(Key["allocation_id"])
        return {"Item": dict(item)} if item else {}

    def update_item(self, **kwargs):
        key = kwargs["Key"]
        expr = kwargs.get("UpdateExpression", "")
        values = kwargs.get("ExpressionAttributeValues", {})
        condition = kwargs.get("ConditionExpression", "")
        names = kwargs.get("ExpressionAttributeNames", {})

        if "allocation_id" in key:
            alloc = self.allocation_items[key["allocation_id"]]
            if ":open" in values and alloc.get("status") != values.get(":open"):
                raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")
            if names.get("#status") == "status" or "#status" in expr:
                alloc["status"] = values.get(":returned", "RETURNED")
                alloc["returned_at"] = values.get(":now")
                alloc["returned_by"] = values.get(":actor")
                alloc["updated_at"] = values.get(":now")
            return

        item = self.items[key["resource_id"]]

        if "Available = :true" in condition and not item.get("Available"):
            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")

        if "operational_status = :reserved" in condition and item.get("operational_status") != "RESERVED":
            if item.get("operational_status") not in (None, "AVAILABLE"):
                raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")

        if "reserved_by = :actor" in condition and item.get("reserved_by") != values.get(":actor"):
            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")

        if "operational_status = :allocated" in condition and item.get("operational_status") not in (
            None,
            "ALLOCATED",
            "IN_USE",
        ):
            # condition uses OR with in_use for return path; allocate path checks Available
            pass

        if (
            "Available = :true" in condition
            or "(attribute_not_exists(operational_status) OR operational_status = :available)" in condition
        ):
            if "Available = :true" in condition and not item.get("Available"):
                raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")
            stored = item.get("operational_status")
            if (
                "operational_status = :available" in condition
                or "OR operational_status = :available" in condition
            ):
                if stored is not None and str(stored).upper() not in {"AVAILABLE", "ALLOCATED", "IN_USE"}:
                    raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")

        if "operational_status = :reserved" in expr:
            item["operational_status"] = values[":reserved"]
            item["Available"] = values[":false"]
            item["reserved_by"] = values[":actor"]
            item["reserved_at"] = values[":now"]

        if "operational_status = :allocated" in expr:
            item["operational_status"] = values[":allocated"]
            item["Available"] = values.get(":false", False)

        if "operational_status = :available" in expr:
            item["operational_status"] = values[":available"]
            item["Available"] = values[":true"]
            item.pop("reserved_by", None)
            item.pop("reserved_at", None)

    def put_item(self, Item, ConditionExpression=None):
        if Item.get("history_id"):
            self.history.append(Item)
            return
        if "allocation_id" in Item:
            if ConditionExpression and Item["allocation_id"] in self.allocation_items:
                raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "PutItem")
            if Item["allocation_id"] in self.allocation_items:
                raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "PutItem")
            self.allocation_items[Item["allocation_id"]] = dict(Item)
        else:
            self.history.append(Item)

    def transact_write_items(self, TransactItems):
        items_before = {key: dict(value) for key, value in self.items.items()}
        allocations_before = {key: dict(value) for key, value in self.allocation_items.items()}
        try:
            for step in TransactItems:
                if "Update" in step:
                    update = step["Update"]
                    if "resource_id" in update["Key"]:
                        key = update["Key"]["resource_id"]["S"]
                        item = self.items[key]
                        condition = update.get("ConditionExpression", "")
                        if "Available = :true" in condition and not item.get("Available", True):
                            raise ClientError(
                                {"Error": {"Code": "ConditionalCheckFailedException"}},
                                "TransactWriteItems",
                            )
                        if "operational_status = :reserved" in condition and item.get("operational_status") != "RESERVED":
                            raise ClientError(
                                {"Error": {"Code": "ConditionalCheckFailedException"}},
                                "TransactWriteItems",
                            )
                        expr = update.get("UpdateExpression", "")
                        if "operational_status = :allocated" in expr:
                            item["operational_status"] = "ALLOCATED"
                            item["Available"] = False
                            if "REMOVE" in expr:
                                item.pop("reserved_by", None)
                                item.pop("reserved_at", None)
                        if "operational_status = :available" in expr:
                            item["operational_status"] = "AVAILABLE"
                            item["Available"] = True
                    elif "allocation_id" in update["Key"]:
                        allocation_id = update["Key"]["allocation_id"]["S"]
                        alloc = self.allocation_items[allocation_id]
                        if alloc.get("status") != "OPEN":
                            raise ClientError(
                                {"Error": {"Code": "ConditionalCheckFailedException"}},
                                "TransactWriteItems",
                            )
                        alloc["status"] = "RETURNED"
                if "Put" in step:
                    alloc = {}
                    for key, value in step["Put"]["Item"].items():
                        alloc[key] = list(value.values())[0]
                    if "attribute_not_exists" in (step["Put"].get("ConditionExpression") or ""):
                        if alloc["allocation_id"] in self.allocation_items:
                            raise ClientError(
                                {"Error": {"Code": "ConditionalCheckFailedException"}},
                                "TransactWriteItems",
                            )
                    self.allocation_items[alloc["allocation_id"]] = alloc
        except ClientError:
            self.items.clear()
            self.items.update(items_before)
            self.allocation_items.clear()
            self.allocation_items.update(allocations_before)
            raise


class AuditTable:
    def put_item(self, Item):
        return None


def tables(resource):
    table = Table("Resources", [resource])
    allocations = type("Alloc", (), {"name": "Allocations", "allocation_items": {}, "meta": table.meta})()
    allocations.allocation_items = table.allocation_items
    allocations.get_item = lambda Key: table.get_item(Key)
    allocations.put_item = table.put_item
    allocations.update_item = table.update_item
    return {
        "resources": table,
        "allocations": allocations,
        "history": table,
        "audit": AuditTable(),
    }


def test_emergency_matchable_legacy():
    assert emergency_matchable({"Available": True}) is True
    assert emergency_matchable({"Available": False}) is False


def test_emergency_matchable_reserved():
    assert emergency_matchable({"Available": False, "operational_status": "RESERVED"}) is False


def test_emergency_matchable_quantity():
    assert emergency_matchable({"Available": True, "tracking_mode": "QUANTITY"}) is False


def test_individual_reserve_and_release():
    resource = {
        "resource_id": "R1",
        "organization_id": ORG,
        "Available": True,
        "operational_status": "AVAILABLE",
        "tracking_mode": "INDIVIDUAL",
        "location_id": "LOC1",
        "Type": "Kit",
        "Location": "HQ",
    }
    store = tables(resource)
    everyday.reserve_individual({}, ORG, USER, "OPERATOR", resource, store)
    updated = store["resources"].items["R1"]
    assert updated["operational_status"] == "RESERVED"
    assert updated["Available"] is False
    everyday.release_reservation({}, ORG, USER, "OPERATOR", updated, store)
    final = store["resources"].items["R1"]
    assert final["operational_status"] == "AVAILABLE"
    assert final["Available"] is True


def test_everyday_allocation_uses_open_status():
    resource = {
        "resource_id": "R2",
        "organization_id": ORG,
        "Available": True,
        "operational_status": "AVAILABLE",
        "tracking_mode": "INDIVIDUAL",
        "location_id": "LOC1",
        "Type": "Kit",
        "Location": "HQ",
    }
    store = tables(resource)
    result = everyday.everyday_allocate_individual({}, ORG, USER, "OPERATOR", resource, store)
    alloc = store["allocations"].allocation_items[result["allocation_id"]]
    assert alloc["allocation_type"] == "EVERYDAY"
    assert alloc["status"] == "OPEN"
    assert "request_id" not in alloc


def test_auto_release_ignores_open_everyday():
    decision = auto_release.plan_release(
        {"allocation_id": "EVERYDAY-X", "status": "OPEN", "organization_id": ORG, "resource_id": "R1"},
        {"resource_id": "R1", "organization_id": ORG},
        None,
        [],
    )
    assert decision["action"] == "skip"


def test_everyday_allocate_then_return():
    resource = {
        "resource_id": "R3",
        "organization_id": ORG,
        "Available": True,
        "operational_status": "AVAILABLE",
        "tracking_mode": "INDIVIDUAL",
        "location_id": "LOC1",
        "Type": "Kit",
        "Location": "HQ",
    }
    store = tables(resource)
    created = everyday.everyday_allocate_individual({}, ORG, USER, "OPERATOR", resource, store)
    allocation_id = created["allocation_id"]
    allocated = store["resources"].items["R3"]
    assert allocated["operational_status"] == "ALLOCATED"
    everyday.everyday_return(
        {"allocation_id": allocation_id},
        ORG,
        USER,
        "OPERATOR",
        allocated,
        store,
    )
    final = store["resources"].items["R3"]
    assert final["operational_status"] == "AVAILABLE"
    assert final["Available"] is True
    assert store["allocations"].allocation_items[allocation_id]["status"] == "RETURNED"


def test_matching_skips_reserved_resource():
    request = {
        "organization_id": ORG,
        "location_id": "LOC1",
        "resource_type": "Kit",
    }
    resources = [
        {
            "resource_id": "R-LEG",
            "organization_id": ORG,
            "location_id": "LOC1",
            "Available": True,
            "Type": "Kit",
            "Location": "HQ",
        },
        {
            "resource_id": "R-RES",
            "organization_id": ORG,
            "location_id": "LOC1",
            "Available": False,
            "operational_status": "RESERVED",
            "Type": "Kit",
            "Location": "HQ",
        },
    ]
    chosen = matching.choose_resource(resources, request)
    assert chosen["resource_id"] == "R-LEG"


def test_auto_release_keeps_emergency_allocated():
    decision = auto_release.plan_release(
        {
            "allocation_id": "ALLOC-REQ1",
            "status": "ALLOCATED",
            "organization_id": ORG,
            "resource_id": "R1",
            "request_id": "REQ1",
        },
        {"resource_id": "R1", "organization_id": ORG},
        {"request_id": "REQ1", "organization_id": ORG},
        [],
    )
    assert decision["action"] == "release"
