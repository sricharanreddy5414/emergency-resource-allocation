"""P9-03: a pending emergency request can be cancelled without touching a resource."""

import importlib.util
import inspect
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
from api_views import REQUEST_FIELDS
from transitions import can_allocate_request, can_cancel_request


def load_module(name, relative):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


request_handler = load_module("request_handler_p9c", "src/request/handler.py")
allocation = load_module("allocation_service_p9c", "src/allocation/service.py")

ORG = "ORG-A"
OTHER = "ORG-B"
USER = "user-p9c"
LOCATION_ID = "LOC-A"


def body_of(result):
    return json.loads(result["body"])


def use_member(monkeypatch, role="MEMBER", status="ACTIVE"):
    monkeypatch.setattr(
        access,
        "list_memberships",
        lambda *args, **kwargs: [
            {
                "organization_id": ORG,
                "name": ORG,
                "role": role,
                "status": status,
            }
        ],
    )


def event(body, method="POST", path="/requests/cancel"):
    return {
        "httpMethod": method,
        "path": path,
        "requestContext": {"authorizer": {"claims": {"sub": USER}}},
        "body": json.dumps(body),
    }


def _attr(token, names):
    if token.startswith("#"):
        return names[token]
    return token


def _condition_holds(item, expression, names, values):
    for clause in expression.split(" AND "):
        left, right = clause.split("=", 1)
        if item.get(_attr(left.strip(), names)) != values[right.strip()]:
            return False
    return True


class RequestTable:
    def __init__(self, item=None, race=None):
        self.item = dict(item) if item else None
        self.updates = []
        self.puts = []
        self.race = race

    def get_item(self, Key):
        if not self.item or self.item.get("request_id") != Key.get("request_id"):
            return {}
        seen = dict(self.item)
        if self.race:
            self.item.update(self.race)
            self.race = None
        return {"Item": seen}

    def put_item(self, Item, **kwargs):
        self.puts.append(dict(Item))
        self.item = dict(Item)

    def update_item(
        self,
        Key,
        UpdateExpression,
        ConditionExpression,
        ExpressionAttributeNames=None,
        ExpressionAttributeValues=None,
    ):
        names = ExpressionAttributeNames or {}
        values = ExpressionAttributeValues or {}
        self.updates.append(
            {
                "Key": dict(Key),
                "UpdateExpression": UpdateExpression,
                "ConditionExpression": ConditionExpression,
                "names": dict(names),
                "values": dict(values),
            }
        )
        if not self.item or self.item.get("request_id") != Key.get("request_id") or not _condition_holds(
            self.item, ConditionExpression, names, values
        ):
            raise ClientError(
                {"Error": {"Code": "ConditionalCheckFailedException", "Message": "The conditional request failed"}},
                "UpdateItem",
            )
        expression = UpdateExpression.strip()
        if not expression.startswith("SET "):
            raise AssertionError(expression)
        for assignment in expression[4:].split(","):
            left, right = assignment.split("=", 1)
            self.item[_attr(left.strip(), names)] = values[right.strip()]


class Store:
    def __init__(self, items=None):
        self.items = [dict(item) for item in (items or [])]
        self.puts = []
        self.calls = 0

    def get_item(self, Key):
        self.calls += 1
        for item in self.items:
            if all(item.get(key) == value for key, value in Key.items()):
                return {"Item": dict(item)}
        return {}

    def put_item(self, Item, **kwargs):
        self.calls += 1
        self.puts.append(dict(Item))
        self.items.append(dict(Item))

    def query(self, **kwargs):
        self.calls += 1
        return {"Items": [dict(item) for item in self.items]}

    def update_item(self, **kwargs):
        self.calls += 1
        raise AssertionError("cancellation must not update this table")


class Audit:
    def __init__(self):
        self.events = []

    def put_item(self, Item, **kwargs):
        self.events.append(dict(Item))


def pending_request(**extra):
    item = {
        "request_id": "Q1",
        "ResourceType": "AMBULANCE",
        "Location": "North",
        "location_id": LOCATION_ID,
        "organization_id": ORG,
        "Priority": 2,
        "Status": "PENDING",
        "CreatedAt": "2026-10-07T00:00:00+00:00",
        "created_by": "original-owner",
        "dispatch_note": "keep-me",
        "allocation_id": "ALLOC-Q1",
    }
    item.update(extra)
    return item


def locations():
    return [
        {
            "organization_id": ORG,
            "location_id": LOCATION_ID,
            "name": "North",
            "status": "ACTIVE",
        }
    ]


def wire(monkeypatch, request=None, race=None, role="MEMBER", status="ACTIVE"):
    use_member(monkeypatch, role=role, status=status)
    table = RequestTable(request, race=race)
    audit = Audit()
    resources = Store([{"resource_id": "R1", "Available": True, "organization_id": ORG}])
    allocations = Store(
        [{"allocation_id": "ALLOC-Q1", "request_id": "Q1", "organization_id": ORG, "status": "ALLOCATED"}]
    )
    monkeypatch.setattr(request_handler, "requests_table", lambda: table)
    monkeypatch.setattr(request_handler, "locations_table", lambda: Store(locations()))
    monkeypatch.setattr(request_handler, "audit_table", lambda: audit)
    monkeypatch.setattr(request_handler, "request_types_table", lambda: Store())
    return table, audit, resources, allocations


def cancel(request_id="Q1", **extra):
    body = {"request_id": request_id, "organization_id": ORG}
    body.update(extra)
    return body


def test_cancel_model_is_terminal_and_not_used_by_the_handler():
    assert can_cancel_request("PENDING")
    assert not can_cancel_request("ALLOCATED")
    assert not can_cancel_request("RELEASED")
    assert not can_cancel_request("CANCELLED")
    assert can_allocate_request("PENDING")
    assert not can_allocate_request("CANCELLED")
    source = inspect.getsource(request_handler.cancel_request)
    assert "transitions" not in source
    assert "put_item" not in source
    assert "resources_table" not in source
    assert "allocations_table" not in source


def test_pending_cancellation_sets_only_status(monkeypatch):
    table, audit, resources, allocations = wire(monkeypatch, pending_request())
    before_resources = [dict(item) for item in resources.items]
    before_allocations = [dict(item) for item in allocations.items]

    result = request_handler.lambda_handler(event(cancel()), None)
    parsed = body_of(result)

    assert result["statusCode"] == 200
    assert parsed["message"] == "Request cancelled"
    assert parsed["request"]["Status"] == "CANCELLED"
    assert set(parsed["request"]) <= set(REQUEST_FIELDS)
    assert "created_by" not in parsed["request"]
    assert "dispatch_note" not in parsed["request"]
    assert "allocation_id" not in parsed["request"]
    assert table.item["Status"] == "CANCELLED"
    assert table.item["allocation_id"] == "ALLOC-Q1"
    assert table.item["CreatedAt"] == "2026-10-07T00:00:00+00:00"
    assert table.item["organization_id"] == ORG
    assert table.puts == []
    assert len(table.updates) == 1
    update = table.updates[0]
    assert update["UpdateExpression"] == "SET #status = :cancelled"
    assert update["ConditionExpression"] == "organization_id = :organization_id AND #status = :pending"
    assert update["values"][":organization_id"] == ORG
    assert update["values"][":pending"] == "PENDING"
    assert update["values"][":cancelled"] == "CANCELLED"
    assert resources.items == before_resources
    assert allocations.items == before_allocations
    assert resources.calls == 0
    assert allocations.calls == 0
    assert len(audit.events) == 1
    recorded = audit.events[0]
    assert recorded["action"] == "request.cancel"
    assert recorded["actor_sub"] == USER
    assert recorded["actor_role"] == "MEMBER"
    assert recorded["organization_id"] == ORG
    assert recorded["entity_id"] == "Q1"
    assert recorded["location_id"] == LOCATION_ID


@pytest.mark.parametrize("role", ["MEMBER", "OPERATOR", "ADMIN", "OWNER"])
def test_request_roles_can_cancel(monkeypatch, role):
    table, audit, _resources, _allocations = wire(monkeypatch, pending_request(), role=role)

    result = request_handler.lambda_handler(event(cancel()), None)

    assert result["statusCode"] == 200
    assert table.item["Status"] == "CANCELLED"
    assert audit.events[0]["actor_role"] == role


def test_unknown_role_is_denied(monkeypatch):
    table, audit, _resources, _allocations = wire(monkeypatch, pending_request(), role="VIEWER")

    result = request_handler.lambda_handler(event(cancel()), None)

    assert result["statusCode"] == 403
    assert table.updates == []
    assert audit.events == []
    assert table.item["Status"] == "PENDING"


def test_inactive_membership_is_denied(monkeypatch):
    table, audit, _resources, _allocations = wire(monkeypatch, pending_request(), status="INACTIVE")

    result = request_handler.lambda_handler(event(cancel()), None)

    assert result["statusCode"] == 403
    assert table.updates == []
    assert audit.events == []
    assert table.item["Status"] == "PENDING"


def test_blocked_subscription_is_denied(monkeypatch):
    table, audit, _resources, _allocations = wire(monkeypatch, pending_request())

    class Blocked:
        def get_item(self, Key):
            return {"Item": {"organization_id": Key["organization_id"], "subscription_status": "CANCELLED"}}

    monkeypatch.setattr(access, "subscriptions_table", lambda: Blocked())

    result = request_handler.lambda_handler(event(cancel()), None)
    parsed = body_of(result)

    assert result["statusCode"] == 403
    assert parsed["error"]["code"] == "BILLING_REQUIRED"
    assert table.updates == []
    assert audit.events == []
    assert table.item["Status"] == "PENDING"


def test_missing_request_id_is_rejected(monkeypatch):
    table, audit, _resources, _allocations = wire(monkeypatch, pending_request())

    result = request_handler.lambda_handler(event(cancel(request_id="")), None)

    assert result["statusCode"] == 400
    assert body_of(result)["message"] == "Request ID is required"
    assert table.updates == []
    assert audit.events == []
    assert table.item["Status"] == "PENDING"


def test_missing_request_is_not_found(monkeypatch):
    table, audit, _resources, _allocations = wire(monkeypatch, pending_request())

    result = request_handler.lambda_handler(event(cancel("Q-MISSING")), None)

    assert result["statusCode"] == 404
    assert body_of(result)["message"] == "Record not found"
    assert table.updates == []
    assert audit.events == []
    assert table.item["Status"] == "PENDING"


def test_foreign_request_is_not_found(monkeypatch):
    table, audit, _resources, _allocations = wire(monkeypatch, pending_request(organization_id=OTHER))

    result = request_handler.lambda_handler(event(cancel()), None)

    assert result["statusCode"] == 404
    assert body_of(result)["message"] == "Record not found"
    assert table.updates == []
    assert audit.events == []
    assert table.item["organization_id"] == OTHER
    assert table.item["Status"] == "PENDING"


@pytest.mark.parametrize("status", ["ALLOCATED", "RELEASED", "CANCELLED"])
def test_terminal_request_cannot_be_cancelled(monkeypatch, status):
    table, audit, resources, allocations = wire(monkeypatch, pending_request(Status=status))

    result = request_handler.lambda_handler(event(cancel()), None)

    assert result["statusCode"] == 409
    assert body_of(result)["message"] == "Request is not eligible for cancellation"
    assert table.updates == []
    assert audit.events == []
    assert table.item["Status"] == status
    assert resources.calls == 0
    assert allocations.calls == 0


def test_lost_race_against_allocation_writes_no_audit(monkeypatch):
    table, audit, resources, allocations = wire(
        monkeypatch,
        pending_request(),
        race={"Status": "ALLOCATED"},
    )

    result = request_handler.lambda_handler(event(cancel()), None)

    assert result["statusCode"] == 409
    assert body_of(result)["message"] == "Request is not eligible for cancellation"
    assert table.updates[0]["ConditionExpression"] == "organization_id = :organization_id AND #status = :pending"
    assert table.item["Status"] == "ALLOCATED"
    assert audit.events == []
    assert resources.calls == 0
    assert allocations.items[0]["status"] == "ALLOCATED"


def test_cancel_body_does_not_fall_through_to_create(monkeypatch):
    table, audit, _resources, _allocations = wire(monkeypatch, pending_request())

    result = request_handler.lambda_handler(event({"request_id": "Q1", "organization_id": ORG}), None)

    assert result["statusCode"] == 200
    assert table.puts == []
    assert table.item["Status"] == "CANCELLED"
    assert audit.events[0]["action"] == "request.cancel"


def test_post_requests_still_creates_pending(monkeypatch):
    table, audit, _resources, _allocations = wire(monkeypatch)

    result = request_handler.lambda_handler(
        event(
            {
                "request_id": "Q-NEW",
                "resource_type": "Ambulance",
                "location_id": LOCATION_ID,
                "priority": 2,
                "organization_id": ORG,
            },
            path="/requests",
        ),
        None,
    )
    parsed = body_of(result)

    assert result["statusCode"] == 201
    assert parsed["request"]["Status"] == "PENDING"
    assert parsed["request"]["request_id"] == "Q-NEW"
    assert table.puts[0]["Status"] == "PENDING"
    assert table.updates == []
    assert audit.events[0]["action"] == "request.create"


def test_put_requests_still_updates_pending_fields(monkeypatch):
    table, audit, _resources, _allocations = wire(monkeypatch, pending_request())

    result = request_handler.lambda_handler(
        event(
            {
                "request_id": "Q1",
                "resource_type": "Generator",
                "location_id": LOCATION_ID,
                "priority": 4,
                "organization_id": ORG,
            },
            method="PUT",
            path="/requests",
        ),
        None,
    )
    parsed = body_of(result)

    assert result["statusCode"] == 200
    assert parsed["request"]["Status"] == "PENDING"
    assert parsed["request"]["ResourceType"] == "GENERATOR"
    assert parsed["request"]["Priority"] == 4
    assert table.item["Status"] == "PENDING"
    assert "Status" not in table.updates[0]["UpdateExpression"]
    assert audit.events[0]["action"] == "request.update"


def test_allocate_rejects_a_cancelled_request(monkeypatch):
    use_member(monkeypatch, role="OPERATOR")
    requests = Store([pending_request(Status="CANCELLED")])
    resources = Store(
        [
            {
                "resource_id": "R1",
                "organization_id": ORG,
                "location_id": LOCATION_ID,
                "Type": "AMBULANCE",
                "Available": True,
                "operational_status": "AVAILABLE",
                "tracking_mode": "INDIVIDUAL",
            }
        ]
    )
    allocations = Store()
    monkeypatch.setattr(allocation, "locations_table", lambda: Store(locations()))
    monkeypatch.setattr(allocation, "requests_table", lambda: requests)
    monkeypatch.setattr(allocation, "resources_table", lambda: resources)
    monkeypatch.setattr(allocation, "allocations_table", lambda: allocations)
    monkeypatch.setattr(allocation, "history_table", lambda: Store())
    monkeypatch.setattr(allocation, "audit_table", lambda: Audit())
    monkeypatch.setattr(allocation, "request_types_table", lambda: Store())
    monkeypatch.setattr(allocation, "_transact_write", lambda items: (_ for _ in ()).throw(AssertionError(items)))

    result = allocation.lambda_handler(
        event(
            {
                "request_id": "Q1",
                "resource_type": "AMBULANCE",
                "location_id": LOCATION_ID,
                "priority": 1,
                "organization_id": ORG,
            },
            path="/allocate",
        ),
        None,
    )

    assert result["statusCode"] == 409
    assert body_of(result)["message"] == "Request is not eligible for allocation"
    assert requests.items[0]["Status"] == "CANCELLED"
    assert resources.items[0]["Available"] is True
    assert allocations.items == []
