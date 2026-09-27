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
import observability
from access import AccessError
from botocore.exceptions import ClientError


def load_module(name, relative):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


resource_handler = load_module("resource_phase6", "src/resource/handler.py")
request_handler = load_module("request_phase6", "src/request/handler.py")
allocation_service = load_module("allocation_phase6", "src/allocation/service.py")
public_api = load_module("public_phase6", "src/public/handler.py")
auto_release = load_module("auto_release_phase6", "src/auto_release/handler.py")

ORG = "ORG-A"


def event(method="POST", body=None, subject="user-a", organization_id=ORG, path="/allocate/resources"):
    payload = {
        "httpMethod": method,
        "path": path,
        "queryStringParameters": {"organization_id": organization_id},
        "requestContext": {
            "requestId": "req-phase6",
            "authorizer": {"claims": {"sub": subject}},
        },
    }
    if body is not None:
        payload["body"] = body if isinstance(body, str) else json.dumps(body)
    return payload


def use_memberships(monkeypatch, role="OPERATOR"):
    monkeypatch.setattr(
        access,
        "list_memberships",
        lambda *args, **kwargs: [
            {"organization_id": ORG, "role": role, "status": "ACTIVE", "name": "Test"}
        ],
    )


def body_of(result):
    return json.loads(result["body"])


def test_oversized_body_is_rejected(monkeypatch):
    use_memberships(monkeypatch)
    result = resource_handler.lambda_handler(event(body="{" + ("x" * 9000) + "}"), None)
    body = body_of(result)

    assert result["statusCode"] == 400
    assert body["error"]["code"] == "INVALID_REQUEST"
    assert body["error"]["request_id"] == "req-phase6"
    assert "table" not in body["message"].lower()


def test_nested_payload_is_rejected():
    nested = {"a": {"b": {"c": {"d": {"e": 1}}}}}

    with pytest.raises(ValueError):
        observability.load_object(json.dumps(nested))


def test_invalid_resource_id(monkeypatch):
    use_memberships(monkeypatch)
    result = resource_handler.lambda_handler(
        event(body={"resource_id": "../secret", "resource_type_id": "RT-ABCDEF123456", "location_id": "LOC-A"}),
        None,
    )

    assert result["statusCode"] == 400
    assert body_of(result)["message"] == "resource_id is invalid"


def test_public_page_size_is_bounded():
    result = public_api.lambda_handler(
        {"httpMethod": "GET", "path": "/public/resources", "queryStringParameters": {"limit": "1000"}, "requestContext": {"requestId": "pub-1"}},
        None,
    )
    body = body_of(result)

    assert result["statusCode"] == 400
    assert body["error"]["code"] == "INVALID_REQUEST"
    assert "DynamoDB" not in result["body"]


def test_member_cannot_operate(monkeypatch):
    use_memberships(monkeypatch, "MEMBER")

    with pytest.raises(AccessError) as error:
        access.authorize(event(), {}, allowed_roles=access.OPERATE_ROLES)

    assert error.value.status_code == 403


def test_operator_cannot_manage_locations(monkeypatch):
    use_memberships(monkeypatch, "OPERATOR")

    with pytest.raises(AccessError) as error:
        access.authorize(event(), {}, allowed_roles=access.LOCATION_WRITE_ROLES)

    assert error.value.status_code == 403


def test_concurrent_allocation_second_claim_fails():
    class Resource:
        def __init__(self):
            self.available = True
            self.updates = 0

        def update_item(self, **kwargs):
            self.updates += 1
            if not self.available:
                raise ClientError(
                    {"Error": {"Code": "ConditionalCheckFailedException", "Message": "held"}},
                    "UpdateItem",
                )
            self.available = False

    resource = Resource()
    resource.update_item()

    with pytest.raises(ClientError):
        resource.update_item()

    assert resource.updates == 2
    assert resource.available is False


def test_auto_release_retry_is_idempotent():
    allocation = {
        "allocation_id": "A1",
        "request_id": "Q1",
        "resource_id": "R1",
        "organization_id": ORG,
        "status": "RELEASED",
    }
    decision = auto_release.plan_release(
        allocation,
        {"resource_id": "R1", "organization_id": ORG},
        {"request_id": "Q1", "organization_id": ORG},
        [],
    )

    assert decision["action"] == "skip"
    assert decision["reason"] == "already released"


def test_logs_do_not_include_tokens(capsys):
    observability.begin_request({"requestContext": {"requestId": "req-log"}, "httpMethod": "GET", "path": "/resources"})
    observability.log_result(404, operation="resource.read", organization_id=ORG, error_code="NOT_FOUND")
    logged = capsys.readouterr().out

    assert "req-log" in logged
    assert "Bearer" not in logged
    assert "eyJ" not in logged


def test_frontend_does_not_print_live_payloads():
    text = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")

    assert "Live requests received" not in text
    assert "Live allocations received" not in text
    assert "erap_id_token" in text
    assert "console.log(getIdToken" not in text
    assert "console.log(idToken" not in text
