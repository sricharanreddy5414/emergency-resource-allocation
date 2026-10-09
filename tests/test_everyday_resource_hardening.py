"""Phase 2.5 hardening: tenant, roles, billing, concurrency, and regressions."""

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

import access
from access import AccessError, BILLING_REQUIRED
from resource_state import effective_operational_status, emergency_matchable


def load_module(name, relative):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


everyday = load_module("everyday_ops_hardening", "src/shared/everyday_operations.py")
matching = load_module("matching_hardening", "src/shared/matching.py")
auto_release = load_module("auto_release_hardening", "src/auto_release/handler.py")
resource_handler = load_module("resource_handler_hardening", "src/resource/handler.py")
allocation_service = load_module("allocation_service_hardening", "src/allocation/service.py")
public_api = load_module("public_api_hardening", "src/public/handler.py")

ORG_A = "ORG-A"
ORG_B = "ORG-B"
USER = "operator-sub"
MEMBER = "member-sub"


def _conditional_failed():
    return ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")


def _ddb_decode(value):
    if "S" in value:
        return value["S"]
    if "N" in value:
        return int(value["N"])
    if "BOOL" in value:
        return value["BOOL"]
    return list(value.values())[0]


class ConditionalTable:
    """Minimal DynamoDB table mock with conditional updates and atomic transact."""

    def __init__(self, name, items=None):
        self.name = name
        self.items = {item["resource_id"]: copy.deepcopy(item) for item in (items or [])}
        self.allocation_items = {}
        self.history = []
        self.fail_next_allocation_put = False
        self.meta = type("Meta", (), {"client": self})()

    def get_item(self, Key):
        if "resource_id" in Key:
            item = self.items.get(Key["resource_id"])
            return {"Item": copy.deepcopy(item)} if item else {}
        item = self.allocation_items.get(Key["allocation_id"])
        return {"Item": copy.deepcopy(item)} if item else {}

    def _check_org(self, item, values, condition):
        org = values.get(":organization_id")
        if org is not None and item.get("organization_id") != org:
            raise _conditional_failed()

    def update_item(self, **kwargs):
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
        self._check_org(item, values, condition)

        if ("Available = :true" in condition or "#a = :true" in condition) and not item.get("Available"):
            raise _conditional_failed()

        if "operational_status = :reserved" in condition and item.get("operational_status") != "RESERVED":
            raise _conditional_failed()

        if "reserved_by = :actor" in condition and item.get("reserved_by") != values.get(":actor"):
            raise _conditional_failed()

        if "operational_status = :allocated" in condition and "operational_status = :in_use" not in condition:
            if item.get("operational_status") != "ALLOCATED":
                raise _conditional_failed()

        if "operational_status = :allocated OR operational_status = :in_use" in condition.replace("(", "").replace(")", ""):
            if item.get("operational_status") not in {"ALLOCATED", "IN_USE"}:
                # keep original parentheses form check below
                pass

        if "(operational_status = :allocated OR operational_status = :in_use)" in condition:
            if item.get("operational_status") not in {"ALLOCATED", "IN_USE"}:
                raise _conditional_failed()
        elif "operational_status = :allocated" in condition and item.get("operational_status") != "ALLOCATED":
            if "Available = :true" not in condition:
                raise _conditional_failed()

        if (
            "operational_status = :available" in condition
            or "(attribute_not_exists(operational_status) OR operational_status = :available)" in condition
        ):
            stored = item.get("operational_status")
            if stored is not None and str(stored).upper() != "AVAILABLE":
                raise _conditional_failed()

        if "operational_status = :allocated" in expr:
            item["operational_status"] = values[":allocated"]
            item["Available"] = values.get(":false", False)
            if "REMOVE" in expr:
                for name in expr.split("REMOVE", 1)[1].split(","):
                    item.pop(name.strip(), None)
            return

        if "operational_status = :reserved" in expr:
            item["operational_status"] = values[":reserved"]
            item["Available"] = values[":false"]
            item["reserved_by"] = values.get(":actor")
            item["reserved_at"] = values.get(":now")
            return

        if "operational_status = :available" in expr:
            item["operational_status"] = values[":available"]
            item["Available"] = values[":true"]
            item.pop("reserved_by", None)
            item.pop("reserved_at", None)
            return

        if "quantity_available = quantity_available - :qty" in expr and "quantity_reserved" in expr:
            qty = values[":qty"]
            if item.get("tracking_mode") != "QUANTITY" or item.get("quantity_available", 0) < qty:
                raise _conditional_failed()
            item["quantity_available"] -= qty
            item["quantity_reserved"] += qty
            return

        if "quantity_available = quantity_available - :qty" in expr and "quantity_allocated" in expr:
            qty = values[":qty"]
            if item.get("quantity_available", 0) < qty:
                raise _conditional_failed()
            item["quantity_available"] -= qty
            item["quantity_allocated"] += qty
            return

        if (
            "quantity_reserved = quantity_reserved - :qty" in expr
            and "quantity_available = quantity_available + :qty" in expr
        ):
            qty = values[":qty"]
            if item.get("organization_id") != values.get(":organization_id"):
                raise _conditional_failed()
            if "tracking_mode = :quantity" in condition and item.get("tracking_mode") != values.get(":quantity"):
                raise _conditional_failed()
            if "operational_status <> :retired" in condition and item.get("operational_status") == values.get(":retired"):
                raise _conditional_failed()
            if item.get("quantity_reserved", 0) < qty:
                raise _conditional_failed()
            if "quantity_available + :qty <= quantity_total" in condition:
                if item.get("quantity_available", 0) + qty > item.get("quantity_total", 0):
                    raise _conditional_failed()
            item["quantity_reserved"] -= qty
            item["quantity_available"] += qty
            return

        if "quantity_reserved = quantity_reserved - :qty" in expr:
            qty = values[":qty"]
            if item.get("quantity_reserved", 0) < qty:
                raise _conditional_failed()
            item["quantity_reserved"] -= qty
            item["quantity_allocated"] += qty
            return

        if "quantity_allocated = quantity_allocated - :qty" in expr:
            qty = values[":qty"]
            if item.get("quantity_allocated", 0) < qty:
                raise _conditional_failed()
            item["quantity_allocated"] -= qty
            item["quantity_available"] += qty
            return

        if "quantity_available = quantity_available + :qty" in expr and "quantity_allocated = quantity_allocated - :qty" in expr:
            qty = values[":qty"]
            if item.get("quantity_allocated", 0) < qty:
                raise _conditional_failed()
            item["quantity_allocated"] -= qty
            item["quantity_available"] += qty
            return

        if "#a = :false" in expr or (names.get("#a") == "Available" and ":false" in values):
            item["Available"] = False
            if values.get(":op_allocated"):
                item["operational_status"] = "ALLOCATED"

    def put_item(self, Item, ConditionExpression=None):
        if Item.get("history_id"):
            self.history.append(copy.deepcopy(Item))
            return
        if "allocation_id" in Item:
            if self.fail_next_allocation_put:
                self.fail_next_allocation_put = False
                raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "PutItem")
            if ConditionExpression and Item["allocation_id"] in self.allocation_items:
                raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "PutItem")
            self.allocation_items[Item["allocation_id"]] = copy.deepcopy(Item)
            return
        self.history.append(copy.deepcopy(Item))

    def transact_write_items(self, TransactItems):
        items_before = copy.deepcopy(self.items)
        allocations_before = copy.deepcopy(self.allocation_items)
        try:
            for step in TransactItems:
                if "Update" in step:
                    update = step["Update"]
                    if "resource_id" in update["Key"]:
                        values = {key: _ddb_decode(value) for key, value in update["ExpressionAttributeValues"].items()}
                        self.update_item(
                            Key={"resource_id": update["Key"]["resource_id"]["S"]},
                            UpdateExpression=update["UpdateExpression"],
                            ConditionExpression=update.get("ConditionExpression", ""),
                            ExpressionAttributeValues=values,
                        )
                    elif "allocation_id" in update["Key"]:
                        allocation_id = update["Key"]["allocation_id"]["S"]
                        alloc = self.allocation_items[allocation_id]
                        if alloc.get("status") != "OPEN":
                            raise _conditional_failed()
                        alloc["status"] = "RETURNED"
                if "Put" in step:
                    if self.fail_next_allocation_put:
                        self.fail_next_allocation_put = False
                        raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "PutItem")
                    alloc = {}
                    for key, value in step["Put"]["Item"].items():
                        alloc[key] = _ddb_decode(value)
                    if alloc["allocation_id"] in self.allocation_items:
                        raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "PutItem")
                    self.allocation_items[alloc["allocation_id"]] = alloc
        except ClientError:
            self.items = items_before
            self.allocation_items = allocations_before
            raise


class AuditCapture:
    def __init__(self):
        self.events = []

    def put_item(self, Item):
        self.events.append(copy.deepcopy(Item))


class Subscriptions:
    def __init__(self, rows=None):
        self.items = {item["organization_id"]: dict(item) for item in rows or []}

    def get_item(self, Key):
        item = self.items.get(Key["organization_id"])
        return {"Item": dict(item)} if item else {}


def store(resource):
    table = ConditionalTable("Resources", [resource])
    everyday._transact_write = table.transact_write_items
    allocations = type("Alloc", (), {"name": "Allocations", "allocation_items": table.allocation_items, "meta": table.meta})()
    allocations.get_item = lambda Key: table.get_item(Key)
    allocations.put_item = table.put_item
    allocations.update_item = table.update_item
    audit = AuditCapture()
    return {
        "resources": table,
        "allocations": allocations,
        "history": table,
        "audit": audit,
    }


def individual_resource(resource_id, organization_id=ORG_A, **extra):
    base = {
        "resource_id": resource_id,
        "organization_id": organization_id,
        "Available": True,
        "operational_status": "AVAILABLE",
        "tracking_mode": "INDIVIDUAL",
        "location_id": "LOC1",
        "Type": "Kit",
        "Location": "HQ",
    }
    base.update(extra)
    return base


def quantity_resource(resource_id, total=10, organization_id=ORG_A):
    return {
        "resource_id": resource_id,
        "organization_id": organization_id,
        "tracking_mode": "QUANTITY",
        "Available": False,
        "operational_status": "AVAILABLE",
        "quantity_total": total,
        "quantity_available": total,
        "quantity_reserved": 0,
        "quantity_allocated": 0,
        "location_id": "LOC1",
        "Type": "Supplies",
        "Location": "HQ",
    }


def assert_quantity_invariant(item):
    total = item["quantity_total"]
    parts = item["quantity_available"] + item["quantity_reserved"] + item["quantity_allocated"]
    assert total == parts
    assert item["quantity_available"] >= 0
    assert item["quantity_reserved"] >= 0
    assert item["quantity_allocated"] >= 0


def memberships(role, organization_id=ORG_A, subject=USER):
    return [
        {
            "organization_id": organization_id,
            "name": organization_id,
            "role": role,
            "status": "ACTIVE",
        }
    ]


def subscription_row(status, organization_id=ORG_A):
    return {
        "organization_id": organization_id,
        "subscription_status": status,
    }


def handler_event(path, body, organization_id=ORG_A, subject=USER):
    return {
        "httpMethod": "POST",
        "path": path,
        "queryStringParameters": {"organization_id": organization_id},
        "requestContext": {"authorizer": {"claims": {"sub": subject}}},
        "body": json.dumps(body),
    }


def wire_handler_tables(monkeypatch, resources, history=None, allocations=None, audit=None):
    monkeypatch.setattr(resource_handler, "resources_table", lambda: resources)
    monkeypatch.setattr(resource_handler, "history_table", lambda: history or resources)
    monkeypatch.setattr(resource_handler, "allocations_table", lambda: allocations or resources)
    monkeypatch.setattr(resource_handler, "audit_table", lambda: audit or AuditCapture())


def use_memberships(monkeypatch, records):
    monkeypatch.setattr(access, "list_memberships", lambda *args, **kwargs: records)


def use_subscriptions(monkeypatch, subscriptions):
    monkeypatch.setattr(access, "subscriptions_table", lambda: subscriptions)


@pytest.mark.parametrize(
    "resource,expected",
    [
        ({"Available": True}, True),
        ({"Available": False}, False),
        ({"Available": True, "operational_status": "AVAILABLE", "tracking_mode": "INDIVIDUAL"}, True),
        ({"Available": True, "operational_status": "RESERVED"}, False),
        ({"Available": False, "operational_status": "ALLOCATED"}, False),
        ({"Available": True, "operational_status": "IN_USE"}, False),
        ({"Available": True, "operational_status": "MAINTENANCE"}, False),
        ({"Available": True, "operational_status": "DAMAGED"}, False),
        ({"Available": True, "operational_status": "RETIRED"}, False),
        ({"Available": True, "tracking_mode": "QUANTITY"}, False),
    ],
)
def test_emergency_matchable_matrix(resource, expected):
    assert emergency_matchable(resource) is expected


def test_effective_operational_status_legacy():
    assert effective_operational_status({"Available": True}) == "AVAILABLE"
    assert effective_operational_status({"Available": False}) == "ALLOCATED"


def test_tenant_wrong_organization_on_reserve():
    resource = individual_resource("RESOURCE-A", ORG_A)
    tables = store(resource)
    with pytest.raises(everyday.EverydayOperationError) as error:
        everyday.reserve_individual({}, ORG_B, USER, "OPERATOR", resource, tables)
    assert error.value.status_code == 409


def test_tenant_handler_reserve_other_org_resource_returns_404(monkeypatch):
    resources = ConditionalTable("Resources", [individual_resource("RESOURCE-B", ORG_B)])
    use_memberships(monkeypatch, memberships("OPERATOR", ORG_A))
    use_subscriptions(monkeypatch, Subscriptions([subscription_row("ACTIVE")]))
    wire_handler_tables(monkeypatch, resources)

    result = resource_handler.lambda_handler(
        handler_event("/allocate/resources/reserve", {"resource_id": "RESOURCE-B"}),
        None,
    )

    assert result["statusCode"] == 404
    assert "Traceback" not in result["body"]


@pytest.mark.parametrize("role", ["OWNER", "ADMIN", "OPERATOR"])
def test_operational_roles_may_reserve_via_handler(monkeypatch, role):
    resources = ConditionalTable("Resources", [individual_resource("RESOURCE-A")])
    audit = AuditCapture()
    use_memberships(monkeypatch, memberships(role))
    use_subscriptions(monkeypatch, Subscriptions([subscription_row("ACTIVE")]))
    wire_handler_tables(monkeypatch, resources, audit=audit)

    result = resource_handler.lambda_handler(
        handler_event("/allocate/resources/reserve", {"resource_id": "RESOURCE-A"}),
        None,
    )

    assert result["statusCode"] == 200
    assert resources.items["RESOURCE-A"]["operational_status"] == "RESERVED"


def test_member_cannot_reserve_via_handler(monkeypatch):
    resources = ConditionalTable("Resources", [individual_resource("RESOURCE-A")])
    use_memberships(monkeypatch, memberships("MEMBER", subject=MEMBER))
    use_subscriptions(monkeypatch, Subscriptions([subscription_row("ACTIVE")]))
    wire_handler_tables(monkeypatch, resources)

    result = resource_handler.lambda_handler(
        handler_event("/allocate/resources/reserve", {"resource_id": "RESOURCE-A"}, subject=MEMBER),
        None,
    )

    assert result["statusCode"] == 403


@pytest.mark.parametrize("status", ["TRIALING", "ACTIVE", "PAST_DUE", "GRANDFATHERED"])
def test_billing_allows_operational_writes(monkeypatch, status):
    resources = ConditionalTable("Resources", [individual_resource("RESOURCE-A")])
    use_memberships(monkeypatch, memberships("OPERATOR"))
    use_subscriptions(monkeypatch, Subscriptions([subscription_row(status)]))
    wire_handler_tables(monkeypatch, resources)

    result = resource_handler.lambda_handler(
        handler_event("/allocate/resources/reserve", {"resource_id": "RESOURCE-A"}),
        None,
    )

    assert result["statusCode"] == 200


@pytest.mark.parametrize("status", ["CANCELLED", "EXPIRED"])
def test_billing_blocks_operational_writes(monkeypatch, status):
    resources = ConditionalTable("Resources", [individual_resource("RESOURCE-A")])
    use_memberships(monkeypatch, memberships("OPERATOR"))
    use_subscriptions(monkeypatch, Subscriptions([subscription_row(status)]))
    wire_handler_tables(monkeypatch, resources)

    result = resource_handler.lambda_handler(
        handler_event("/allocate/resources/reserve", {"resource_id": "RESOURCE-A"}),
        None,
    )

    assert result["statusCode"] == 403
    body = json.loads(result["body"])
    assert body.get("error", {}).get("code") == "BILLING_REQUIRED" or BILLING_REQUIRED in result["body"]


def test_concurrent_reserve_only_one_succeeds():
    resource = individual_resource("R-CONC")
    tables = store(resource)
    everyday.reserve_individual({}, ORG_A, USER, "OPERATOR", resource, tables)
    with pytest.raises(everyday.EverydayOperationError) as error:
        everyday.reserve_individual({}, ORG_A, USER, "OPERATOR", tables["resources"].items["R-CONC"], tables)
    assert error.value.status_code == 409
    assert len([event for event in tables["audit"].events if event.get("action") == "resource.reserve"]) == 1


def test_concurrent_release_reservation():
    resource = individual_resource("R-REL")
    tables = store(resource)
    everyday.reserve_individual({}, ORG_A, USER, "OPERATOR", resource, tables)
    reserved = tables["resources"].items["R-REL"]
    everyday.release_reservation({}, ORG_A, USER, "OPERATOR", reserved, tables)
    with pytest.raises(everyday.EverydayOperationError) as error:
        everyday.release_reservation({}, ORG_A, USER, "OPERATOR", tables["resources"].items["R-REL"], tables)
    assert error.value.status_code == 409


def test_concurrent_everyday_allocate():
    resource = individual_resource("R-ALLOC")
    tables = store(resource)
    everyday.everyday_allocate_individual({}, ORG_A, USER, "OPERATOR", resource, tables)
    with pytest.raises(everyday.EverydayOperationError) as error:
        everyday.everyday_allocate_individual({}, ORG_A, USER, "OPERATOR", tables["resources"].items["R-ALLOC"], tables)
    assert error.value.status_code == 409
    open_rows = [item for item in tables["resources"].allocation_items.values() if item.get("status") == "OPEN"]
    assert len(open_rows) == 1
    assert open_rows[0]["allocation_type"] == "EVERYDAY"
    assert open_rows[0]["allocation_id"].startswith("EVERYDAY-")


def test_concurrent_everyday_return():
    resource = individual_resource("R-RET")
    tables = store(resource)
    created = everyday.everyday_allocate_individual({}, ORG_A, USER, "OPERATOR", resource, tables)
    allocated = tables["resources"].items["R-RET"]
    everyday.everyday_return({"allocation_id": created["allocation_id"]}, ORG_A, USER, "OPERATOR", allocated, tables)
    with pytest.raises(everyday.EverydayOperationError) as error:
        everyday.everyday_return(
            {"allocation_id": created["allocation_id"]},
            ORG_A,
            USER,
            "OPERATOR",
            tables["resources"].items["R-RET"],
            tables,
        )
    assert error.value.status_code == 409


def test_quantity_concurrent_reserve():
    resource = quantity_resource("Q1", total=10)
    tables = store(resource)
    everyday.reserve_quantity({"quantity": 6}, ORG_A, USER, "OPERATOR", resource, tables)
    with pytest.raises(everyday.EverydayOperationError):
        everyday.reserve_quantity({"quantity": 6}, ORG_A, USER, "OPERATOR", tables["resources"].items["Q1"], tables)
    item = tables["resources"].items["Q1"]
    assert_quantity_invariant(item)
    assert item["quantity_available"] == 4
    assert item["quantity_reserved"] == 6


def test_quantity_concurrent_allocate():
    resource = quantity_resource("Q2", total=10)
    tables = store(resource)
    everyday.everyday_allocate_quantity({"quantity": 7}, ORG_A, USER, "OPERATOR", resource, tables)
    with pytest.raises(everyday.EverydayOperationError):
        everyday.everyday_allocate_quantity(
            {"quantity": 6}, ORG_A, USER, "OPERATOR", tables["resources"].items["Q2"], tables
        )
    item = tables["resources"].items["Q2"]
    assert_quantity_invariant(item)
    assert item["quantity_available"] == 3
    assert item["quantity_allocated"] == 7


def test_quantity_return_and_double_return():
    resource = quantity_resource("Q3", total=10)
    tables = store(resource)
    created = everyday.everyday_allocate_quantity({"quantity": 5}, ORG_A, USER, "OPERATOR", resource, tables)
    allocated = tables["resources"].items["Q3"]
    everyday.everyday_return({"allocation_id": created["allocation_id"]}, ORG_A, USER, "OPERATOR", allocated, tables)
    item = tables["resources"].items["Q3"]
    assert item["quantity_allocated"] == 0
    assert item["quantity_available"] == 10
    assert_quantity_invariant(item)
    with pytest.raises(everyday.EverydayOperationError):
        everyday.everyday_return(
            {"allocation_id": created["allocation_id"]},
            ORG_A,
            USER,
            "OPERATOR",
            item,
            tables,
        )


def test_transact_allocate_rolls_back_resource_on_failed_put():
    resource = individual_resource("R-ATOMIC")
    tables = store(resource)
    tables["resources"].fail_next_allocation_put = True
    with pytest.raises(everyday.EverydayOperationError):
        everyday.everyday_allocate_individual({}, ORG_A, USER, "OPERATOR", resource, tables)
    item = tables["resources"].items["R-ATOMIC"]
    assert item["operational_status"] == "AVAILABLE"
    assert item["Available"] is True
    assert not tables["resources"].allocation_items


def test_history_written_on_reserve():
    resource = individual_resource("R-HIST")
    tables = store(resource)
    everyday.reserve_individual({}, ORG_A, USER, "OPERATOR", resource, tables)
    assert any(entry.get("reason") == "RESOURCE_RESERVED" for entry in tables["history"].history)


def test_audit_not_written_on_failed_reserve():
    resource = individual_resource("R-AUD")
    tables = store(resource)
    everyday.reserve_individual({}, ORG_A, USER, "OPERATOR", resource, tables)
    with pytest.raises(everyday.EverydayOperationError):
        everyday.reserve_individual({}, ORG_A, USER, "OPERATOR", tables["resources"].items["R-AUD"], tables)
    assert len(tables["audit"].events) == 1


@pytest.mark.parametrize(
    "status",
    ["OPEN", "RETURNED"],
)
def test_auto_release_skips_everyday_statuses(status):
    decision = auto_release.plan_release(
        {
            "allocation_id": "EVERYDAY-1",
            "allocation_type": "EVERYDAY",
            "status": status,
            "organization_id": ORG_A,
            "resource_id": "R1",
        },
        {"resource_id": "R1", "organization_id": ORG_A},
        {"request_id": "REQ", "organization_id": ORG_A},
        [],
    )
    assert decision["action"] == "skip"


def test_auto_release_legacy_emergency_without_allocation_type():
    decision = auto_release.plan_release(
        {
            "allocation_id": "ALLOC-REQ1",
            "status": "ALLOCATED",
            "organization_id": ORG_A,
            "resource_id": "R1",
            "request_id": "REQ1",
        },
        {"resource_id": "R1", "organization_id": ORG_A},
        {"request_id": "REQ1", "organization_id": ORG_A},
        [],
    )
    assert decision["action"] == "release"


def test_public_view_hides_internal_fields():
    item = {
        "visibility_key": "PUBLIC",
        "visibility": "PUBLIC",
        "public_type_name": "Kit",
        "public_name": "Public kit",
        "public_city": "City",
        "public_state": "ST",
        "organization_id": ORG_A,
        "resource_id": "SECRET-ID",
        "operational_status": "RESERVED",
        "quantity_allocated": 5,
        "assigned_to": "user-1",
        "show_availability": True,
        "public_status": "AVAILABLE",
    }
    view = public_api.public_view(item)
    assert view is not None
    exposed = json.dumps(view)
    assert "SECRET-ID" not in exposed
    assert "organization_id" not in exposed
    assert "operational_status" not in exposed
    assert "quantity_allocated" not in exposed
    assert "assigned_to" not in exposed


def test_validation_missing_resource_id():
    with pytest.raises(everyday.EverydayOperationError) as error:
        everyday._resource_id({})
    assert error.value.status_code == 400


def test_validation_invalid_quantity():
    resource = quantity_resource("Q-VAL")
    tables = store(resource)
    with pytest.raises(everyday.EverydayOperationError) as error:
        everyday.reserve_quantity({"quantity": 0}, ORG_A, USER, "OPERATOR", resource, tables)
    assert error.value.status_code == 400


def test_emergency_allocation_still_writes_alloc_id(monkeypatch):
    use_memberships(monkeypatch, memberships("OPERATOR"))

    class QueryStore:
        def __init__(self, items):
            self.items = list(items)
            self.updates = []
            self.puts = []

        def query(self, **kwargs):
            return {"Items": list(self.items)}

        def get_item(self, Key):
            for item in self.items:
                if all(item.get(k) == v for k, v in Key.items()):
                    return {"Item": dict(item)}
            return {}

        def put_item(self, Item, ConditionExpression=None):
            self.puts.append(Item)
            self.items.append(Item)

        def update_item(self, **kwargs):
            self.updates.append(kwargs)
            resource_id = kwargs.get("Key", {}).get("resource_id")
            if not resource_id:
                return
            for item in self.items:
                if item.get("resource_id") == resource_id:
                    item["Available"] = False
                    item["operational_status"] = "ALLOCATED"

    locations = QueryStore(
        [{"organization_id": ORG_A, "location_id": "LOC-A", "status": "ACTIVE", "name": "North"}]
    )
    requests = QueryStore(
        [
            {
                "request_id": "Q1",
                "organization_id": ORG_A,
                "location_id": "LOC-A",
                "Status": "PENDING",
                "ResourceType": "Kit",
                "Priority": 1,
            }
        ]
    )
    resources = QueryStore(
        [
            {
                "resource_id": "R1",
                "organization_id": ORG_A,
                "location_id": "LOC-A",
                "Type": "Kit",
                "Available": True,
                "operational_status": "AVAILABLE",
            }
        ]
    )
    allocations = QueryStore([])
    history = QueryStore([])
    use_subscriptions(monkeypatch, Subscriptions([subscription_row("ACTIVE")]))
    monkeypatch.setattr(allocation_service, "locations_table", lambda: locations)
    monkeypatch.setattr(allocation_service, "requests_table", lambda: requests)
    monkeypatch.setattr(allocation_service, "resources_table", lambda: resources)
    monkeypatch.setattr(allocation_service, "allocations_table", lambda: allocations)
    monkeypatch.setattr(allocation_service, "history_table", lambda: history)
    monkeypatch.setattr(allocation_service, "audit_table", lambda: None)
    monkeypatch.setattr(allocation_service, "request_types_table", lambda: QueryStore([]))
    monkeypatch.setattr(
        allocation_service,
        "events_client",
        lambda: type("Events", (), {"put_events": lambda self, **kwargs: {}})(),
    )

    def _transact(items):
        from transact_memory import apply_transact
        from boto3.dynamodb.types import TypeDeserializer

        apply_transact(
            {
                "Resources": resources.items,
                "Allocations": allocations.items,
                "EmergencyRequests": requests.items,
            },
            items,
        )
        decoder = TypeDeserializer()
        for entry in items:
            if "Put" in entry:
                allocations.puts.append(
                    {key: decoder.deserialize(value) for key, value in entry["Put"]["Item"].items()}
                )

    monkeypatch.setattr(allocation_service, "_transact_write", _transact)

    result = allocation_service.lambda_handler(
        {
            "httpMethod": "POST",
            "path": "/allocate",
            "queryStringParameters": {"organization_id": ORG_A},
            "requestContext": {"authorizer": {"claims": {"sub": USER}}},
            "body": json.dumps(
                {
                    "request_id": "Q1",
                    "resource_type": "Kit",
                    "location_id": "LOC-A",
                    "organization_id": ORG_A,
                    "priority": 1,
                    "confirm": True,
                    "resource_id": "R1",
                }
            ),
        },
        None,
    )

    assert result["statusCode"] == 200
    assert any(item.get("allocation_id") == "ALLOC-Q1" for item in allocations.puts)
    assert resources.items[0]["Available"] is False


def test_emergency_release_restores_resource(monkeypatch):
    use_memberships(monkeypatch, memberships("OPERATOR"))
    use_subscriptions(monkeypatch, Subscriptions([subscription_row("ACTIVE")]))

    class Table:
        def __init__(self, items):
            self.rows = list(items)

        def get_item(self, Key):
            for item in self.rows:
                if all(item.get(k) == v for k, v in Key.items()):
                    return {"Item": dict(item)}
            return {}

        def query(self, **kwargs):
            return {"Items": list(self.rows)}

        def put_item(self, Item, ConditionExpression=None):
            self.rows.append(Item)

        def update_item(self, **kwargs):
            key = kwargs["Key"]
            for item in self.rows:
                if all(item.get(k) == v for k, v in key.items()):
                    if "Available" in kwargs.get("UpdateExpression", ""):
                        item["Available"] = True
                        item["operational_status"] = "AVAILABLE"
                    if kwargs.get("ExpressionAttributeValues", {}).get(":released"):
                        item["status"] = "RELEASED"
                    if kwargs.get("ExpressionAttributeValues", {}).get(":released") and "Status" in str(
                        kwargs.get("ExpressionAttributeNames", {})
                    ):
                        item["Status"] = "RELEASED"

    resources = Table(
        [
            {
                "resource_id": "R-EM",
                "organization_id": ORG_A,
                "Available": False,
                "operational_status": "ALLOCATED",
                "location_id": "LOC1",
            }
        ]
    )
    allocations = Table(
        [
            {
                "allocation_id": "ALLOC-Q-EM",
                "request_id": "Q-EM",
                "resource_id": "R-EM",
                "organization_id": ORG_A,
                "status": "ALLOCATED",
                "allocated_at": "2026-01-01T00:00:00+00:00",
            }
        ]
    )
    requests = Table(
        [
            {
                "request_id": "Q-EM",
                "organization_id": ORG_A,
                "Status": "ALLOCATED",
            }
        ]
    )
    history = Table([])
    import emergency_release
    from transact_memory import apply_transact

    def _transact(items):
        apply_transact(
            {
                "Resources": resources.rows,
                "Allocations": allocations.rows,
                "EmergencyRequests": requests.rows,
            },
            items,
        )

    monkeypatch.setattr(emergency_release, "_transact_write", _transact)
    monkeypatch.setattr(resource_handler, "resources_table", lambda: resources)
    monkeypatch.setattr(resource_handler, "allocations_table", lambda: allocations)
    monkeypatch.setattr(resource_handler, "requests_table", lambda: requests)
    monkeypatch.setattr(resource_handler, "history_table", lambda: history)
    monkeypatch.setattr(resource_handler, "audit_table", lambda: AuditCapture())

    result = resource_handler.lambda_handler(
        handler_event("/allocate/resources/release", {"resource_id": "R-EM"}),
        None,
    )

    assert result["statusCode"] == 200
    assert resources.rows[0]["Available"] is True
    assert resources.rows[0]["operational_status"] == "AVAILABLE"
    assert allocations.rows[0]["status"] == "RELEASED"
    assert requests.rows[0]["Status"] == "RELEASED"
