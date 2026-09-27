import importlib.util
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
import attributes
import matching
import pages
from access import AccessError


def load_module(name, relative):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


catalog = load_module("catalog_phase5", "src/catalog/handler.py")
public_api = load_module("public_phase5", "src/public/handler.py")
resource_handler = load_module("resource_phase5", "src/resource/handler.py")
request_handler = load_module("request_phase5", "src/request/handler.py")

ORG_A = "ORG-A"
ORG_B = "ORG-B"
USER = "user-a"
TYPE_ID = "RT-ABCDEF123456"
REQUEST_TYPE_ID = "RQ-ABCDEF123456"


def event(method="GET", body=None, organization_id=ORG_A, path="/resource-types", params=None, query=None, subject=USER):
    payload = {
        "httpMethod": method,
        "path": path,
        "pathParameters": params or {},
        "queryStringParameters": {"organization_id": organization_id, **(query or {})},
        "requestContext": {"authorizer": {"claims": {"sub": subject}}},
    }

    if body is not None:
        payload["body"] = json.dumps(body)

    return payload


def memberships(*pairs):
    return [
        {"organization_id": organization_id, "name": organization_id, "role": role, "status": "ACTIVE"}
        for organization_id, role in pairs
    ]


def use_memberships(monkeypatch, records):
    monkeypatch.setattr(access, "list_memberships", lambda *args, **kwargs: records)


class Store:
    def __init__(self, items=None):
        self.items = list(items or [])
        self.puts = []
        self.queries = []

    def query(self, **kwargs):
        self.queries.append(kwargs)
        return {"Items": list(self.items), "LastEvaluatedKey": kwargs.get("_last")}

    def get_item(self, Key):
        for item in self.items:
            if all(item.get(key) == value for key, value in Key.items()):
                return {"Item": dict(item)}

        return {}

    def put_item(self, Item, **kwargs):
        self.puts.append(Item)
        self.items.append(Item)

    def update_item(self, **kwargs):
        return {}

    def scan(self, **kwargs):
        raise AssertionError("scan")


def body_of(result):
    return json.loads(result["body"])


def schema():
    return {"fields": [{"key": "capacity", "type": "number", "required": True, "minimum": 1, "label": "Capacity"}]}


def test_attribute_limits():
    assert attributes.validate_attributes({"capacity": 4}, schema()) == {"capacity": 4}

    with pytest.raises(AccessError):
        attributes.validate_attributes({"capacity": 0}, schema())

    with pytest.raises(AccessError):
        attributes.validate_attributes({"unexpected": "x"}, schema())

    with pytest.raises(AccessError):
        attributes.validate_schema({"fields": [{"key": "Bad", "type": "string"}]})


def test_resource_type_crud_and_roles(monkeypatch):
    use_memberships(monkeypatch, memberships((ORG_A, "ADMIN")))
    table = Store()
    monkeypatch.setattr(catalog, "table_for", lambda kind: table)
    monkeypatch.setattr(catalog, "audit_table", lambda: Store())
    created = catalog.lambda_handler(
        event(
            "POST",
            {"name": "Ambulance", "attributes_schema": schema(), "organization_id": ORG_B, "role": "OWNER"},
            path="/resource-types",
        ),
        None,
    )
    created_body = body_of(created)

    assert created["statusCode"] == 201
    assert created_body["type"]["organization_id"] == ORG_A
    assert created_body["type"]["name"] == "Ambulance"
    assert table.puts[0]["created_by"] == USER

    use_memberships(monkeypatch, memberships((ORG_A, "OPERATOR")))
    denied = catalog.lambda_handler(event("POST", {"name": "Blocked"}, path="/resource-types"), None)

    assert denied["statusCode"] == 403


def test_cross_tenant_type_is_hidden(monkeypatch):
    use_memberships(monkeypatch, memberships((ORG_A, "ADMIN")))
    table = Store(
        [
            {
                "organization_id": ORG_B,
                "resource_type_id": TYPE_ID,
                "name": "Secret",
                "status": "ACTIVE",
                "attributes_schema": {"fields": []},
                "matching_config": {},
            }
        ]
    )
    monkeypatch.setattr(catalog, "table_for", lambda kind: table)
    result = catalog.lambda_handler(
        event("GET", path="/resource-types/" + TYPE_ID, params={"resource_type_id": TYPE_ID}),
        None,
    )

    assert result["statusCode"] == 404
    assert "Secret" not in result["body"]


def test_inactive_type_and_resource_attributes(monkeypatch):
    use_memberships(monkeypatch, memberships((ORG_A, "OPERATOR")))
    types = Store(
        [
            {
                "organization_id": ORG_A,
                "resource_type_id": TYPE_ID,
                "name": "Ambulance",
                "status": "INACTIVE",
                "attributes_schema": schema(),
            }
        ]
    )
    monkeypatch.setattr(resource_handler, "resource_types_table", lambda: types)
    monkeypatch.setattr(resource_handler, "locations_table", lambda: Store([
        {"organization_id": ORG_A, "location_id": "LOC-A", "name": "North", "status": "ACTIVE", "city": "Stockholm"}
    ]))
    monkeypatch.setattr(resource_handler, "resources_table", lambda: Store())
    monkeypatch.setattr(resource_handler, "audit_table", lambda: Store())
    inactive = resource_handler.lambda_handler(
        event(
            "POST",
            {
                "resource_id": "R-1",
                "resource_type_id": TYPE_ID,
                "location_id": "LOC-A",
                "attributes": {"capacity": 2},
            },
            path="/allocate/resources",
        ),
        None,
    )

    assert inactive["statusCode"] == 404

    types.items[0]["status"] = "ACTIVE"
    invalid = resource_handler.lambda_handler(
        event(
            "POST",
            {
                "resource_id": "R-1",
                "resource_type_id": TYPE_ID,
                "location_id": "LOC-A",
                "attributes": {"capacity": "many"},
            },
            path="/allocate/resources",
        ),
        None,
    )
    resources = Store()
    monkeypatch.setattr(resource_handler, "resources_table", lambda: resources)
    valid = resource_handler.lambda_handler(
        event(
            "POST",
            {
                "resource_id": "R-1",
                "resource_type_id": TYPE_ID,
                "location_id": "LOC-A",
                "attributes": {"capacity": 2},
                "visibility": "PUBLIC",
                "show_availability": True,
                "organization_id": ORG_B,
            },
            path="/allocate/resources",
        ),
        None,
    )

    assert invalid["statusCode"] == 400
    assert valid["statusCode"] == 201
    saved = resources.puts[0]
    assert saved["organization_id"] == ORG_A
    assert saved["visibility"] == "PUBLIC"
    assert saved["visibility_key"] == "PUBLIC"
    assert "actor_sub" not in saved


def test_request_type_matching_and_rejection(monkeypatch):
    use_memberships(monkeypatch, memberships((ORG_A, "MEMBER")))
    request_types = Store(
        [
            {
                "organization_id": ORG_A,
                "request_type_id": REQUEST_TYPE_ID,
                "name": "Medical assistance",
                "status": "ACTIVE",
                "attributes_schema": {"fields": []},
                "matching_config": {
                    "compatible_resource_type_ids": [TYPE_ID],
                    "required_attributes": {"capacity": {"minimum": 2}},
                    "same_location_preferred": True,
                },
                "default_priority": 2,
            }
        ]
    )
    monkeypatch.setattr(request_handler, "request_types_table", lambda: request_types)
    monkeypatch.setattr(request_handler, "locations_table", lambda: Store([
        {"organization_id": ORG_A, "location_id": "LOC-A", "name": "North", "status": "ACTIVE"}
    ]))
    requests = Store()
    monkeypatch.setattr(request_handler, "requests_table", lambda: requests)
    monkeypatch.setattr(request_handler, "audit_table", lambda: Store())
    created = request_handler.lambda_handler(
        event(
            "POST",
            {"request_id": "Q-1", "request_type_id": REQUEST_TYPE_ID, "location_id": "LOC-A", "priority": 1},
            path="/requests",
        ),
        None,
    )

    assert created["statusCode"] == 201
    assert requests.puts[0]["matching_config"]["compatible_resource_type_ids"] == [TYPE_ID]

    request = requests.puts[0]
    resources = [
        {
            "resource_id": "R-SAME",
            "organization_id": ORG_A,
            "location_id": "LOC-A",
            "resource_type_id": TYPE_ID,
            "Type": "Ambulance",
            "Available": True,
            "attributes": {"capacity": 3},
        },
        {
            "resource_id": "R-OTHER",
            "organization_id": ORG_A,
            "location_id": "LOC-B",
            "resource_type_id": TYPE_ID,
            "Type": "Ambulance",
            "Available": True,
            "attributes": {"capacity": 3},
        },
        {
            "resource_id": "R-SMALL",
            "organization_id": ORG_A,
            "location_id": "LOC-A",
            "resource_type_id": TYPE_ID,
            "Available": True,
            "attributes": {"capacity": 1},
        },
        {
            "resource_id": "R-FOREIGN",
            "organization_id": ORG_B,
            "location_id": "LOC-A",
            "resource_type_id": TYPE_ID,
            "Available": True,
            "attributes": {"capacity": 9},
        },
    ]

    chosen = matching.choose_resource(resources, request)

    assert chosen["resource_id"] == "R-SAME"
    assert "same organization" in matching.explain_match(chosen, request)
    assert "same location" in matching.explain_match(chosen, request)
    assert matching.choose_resource([resources[2]], request) is None
    assert matching.choose_resource([resources[3]], request) is None


def test_public_response_hides_private_fields():
    view = public_api.public_view(
        {
            "visibility": "PUBLIC",
            "visibility_key": "PUBLIC",
            "public_type_name": "Ambulance",
            "public_name": "North unit",
            "public_city": "Stockholm",
            "public_description": "Available for coordination",
            "organization_id": ORG_A,
            "resource_id": "R-SECRET",
            "attributes": {"capacity": 2},
            "actor_sub": USER,
            "show_availability": False,
            "public_status": "AVAILABLE",
        }
    )

    assert view["resource_type"] == "Ambulance"
    assert "organization_id" not in view
    assert "resource_id" not in view
    assert "attributes" not in view
    assert "availability" not in view
    assert public_api.public_view({"visibility": "PRIVATE", "visibility_key": "PUBLIC", "public_name": "Hidden"}) is None


def test_public_query_is_indexed_and_paginated(monkeypatch):
    class PublicStore(Store):
        def query(self, **kwargs):
            self.queries.append(kwargs)
            return {
                "Items": list(self.items),
                "LastEvaluatedKey": {
                    "visibility_key": "PUBLIC",
                    "discovery_key": "AMBULANCE#STOCKHOLM#R1",
                },
            }

    table = PublicStore(
        [
            {
                "visibility": "PUBLIC",
                "visibility_key": "PUBLIC",
                "discovery_key": "AMBULANCE#STOCKHOLM#R1",
                "public_type_name": "Ambulance",
                "public_name": "North unit",
                "public_city": "Stockholm",
            }
        ]
    )
    monkeypatch.setattr(public_api, "resources_table", lambda: table)
    first = public_api.lambda_handler({"httpMethod": "GET", "path": "/public/resources", "queryStringParameters": {"limit": "1"}}, None)
    token = body_of(first)["next_token"]
    second = public_api.lambda_handler(
        {"httpMethod": "GET", "path": "/public/resources", "queryStringParameters": {"page_token": token}},
        None,
    )

    assert first["statusCode"] == 200
    assert body_of(first)["resources"][0]["name"] == "North unit"
    assert table.queries[0]["IndexName"] == "PublicDiscoveryIndex"
    assert "scan" not in table.queries[0]
    assert second["statusCode"] == 200
    assert table.queries[1]["ExclusiveStartKey"]["visibility_key"] == "PUBLIC"


def test_page_token_round_trip():
    token = pages.encode_token({"organization_id": ORG_B, "resource_type_id": TYPE_ID})
    parsed = pages.decode_token(token, ["organization_id", "resource_type_id"])

    assert parsed["organization_id"] == ORG_B

    with pytest.raises(AccessError):
        pages.decode_token("not-a-token", ["organization_id", "resource_type_id"])


def test_catalog_page_token_cannot_cross_tenants(monkeypatch):
    use_memberships(monkeypatch, memberships((ORG_A, "MEMBER")))
    table = Store()
    monkeypatch.setattr(catalog, "table_for", lambda kind: table)
    token = pages.encode_token({"organization_id": ORG_B, "resource_type_id": TYPE_ID})
    result = catalog.lambda_handler(
        event("GET", path="/resource-types", query={"page_token": token}),
        None,
    )

    assert result["statusCode"] == 400
    assert table.queries == []


def test_frontend_clears_catalog_on_organization_switch():
    text = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")

    assert "resourceTypes = []" in text
    assert "requestTypes = []" in text
    assert "await loadResourceTypes();" in text
    assert "await loadRequestTypes();" in text
    assert "tenantContextLoading" in text
