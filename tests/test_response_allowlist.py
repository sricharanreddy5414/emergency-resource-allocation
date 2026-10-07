"""Emergency request and allocation responses expose an explicit field allowlist."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [
    str(ROOT / "src" / "shared"),
    str(ROOT / "src" / "organization"),
]

import access
from api_views import ALLOCATION_FIELDS, REQUEST_FIELDS, allocation_view, request_view


def load_module(name, relative):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


request_handler = load_module("request_handler_allowlist", "src/request/handler.py")
allocation_service = load_module("allocation_service_allowlist", "src/allocation/service.py")

ORG_A = "ORG-A"
ORG_B = "ORG-B"
USER = "user-allowlist"
INTERNAL = {
    "pk": "REQUEST#Q1",
    "sk": "META",
    "created_by": "cognito-sub-should-not-leak",
    "user_sub": "cognito-sub-should-not-leak",
    "actor_sub": "cognito-sub-should-not-leak",
    "actor_role": "OWNER",
    "idempotency_key": "idem-1",
    "organization_location_key": "ORG-A#LOC-A",
    "future_internal_field": "not-approved",
}


def event(method="GET", body=None, organization_id=ORG_A, path="/allocate/requests"):
    payload = {
        "httpMethod": method,
        "path": path,
        "queryStringParameters": {"organization_id": organization_id},
        "requestContext": {"authorizer": {"claims": {"sub": USER}}},
    }
    if body is not None:
        payload["body"] = json.dumps(body)
    return payload


def body_of(result):
    return json.loads(result["body"])


def use_member(monkeypatch, role="MEMBER"):
    monkeypatch.setattr(
        access,
        "list_memberships",
        lambda *args, **kwargs: [
            {
                "organization_id": ORG_A,
                "name": ORG_A,
                "role": role,
                "status": "ACTIVE",
            }
        ],
    )


class OrgTable:
    def __init__(self, items=None):
        self.items = [dict(item) for item in (items or [])]
        self.puts = []

    def query(self, **kwargs):
        pairs = _pairs(kwargs["KeyConditionExpression"])
        matched = [
            item
            for item in self.items
            if all(item.get(name) == value for name, value in pairs)
        ]
        return {"Items": matched}

    def get_item(self, Key):
        for item in self.items:
            if all(item.get(key) == value for key, value in Key.items()):
                return {"Item": dict(item)}
        return {}

    def put_item(self, Item, ConditionExpression=None, **kwargs):
        self.puts.append(dict(Item))
        self.items = [
            item for item in self.items if item.get("request_id") != Item.get("request_id")
        ]
        self.items.append(dict(Item))


def _pairs(expression):
    data = expression.get_expression()
    if data.get("operator") == "AND":
        pairs = []
        for value in data["values"]:
            pairs.extend(_pairs(value))
        return pairs
    key, value = data["values"]
    return [(key.name, value)]


def _request(**extra):
    item = {
        "request_id": "Q1",
        "ResourceType": "Ambulance",
        "Location": "North",
        "location_id": "LOC-A",
        "organization_id": ORG_A,
        "Priority": 1,
        "Status": "PENDING",
        "CreatedAt": "2026-10-07T00:00:00+00:00",
        "request_type_id": "RQ-1",
        "attributes": {"quantity": 2},
        "matching_config": {"compatible_resource_type_ids": ["RT-1"]},
    }
    item.update(extra)
    return item


def _allocation(**extra):
    item = {
        "allocation_id": "ALLOC-Q1",
        "request_id": "Q1",
        "resource_id": "R1",
        "resource_type": "Ambulance",
        "location": "North",
        "location_id": "LOC-A",
        "organization_id": ORG_A,
        "priority": 1,
        "status": "ALLOCATED",
        "allocated_at": "2026-10-07T01:00:00+00:00",
    }
    item.update(extra)
    return item


def test_request_and_allocation_views_drop_unapproved_fields():
    request = request_view(_request(**INTERNAL))
    allocation = allocation_view(
        _allocation(
            **INTERNAL,
            allocation_type="EVERYDAY",
            quantity=3,
            purpose="Clinic",
            released_at="2026-10-07T02:00:00+00:00",
            created_at="2026-10-07T01:00:00+00:00",
            updated_at="2026-10-07T02:00:00+00:00",
        )
    )

    assert set(request) <= set(REQUEST_FIELDS)
    assert set(allocation) <= set(ALLOCATION_FIELDS)
    for leaked in INTERNAL:
        assert leaked not in request
        assert leaked not in allocation
    assert "cognito-sub-should-not-leak" not in json.dumps(request)
    assert "cognito-sub-should-not-leak" not in json.dumps(allocation)
    assert request["request_id"] == "Q1"
    assert request["ResourceType"] == "Ambulance"
    assert request["Priority"] == 1
    assert request["Status"] == "PENDING"
    assert request["CreatedAt"] == "2026-10-07T00:00:00+00:00"
    assert request["matching_config"]["compatible_resource_type_ids"] == ["RT-1"]
    assert allocation["allocation_id"] == "ALLOC-Q1"
    assert allocation["resource_id"] == "R1"
    assert allocation["status"] == "ALLOCATED"
    assert allocation["quantity"] == 3
    assert allocation["purpose"] == "Clinic"


def test_optional_request_fields_stay_absent():
    viewed = request_view({"request_id": "Q2", "Status": "PENDING", "organization_id": ORG_A})
    assert viewed == {"request_id": "Q2", "Status": "PENDING", "organization_id": ORG_A}
    assert "matching_config" not in viewed
    assert "attributes" not in viewed
    assert "CreatedAt" not in viewed


def test_request_list_returns_only_approved_fields(monkeypatch):
    use_member(monkeypatch)
    foreign = _request(request_id="Q-OTHER", organization_id=ORG_B, **INTERNAL)
    table = OrgTable([_request(**INTERNAL), foreign])
    monkeypatch.setattr(allocation_service, "requests_table", lambda: table)
    monkeypatch.setattr(allocation_service, "allocations_table", lambda: OrgTable())

    result = allocation_service.lambda_handler(event(), None)
    body = body_of(result)

    assert result["statusCode"] == 200
    assert body["count"] == 1
    assert body["requests"] == [request_view(_request())]
    assert "Q-OTHER" not in result["body"]
    assert "future_internal_field" not in result["body"]
    assert "cognito-sub-should-not-leak" not in result["body"]


def test_allocation_list_returns_only_approved_fields(monkeypatch):
    use_member(monkeypatch)
    active = _allocation(**INTERNAL)
    released = _allocation(
        allocation_id="ALLOC-REL",
        status="RELEASED",
        released_at="2026-10-07T03:00:00+00:00",
        **INTERNAL,
    )
    returned = _allocation(
        allocation_id="EVERYDAY-R1",
        allocation_type="EVERYDAY",
        status="RETURNED",
        quantity=1,
        purpose="Ward",
        created_at="2026-10-07T01:00:00+00:00",
        updated_at="2026-10-07T04:00:00+00:00",
        **INTERNAL,
    )
    foreign = _allocation(allocation_id="ALLOC-OTHER", organization_id=ORG_B, **INTERNAL)
    table = OrgTable([active, released, returned, foreign])
    monkeypatch.setattr(allocation_service, "allocations_table", lambda: table)
    monkeypatch.setattr(allocation_service, "requests_table", lambda: OrgTable())

    result = allocation_service.lambda_handler(
        event(path="/allocate/allocations"),
        None,
    )
    body = body_of(result)
    by_id = {item["allocation_id"]: item for item in body["allocations"]}

    assert result["statusCode"] == 200
    assert body["count"] == 3
    assert set(by_id) == {"ALLOC-Q1", "ALLOC-REL", "EVERYDAY-R1"}
    assert by_id["ALLOC-REL"]["status"] == "RELEASED"
    assert by_id["ALLOC-REL"]["released_at"] == "2026-10-07T03:00:00+00:00"
    assert by_id["EVERYDAY-R1"]["status"] == "RETURNED"
    assert by_id["EVERYDAY-R1"]["purpose"] == "Ward"
    assert "created_by" not in by_id["EVERYDAY-R1"]
    assert "ALLOC-OTHER" not in result["body"]
    assert "pk" not in json.dumps(body["allocations"])
    for item in body["allocations"]:
        assert set(item) <= set(ALLOCATION_FIELDS)


def test_create_and_update_request_responses_are_allowlisted(monkeypatch):
    use_member(monkeypatch)
    locations = OrgTable(
        [
            {
                "organization_id": ORG_A,
                "location_id": "LOC-A",
                "name": "North",
                "status": "ACTIVE",
            }
        ]
    )
    stored = _request(**INTERNAL)
    requests = OrgTable([stored])
    monkeypatch.setattr(request_handler, "locations_table", lambda: locations)
    monkeypatch.setattr(request_handler, "requests_table", lambda: requests)
    monkeypatch.setattr(request_handler, "audit_table", lambda: OrgTable())

    created = request_handler.lambda_handler(
        event(
            "POST",
            {
                "request_id": "Q-NEW",
                "resource_type": "Ambulance",
                "location_id": "LOC-A",
                "priority": 2,
                "organization_id": ORG_A,
                "created_by": "forged-sub",
                "pk": "REQUEST#Q-NEW",
            },
            path="/requests",
        ),
        None,
    )
    created_body = body_of(created)
    assert created["statusCode"] == 201
    assert set(created_body["request"]) <= set(REQUEST_FIELDS)
    assert created_body["request"]["request_id"] == "Q-NEW"
    assert created_body["request"]["ResourceType"] == "AMBULANCE"
    assert created_body["request"]["Status"] == "PENDING"
    assert created_body["request"]["location_id"] == "LOC-A"
    assert "forged-sub" not in created["body"]
    assert "matching_config" not in created_body["request"]

    updated = request_handler.lambda_handler(
        event(
            "PUT",
            {
                "request_id": "Q1",
                "resource_type": "Ambulance",
                "location_id": "LOC-A",
                "priority": 4,
                "organization_id": ORG_A,
            },
            path="/requests",
        ),
        None,
    )
    updated_body = body_of(updated)
    assert updated["statusCode"] == 200
    assert updated_body["request"]["Priority"] == 4
    assert updated_body["request"]["request_type_id"] == "RQ-1"
    assert set(updated_body["request"]) <= set(REQUEST_FIELDS)
    assert "future_internal_field" not in updated["body"]
    assert "cognito-sub-should-not-leak" not in updated["body"]


def test_cross_tenant_request_read_stays_denied(monkeypatch):
    use_member(monkeypatch, role="OPERATOR")
    requests = OrgTable(
        [
            {
                "request_id": "Q-OTHER",
                "organization_id": ORG_B,
                "location_id": "LOC-B",
                "Status": "PENDING",
                "created_by": "other-sub",
            }
        ]
    )
    monkeypatch.setattr(allocation_service, "requests_table", lambda: requests)
    monkeypatch.setattr(allocation_service, "locations_table", lambda: OrgTable([
        {"organization_id": ORG_A, "location_id": "LOC-A", "name": "North", "status": "ACTIVE"}
    ]))
    monkeypatch.setattr(allocation_service, "resources_table", lambda: OrgTable())

    listed = allocation_service.lambda_handler(event(), None)
    assert listed["statusCode"] == 200
    assert body_of(listed)["requests"] == []

    denied = allocation_service.lambda_handler(
        event(
            "POST",
            {
                "request_id": "Q-OTHER",
                "resource_type": "Ambulance",
                "location_id": "LOC-A",
                "priority": 1,
                "organization_id": ORG_A,
            },
            path="/allocate",
        ),
        None,
    )
    assert denied["statusCode"] == 404
    assert "other-sub" not in denied["body"]


def test_allocate_success_response_is_already_explicit():
    text = (ROOT / "src" / "allocation" / "service.py").read_text(encoding="utf-8")
    start = text.index('"message": "Resource allocated successfully"')
    window = text[start:start + 500]
    assert "created_by" not in window
    assert "dict(allocation)" not in window
    assert '"allocation_id": allocation_id' in window


@pytest.mark.parametrize("field", ["pk", "sk", "created_by", "idempotency_key", "future_internal_field"])
def test_new_stored_field_is_omitted_until_added_to_the_allowlist(field):
    assert field not in REQUEST_FIELDS
    assert field not in ALLOCATION_FIELDS
    request = request_view(_request(**{field: "leak"}))
    allocation = allocation_view(_allocation(**{field: "leak"}))
    assert field not in request
    assert field not in allocation
    assert "leak" not in json.dumps(request)
    assert "leak" not in json.dumps(allocation)
