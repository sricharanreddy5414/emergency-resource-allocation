"""RM-13A: everyday return commits the resource and allocation together."""

import copy
import inspect

import pytest
from boto3.dynamodb.types import TypeSerializer
from botocore.exceptions import ClientError

import everyday_operations as everyday

ORG = "ORG-A"
OTHER = "ORG-B"
USER = "operator-sub"


def _failed():
    return ClientError(
        {
            "Error": {"Code": "TransactionCanceledException", "Message": "cancelled"},
            "CancellationReasons": [{"Code": "ConditionalCheckFailed"}, {"Code": "None"}],
        },
        "TransactWriteItems",
    )


def _decode(value):
    if "S" in value:
        return value["S"]
    if "N" in value:
        number = value["N"]
        return int(number) if "." not in str(number) else float(number)
    if "BOOL" in value:
        return value["BOOL"]
    return next(iter(value.values()))


def _resource_holds(item, condition, values):
    if "organization_id = :organization_id" in condition and item.get("organization_id") != values.get(":organization_id"):
        return False
    if "attribute_exists(resource_id)" in condition and "resource_id" not in item:
        return False
    if "(operational_status = :allocated OR operational_status = :in_use)" in condition:
        if item.get("operational_status") not in {values.get(":allocated"), values.get(":in_use")}:
            return False
    if "tracking_mode = :quantity" in condition and item.get("tracking_mode") != values.get(":quantity"):
        return False
    if "quantity_allocated >= :qty" in condition and item.get("quantity_allocated", 0) < values.get(":qty"):
        return False
    if "quantity_available + quantity_reserved + quantity_allocated = quantity_total" in condition:
        total = item.get("quantity_available", 0) + item.get("quantity_reserved", 0) + item.get("quantity_allocated", 0)
        if total != item.get("quantity_total"):
            return False
    return True


def _allocation_holds(item, condition, values):
    if "attribute_exists(allocation_id)" in condition and "allocation_id" not in item:
        return False
    if "#status = :open" in condition and item.get("status") != values.get(":open"):
        return False
    if "organization_id = :organization_id" in condition and item.get("organization_id") != values.get(":organization_id"):
        return False
    if "resource_id = :resource_id" in condition and item.get("resource_id") != values.get(":resource_id"):
        return False
    if "allocation_type = :everyday" in condition and item.get("allocation_type") != values.get(":everyday"):
        return False
    return True


def _apply_resource(item, expression, values):
    if "operational_status = :available" in expression and "quantity_" not in expression:
        item["operational_status"] = values[":available"]
        item["Available"] = values[":true"]
        return
    if "quantity_allocated = quantity_allocated - :qty" in expression:
        quantity = values[":qty"]
        item["quantity_allocated"] -= quantity
        item["quantity_available"] += quantity
        return
    raise AssertionError(expression)


def _apply_allocation(item, values):
    item["status"] = values[":returned"]
    item["returned_at"] = values[":now"]
    item["returned_by"] = values[":actor"]
    item["updated_at"] = values[":now"]


class Store:
    def __init__(self, resource, allocation):
        self.item = copy.deepcopy(resource)
        self.allocations = {allocation["allocation_id"]: copy.deepcopy(allocation)}
        self.history = []
        self.events = []
        self.calls = []
        self.fail_allocation = False
        self.meta = type("Meta", (), {"client": self})()

    def get_item(self, Key):
        if "allocation_id" in Key:
            item = self.allocations.get(Key["allocation_id"])
            return {"Item": copy.deepcopy(item)} if item else {}
        item = self.item if self.item.get("resource_id") == Key.get("resource_id") else None
        return {"Item": copy.deepcopy(item)} if item else {}

    def put_item(self, Item):
        if Item.get("history_id"):
            self.history.append(dict(Item))
            return
        self.events.append(dict(Item))

    def transact_write_items(self, TransactItems):
        self.calls.append(copy.deepcopy(TransactItems))
        resource_before = copy.deepcopy(self.item)
        allocations_before = copy.deepcopy(self.allocations)
        try:
            for step in TransactItems:
                update = step["Update"]
                values = {key: _decode(value) for key, value in update["ExpressionAttributeValues"].items()}
                condition = update.get("ConditionExpression") or ""
                if "resource_id" in update["Key"] and "allocation_id" not in update["Key"]:
                    if not _resource_holds(self.item, condition, values):
                        raise _failed()
                    _apply_resource(self.item, update["UpdateExpression"], values)
                else:
                    allocation_id = _decode(update["Key"]["allocation_id"])
                    current = self.allocations.get(allocation_id)
                    if current is None or not _allocation_holds(current, condition, values) or self.fail_allocation:
                        self.fail_allocation = False
                        raise _failed()
                    _apply_allocation(current, values)
        except ClientError:
            self.item = resource_before
            self.allocations = allocations_before
            raise


def _tables(resource, allocation):
    store = Store(resource, allocation)
    everyday._transact_write = store.transact_write_items
    allocations = type("Alloc", (), {})()
    allocations.get_item = store.get_item
    return {
        "resources": store,
        "allocations": allocations,
        "history": store,
        "audit": store,
    }


def _individual(status="ALLOCATED"):
    return {
        "resource_id": "R1",
        "organization_id": ORG,
        "Available": False,
        "operational_status": status,
        "tracking_mode": "INDIVIDUAL",
        "location_id": "LOC1",
        "Type": "Kit",
        "Location": "HQ",
    }


def _pool(available=6, allocated=4, reserved=0, total=10):
    return {
        "resource_id": "Q1",
        "organization_id": ORG,
        "tracking_mode": "QUANTITY",
        "Available": False,
        "operational_status": "AVAILABLE",
        "quantity_total": total,
        "quantity_available": available,
        "quantity_reserved": reserved,
        "quantity_allocated": allocated,
        "location_id": "LOC1",
        "Type": "Supplies",
        "Location": "HQ",
    }


def _allocation(allocation_id, resource_id, quantity=1, status="OPEN", organization_id=ORG):
    return {
        "allocation_id": allocation_id,
        "organization_id": organization_id,
        "resource_id": resource_id,
        "allocation_type": "EVERYDAY",
        "status": status,
        "quantity": quantity,
    }


def _return(tables, resource, allocation_id, organization_id=ORG):
    return everyday.everyday_return(
        {"allocation_id": allocation_id},
        organization_id,
        USER,
        "OPERATOR",
        resource,
        tables,
    )


@pytest.mark.parametrize("status", ["ALLOCATED", "IN_USE"])
def test_individual_return_commits_resource_and_allocation(status):
    allocation = _allocation("EVERYDAY-R1", "R1")
    tables = _tables(_individual(status), allocation)
    result = _return(tables, tables["resources"].item, "EVERYDAY-R1")
    item = tables["resources"].item
    stored = tables["resources"].allocations["EVERYDAY-R1"]
    assert result["status"] == "RETURNED"
    assert item["operational_status"] == "AVAILABLE"
    assert item["Available"] is True
    assert stored["status"] == "RETURNED"
    assert stored["returned_by"] == USER
    assert len(tables["resources"].calls) == 1
    assert [next(iter(step)) for step in tables["resources"].calls[0]] == ["Update", "Update"]
    assert len(tables["history"].history) == 1
    assert tables["history"].history[0]["reason"] == "EVERYDAY_RESOURCE_RETURNED"
    assert tables["history"].history[0]["actor_sub"] == USER
    assert len(tables["audit"].events) == 1
    assert tables["audit"].events[0]["action"] == "resource.everyday_return"


def test_individual_transaction_failure_changes_nothing():
    allocation = _allocation("EVERYDAY-R1", "R1")
    tables = _tables(_individual(), allocation)
    before_resource = copy.deepcopy(tables["resources"].item)
    before_allocation = copy.deepcopy(tables["resources"].allocations["EVERYDAY-R1"])
    tables["resources"].fail_allocation = True
    with pytest.raises(everyday.EverydayOperationError) as error:
        _return(tables, tables["resources"].item, "EVERYDAY-R1")
    assert error.value.status_code == 409
    assert error.value.message == "Resource state conflict"
    assert tables["resources"].item == before_resource
    assert tables["resources"].allocations["EVERYDAY-R1"] == before_allocation
    assert tables["history"].history == []
    assert tables["audit"].events == []


def test_repeated_individual_return_does_not_change_the_resource():
    allocation = _allocation("EVERYDAY-R1", "R1")
    tables = _tables(_individual(), allocation)
    _return(tables, tables["resources"].item, "EVERYDAY-R1")
    returned = copy.deepcopy(tables["resources"].item)
    with pytest.raises(everyday.EverydayOperationError) as error:
        _return(tables, tables["resources"].item, "EVERYDAY-R1")
    assert error.value.status_code == 409
    assert tables["resources"].item == returned
    assert tables["resources"].allocations["EVERYDAY-R1"]["status"] == "RETURNED"
    assert len(tables["history"].history) == 1


def test_quantity_return_commits_counters_and_allocation():
    allocation = _allocation("EVERYDAY-Q1", "Q1", quantity=4)
    tables = _tables(_pool(), allocation)
    _return(tables, tables["resources"].item, "EVERYDAY-Q1")
    item = tables["resources"].item
    assert item["quantity_available"] == 10
    assert item["quantity_allocated"] == 0
    assert item["quantity_reserved"] == 0
    assert tables["resources"].allocations["EVERYDAY-Q1"]["status"] == "RETURNED"
    assert len(tables["resources"].calls) == 1
    assert len(tables["history"].history) == 1


def test_quantity_transaction_failure_leaves_counters_unchanged():
    allocation = _allocation("EVERYDAY-Q1", "Q1", quantity=4)
    tables = _tables(_pool(), allocation)
    tables["resources"].fail_allocation = True
    with pytest.raises(everyday.EverydayOperationError) as error:
        _return(tables, tables["resources"].item, "EVERYDAY-Q1")
    item = tables["resources"].item
    assert error.value.status_code == 409
    assert item["quantity_available"] == 6
    assert item["quantity_allocated"] == 4
    assert tables["resources"].allocations["EVERYDAY-Q1"]["status"] == "OPEN"
    assert tables["history"].history == []


def test_repeated_quantity_return_does_not_move_counters_twice():
    allocation = _allocation("EVERYDAY-Q1", "Q1", quantity=4)
    tables = _tables(_pool(), allocation)
    _return(tables, tables["resources"].item, "EVERYDAY-Q1")
    with pytest.raises(everyday.EverydayOperationError):
        _return(tables, tables["resources"].item, "EVERYDAY-Q1")
    item = tables["resources"].item
    assert item["quantity_available"] == 10
    assert item["quantity_allocated"] == 0
    assert len(tables["resources"].calls) == 1


def test_returned_allocation_is_rejected():
    allocation = _allocation("EVERYDAY-Q1", "Q1", quantity=4, status="RETURNED")
    tables = _tables(_pool(), allocation)
    with pytest.raises(everyday.EverydayOperationError) as error:
        _return(tables, tables["resources"].item, "EVERYDAY-Q1")
    assert error.value.status_code == 409
    assert error.value.message == "Allocation is not open"
    assert tables["resources"].item["quantity_available"] == 6
    assert tables["resources"].item["quantity_allocated"] == 4
    assert tables["resources"].calls == []


def test_wrong_organization_is_rejected():
    allocation = _allocation("EVERYDAY-R1", "R1")
    tables = _tables(_individual(), allocation)
    with pytest.raises(everyday.EverydayOperationError) as error:
        _return(tables, tables["resources"].item, "EVERYDAY-R1", organization_id=OTHER)
    assert error.value.status_code == 404
    assert tables["resources"].item["operational_status"] == "ALLOCATED"
    assert tables["resources"].allocations["EVERYDAY-R1"]["status"] == "OPEN"


def test_allocation_for_another_resource_is_rejected():
    allocation = _allocation("EVERYDAY-OTHER", "OTHER")
    tables = _tables(_individual(), allocation)
    with pytest.raises(everyday.EverydayOperationError) as error:
        _return(tables, tables["resources"].item, "EVERYDAY-OTHER")
    assert error.value.status_code == 404
    assert tables["resources"].item["operational_status"] == "ALLOCATED"
    assert tables["resources"].calls == []


class _HighLevelResourceClient:
    """Serializes AttributeValues again, which is what the resource client does."""

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
            for value in values:
                if isinstance(value, dict) and "M" in serializer.serialize(value):
                    raise ClientError(
                        {
                            "Error": {
                                "Code": "TransactionCanceledException",
                                "Message": "Transaction cancelled",
                            },
                            "CancellationReasons": [
                                {
                                    "Code": "ValidationError",
                                    "Message": "The provided key element does not match the schema",
                                },
                                {"Code": "None"},
                            ],
                        },
                        "TransactWriteItems",
                    )


class _LowLevelClient:
    def __init__(self):
        self.calls = []

    def transact_write_items(self, TransactItems):
        self.calls.append(copy.deepcopy(TransactItems))


class _Sink:
    def __init__(self):
        self.rows = []

    def put_item(self, Item, ConditionExpression=None):
        self.rows.append(dict(Item))

    def get_item(self, Key):
        return {}


def _string_key(value):
    assert list(value) == ["S"]
    assert isinstance(value["S"], str)
    assert value["S"]
    return value["S"]


def test_everyday_transactions_reach_the_low_level_client_once(monkeypatch):
    source = inspect.getsource(everyday._commit_everyday_allocation)
    assert "_transact_write" in source
    assert "meta" not in source

    high = _HighLevelResourceClient()
    low = _LowLevelClient()
    monkeypatch.setattr(everyday, "_transact_write", low.transact_write_items)
    sink = _Sink()

    def tables_for(resource, allocation=None):
        allocations = _Sink()
        if allocation:
            allocations.get_item = lambda Key: {"Item": dict(allocation)}
        return {
            "resources": type("Resources", (), {"meta": type("Meta", (), {"client": high})()})(),
            "allocations": allocations,
            "history": sink,
            "audit": sink,
        }

    individual = {
        "resource_id": "RM12-TEST-1",
        "organization_id": ORG,
        "Available": True,
        "operational_status": "AVAILABLE",
        "tracking_mode": "INDIVIDUAL",
        "location_id": "LOC1",
        "Type": "Kit",
        "Location": "HQ",
    }
    everyday.everyday_allocate_individual({}, ORG, USER, "OPERATOR", individual, tables_for(individual))

    reserved = dict(individual)
    reserved["resource_id"] = "RM12-RESERVED"
    reserved["Available"] = False
    reserved["operational_status"] = "RESERVED"
    reserved["reserved_by"] = USER
    reserved["reserved_at"] = "2026-10-08T00:00:00+00:00"
    everyday.everyday_allocate_individual({}, ORG, USER, "OPERATOR", reserved, tables_for(reserved))

    pool = {
        "resource_id": "RM12-QTY",
        "organization_id": ORG,
        "tracking_mode": "QUANTITY",
        "operational_status": "AVAILABLE",
        "Available": False,
        "quantity_total": 10,
        "quantity_available": 6,
        "quantity_reserved": 0,
        "quantity_allocated": 4,
        "location_id": "LOC1",
        "Type": "Supplies",
        "Location": "HQ",
    }
    everyday.everyday_allocate_quantity({"quantity": 2}, ORG, USER, "OPERATOR", pool, tables_for(pool))

    returned = dict(individual)
    returned["resource_id"] = "RM12-RETURN"
    returned["Available"] = False
    returned["operational_status"] = "ALLOCATED"
    allocation = _allocation("EVERYDAY-RETURN", "RM12-RETURN")
    everyday.everyday_return(
        {"allocation_id": "EVERYDAY-RETURN"},
        ORG,
        USER,
        "OPERATOR",
        returned,
        tables_for(returned, allocation),
    )

    assert high.calls == []
    assert len(low.calls) == 4
    resource_ids = []
    for call in low.calls:
        resource_key = call[0]["Update"]["Key"]["resource_id"]
        resource_ids.append(_string_key(resource_key))
        second = call[1]
        if "Put" in second:
            _string_key(second["Put"]["Item"]["allocation_id"])
        else:
            _string_key(second["Update"]["Key"]["allocation_id"])
    assert resource_ids == ["RM12-TEST-1", "RM12-RESERVED", "RM12-QTY", "RM12-RETURN"]
