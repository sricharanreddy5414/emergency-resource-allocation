"""Phase 17: approval, rejection, allocation gate, and completion."""

import importlib.util
import json
import sys
from pathlib import Path

from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [
    str(ROOT / "src" / "shared"),
    str(ROOT / "src" / "organization"),
]

import access
from request_lifecycle import REVIEW_APPROVED, REVIEW_PENDING, allocation_review_allowed
from transitions import can_complete_request, can_reject_request


def load_module(name, relative):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


request_handler = load_module("request_handler_lifecycle", "src/request/handler.py")
allocation = load_module("allocation_service_lifecycle", "src/allocation/service.py")

ORG = "ORG-A"
USER = "operator-sub"
CREATOR = "member-sub"
LOCATION_ID = "LOC-A"


def body_of(result):
    return json.loads(result["body"])


def use_role(monkeypatch, role="OPERATOR", subject=USER):
    monkeypatch.setattr(
        access,
        "list_memberships",
        lambda *args, **kwargs: [
            {"organization_id": ORG, "name": ORG, "role": role, "status": "ACTIVE"}
        ],
    )
    return subject


class RequestTable:
    def __init__(self, item):
        self.item = dict(item)

    def get_item(self, Key):
        if self.item.get("request_id") != Key.get("request_id"):
            return {}
        return {"Item": dict(self.item)}

    def put_item(self, Item, **kwargs):
        self.item = dict(Item)

    def update_item(self, Key, UpdateExpression, ConditionExpression, ExpressionAttributeNames=None, ExpressionAttributeValues=None):
        names = ExpressionAttributeNames or {}
        values = ExpressionAttributeValues or {}
        condition = ConditionExpression
        for placeholder, attribute in names.items():
            condition = condition.replace(placeholder, attribute)
        if self.item.get("request_id") != Key.get("request_id"):
            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")
        if "organization_id = :organization_id" in condition and self.item.get("organization_id") != values[":organization_id"]:
            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")
        if "#status = :pending" in ConditionExpression or "Status = :pending" in condition:
            if self.item.get("Status") != values.get(":pending"):
                raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")
        if "Status = :allocated" in condition or ":allocated" in values and "ALLOCATED" in str(values.get(":allocated")):
            if ":allocated" in values and "AND #status = :allocated" in ConditionExpression:
                if self.item.get("Status") != values[":allocated"]:
                    raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")
        if "attribute_not_exists(review_status) OR review_status = :pending_review" in condition:
            current = str(self.item.get("review_status") or "")
            if current and current != values.get(":pending_review"):
                raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")
        expression = UpdateExpression
        for placeholder, attribute in names.items():
            expression = expression.replace(placeholder, attribute)
        body = expression[4:] if expression.startswith("SET ") else expression
        for part in body.split(","):
            left, right = [piece.strip() for piece in part.split("=", 1)]
            self.item[left] = values[right]


class Store:
    def __init__(self, items=None):
        self.items = [dict(item) for item in (items or [])]

    def get_item(self, Key):
        for item in self.items:
            if all(item.get(key) == value for key, value in Key.items()):
                return {"Item": dict(item)}
        return {}

    def query(self, **kwargs):
        return {"Items": [dict(item) for item in self.items]}

    def put_item(self, Item, **kwargs):
        self.items.append(dict(Item))


def pending_request(**extra):
    item = {
        "request_id": "REQ-L1",
        "organization_id": ORG,
        "location_id": LOCATION_ID,
        "Status": "PENDING",
        "ResourceType": "Water",
        "Location": "North",
        "Priority": 2,
        "created_by": CREATOR,
        "review_status": REVIEW_PENDING,
        "quantity_requested": 4,
        "quantity_unit": "LITERS",
        "resource_id": "POOL-1",
    }
    item.update(extra)
    return item


def event(path, body, subject=USER):
    return {
        "httpMethod": "POST",
        "path": path,
        "requestContext": {"authorizer": {"claims": {"sub": subject}}},
        "body": json.dumps(body),
    }


def bind_request(monkeypatch, item, role="OPERATOR", subject=USER):
    use_role(monkeypatch, role, subject)
    table = RequestTable(item)
    monkeypatch.setattr(request_handler, "requests_table", lambda: table)
    monkeypatch.setattr(request_handler, "audit_table", lambda: Store())
    monkeypatch.setattr(request_handler, "locations_table", lambda: Store())
    monkeypatch.setattr(request_handler, "allocations_table", lambda: Store())
    monkeypatch.setattr(request_handler, "deliveries_table", lambda: Store())
    return table


def test_legacy_pending_without_review_remains_allocatable():
    assert allocation_review_allowed({"Status": "PENDING"})
    assert not allocation_review_allowed({"review_status": REVIEW_PENDING})
    assert allocation_review_allowed({"review_status": REVIEW_APPROVED})
    assert can_reject_request("PENDING")
    assert can_complete_request("ALLOCATED")
    assert not can_complete_request("PENDING")


def test_operator_can_approve_without_allocating(monkeypatch):
    table = bind_request(monkeypatch, pending_request())
    result = request_handler.lambda_handler(
        event("/requests/approve", {"request_id": "REQ-L1", "organization_id": ORG}),
        None,
    )
    payload = body_of(result)
    assert result["statusCode"] == 200
    assert table.item["review_status"] == REVIEW_APPROVED
    assert table.item["Status"] == "PENDING"
    assert payload["request"]["review_status"] == REVIEW_APPROVED
    assert "created_by" not in payload["request"]


def test_member_cannot_approve(monkeypatch):
    bind_request(monkeypatch, pending_request(), role="MEMBER")
    result = request_handler.lambda_handler(
        event("/requests/approve", {"request_id": "REQ-L1", "organization_id": ORG}),
        None,
    )
    assert result["statusCode"] == 403


def test_creator_cannot_approve_own_request(monkeypatch):
    bind_request(monkeypatch, pending_request(), role="OPERATOR", subject=CREATOR)
    result = request_handler.lambda_handler(
        event("/requests/approve", {"request_id": "REQ-L1", "organization_id": ORG}, subject=CREATOR),
        None,
    )
    assert result["statusCode"] == 403
    assert "own requests" in body_of(result)["message"]


def test_reject_requires_reason_and_preserves_row(monkeypatch):
    table = bind_request(monkeypatch, pending_request())
    missing = request_handler.lambda_handler(
        event("/requests/reject", {"request_id": "REQ-L1", "organization_id": ORG}),
        None,
    )
    assert missing["statusCode"] == 400
    result = request_handler.lambda_handler(
        event(
            "/requests/reject",
            {"request_id": "REQ-L1", "organization_id": ORG, "reason": "Out of area"},
        ),
        None,
    )
    assert result["statusCode"] == 200
    assert table.item["Status"] == "REJECTED"
    assert table.item["rejection_reason"] == "Out of area"
    assert table.item["request_id"] == "REQ-L1"


def test_unapproved_request_is_not_allocated(monkeypatch):
    use_role(monkeypatch)
    requests = Store([pending_request()])
    monkeypatch.setattr(allocation, "requests_table", lambda: requests)
    monkeypatch.setattr(allocation, "locations_table", lambda: Store([
        {"organization_id": ORG, "location_id": LOCATION_ID, "name": "North", "status": "ACTIVE"}
    ]))
    monkeypatch.setattr(allocation, "resources_table", lambda: Store())
    monkeypatch.setattr(allocation, "request_types_table", lambda: Store())
    result = allocation.lambda_handler(
        {
            "httpMethod": "POST",
            "path": "/allocate",
            "queryStringParameters": {"organization_id": ORG},
            "requestContext": {"authorizer": {"claims": {"sub": USER}}},
            "body": json.dumps(
                {
                    "request_id": "REQ-L1",
                    "resource_type": "Water",
                    "location_id": LOCATION_ID,
                    "priority": 2,
                    "organization_id": ORG,
                    "confirm": True,
                    "resource_id": "POOL-1",
                }
            ),
        },
        None,
    )
    assert result["statusCode"] == 409
    assert body_of(result)["message"] == "Request is not approved for allocation"
    assert requests.items[0]["Status"] == "PENDING"


def test_complete_rejects_partially_allocated_request(monkeypatch):
    bind_request(monkeypatch, pending_request(Status="PARTIALLY_ALLOCATED", review_status=REVIEW_APPROVED, quantity_fulfilled=2))
    result = request_handler.lambda_handler(
        event("/requests/complete", {"request_id": "REQ-L1", "organization_id": ORG}),
        None,
    )
    assert result["statusCode"] == 409
    assert "Remaining quantity" in body_of(result)["message"]


def test_complete_is_idempotent_when_already_completed(monkeypatch):
    table = bind_request(monkeypatch, pending_request(Status="COMPLETED", review_status=REVIEW_APPROVED))
    result = request_handler.lambda_handler(
        event("/requests/complete", {"request_id": "REQ-L1", "organization_id": ORG}),
        None,
    )
    assert result["statusCode"] == 200
    assert table.item["Status"] == "COMPLETED"


def test_complete_requires_receipt(monkeypatch):
    table = bind_request(
        monkeypatch,
        pending_request(Status="ALLOCATED", review_status=REVIEW_APPROVED, quantity_fulfilled=4),
    )
    monkeypatch.setattr(
        request_handler,
        "allocations_table",
        lambda: Store(
            [
                {
                    "allocation_id": "ALLOC-REQ-L1",
                    "organization_id": ORG,
                    "status": "ALLOCATED",
                    "request_id": "REQ-L1",
                    "quantity": 4,
                }
            ]
        ),
    )
    result = request_handler.lambda_handler(
        event("/requests/complete", {"request_id": "REQ-L1", "organization_id": ORG}),
        None,
    )
    assert result["statusCode"] == 409
    assert "Receipt confirmation is required" in body_of(result)["message"]
    monkeypatch.setattr(
        request_handler,
        "deliveries_table",
        lambda: Store(
            [
                {
                    "delivery_id": "DELIV-1",
                    "organization_id": ORG,
                    "allocation_id": "ALLOC-REQ-L1",
                    "request_id": "REQ-L1",
                    "status": "RECEIVED",
                }
            ]
        ),
    )
    done = request_handler.lambda_handler(
        event("/requests/complete", {"request_id": "REQ-L1", "organization_id": ORG}),
        None,
    )
    assert done["statusCode"] == 200
    assert table.item["Status"] == "COMPLETED"


def test_create_sets_pending_review(monkeypatch):
    use_role(monkeypatch, "MEMBER", CREATOR)
    requests = Store()
    monkeypatch.setattr(request_handler, "requests_table", lambda: requests)
    monkeypatch.setattr(request_handler, "audit_table", lambda: Store())
    monkeypatch.setattr(
        request_handler,
        "locations_table",
        lambda: Store(
            [{"organization_id": ORG, "location_id": LOCATION_ID, "name": "North", "status": "ACTIVE"}]
        ),
    )
    monkeypatch.setattr(request_handler, "request_types_table", lambda: Store())
    monkeypatch.setattr(request_handler, "resources_table", lambda: Store())
    result = request_handler.lambda_handler(
        event(
            "/requests",
            {
                "request_id": "REQ-NEW",
                "resource_type": "Water",
                "location_id": LOCATION_ID,
                "priority": 2,
                "organization_id": ORG,
                "description": "Need oxygen",
            },
            subject=CREATOR,
        ),
        None,
    )
    assert result["statusCode"] == 201
    item = requests.items[0]
    assert item["review_status"] == REVIEW_PENDING
    assert item["Status"] == "PENDING"
    assert item["created_by"] == CREATOR
    assert item["description"] == "Need oxygen"
    assert "created_by" not in body_of(result)["request"]
