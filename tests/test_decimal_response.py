import importlib.util
import json
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [
    str(ROOT / "src" / "shared"),
    str(ROOT / "src" / "organization"),
]

import access
import common


def load_module(name, relative):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


catalog = load_module("catalog_decimal", "src/catalog/handler.py")
public_api = load_module("public_decimal", "src/public/handler.py")

ORG = "ORG-D13B30D99127"
USER = "pilot-owner"
REQUEST_TYPE_ID = "RQ-5201EFBA2A5C"


def event(method="GET", path="/request-types", query=None):
    return {
        "httpMethod": method,
        "path": path,
        "pathParameters": {},
        "queryStringParameters": {"organization_id": ORG, **(query or {})},
        "headers": {"Authorization": "Bearer secret-token-value"},
        "requestContext": {"authorizer": {"claims": {"sub": USER}}},
    }


def use_owner(monkeypatch):
    monkeypatch.setattr(
        access,
        "list_memberships",
        lambda *args, **kwargs: [
            {"organization_id": ORG, "name": "ERAP Pilot Operations", "role": "OWNER", "status": "ACTIVE"}
        ],
    )


class Store:
    def __init__(self, items):
        self.items = items

    def query(self, **kwargs):
        return {"Items": list(self.items)}


def body_of(result):
    return json.loads(result["body"])


def test_api_response_serializes_decimal_integer_as_json_number():
    result = common.api_response(200, {"default_priority": Decimal("3")})
    parsed = body_of(result)

    assert result["statusCode"] == 200
    assert parsed["default_priority"] == 3
    assert isinstance(parsed["default_priority"], int)
    assert '"default_priority": 3' in result["body"]
    assert '"default_priority": "3"' not in result["body"]


def test_api_response_preserves_fractional_decimal_as_json_number():
    result = common.api_response(200, {"quantity": Decimal("2.50"), "nested": {"minimum": Decimal("0.10")}})
    parsed = body_of(result)

    assert parsed["quantity"] == 2.5
    assert parsed["nested"]["minimum"] == 0.1
    assert '"quantity": 2.5' in result["body"]
    assert '"minimum": 0.1' in result["body"]
    assert '"2.5"' not in result["body"]


def test_api_response_leaves_strings_errors_and_secrets_unchanged():
    success = common.api_response(200, {"name": "Emergency Medical Supply Request"})
    result = common.api_response(404, {"message": "Request type not found", "password": "do-not-copy"})
    parsed = body_of(result)

    assert body_of(success)["name"] == "Emergency Medical Supply Request"
    assert result["statusCode"] == 404
    assert parsed["message"] == "Request type not found"
    assert parsed["error"]["code"] == "NOT_FOUND"
    assert "password" not in parsed
    assert "do-not-copy" not in result["body"]
    assert "secret-token-value" not in result["body"]


def test_get_request_types_serializes_dynamodb_decimal_priority(monkeypatch):
    """Reproduce the live GET /request-types TypeError and require HTTP 200."""
    use_owner(monkeypatch)
    monkeypatch.setattr(
        catalog,
        "table_for",
        lambda kind: Store(
            [
                {
                    "organization_id": ORG,
                    "request_type_id": REQUEST_TYPE_ID,
                    "name": "Emergency Medical Supply Request",
                    "description": "Request for emergency medical supplies.",
                    "status": "ACTIVE",
                    "attributes_schema": {"fields": [{"key": "quantity", "minimum": Decimal("1")}]},
                    "matching_config": {},
                    "default_priority": Decimal("3"),
                }
            ]
        ),
    )

    result = catalog.lambda_handler(event(), None)
    parsed = body_of(result)
    listed = parsed["types"][0]

    assert result["statusCode"] == 200
    assert listed["request_type_id"] == REQUEST_TYPE_ID
    assert listed["name"] == "Emergency Medical Supply Request"
    assert listed["status"] == "ACTIVE"
    assert listed["default_priority"] == 3
    assert isinstance(listed["default_priority"], int)
    assert listed["attributes_schema"]["fields"][0]["minimum"] == 1
    assert '"default_priority": 3' in result["body"]
    assert "secret-token-value" not in result["body"]
    assert "password" not in result["body"]


def test_resource_type_list_stays_successful_without_decimal_priority(monkeypatch):
    use_owner(monkeypatch)
    monkeypatch.setattr(
        catalog,
        "table_for",
        lambda kind: Store(
            [
                {
                    "organization_id": ORG,
                    "resource_type_id": "RT-2ED4DE323557",
                    "name": "Emergency Medical Kit",
                    "status": "ACTIVE",
                    "attributes_schema": {"fields": []},
                    "matching_config": {},
                }
            ]
        ),
    )

    result = catalog.lambda_handler(event(path="/resource-types"), None)
    parsed = body_of(result)

    assert result["statusCode"] == 200
    assert parsed["types"][0]["name"] == "Emergency Medical Kit"
    assert parsed["types"][0]["default_priority"] == 3
    assert isinstance(parsed["types"][0]["default_priority"], int)


def test_catalog_missing_authentication_remains_401():
    result = catalog.lambda_handler(
        {
            "httpMethod": "GET",
            "path": "/request-types",
            "queryStringParameters": {},
            "requestContext": {},
        },
        None,
    )
    parsed = body_of(result)

    assert result["statusCode"] == 401
    assert parsed["message"] == "Authentication required"
    assert parsed["error"]["code"] == "UNAUTHENTICATED"
    assert "secret-token-value" not in result["body"]


def test_catalog_invalid_page_size_remains_400(monkeypatch):
    use_owner(monkeypatch)
    result = catalog.lambda_handler(event(query={"limit": "0"}), None)
    parsed = body_of(result)

    assert result["statusCode"] == 400
    assert parsed["message"] == "Page size is invalid"
    assert parsed["error"]["code"] == "INVALID_REQUEST"


def test_public_discovery_still_hides_internal_fields():
    view = public_api.public_view(
        {
            "visibility": "PUBLIC",
            "visibility_key": "PUBLIC",
            "public_type_name": "Emergency Medical Kit",
            "public_name": "Public Emergency Medical Supplies",
            "public_city": "Bengaluru",
            "public_description": "Emergency medical supplies for response coordination.",
            "organization_id": ORG,
            "resource_id": "PILOT-MED-001",
            "attributes": {"quantity": Decimal("10")},
            "actor_sub": USER,
            "show_availability": True,
            "public_status": "AVAILABLE",
        }
    )

    assert view == {
        "resource_type": "Emergency Medical Kit",
        "name": "Public Emergency Medical Supplies",
        "city": "Bengaluru",
        "description": "Emergency medical supplies for response coordination.",
        "availability": "AVAILABLE",
    }
    assert public_api.public_view(
        {"visibility": "PRIVATE", "visibility_key": "PRIVATE", "public_name": "Pilot Emergency Medical Kit"}
    ) is None
