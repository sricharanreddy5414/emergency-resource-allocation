"""RM-04: everyday allocation commits the resource and allocation together."""

import copy
import inspect

import pytest
from botocore.exceptions import ClientError

import everyday_operations as everyday

ORG = "ORG-A"
USER = "operator-sub"


def _failed():
    return ClientError({"Error": {"Code": "ConditionalCheckFailedException", "Message": "conflict"}}, "TransactWriteItems")


def _decode(value):
    if "S" in value:
        return value["S"]
    if "N" in value:
        number = value["N"]
        return int(number) if "." not in str(number) else float(number)
    if "BOOL" in value:
        return value["BOOL"]
    return next(iter(value.values()))


def _holds(item, condition, values):
    if "organization_id = :organization_id" in condition and item.get("organization_id") != values.get(":organization_id"):
        return False
    if "attribute_exists(resource_id)" in condition and "resource_id" not in item:
        return False
    if "tracking_mode = :quantity" in condition and item.get("tracking_mode") != values.get(":quantity"):
        return False
    if "Available = :true" in condition and item.get("Available") is not True:
        return False
    if "operational_status = :reserved" in condition and item.get("operational_status") != values.get(":reserved"):
        return False
    if "operational_status = :available" in condition or "OR operational_status = :available" in condition:
        stored = item.get("operational_status")
        if stored is not None and stored != "AVAILABLE":
            return False
    if "quantity_available >= :qty" in condition and item.get("quantity_available", 0) < values[":qty"]:
        return False
    if "quantity_reserved >= :qty" in condition and item.get("quantity_reserved", 0) < values[":qty"]:
        return False
    if "quantity_allocated + :qty <= quantity_total" in condition:
        if item.get("quantity_allocated", 0) + values[":qty"] > item.get("quantity_total", 0):
            return False
    if "quantity_available + quantity_reserved + quantity_allocated = quantity_total" in condition:
        total = item.get("quantity_available", 0) + item.get("quantity_reserved", 0) + item.get("quantity_allocated", 0)
        if total != item.get("quantity_total"):
            return False
    if "operational_status <> :retired" in condition and item.get("operational_status") == values.get(":retired"):
        return False
    return True


def _apply(item, expression, values):
    if "operational_status = :allocated" in expression and "quantity_" not in expression:
        item["operational_status"] = values[":allocated"]
        item["Available"] = values[":false"]
        if "REMOVE" in expression:
            item.pop("reserved_by", None)
            item.pop("reserved_at", None)
        return
    quantity = values.get(":qty")
    if "quantity_available = quantity_available - :qty" in expression and "quantity_allocated = quantity_allocated + :qty" in expression:
        item["quantity_available"] -= quantity
        item["quantity_allocated"] += quantity
        return
    if "quantity_reserved = quantity_reserved - :qty" in expression and "quantity_allocated = quantity_allocated + :qty" in expression:
        item["quantity_reserved"] -= quantity
        item["quantity_allocated"] += quantity
        return
    raise AssertionError(expression)


class Resources:
    def __init__(self, item):
        self.item = copy.deepcopy(item)
        self.history = []
        self.allocation_items = {}
        self.calls = []
        self.before_commit = None
        self.fail_update = False
        self.fail_put = False
        self.meta = type("Meta", (), {"client": self})()

    def transact_write_items(self, TransactItems):
        self.calls.append(copy.deepcopy(TransactItems))
        if self.before_commit:
            callback = self.before_commit
            self.before_commit = None
            callback()
        item_before = copy.deepcopy(self.item)
        allocations_before = copy.deepcopy(self.allocation_items)
        try:
            if self.fail_update:
                self.fail_update = False
                self.item["operational_status"] = "BROKEN"
                raise _failed()
            for step in TransactItems:
                if "Update" in step:
                    update = step["Update"]
                    values = {key: _decode(value) for key, value in update["ExpressionAttributeValues"].items()}
                    if not _holds(self.item, update.get("ConditionExpression") or "", values):
                        raise _failed()
                    _apply(self.item, update.get("UpdateExpression") or "", values)
                if "Put" in step:
                    if self.fail_put:
                        self.fail_put = False
                        raise _failed()
                    allocation = {key: _decode(value) for key, value in step["Put"]["Item"].items()}
                    if "attribute_not_exists" in (step["Put"].get("ConditionExpression") or ""):
                        if allocation["allocation_id"] in self.allocation_items:
                            raise _failed()
                    self.allocation_items[allocation["allocation_id"]] = allocation
        except ClientError:
            self.item = item_before
            self.allocation_items.clear()
            self.allocation_items.update(allocations_before)
            raise

    def put_item(self, Item, ConditionExpression=None):
        self.history.append(dict(Item))


class Audit:
    def __init__(self):
        self.events = []

    def put_item(self, Item):
        self.events.append(dict(Item))


def individual(**extra):
    item = {
        "resource_id": "R1",
        "organization_id": ORG,
        "Available": True,
        "operational_status": "AVAILABLE",
        "tracking_mode": "INDIVIDUAL",
        "location_id": "LOC1",
        "Type": "Kit",
        "Location": "HQ",
        "reserved_by": "kept-until-reserved-allocate",
        "reserved_at": "kept-at",
    }
    item.update(extra)
    return item


def pool(**extra):
    item = {
        "resource_id": "Q1",
        "organization_id": ORG,
        "tracking_mode": "QUANTITY",
        "Available": False,
        "operational_status": "AVAILABLE",
        "quantity_total": 100,
        "quantity_available": 100,
        "quantity_reserved": 0,
        "quantity_allocated": 0,
        "location_id": "LOC1",
        "Type": "Supplies",
        "Location": "HQ",
    }
    item.update(extra)
    return item


def tables_for(item):
    resources = Resources(item)
    return {
        "resources": resources,
        "allocations": type("Alloc", (), {"allocation_items": resources.allocation_items})(),
        "history": resources,
        "audit": Audit(),
    }


def allocate_individual(tables, resource=None):
    return everyday.everyday_allocate_individual(
        {},
        ORG,
        USER,
        "OPERATOR",
        resource or tables["resources"].item,
        tables,
    )


def allocate_quantity(tables, quantity, from_reserved=False, resource=None):
    body = {"quantity": quantity}
    if from_reserved:
        body["from_reserved"] = True
    return everyday.everyday_allocate_quantity(
        body,
        ORG,
        USER,
        "OPERATOR",
        resource or tables["resources"].item,
        tables,
    )


def test_allocation_paths_use_one_transaction():
    for function in (everyday.everyday_allocate_individual, everyday.everyday_allocate_quantity):
        source = inspect.getsource(function)
        assert "_commit_everyday_allocation" in source
        assert "revert" not in source.lower()


def test_individual_allocation_commits_resource_and_record():
    tables = tables_for(individual())
    result = allocate_individual(tables)
    item = tables["resources"].item
    allocation = tables["resources"].allocation_items[result["allocation_id"]]
    assert item["operational_status"] == "ALLOCATED"
    assert item["Available"] is False
    assert allocation["status"] == "OPEN"
    assert allocation["allocation_type"] == "EVERYDAY"
    assert allocation["quantity"] == 1
    assert len(tables["resources"].calls) == 1
    assert [next(iter(step)) for step in tables["resources"].calls[0]] == ["Update", "Put"]
    assert len(tables["history"].history) == 1


def test_quantity_allocation_commits_counters_and_record():
    tables = tables_for(pool())
    result = allocate_quantity(tables, 20)
    item = tables["resources"].item
    allocation = tables["resources"].allocation_items[result["allocation_id"]]
    assert item["quantity_available"] == 80
    assert item["quantity_allocated"] == 20
    assert item["quantity_reserved"] == 0
    assert item["operational_status"] == "AVAILABLE"
    assert allocation["quantity"] == 20
    assert allocation["status"] == "OPEN"
    assert item["quantity_available"] + item["quantity_reserved"] + item["quantity_allocated"] == 100


def test_allocation_from_reserved_keeps_available_unchanged():
    tables = tables_for(pool(quantity_available=70, quantity_reserved=30, quantity_allocated=0))
    result = allocate_quantity(tables, 10, from_reserved=True)
    item = tables["resources"].item
    assert item["quantity_reserved"] == 20
    assert item["quantity_allocated"] == 10
    assert item["quantity_available"] == 70
    assert tables["resources"].allocation_items[result["allocation_id"]]["quantity"] == 10
    expression = tables["resources"].calls[0][0]["Update"]["UpdateExpression"]
    assert "quantity_available" not in expression


def test_concurrent_lifecycle_change_blocks_allocation():
    tables = tables_for(individual())

    def change_resource():
        tables["resources"].item["operational_status"] = "MAINTENANCE"
        tables["resources"].item["Available"] = False

    tables["resources"].before_commit = change_resource
    with pytest.raises(everyday.EverydayOperationError) as error:
        allocate_individual(tables)
    assert error.value.status_code == 409
    assert error.value.message == "Resource state conflict"
    assert "TransactionCanceled" not in error.value.message
    assert tables["resources"].allocation_items == {}
    assert tables["resources"].item["operational_status"] == "MAINTENANCE"
    assert tables["history"].history == []


def test_insufficient_quantity_creates_no_allocation():
    tables = tables_for(pool(quantity_available=20, quantity_reserved=0, quantity_allocated=80))
    with pytest.raises(everyday.EverydayOperationError) as error:
        allocate_quantity(tables, 25)
    assert error.value.status_code == 409
    assert tables["resources"].allocation_items == {}
    assert tables["resources"].item["quantity_available"] == 20
    assert tables["resources"].item["quantity_allocated"] == 80


def test_insufficient_reserved_quantity_creates_no_allocation():
    tables = tables_for(pool(quantity_available=80, quantity_reserved=10, quantity_allocated=10))
    with pytest.raises(everyday.EverydayOperationError) as error:
        allocate_quantity(tables, 15, from_reserved=True)
    assert error.value.status_code == 409
    item = tables["resources"].item
    assert tables["resources"].allocation_items == {}
    assert item["quantity_reserved"] == 10
    assert item["quantity_allocated"] == 10
    assert item["quantity_available"] == 80


def test_same_allocation_id_does_not_apply_twice(monkeypatch):
    monkeypatch.setattr(everyday, "_everyday_allocation_id", lambda resource_id: "EVERYDAY-SAME")
    tables = tables_for(pool())
    allocate_quantity(tables, 20)
    with pytest.raises(everyday.EverydayOperationError) as error:
        allocate_quantity(tables, 20)
    assert error.value.status_code == 409
    assert error.value.message == "Resource state conflict"
    item = tables["resources"].item
    assert len(tables["resources"].allocation_items) == 1
    assert item["quantity_available"] == 80
    assert item["quantity_allocated"] == 20
    assert len(tables["history"].history) == 1


def test_repeated_individual_allocation_does_not_duplicate():
    tables = tables_for(individual())
    first = allocate_individual(tables)
    with pytest.raises(everyday.EverydayOperationError):
        allocate_individual(tables)
    assert list(tables["resources"].allocation_items) == [first["allocation_id"]]
    assert tables["resources"].item["operational_status"] == "ALLOCATED"
    assert tables["resources"].item["Available"] is False


def test_failed_allocation_insert_restores_resource():
    tables = tables_for(individual())
    tables["resources"].fail_put = True
    with pytest.raises(everyday.EverydayOperationError):
        allocate_individual(tables)
    assert tables["resources"].allocation_items == {}
    assert tables["resources"].item["operational_status"] == "AVAILABLE"
    assert tables["resources"].item["Available"] is True
    assert tables["history"].history == []


def test_failed_resource_update_creates_no_allocation():
    tables = tables_for(individual())
    tables["resources"].fail_update = True
    with pytest.raises(everyday.EverydayOperationError):
        allocate_individual(tables)
    assert tables["resources"].allocation_items == {}
    assert tables["resources"].item["operational_status"] == "AVAILABLE"
    assert tables["resources"].item["operational_status"] != "BROKEN"
    assert tables["history"].history == []


def test_concurrent_quantity_allocation_cannot_over_allocate():
    tables = tables_for(pool(quantity_available=100, quantity_allocated=0))
    stale = copy.deepcopy(tables["resources"].item)
    allocate_quantity(tables, 60)
    with pytest.raises(everyday.EverydayOperationError):
        allocate_quantity(tables, 60, resource=stale)
    item = tables["resources"].item
    assert len(tables["resources"].allocation_items) == 1
    assert item["quantity_available"] == 40
    assert item["quantity_allocated"] == 60
    assert item["quantity_available"] + item["quantity_reserved"] + item["quantity_allocated"] == 100


def test_reserved_individual_allocation_clears_reservation_fields():
    tables = tables_for(
        individual(Available=False, operational_status="RESERVED", reserved_by=USER, reserved_at="2026-10-01T00:00:00+00:00")
    )
    allocate_individual(tables)
    item = tables["resources"].item
    assert item["operational_status"] == "ALLOCATED"
    assert item["Available"] is False
    assert "reserved_by" not in item
    assert "reserved_at" not in item
    assert len(tables["resources"].allocation_items) == 1
