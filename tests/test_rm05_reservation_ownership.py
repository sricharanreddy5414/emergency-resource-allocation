"""RM-05: only the authenticated reservation owner can release an individual hold."""

import copy

import pytest
from botocore.exceptions import ClientError

import everyday_operations as everyday
import matching
from resource_state import emergency_matchable

ORG = "ORG-A"
ACTOR_A = "operator-a"
ACTOR_B = "operator-b"


def _failed():
    return ClientError({"Error": {"Code": "ConditionalCheckFailedException", "Message": "conflict"}}, "UpdateItem")


def _decode(value):
    if "S" in value:
        return value["S"]
    if "N" in value:
        return int(value["N"])
    if "BOOL" in value:
        return value["BOOL"]
    return next(iter(value.values()))


class Resources:
    def __init__(self, item):
        self.item = copy.deepcopy(item)
        self.history = []
        self.allocation_items = {}
        self.meta = type("Meta", (), {"client": self})()

    def update_item(self, **kwargs):
        values = kwargs.get("ExpressionAttributeValues") or {}
        condition = kwargs.get("ConditionExpression") or ""
        expression = kwargs.get("UpdateExpression") or ""
        item = self.item
        if "organization_id = :organization_id" in condition and item.get("organization_id") != values.get(":organization_id"):
            raise _failed()
        if "Available = :true" in condition and item.get("Available") is not True:
            raise _failed()
        if "operational_status = :available" in condition or "OR operational_status = :available" in condition:
            stored = item.get("operational_status")
            if stored is not None and stored != "AVAILABLE":
                raise _failed()
        if "operational_status = :reserved" in condition and item.get("operational_status") != "RESERVED":
            raise _failed()
        if "reserved_by = :actor" in condition and item.get("reserved_by") != values.get(":actor"):
            raise _failed()
        if "operational_status = :reserved" in expression and "REMOVE" not in expression:
            item["operational_status"] = values[":reserved"]
            item["Available"] = values[":false"]
            item["reserved_by"] = values[":actor"]
            item["reserved_at"] = values[":now"]
            item["reservation_expires_at"] = values[":expires"]
            item["reservation_due_key"] = values[":due"]
            return
        if "operational_status = :available" in expression:
            item["operational_status"] = values[":available"]
            item["Available"] = values[":true"]
            if "REMOVE" in expression:
                item.pop("reserved_by", None)
                item.pop("reserved_at", None)
                item.pop("reservation_expires_at", None)
                item.pop("reservation_due_key", None)

    def transact_write_items(self, TransactItems):
        before = copy.deepcopy(self.item)
        allocations_before = copy.deepcopy(self.allocation_items)
        try:
            for step in TransactItems:
                if "Update" in step:
                    update = step["Update"]
                    values = {key: _decode(value) for key, value in update["ExpressionAttributeValues"].items()}
                    condition = update.get("ConditionExpression") or ""
                    if "operational_status = :reserved" in condition and self.item.get("operational_status") != "RESERVED":
                        raise _failed()
                    expression = update.get("UpdateExpression") or ""
                    if "operational_status = :allocated" in expression:
                        self.item["operational_status"] = "ALLOCATED"
                        self.item["Available"] = False
                        if "REMOVE" in expression:
                            self.item.pop("reserved_by", None)
                            self.item.pop("reserved_at", None)
                            self.item.pop("reservation_expires_at", None)
                            self.item.pop("reservation_due_key", None)
                if "Put" in step:
                    allocation = {key: _decode(value) for key, value in step["Put"]["Item"].items()}
                    if allocation["allocation_id"] in self.allocation_items:
                        raise _failed()
                    self.allocation_items[allocation["allocation_id"]] = allocation
        except ClientError:
            self.item = before
            self.allocation_items = allocations_before
            raise

    def put_item(self, Item, ConditionExpression=None):
        self.history.append(dict(Item))


class Audit:
    def __init__(self):
        self.events = []

    def put_item(self, Item):
        self.events.append(dict(Item))


def resource():
    return {
        "resource_id": "R1",
        "organization_id": ORG,
        "Available": True,
        "operational_status": "AVAILABLE",
        "tracking_mode": "INDIVIDUAL",
        "location_id": "LOC1",
        "Type": "Kit",
        "resource_type_id": "TYPE-KIT",
        "Location": "HQ",
    }


def tables_for(item=None):
    resources = Resources(item or resource())
    return {
        "resources": resources,
        "allocations": type("Alloc", (), {"allocation_items": resources.allocation_items})(),
        "history": resources,
        "audit": Audit(),
    }


def reserve(tables, actor, body=None):
    payload = {"resource_id": "R1"}
    if body:
        payload.update(body)
    return everyday.reserve_individual(payload, ORG, actor, "OPERATOR", tables["resources"].item, tables)


def release(tables, actor, role="OPERATOR", body=None, resource_view=None):
    payload = {"resource_id": "R1"}
    if body:
        payload.update(body)
    return everyday.release_reservation(
        payload,
        ORG,
        actor,
        role,
        resource_view or tables["resources"].item,
        tables,
    )


def test_owner_can_release_own_reservation():
    tables = tables_for()
    reserve(tables, ACTOR_A)
    result = release(tables, ACTOR_A)
    item = tables["resources"].item
    assert result["operational_status"] == "AVAILABLE"
    assert item["operational_status"] == "AVAILABLE"
    assert item["Available"] is True
    assert "reserved_by" not in item
    assert "reserved_at" not in item
    assert len(tables["history"].history) == 2


def test_other_operator_cannot_release_reservation():
    tables = tables_for()
    reserve(tables, ACTOR_A)
    with pytest.raises(everyday.EverydayOperationError) as error:
        release(tables, ACTOR_B, role="OPERATOR")
    assert error.value.status_code == 409
    assert error.value.message == "Resource state conflict"
    item = tables["resources"].item
    assert item["operational_status"] == "RESERVED"
    assert item["Available"] is False
    assert item["reserved_by"] == ACTOR_A
    assert len(tables["history"].history) == 1


@pytest.mark.parametrize("role", ["ADMIN", "OWNER"])
def test_admin_and_owner_cannot_release_another_operators_reservation(role):
    tables = tables_for()
    reserve(tables, ACTOR_A)
    with pytest.raises(everyday.EverydayOperationError) as error:
        release(tables, ACTOR_B, role=role)
    assert error.value.status_code == 409
    item = tables["resources"].item
    assert item["reserved_by"] == ACTOR_A
    assert item["operational_status"] == "RESERVED"


def test_request_body_cannot_impersonate_reservation_owner():
    tables = tables_for()
    reserve(tables, ACTOR_A)
    with pytest.raises(everyday.EverydayOperationError) as error:
        release(
            tables,
            ACTOR_B,
            body={"reserved_by": ACTOR_A, "user_sub": ACTOR_A, "actor_sub": ACTOR_A},
        )
    assert error.value.status_code == 409
    assert tables["resources"].item["reserved_by"] == ACTOR_A
    assert tables["resources"].item["operational_status"] == "RESERVED"


def test_repeated_release_does_not_duplicate_history():
    tables = tables_for()
    reserve(tables, ACTOR_A)
    release(tables, ACTOR_A)
    with pytest.raises(everyday.EverydayOperationError) as error:
        release(tables, ACTOR_A)
    assert error.value.status_code == 409
    assert len(tables["history"].history) == 2
    assert len(tables["audit"].events) == 2
    assert tables["resources"].item["operational_status"] == "AVAILABLE"
    assert "quantity_available" not in tables["resources"].item
    assert "quantity_reserved" not in tables["resources"].item


def test_release_does_not_overwrite_allocation():
    tables = tables_for()
    reserve(tables, ACTOR_A)
    stale = copy.deepcopy(tables["resources"].item)
    everyday.everyday_allocate_individual({}, ORG, ACTOR_A, "OPERATOR", tables["resources"].item, tables)
    with pytest.raises(everyday.EverydayOperationError):
        release(tables, ACTOR_A, resource_view=stale)
    item = tables["resources"].item
    assert item["operational_status"] == "ALLOCATED"
    assert item["Available"] is False
    assert len(tables["resources"].allocation_items) == 1
    assert len([row for row in tables["history"].history if row.get("reason") == "RESOURCE_RESERVATION_RELEASED"]) == 0


def test_second_release_does_not_repeat_the_transition():
    tables = tables_for()
    reserve(tables, ACTOR_A)
    stale = copy.deepcopy(tables["resources"].item)
    release(tables, ACTOR_A)
    with pytest.raises(everyday.EverydayOperationError):
        release(tables, ACTOR_A, resource_view=stale)
    assert tables["resources"].item["operational_status"] == "AVAILABLE"
    assert len([row for row in tables["history"].history if row.get("reason") == "RESOURCE_RESERVATION_RELEASED"]) == 1


def test_reserved_resource_is_not_emergency_matchable_until_released():
    tables = tables_for()
    reserve(tables, ACTOR_A)
    held = dict(tables["resources"].item)
    held["Available"] = True
    request = {
        "organization_id": ORG,
        "resource_type": "Kit",
        "location_id": "LOC1",
    }
    assert emergency_matchable(tables["resources"].item) is False
    assert emergency_matchable(held) is False
    assert matching.choose_resource([tables["resources"].item], request) is None
    release(tables, ACTOR_A)
    restored = tables["resources"].item
    assert emergency_matchable(restored) is True
    chosen = matching.choose_resource([restored], request)
    assert chosen["resource_id"] == "R1"


def test_reservation_owner_comes_from_authenticated_actor():
    tables = tables_for()
    reserve(
        tables,
        ACTOR_A,
        body={"reserved_by": ACTOR_B, "user_sub": ACTOR_B, "reserved_at": "2000-01-01T00:00:00+00:00"},
    )
    item = tables["resources"].item
    assert item["reserved_by"] == ACTOR_A
    assert item["reserved_at"] != "2000-01-01T00:00:00+00:00"
    assert item["operational_status"] == "RESERVED"
    with pytest.raises(everyday.EverydayOperationError):
        reserve(tables, ACTOR_B)
    assert tables["resources"].item["reserved_by"] == ACTOR_A
