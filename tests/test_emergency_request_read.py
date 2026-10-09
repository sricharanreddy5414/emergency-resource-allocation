"""Membership-scoped read of one emergency request."""

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [
    str(ROOT / "src" / "shared"),
    str(ROOT / "src" / "organization"),
]

import access
from api_views import REQUEST_FIELDS, request_view


def load_module(name, relative):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


allocation = load_module("allocation_service_request_read", "src/allocation/service.py")

ORG = "ORG-A"
OTHER = "ORG-B"
USER = "user-request-read"


def body_of(result):
    return json.loads(result["body"])


class RequestTable:
    def __init__(self, items):
        self.items = {item["request_id"]: dict(item) for item in items}
        self.gets = []
        self.queries = []

    def get_item(self, Key):
        self.gets.append(dict(Key))
        item = self.items.get(Key["request_id"])
        return {"Item": dict(item)} if item else {}

    def query(self, **kwargs):
        self.queries.append(kwargs)
        return {"Items": [dict(item) for item in self.items.values() if item["organization_id"] == ORG]}


def use_member(monkeypatch, role="MEMBER", status="ACTIVE", organization_id=ORG):
    monkeypatch.setattr(
        access,
        "list_memberships",
        lambda *args, **kwargs: [
            {
                "organization_id": organization_id,
                "name": organization_id,
                "role": role,
                "status": status,
            }
        ]
        if role
        else [],
    )


def event(path, method="GET", organization_id=ORG, subject=USER, body=None):
    payload = {
        "httpMethod": method,
        "path": path,
        "queryStringParameters": {"organization_id": organization_id} if organization_id else None,
        "requestContext": {"authorizer": {"claims": {"sub": subject}}} if subject else {"authorizer": {}},
    }
    if body is not None:
        payload["body"] = json.dumps(body)
    return payload


def owned_request():
    return {
        "request_id": "Q-A",
        "organization_id": ORG,
        "location_id": "LOC-A",
        "ResourceType": "ICU_BED",
        "Location": "North",
        "Priority": 2,
        "Status": "PENDING",
        "CreatedAt": "2026-10-09T00:00:00+00:00",
        "created_by": "secret-sub",
        "pk": "REQUEST#Q-A",
    }


def bind(monkeypatch, rows):
    table = RequestTable(rows)
    monkeypatch.setattr(allocation, "requests_table", lambda: table)
    return table


def test_member_reads_one_request_in_the_organization(monkeypatch):
    use_member(monkeypatch)
    table = bind(monkeypatch, [owned_request()])
    result = allocation.lambda_handler(event("/requests/Q-A"), None)
    payload = body_of(result)
    assert result["statusCode"] == 200
    assert payload["message"] == "Request retrieved successfully"
    assert payload["request"] == request_view(owned_request())
    assert set(payload["request"]) <= set(REQUEST_FIELDS)
    assert "secret-sub" not in result["body"]
    assert "pk" not in payload["request"]
    assert table.gets == [{"request_id": "Q-A"}]
    assert table.queries == []


def test_missing_request_is_not_found(monkeypatch):
    use_member(monkeypatch)
    table = bind(monkeypatch, [])
    result = allocation.lambda_handler(event("/requests/Q-MISSING"), None)
    assert result["statusCode"] == 404
    assert body_of(result)["message"] == "Record not found"
    assert table.queries == []


def test_other_organization_request_is_not_found(monkeypatch):
    use_member(monkeypatch)
    table = bind(
        monkeypatch,
        [
            {
                "request_id": "Q-OTHER",
                "organization_id": OTHER,
                "Status": "PENDING",
                "Location": "Foreign ward",
                "created_by": "foreign-sub",
            }
        ],
    )
    result = allocation.lambda_handler(event("/requests/Q-OTHER"), None)
    assert result["statusCode"] == 404
    assert body_of(result)["message"] == "Record not found"
    assert "Foreign ward" not in result["body"]
    assert "foreign-sub" not in result["body"]
    assert table.queries == []


def test_invalid_request_id_does_not_read_storage(monkeypatch):
    use_member(monkeypatch)
    table = bind(monkeypatch, [owned_request()])
    result = allocation.lambda_handler(event("/requests/Q A"), None)
    assert result["statusCode"] == 400
    assert body_of(result)["message"] == "Request ID is invalid"
    assert table.gets == []
    assert table.queries == []


def test_missing_membership_and_unknown_role_are_denied(monkeypatch):
    table = bind(monkeypatch, [owned_request()])
    anonymous = allocation.lambda_handler(event("/requests/Q-A", subject=""), None)
    assert anonymous["statusCode"] == 401
    use_member(monkeypatch, role=None)
    missing = allocation.lambda_handler(event("/requests/Q-A"), None)
    assert missing["statusCode"] == 403
    assert body_of(missing)["message"] == "Organization membership is required"
    use_member(monkeypatch, role="AUDITOR")
    denied = allocation.lambda_handler(event("/requests/Q-A"), None)
    assert denied["statusCode"] == 403
    assert body_of(denied)["message"] == "You are not allowed to perform this action"
    use_member(monkeypatch, status="INACTIVE")
    inactive = allocation.lambda_handler(event("/requests/Q-A"), None)
    assert inactive["statusCode"] == 403
    assert table.gets == []


def test_requested_organization_must_be_a_membership(monkeypatch):
    use_member(monkeypatch)
    table = bind(monkeypatch, [owned_request()])
    result = allocation.lambda_handler(event("/requests/Q-A", organization_id=OTHER), None)
    assert result["statusCode"] == 403
    assert body_of(result)["message"] == "Organization access denied"
    assert table.gets == []


def test_list_and_allocate_paths_stay_separate(monkeypatch):
    use_member(monkeypatch, role="OPERATOR")
    table = bind(monkeypatch, [owned_request()])
    listed = allocation.lambda_handler(event("/requests"), None)
    listed_body = body_of(listed)
    assert listed["statusCode"] == 200
    assert listed_body["message"] == "Requests retrieved successfully"
    assert listed_body["requests"][0]["request_id"] == "Q-A"
    assert table.gets == []
    assert table.queries

    posted = allocation.lambda_handler(
        event("/requests/Q-A", method="POST", body={}),
        None,
    )
    assert posted["statusCode"] == 400
    assert "Request retrieved successfully" not in posted["body"]

    exchange = allocation.lambda_handler(event("/exchange/requests/EXREQ-1"), None)
    assert exchange["statusCode"] == 405
