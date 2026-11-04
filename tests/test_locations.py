"""Location API authorization. These tests do not call AWS."""

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

from access import AccessError


def load_module():
    path = ROOT / "src" / "locations" / "handler.py"
    spec = importlib.util.spec_from_file_location("locations_handler", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LocationStore:
    def __init__(self):
        self.items = []

    def put_item(self, Item, ConditionExpression=None):
        self.items.append(dict(Item))


def event(method="GET", body=None, origin="http://localhost:8080"):
    payload = {
        "httpMethod": method,
        "headers": {"origin": origin},
        "queryStringParameters": {"organization_id": "ORG-1"},
        "requestContext": {
            "authorizer": {"claims": {"sub": "user-1"}},
        },
    }
    if body is not None:
        payload["body"] = json.dumps(body)
    return payload


def test_empty_location_list(monkeypatch):
    handler = load_module()
    monkeypatch.setattr(
        handler,
        "authorize",
        lambda *args, **kwargs: (
            "user-1",
            {"organization_id": "ORG-1", "role": "OWNER"},
        ),
    )
    monkeypatch.setattr(handler, "query_by_organization", lambda *args, **kwargs: [])
    monkeypatch.setattr(handler, "locations_table", lambda: LocationStore())

    result = handler.lambda_handler(event("GET"), None)
    body = json.loads(result["body"])

    assert result["statusCode"] == 200
    assert body["locations"] == []
    assert result["headers"]["Access-Control-Allow-Origin"] == "http://localhost:8080"


def test_owner_can_create_location(monkeypatch):
    handler = load_module()
    store = LocationStore()
    monkeypatch.setattr(
        handler,
        "authorize",
        lambda *args, **kwargs: (
            "user-1",
            {"organization_id": "ORG-1", "role": "OWNER"},
        ),
    )
    monkeypatch.setattr(handler, "locations_table", lambda: store)
    monkeypatch.setattr(handler, "audit_table", lambda: None)
    monkeypatch.setattr(handler, "record_audit", lambda *args, **kwargs: None)

    result = handler.lambda_handler(
        event("POST", {"name": "Main Warehouse", "city": "Hyderabad"}),
        None,
    )
    body = json.loads(result["body"])

    assert result["statusCode"] == 201
    assert store.items[0]["organization_id"] == "ORG-1"
    assert store.items[0]["name"] == "Main Warehouse"
    assert body["location"]["name"] == "Main Warehouse"


def test_member_cannot_create_location(monkeypatch):
    handler = load_module()

    def deny(*args, **kwargs):
        raise AccessError(403, "You are not allowed to perform this action")

    monkeypatch.setattr(handler, "authorize", deny)
    monkeypatch.setattr(handler, "locations_table", lambda: LocationStore())

    result = handler.lambda_handler(
        event("POST", {"name": "Main Warehouse"}),
        None,
    )
    body = json.loads(result["body"])

    assert result["statusCode"] == 403
    assert "not allowed" in body["message"].lower()


def test_member_role_is_rejected_for_location_writes(monkeypatch):
    import access

    monkeypatch.setattr(
        access,
        "list_memberships",
        lambda *args, **kwargs: [
            {
                "organization_id": "ORG-1",
                "role": "MEMBER",
                "status": "ACTIVE",
                "name": "City",
            }
        ],
    )

    with pytest.raises(AccessError) as error:
        access.authorize(
            {
                "httpMethod": "POST",
                "queryStringParameters": {"organization_id": "ORG-1"},
                "requestContext": {
                    "authorizer": {"claims": {"sub": "member-1"}}
                },
            },
            {},
            allowed_roles=access.LOCATION_WRITE_ROLES,
        )

    assert error.value.status_code == 403
