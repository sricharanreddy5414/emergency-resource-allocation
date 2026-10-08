"""P9-01: a request update cannot replace an allocated row with a stale pending copy."""

import importlib.util
import inspect
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


def load_module(name, relative):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


request_handler = load_module("request_handler_p9a", "src/request/handler.py")

ORG = "ORG-A"
OTHER = "ORG-B"
USER = "user-p9a"
LOCATION_ID = "LOC-A"
OTHER_LOCATION = "LOC-B"


def event(body, method="PUT"):
    return {
        "httpMethod": method,
        "path": "/requests",
        "requestContext": {"authorizer": {"claims": {"sub": USER}}},
        "body": json.dumps(body),
    }


def body_of(result):
    return json.loads(result["body"])


def use_member(monkeypatch, role="OWNER"):
    monkeypatch.setattr(
        access,
        "list_memberships",
        lambda *args, **kwargs: [
            {
                "organization_id": ORG,
                "name": ORG,
                "role": role,
                "status": "ACTIVE",
            }
        ],
    )


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
    def __init__(self, item, race=None):
        self.item = dict(item)
        self.updates = []
        self.puts = []
        self.race = race

    def get_item(self, Key):
        if self.item.get("request_id") != Key.get("request_id"):
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
        if self.item.get("request_id") != Key.get("request_id") or not _condition_holds(
            self.item, ConditionExpression, names, values
        ):
            raise ClientError(
                {
                    "Error": {
                        "Code": "ConditionalCheckFailedException",
                        "Message": "The conditional request failed",
                    }
                },
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

    def get_item(self, Key):
        for item in self.items:
            if all(item.get(key) == value for key, value in Key.items()):
                return {"Item": dict(item)}
        return {}

    def put_item(self, Item, **kwargs):
        self.puts.append(dict(Item))


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
        },
        {
            "organization_id": ORG,
            "location_id": OTHER_LOCATION,
            "name": "South",
            "status": "ACTIVE",
        },
    ]


def wire(monkeypatch, request, race=None):
    use_member(monkeypatch)
    table = RequestTable(request, race=race)
    monkeypatch.setattr(request_handler, "requests_table", lambda: table)
    monkeypatch.setattr(request_handler, "locations_table", lambda: Store(locations()))
    monkeypatch.setattr(request_handler, "audit_table", lambda: Store())
    monkeypatch.setattr(request_handler, "request_types_table", lambda: Store())
    return table


def payload(**extra):
    body = {
        "request_id": "Q1",
        "resource_type": "Generator",
        "location_id": OTHER_LOCATION,
        "priority": 4,
        "organization_id": ORG,
    }
    body.update(extra)
    return body


def test_update_pending_request_succeeds(monkeypatch):
    table = wire(monkeypatch, pending_request())

    result = request_handler.lambda_handler(event(payload()), None)
    parsed = body_of(result)

    assert result["statusCode"] == 200
    assert parsed["message"] == "Request updated"
    assert parsed["request"]["Status"] == "PENDING"
    assert parsed["request"]["ResourceType"] == "GENERATOR"
    assert parsed["request"]["location_id"] == OTHER_LOCATION
    assert parsed["request"]["Priority"] == 4
    assert table.puts == []
    assert len(table.updates) == 1
    assert table.item["Status"] == "PENDING"
    assert "put_item" not in inspect.getsource(request_handler.update_request)


def test_update_allocated_request_rejected(monkeypatch):
    table = wire(monkeypatch, pending_request(Status="ALLOCATED"))

    result = request_handler.lambda_handler(event(payload()), None)

    assert result["statusCode"] == 409
    assert body_of(result)["message"] == "Request is not eligible for update"
    assert table.updates == []
    assert table.puts == []
    assert table.item["Status"] == "ALLOCATED"
    assert table.item["ResourceType"] == "AMBULANCE"
    assert "ConditionalCheckFailed" not in result["body"]


def test_update_released_request_rejected(monkeypatch):
    table = wire(monkeypatch, pending_request(Status="RELEASED"))

    result = request_handler.lambda_handler(event(payload()), None)

    assert result["statusCode"] == 409
    assert body_of(result)["message"] == "Request is not eligible for update"
    assert table.updates == []
    assert table.item["Status"] == "RELEASED"
    assert table.item["ResourceType"] == "AMBULANCE"
    assert table.item["dispatch_note"] == "keep-me"


def test_concurrent_allocation_cannot_be_overwritten_by_update(monkeypatch):
    table = wire(monkeypatch, pending_request(), race={"Status": "ALLOCATED"})

    result = request_handler.lambda_handler(event(payload()), None)

    assert result["statusCode"] == 409
    assert body_of(result)["message"] == "Request is not eligible for update"
    assert len(table.updates) == 1
    assert table.puts == []
    assert table.item["Status"] == "ALLOCATED"
    assert table.item["ResourceType"] == "AMBULANCE"
    assert table.item["Location"] == "North"
    assert table.item["location_id"] == LOCATION_ID
    assert table.item["Priority"] == 2
    assert table.item["CreatedAt"] == "2026-10-07T00:00:00+00:00"
    assert table.item["created_by"] == "original-owner"
    assert table.item["dispatch_note"] == "keep-me"
    assert "PENDING" not in result["body"] or body_of(result).get("request") is None


def test_update_changes_only_mutable_fields(monkeypatch):
    original = pending_request(
        request_type_id="RQ-OLD",
        attributes={"quantity": 1},
        matching_config={"compatible_resource_type_ids": ["RT-OLD"]},
    )
    table = wire(monkeypatch, original)

    result = request_handler.lambda_handler(event(payload()), None)

    assert result["statusCode"] == 200
    saved = table.item
    assert saved["ResourceType"] == "GENERATOR"
    assert saved["Location"] == "South"
    assert saved["location_id"] == OTHER_LOCATION
    assert saved["Priority"] == 4
    assert saved["request_id"] == "Q1"
    assert saved["organization_id"] == ORG
    assert saved["Status"] == "PENDING"
    assert saved["CreatedAt"] == original["CreatedAt"]
    assert saved["created_by"] == "original-owner"
    assert saved["dispatch_note"] == "keep-me"
    assert saved["request_type_id"] == "RQ-OLD"
    assert saved["attributes"] == {"quantity": 1}
    assert saved["matching_config"]["compatible_resource_type_ids"] == ["RT-OLD"]
    expression = table.updates[0]["UpdateExpression"]
    assert "organization_id" not in expression
    assert "CreatedAt" not in expression
    assert "Status" not in expression.replace("#status", "")
    assert table.updates[0]["ConditionExpression"] == (
        "organization_id = :organization_id AND #status = :pending"
    )


def test_typed_update_replaces_only_request_type_fields(monkeypatch):
    table = wire(
        monkeypatch,
        pending_request(created_by="original-owner", dispatch_note="keep-me"),
    )
    monkeypatch.setattr(
        request_handler,
        "request_types_table",
        lambda: Store(
            [
                {
                    "organization_id": ORG,
                    "request_type_id": "RQ-NEW",
                    "name": "Power",
                    "status": "ACTIVE",
                    "attributes_schema": {"fields": []},
                    "matching_config": {"compatible_resource_type_ids": ["RT-NEW"]},
                }
            ]
        ),
    )

    result = request_handler.lambda_handler(
        event(payload(request_type_id="RQ-NEW", attributes={})),
        None,
    )

    assert result["statusCode"] == 200
    assert table.item["request_type_id"] == "RQ-NEW"
    assert table.item["ResourceType"] == "Power"
    assert table.item["attributes"] == {}
    assert table.item["matching_config"]["compatible_resource_type_ids"] == ["RT-NEW"]
    assert table.item["created_by"] == "original-owner"
    assert table.item["dispatch_note"] == "keep-me"
    assert table.item["Status"] == "PENDING"
    assert table.puts == []


def test_update_organization_condition_rejects_a_moved_request(monkeypatch):
    table = wire(monkeypatch, pending_request(), race={"organization_id": OTHER})

    result = request_handler.lambda_handler(event(payload()), None)

    assert result["statusCode"] == 409
    assert body_of(result)["message"] == "Request is not eligible for update"
    assert table.item["organization_id"] == OTHER
    assert table.item["Status"] == "PENDING"
    assert table.item["ResourceType"] == "AMBULANCE"
    assert table.item["dispatch_note"] == "keep-me"
    assert table.puts == []


def test_update_for_another_organization_is_not_found(monkeypatch):
    table = wire(monkeypatch, pending_request(organization_id=OTHER))

    result = request_handler.lambda_handler(event(payload()), None)

    assert result["statusCode"] == 404
    assert body_of(result)["message"] == "Record not found"
    assert table.updates == []
    assert table.puts == []
    assert table.item["organization_id"] == OTHER
    assert table.item["ResourceType"] == "AMBULANCE"
