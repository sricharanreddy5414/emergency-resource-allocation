"""RM-03: quantity reservation release returns reserved stock to available."""

import copy
import inspect
from pathlib import Path

import pytest
from botocore.exceptions import ClientError

import everyday_operations as everyday
import lifecycle_operations as lifecycle

ROOT = Path(__file__).resolve().parents[1]
ORG = "ORG-A"
USER = "operator-sub"


def _failed():
    return ClientError({"Error": {"Code": "ConditionalCheckFailedException", "Message": "conflict"}}, "UpdateItem")


def _holds(item, condition, values):
    if "organization_id = :organization_id" in condition and item.get("organization_id") != values.get(":organization_id"):
        return False
    if "tracking_mode = :quantity" in condition and item.get("tracking_mode") != values.get(":quantity"):
        return False
    if "attribute_exists(resource_id)" in condition and "resource_id" not in item:
        return False
    if "quantity_reserved >= :qty" in condition and item.get("quantity_reserved", 0) < values[":qty"]:
        return False
    if "quantity_available >= :qty" in condition and item.get("quantity_available", 0) < values[":qty"]:
        return False
    if "quantity_available + :qty <= quantity_total" in condition:
        if item.get("quantity_available", 0) + values[":qty"] > item.get("quantity_total", 0):
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
    if "Available = :true" in condition and item.get("Available") is not True:
        return False
    if "operational_status = :current" in condition and item.get("operational_status") != values.get(":current"):
        return False
    return True


def _apply(item, expression, values):
    quantity = values.get(":qty")
    if (
        "quantity_available = quantity_available - :qty" in expression
        and "quantity_reserved = quantity_reserved + :qty" in expression
    ):
        item["quantity_available"] -= quantity
        item["quantity_reserved"] += quantity
        return
    if (
        "quantity_reserved = quantity_reserved - :qty" in expression
        and "quantity_available = quantity_available + :qty" in expression
    ):
        item["quantity_reserved"] -= quantity
        item["quantity_available"] += quantity
        return
    if (
        "quantity_reserved = quantity_reserved - :qty" in expression
        and "quantity_allocated = quantity_allocated + :qty" in expression
    ):
        item["quantity_reserved"] -= quantity
        item["quantity_allocated"] += quantity
        return
    if "operational_status = :target" in expression:
        item["operational_status"] = values[":target"]
        item["Available"] = values[":available"]
        return
    raise AssertionError(expression)


def _decode(value):
    if "S" in value:
        return value["S"]
    if "N" in value:
        number = value["N"]
        return int(number) if "." not in number else float(number)
    if "BOOL" in value:
        return value["BOOL"]
    return next(iter(value.values()))


class Resources:
    def __init__(self, item):
        self.item = copy.deepcopy(item)
        self.history = []
        self.updates = []
        self.allocation_items = {}
        self.meta = type("Meta", (), {"client": self})()

    def transact_write_items(self, TransactItems):
        item_before = copy.deepcopy(self.item)
        allocations_before = copy.deepcopy(self.allocation_items)
        try:
            for step in TransactItems:
                if "Update" in step:
                    update = step["Update"]
                    values = {key: _decode(value) for key, value in update["ExpressionAttributeValues"].items()}
                    if not _holds(self.item, update.get("ConditionExpression") or "", values):
                        raise _failed()
                    _apply(self.item, update.get("UpdateExpression") or "", values)
                if "Put" in step:
                    allocation = {key: _decode(value) for key, value in step["Put"]["Item"].items()}
                    if allocation["allocation_id"] in self.allocation_items:
                        raise _failed()
                    self.allocation_items[allocation["allocation_id"]] = allocation
        except ClientError:
            self.item = item_before
            self.allocation_items.clear()
            self.allocation_items.update(allocations_before)
            raise

    def update_item(self, **kwargs):
        self.updates.append(copy.deepcopy(kwargs))
        condition = kwargs.get("ConditionExpression") or ""
        values = kwargs.get("ExpressionAttributeValues") or {}
        if not _holds(self.item, condition, values):
            raise _failed()
        _apply(self.item, kwargs.get("UpdateExpression") or "", values)

    def put_item(self, Item, ConditionExpression=None):
        self.history.append(dict(Item))


class Allocations:
    def __init__(self):
        self.allocation_items = {}

    def get_item(self, Key):
        item = self.allocation_items.get(Key["allocation_id"])
        return {"Item": dict(item)} if item else {}

    def put_item(self, Item, ConditionExpression=None):
        if ConditionExpression and Item["allocation_id"] in self.allocation_items:
            raise _failed()
        self.allocation_items[Item["allocation_id"]] = dict(Item)


class Audit:
    def __init__(self):
        self.events = []

    def put_item(self, Item):
        self.events.append(dict(Item))


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
        "reserved_by": "kept",
        "reserved_at": "kept-at",
    }
    item.update(extra)
    return item


def tables_for(item):
    resources = Resources(item)
    everyday._transact_write = resources.transact_write_items
    allocations = Allocations()
    resources.allocation_items = allocations.allocation_items
    return {
        "resources": resources,
        "allocations": allocations,
        "history": resources,
        "audit": Audit(),
    }


def invariant(item):
    assert item["quantity_available"] + item["quantity_reserved"] + item["quantity_allocated"] == item["quantity_total"]
    assert item["quantity_reserved"] >= 0
    assert item["quantity_available"] >= 0
    assert item["quantity_available"] <= item["quantity_total"]
    assert item["Available"] is False
    assert item["reserved_by"] == "kept"
    assert item["reserved_at"] == "kept-at"


def release(tables, quantity, resource=None):
    return everyday.release_reservation(
        {"resource_id": "Q1", "quantity": quantity},
        ORG,
        USER,
        "OPERATOR",
        resource or tables["resources"].item,
        tables,
    )


def test_quantity_release_uses_conditional_arithmetic():
    source = inspect.getsource(everyday.release_quantity_reservation)
    assert "put_item" not in source
    assert "Scan" not in source
    assert "quantity_reserved = quantity_reserved - :qty" in source
    assert "quantity_available = quantity_available + :qty" in source
    assert "quantity_reserved >= :qty" in source
    assert "operational_status <> :retired" in source
    assert "quantity_available + :qty <= quantity_total" in source


def test_quantity_reserve_moves_available_to_reserved():
    tables = tables_for(pool())
    everyday.reserve_quantity({"quantity": 20}, ORG, USER, "OPERATOR", tables["resources"].item, tables)
    item = tables["resources"].item
    assert item["quantity_available"] == 80
    assert item["quantity_reserved"] == 20
    assert item["operational_status"] == "AVAILABLE"
    invariant(item)


def test_quantity_reservation_release_restores_available():
    tables = tables_for(pool(quantity_available=80, quantity_reserved=20))
    result = release(tables, 20)
    item = tables["resources"].item
    assert result["quantity"] == 20
    assert item["quantity_available"] == 100
    assert item["quantity_reserved"] == 0
    assert item["operational_status"] == "AVAILABLE"
    values = tables["resources"].updates[-1]["ExpressionAttributeValues"]
    assert set(values) == {":qty", ":organization_id", ":quantity", ":retired"}
    invariant(item)


def test_partial_quantity_release():
    tables = tables_for(pool(quantity_available=60, quantity_reserved=40))
    release(tables, 15)
    item = tables["resources"].item
    assert item["quantity_available"] == 75
    assert item["quantity_reserved"] == 25
    invariant(item)


def test_over_release_is_rejected():
    tables = tables_for(pool(quantity_available=80, quantity_reserved=20))
    with pytest.raises(everyday.EverydayOperationError) as error:
        release(tables, 25)
    assert error.value.status_code == 409
    assert error.value.message == "Resource state conflict"
    assert "ConditionalCheck" not in error.value.message
    item = tables["resources"].item
    assert item["quantity_available"] == 80
    assert item["quantity_reserved"] == 20
    assert tables["history"].history == []
    invariant(item)


@pytest.mark.parametrize("quantity", [0, -5])
def test_zero_and_negative_release_rejected(quantity):
    tables = tables_for(pool(quantity_available=80, quantity_reserved=20))
    with pytest.raises(everyday.EverydayOperationError) as error:
        release(tables, quantity)
    assert error.value.status_code == 400
    assert error.value.message == "Quantity is invalid"
    assert tables["resources"].updates == []
    assert tables["resources"].item["quantity_reserved"] == 20
    assert tables["resources"].item["quantity_available"] == 80


def test_individual_resource_is_rejected_by_quantity_release():
    item = {
        "resource_id": "R1",
        "organization_id": ORG,
        "tracking_mode": "INDIVIDUAL",
        "Available": False,
        "operational_status": "RESERVED",
        "reserved_by": USER,
        "reserved_at": "2026-10-01T00:00:00+00:00",
    }
    tables = tables_for(item)
    with pytest.raises(everyday.EverydayOperationError) as error:
        everyday.release_quantity_reservation({"quantity": 1}, ORG, USER, "OPERATOR", item, tables)
    assert error.value.status_code == 409
    assert error.value.message == "Resource is not a quantity pool"
    assert tables["resources"].updates == []
    assert tables["resources"].item["Available"] is False
    assert tables["resources"].item["operational_status"] == "RESERVED"
    assert tables["resources"].item["reserved_by"] == USER
    assert tables["resources"].item["reserved_at"] == "2026-10-01T00:00:00+00:00"


def test_retired_quantity_pool_is_rejected():
    tables = tables_for(
        pool(operational_status="RETIRED", quantity_available=80, quantity_reserved=20)
    )
    with pytest.raises(everyday.EverydayOperationError) as error:
        release(tables, 10)
    assert error.value.status_code == 409
    assert error.value.message == "Resource state conflict"
    item = tables["resources"].item
    assert item["operational_status"] == "RETIRED"
    assert item["quantity_available"] == 80
    assert item["quantity_reserved"] == 20
    assert tables["history"].history == []


def test_concurrent_release_cannot_double_credit():
    tables = tables_for(pool(quantity_available=80, quantity_reserved=20))
    stale = copy.deepcopy(tables["resources"].item)
    release(tables, 20)
    with pytest.raises(everyday.EverydayOperationError) as error:
        release(tables, 20, resource=stale)
    assert error.value.status_code == 409
    item = tables["resources"].item
    assert item["quantity_available"] == 100
    assert item["quantity_reserved"] == 0
    assert len(tables["history"].history) == 1
    assert len(tables["audit"].events) == 1
    invariant(item)


def test_repeated_release_does_not_release_twice():
    tables = tables_for(pool(quantity_available=80, quantity_reserved=20))
    release(tables, 20)
    with pytest.raises(everyday.EverydayOperationError) as error:
        release(tables, 20)
    assert error.value.status_code == 409
    assert len(tables["history"].history) == 1
    assert tables["resources"].item["quantity_reserved"] == 0
    assert tables["resources"].item["quantity_available"] == 100
    invariant(tables["resources"].item)


def test_reserve_release_reserve_cycle():
    tables = tables_for(pool())
    resource = tables["resources"].item
    everyday.reserve_quantity({"quantity": 20}, ORG, USER, "OPERATOR", resource, tables)
    assert resource["quantity_available"] == 80
    assert resource["quantity_reserved"] == 20
    release(tables, 20)
    assert resource["quantity_available"] == 100
    assert resource["quantity_reserved"] == 0
    everyday.reserve_quantity({"quantity": 30}, ORG, USER, "OPERATOR", resource, tables)
    assert resource["quantity_available"] == 70
    assert resource["quantity_reserved"] == 30
    assert resource["quantity_allocated"] == 0
    invariant(resource)


def test_allocation_from_reserved_still_consumes_reserved_quantity():
    tables = tables_for(pool(quantity_available=80, quantity_reserved=20))
    result = everyday.everyday_allocate_quantity(
        {"quantity": 20, "from_reserved": True},
        ORG,
        USER,
        "OPERATOR",
        tables["resources"].item,
        tables,
    )
    item = tables["resources"].item
    assert result["status"] == "OPEN"
    assert item["quantity_reserved"] == 0
    assert item["quantity_allocated"] == 20
    assert item["quantity_available"] == 80
    invariant(item)


def test_reserved_quantity_prevents_retirement():
    tables = tables_for(pool(quantity_available=80, quantity_reserved=20))
    with pytest.raises(lifecycle.LifecycleOperationError) as error:
        lifecycle.retire_resource({}, ORG, USER, "OPERATOR", tables["resources"].item, tables)
    assert error.value.status_code == 409
    assert "reserved or allocated" in error.value.message
    assert tables["resources"].updates == []
    assert tables["resources"].item["operational_status"] == "AVAILABLE"
    assert tables["resources"].item["quantity_reserved"] == 20


def test_retirement_after_release_follows_existing_rule():
    released = tables_for(pool(quantity_available=80, quantity_reserved=20))
    release(released, 20)
    idle = tables_for(pool())

    with pytest.raises(lifecycle.LifecycleOperationError) as released_error:
        lifecycle.retire_resource({}, ORG, USER, "OPERATOR", released["resources"].item, released)
    with pytest.raises(lifecycle.LifecycleOperationError) as idle_error:
        lifecycle.retire_resource({}, ORG, USER, "OPERATOR", idle["resources"].item, idle)

    assert released_error.value.status_code == 409
    assert released_error.value.message == idle_error.value.message
    assert "reserved or allocated" not in released_error.value.message
    assert released["resources"].item["operational_status"] == "AVAILABLE"
    assert released["resources"].item["quantity_reserved"] == 0
    assert released["resources"].item["quantity_available"] == 100
    assert idle["resources"].item["operational_status"] == "AVAILABLE"


def test_other_organization_cannot_release_reserved_quantity():
    tables = tables_for(pool(quantity_available=80, quantity_reserved=20))
    with pytest.raises(everyday.EverydayOperationError) as error:
        everyday.release_quantity_reservation(
            {"quantity": 20},
            "ORG-OTHER",
            USER,
            "OPERATOR",
            tables["resources"].item,
            tables,
        )
    assert error.value.status_code == 409
    assert tables["resources"].item["quantity_reserved"] == 20
    assert tables["history"].history == []


def test_rm01_and_rm02_sources_remain():
    source = (ROOT / "src" / "resource" / "handler.py").read_text(encoding="utf-8")
    update = source.split("def update_resource", 1)[1].split("\ndef release_resource", 1)[0]
    assert "put_item" not in update
    assert "_seen_state_condition" in update
    assert "commit_emergency_release" in source
    auto = (ROOT / "src" / "auto_release" / "handler.py").read_text(encoding="utf-8")
    assert "commit_emergency_release" in auto
    assert "operational_status = :op_available" in (ROOT / "src" / "shared" / "emergency_release.py").read_text(
        encoding="utf-8"
    )
