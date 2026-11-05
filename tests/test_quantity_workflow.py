"""Phase 15L: request quantity, authorized inventory counters, and quantity allocation."""

import copy
import importlib.util
import json
import sys
from decimal import Decimal
from pathlib import Path

from boto3.dynamodb.types import TypeDeserializer
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [
    str(ROOT / "src" / "shared"),
    str(ROOT / "src" / "organization"),
]

import access
from api_views import RESOURCE_FIELDS, request_view, resource_view
from resource_state import lifecycle_fields_from_body
from transact_memory import _decode

request_handler = importlib.util.spec_from_file_location(
    "request_handler_qty_workflow", ROOT / "src" / "request" / "handler.py"
)
request_mod = importlib.util.module_from_spec(request_handler)
sys.modules["request_handler_qty_workflow"] = request_mod
request_handler.loader.exec_module(request_mod)

allocation_spec = importlib.util.spec_from_file_location(
    "allocation_service_qty_workflow", ROOT / "src" / "allocation" / "service.py"
)
allocation = importlib.util.module_from_spec(allocation_spec)
sys.modules["allocation_service_qty_workflow"] = allocation
allocation_spec.loader.exec_module(allocation)

public_spec = importlib.util.spec_from_file_location(
    "public_handler_qty_workflow", ROOT / "src" / "public" / "handler.py"
)
public_api = importlib.util.module_from_spec(public_spec)
sys.modules["public_handler_qty_workflow"] = public_api
public_spec.loader.exec_module(public_api)

ORG = "ORG-A"
USER = "operator-sub"
LOCATION_ID = "LOC-A"
REQUEST_TYPE_ID = "RQ-1"
RESOURCE_ID = "POOL-1"
_decoder = TypeDeserializer()


class Store:
    def __init__(self, items=None):
        self.items = [dict(item) for item in (items or [])]
        self.puts = []
        self.updates = []

    def query(self, **kwargs):
        return {"Items": [dict(item) for item in self.items]}

    def get_item(self, Key):
        for item in self.items:
            if all(item.get(key) == value for key, value in Key.items()):
                return {"Item": dict(item)}
        return {}

    def put_item(self, Item, ConditionExpression=None):
        self.puts.append(dict(Item))
        self.items.append(dict(Item))

    def update_item(self, **kwargs):
        self.updates.append(kwargs)


def use_owner(monkeypatch):
    monkeypatch.setattr(
        access,
        "list_memberships",
        lambda *args, **kwargs: [
            {"organization_id": ORG, "name": ORG, "role": "OWNER", "status": "ACTIVE"}
        ],
    )


def location():
    return {
        "organization_id": ORG,
        "location_id": LOCATION_ID,
        "name": "North",
        "status": "ACTIVE",
    }


def request_type():
    return {
        "organization_id": ORG,
        "request_type_id": REQUEST_TYPE_ID,
        "name": "Supply",
        "status": "ACTIVE",
        "attributes_schema": {"fields": []},
        "matching_config": {},
        "default_priority": Decimal("3"),
    }


def quantity_resource(**extra):
    item = {
        "resource_id": RESOURCE_ID,
        "organization_id": ORG,
        "location_id": LOCATION_ID,
        "name": "Water",
        "Type": "Water",
        "Available": False,
        "operational_status": "AVAILABLE",
        "tracking_mode": "QUANTITY",
        "quantity_total": 10,
        "quantity_available": 10,
        "quantity_reserved": 0,
        "quantity_allocated": 0,
        "quantity_unit": "LITERS",
    }
    item.update(extra)
    return item


def request_event(body):
    return {
        "httpMethod": "POST",
        "path": "/requests",
        "headers": {"Authorization": "Bearer secret-token-value"},
        "requestContext": {"authorizer": {"claims": {"sub": USER}}},
        "body": json.dumps(body),
    }


def wire_request(monkeypatch, resource=None):
    use_owner(monkeypatch)
    resources = Store([resource] if resource else [])
    requests = Store()
    monkeypatch.setattr(request_mod, "request_types_table", lambda: Store([request_type()]))
    monkeypatch.setattr(request_mod, "locations_table", lambda: Store([location()]))
    monkeypatch.setattr(request_mod, "resources_table", lambda: resources)
    monkeypatch.setattr(request_mod, "requests_table", lambda: requests)
    monkeypatch.setattr(request_mod, "audit_table", lambda: Store())
    return resources, requests


def test_create_request_persists_quantity_without_decrementing_inventory(monkeypatch):
    resources, requests = wire_request(monkeypatch, quantity_resource())
    before = copy.deepcopy(resources.items[0])
    result = request_mod.lambda_handler(
        request_event(
            {
                "request_id": "REQ-Q1",
                "request_type_id": REQUEST_TYPE_ID,
                "location_id": LOCATION_ID,
                "priority": 2,
                "organization_id": ORG,
                "resource_id": RESOURCE_ID,
                "quantity_requested": 4,
                "quantity_unit": "LITERS",
            }
        ),
        None,
    )
    body = json.loads(result["body"])
    saved = requests.puts[0]
    assert result["statusCode"] == 201
    assert saved["resource_id"] == RESOURCE_ID
    assert saved["quantity_requested"] == 4
    assert saved["quantity_unit"] == "LITERS"
    assert saved["Status"] == "PENDING"
    assert body["request"]["resource_id"] == RESOURCE_ID
    assert body["request"]["quantity_requested"] == 4
    assert body["request"]["quantity_unit"] == "LITERS"
    assert resources.items[0] == before
    assert resources.updates == []


def test_create_request_rejects_invalid_and_unavailable_quantity(monkeypatch):
    wire_request(monkeypatch, quantity_resource(quantity_available=2, quantity_total=10, quantity_allocated=8))
    zero = request_mod.lambda_handler(
        request_event(
            {
                "request_id": "REQ-BAD",
                "request_type_id": REQUEST_TYPE_ID,
                "location_id": LOCATION_ID,
                "priority": 2,
                "resource_id": RESOURCE_ID,
                "quantity_requested": 0,
            }
        ),
        None,
    )
    over = request_mod.lambda_handler(
        request_event(
            {
                "request_id": "REQ-OVER",
                "request_type_id": REQUEST_TYPE_ID,
                "location_id": LOCATION_ID,
                "priority": 2,
                "resource_id": RESOURCE_ID,
                "quantity_requested": 5,
            }
        ),
        None,
    )
    assert zero["statusCode"] == 400
    assert over["statusCode"] == 400
    assert "not available" in json.loads(over["body"])["message"]


def test_authorized_inventory_returns_quantity_counters_public_does_not():
    item = quantity_resource()
    view = resource_view(item)
    assert view["quantity_total"] == 10
    assert view["quantity_available"] == 10
    assert view["quantity_reserved"] == 0
    assert view["quantity_allocated"] == 0
    assert view["quantity_unit"] == "LITERS"
    assert set(view) <= set(RESOURCE_FIELDS)
    public = public_api.public_view(
        {
            "visibility": "PUBLIC",
            "visibility_key": "PUBLIC",
            "public_name": "Water",
            "public_type_name": "Water",
            "public_city": "City",
            "quantity_total": 10,
            "quantity_available": 10,
            "quantity_reserved": 1,
            "quantity_allocated": 2,
            "resource_id": RESOURCE_ID,
            "organization_id": ORG,
        }
    )
    assert "quantity_total" not in public
    assert "quantity_available" not in public
    assert "quantity_reserved" not in public
    assert "quantity_allocated" not in public


def test_legacy_individual_resource_view_omits_invented_totals():
    view = resource_view(
        {
            "resource_id": "R-LEGACY",
            "organization_id": ORG,
            "tracking_mode": "INDIVIDUAL",
            "Available": True,
            "operational_status": "AVAILABLE",
        }
    )
    assert "quantity_total" not in view
    assert "quantity_available" not in view
    assert lifecycle_fields_from_body({"quantity_total": 9, "quantity_available": 9})


def _eval_expr(item, text, values):
    text = text.strip()
    if text.startswith(":"):
        return values[text]
    if " + " in text:
        total = 0
        for part in text.split(" + "):
            part = part.strip()
            total += values[part] if part.startswith(":") else item.get(part, 0)
        return total
    if text.startswith(":"):
        return values[text]
    return item.get(text)


def _holds_quantity(item, condition, values):
    if not condition:
        return True
    for clause in condition.split(" AND "):
        text = clause.strip()
        if text.startswith("(") and text.endswith(")"):
            inner = text[1:-1]
            if " OR " in inner:
                left, right = inner.split(" OR ", 1)
                if not (_holds_quantity(item, left, values) or _holds_quantity(item, right, values)):
                    return False
                continue
            text = inner
        if text.startswith("attribute_exists("):
            if text[len("attribute_exists(") : -1] not in item:
                return False
            continue
        if text.startswith("attribute_not_exists("):
            if text[len("attribute_not_exists(") : -1] in item:
                return False
            continue
        if " <> " in text:
            left, right = text.split(" <> ", 1)
            if item.get(left.strip()) == values[right.strip()]:
                return False
            continue
        if " >= " in text:
            left, right = text.split(" >= ", 1)
            if _eval_expr(item, left, values) < _eval_expr(item, right, values):
                return False
            continue
        if " <= " in text:
            left, right = text.split(" <= ", 1)
            if _eval_expr(item, left, values) > _eval_expr(item, right, values):
                return False
            continue
        left, right = text.split(" = ", 1)
        if _eval_expr(item, left, values) != _eval_expr(item, right, values):
            return False
    return True


def apply_quantity_transact(tables, transact_items):
    pending = []
    reasons = []
    failed = False
    for entry in transact_items:
        if "Update" in entry:
            update = entry["Update"]
            values = _decode(update.get("ExpressionAttributeValues") or {})
            names = update.get("ExpressionAttributeNames") or {}
            key = _decode(update["Key"])
            rows = tables[update["TableName"]]
            current = next((row for row in rows if all(row.get(k) == v for k, v in key.items())), None)
            condition = update.get("ConditionExpression") or ""
            for placeholder, attribute in names.items():
                condition = condition.replace(placeholder, attribute)
            if current is None or not _holds_quantity(current, condition, values):
                reasons.append({"Code": "ConditionalCheckFailed"})
                failed = True
                continue
            expression = update.get("UpdateExpression") or ""
            for placeholder, attribute in names.items():
                expression = expression.replace(placeholder, attribute)
            body = expression[4:] if expression.startswith("SET ") else expression
            snapshot = dict(current)

            def apply(current=current, body=body, values=values, snapshot=snapshot):
                for part in body.split(","):
                    left, right = [piece.strip() for piece in part.split("=", 1)]
                    if " - " in right:
                        attr, token = [piece.strip() for piece in right.split(" - ", 1)]
                        current[left] = snapshot[attr] - values[token]
                    elif " + " in right:
                        attr, token = [piece.strip() for piece in right.split(" + ", 1)]
                        current[left] = snapshot[attr] + values[token]
                    else:
                        current[left] = values[right]

            reasons.append({"Code": "None"})
            pending.append(apply)
        elif "Put" in entry:
            put = entry["Put"]
            item = _decode(put["Item"])
            rows = tables[put["TableName"]]
            exists = any(row.get("allocation_id") == item.get("allocation_id") for row in rows)
            if "attribute_not_exists(allocation_id)" in (put.get("ConditionExpression") or "") and exists:
                reasons.append({"Code": "ConditionalCheckFailed"})
                failed = True
                continue
            reasons.append({"Code": "None"})
            pending.append(lambda rows=rows, item=item: rows.append(dict(item)))
        else:
            raise AssertionError("unsupported")
    if failed:
        raise ClientError(
            {
                "Error": {"Code": "TransactionCanceledException", "Message": "Transaction cancelled"},
                "CancellationReasons": reasons,
            },
            "TransactWriteItems",
        )
    for action in pending:
        action()


class AllocationWorld:
    def __init__(self, resource, request):
        self.resources = Store([resource])
        self.requests = Store([request])
        self.allocations = Store()
        self.history = Store()
        self.audit = Store()

    def bind(self, monkeypatch):
        use_owner(monkeypatch)
        monkeypatch.setattr(allocation, "locations_table", lambda: Store([location()]))
        monkeypatch.setattr(allocation, "resources_table", lambda: self.resources)
        monkeypatch.setattr(allocation, "requests_table", lambda: self.requests)
        monkeypatch.setattr(allocation, "allocations_table", lambda: self.allocations)
        monkeypatch.setattr(allocation, "history_table", lambda: self.history)
        monkeypatch.setattr(allocation, "audit_table", lambda: self.audit)
        class Events:
            def put_events(self, **kwargs):
                return None

        monkeypatch.setattr(allocation, "events_client", lambda: Events())
        monkeypatch.setattr(allocation, "request_types_table", lambda: Store())

        def _transact(items):
            apply_quantity_transact(
                {
                    "Resources": self.resources.items,
                    "Allocations": self.allocations.items,
                    "EmergencyRequests": self.requests.items,
                },
                items,
            )

        monkeypatch.setattr(allocation, "_transact_write", _transact)

    def allocate(self, confirm=True):
        result = allocation.lambda_handler(
            {
                "httpMethod": "POST",
                "path": "/allocate",
                "queryStringParameters": {"organization_id": ORG},
                "requestContext": {"authorizer": {"claims": {"sub": USER}}},
                "body": json.dumps(
                    {
                        "request_id": "REQ-Q1",
                        "resource_type": "Water",
                        "location_id": LOCATION_ID,
                        "priority": 2,
                        "organization_id": ORG,
                        "confirm": confirm,
                        "resource_id": RESOURCE_ID,
                    }
                ),
            },
            None,
        )
        return result["statusCode"], json.loads(result["body"])


def quantity_request():
    return {
        "request_id": "REQ-Q1",
        "organization_id": ORG,
        "location_id": LOCATION_ID,
        "Status": "PENDING",
        "ResourceType": "Water",
        "Priority": 2,
        "resource_id": RESOURCE_ID,
        "quantity_requested": 4,
        "quantity_unit": "LITERS",
    }


def test_quantity_allocation_updates_counters_and_rejects_retry(monkeypatch):
    world = AllocationWorld(quantity_resource(), quantity_request())
    world.bind(monkeypatch)
    first, body = world.allocate()
    assert first == 200
    item = world.resources.items[0]
    assert item["quantity_available"] == 6
    assert item["quantity_allocated"] == 4
    assert item["quantity_reserved"] == 0
    assert item["quantity_total"] == 10
    assert world.requests.items[0]["Status"] == "ALLOCATED"
    assert world.allocations.items[0]["quantity"] == 4
    second, retry = world.allocate()
    assert second == 409
    assert retry["message"] == "Request is not eligible for allocation"
    assert world.resources.items[0]["quantity_available"] == 6
    assert len(world.allocations.items) == 1


def test_quantity_allocation_cannot_exceed_available(monkeypatch):
    world = AllocationWorld(quantity_resource(quantity_available=2, quantity_allocated=8), quantity_request())
    world.bind(monkeypatch)
    status, body = world.allocate()
    assert status == 200
    assert body["status"] == "PARTIALLY_ALLOCATED"
    assert body["quantity"] == 2
    assert body["quantity_remaining"] == 2
    assert world.resources.items[0]["quantity_available"] == 0
    assert world.resources.items[0]["quantity_allocated"] == 10
    assert world.requests.items[0]["Status"] == "PARTIALLY_ALLOCATED"
    assert world.requests.items[0]["quantity_fulfilled"] == 2
    assert world.allocations.items[0]["quantity"] == 2


def test_quantity_allocation_rejects_zero_available(monkeypatch):
    world = AllocationWorld(quantity_resource(quantity_available=0, quantity_allocated=10), quantity_request())
    world.bind(monkeypatch)
    status, body = world.allocate()
    assert status == 404
    assert body["message"] == "No suitable resource available"
    assert world.allocations.items == []
    assert world.requests.items[0]["Status"] == "PENDING"


def test_quantity_partial_then_remaining_allocation(monkeypatch):
    world = AllocationWorld(quantity_resource(quantity_available=2, quantity_allocated=8), quantity_request())
    world.bind(monkeypatch)
    first, body = world.allocate()
    assert first == 200
    assert body["status"] == "PARTIALLY_ALLOCATED"
    world.resources.items[0]["quantity_available"] = 5
    world.resources.items[0]["quantity_allocated"] = 5
    world.resources.items[0]["quantity_reserved"] = 0
    world.resources.items[0]["quantity_total"] = 10
    second, filled = world.allocate()
    assert second == 200
    assert filled["status"] == "ALLOCATED"
    assert filled["quantity"] == 4
    assert filled["quantity_remaining"] == 0
    assert world.requests.items[0]["Status"] == "ALLOCATED"
    assert world.requests.items[0]["quantity_fulfilled"] == 4
    assert world.allocations.items[0]["quantity"] == 4
    assert len(world.allocations.items) == 1
    third, retry = world.allocate()
    assert third == 409
    assert world.resources.items[0]["quantity_available"] == 3
