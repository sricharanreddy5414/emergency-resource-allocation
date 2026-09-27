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


def load_module(name, relative):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


request_handler = load_module("request_form_handler", "src/request/handler.py")

ORG = "ORG-D13B30D99127"
OTHER = "ORG-OTHER"
USER = "pilot-owner"
REQUEST_TYPE_ID = "RQ-5201EFBA2A5C"
LOCATION_ID = "LOC-9497150CB853"


def event(body):
    return {
        "httpMethod": "POST",
        "path": "/requests",
        "headers": {"Authorization": "Bearer secret-token-value"},
        "requestContext": {"authorizer": {"claims": {"sub": USER}}},
        "body": json.dumps(body),
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
    def __init__(self, items=None):
        self.items = list(items or [])
        self.puts = []

    def get_item(self, Key):
        for item in self.items:
            if all(item.get(key) == value for key, value in Key.items()):
                return {"Item": dict(item)}
        return {}

    def put_item(self, Item, **kwargs):
        self.puts.append(Item)
        self.items.append(Item)


def body_of(result):
    return json.loads(result["body"])


def request_type(**overrides):
    record = {
        "organization_id": ORG,
        "request_type_id": REQUEST_TYPE_ID,
        "name": "Emergency Medical Supply Request",
        "status": "ACTIVE",
        "attributes_schema": {"fields": []},
        "matching_config": {
            "compatible_resource_type_ids": ["RT-2ED4DE323557"],
            "required_attributes": {"quantity": {"minimum": Decimal("2")}},
            "same_location_preferred": True,
        },
        "default_priority": Decimal("3"),
    }
    record.update(overrides)
    return record


def location():
    return {
        "organization_id": ORG,
        "location_id": LOCATION_ID,
        "name": "Bengaluru Operations Center",
        "status": "ACTIVE",
    }


def wire(monkeypatch, types, locations=None):
    use_owner(monkeypatch)
    monkeypatch.setattr(request_handler, "request_types_table", lambda: Store(types))
    monkeypatch.setattr(request_handler, "locations_table", lambda: Store(locations if locations is not None else [location()]))
    requests = Store()
    monkeypatch.setattr(request_handler, "requests_table", lambda: requests)
    monkeypatch.setattr(request_handler, "audit_table", lambda: Store())
    return requests


def test_request_modal_submits_request_type_not_hardcoded_resources():
    page = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
    modal = page.split('id="requestModal"', 1)[1].split("</form>", 1)[0]

    assert "Request Type" in modal
    assert 'id="modalRequestType"' in modal
    assert 'id="modalLocation"' in modal
    assert "ICU_BED" not in page
    assert "GENERAL_BED" not in page
    assert "modalResourceType" not in page
    assert "erapRequests" not in page
    assert "submitRequestModal" in script
    assert "request_type_id: requestTypeId" in script
    assert '"Authorization": "Bearer " + getIdToken()' in script
    assert "resource_type:" not in script[script.index("async function submitRequestModal"):script.index("async function submitRequestModal") + 2500]


def test_create_request_stores_request_type_and_serializes_decimals(monkeypatch):
    requests = wire(monkeypatch, [request_type()])
    result = request_handler.lambda_handler(
        event({
            "request_id": "PILOT-REQ-002",
            "request_type_id": REQUEST_TYPE_ID,
            "location_id": LOCATION_ID,
            "priority": 3,
            "organization_id": ORG,
        }),
        None,
    )
    parsed = body_of(result)
    saved = requests.puts[0]

    assert result["statusCode"] == 201
    assert saved["organization_id"] == ORG
    assert saved["request_type_id"] == REQUEST_TYPE_ID
    assert saved["ResourceType"] == "Emergency Medical Supply Request"
    assert saved["location_id"] == LOCATION_ID
    assert saved["Status"] == "PENDING"
    assert saved["matching_config"]["compatible_resource_type_ids"] == ["RT-2ED4DE323557"]
    assert parsed["request"]["request_type_id"] == REQUEST_TYPE_ID
    assert parsed["request"]["matching_config"]["required_attributes"]["quantity"]["minimum"] == 2
    assert isinstance(parsed["request"]["matching_config"]["required_attributes"]["quantity"]["minimum"], int)
    assert '"minimum": 2' in result["body"]
    assert "secret-token-value" not in result["body"]

    denied = request_handler.lambda_handler(
        event({
            "request_id": "PILOT-REQ-003",
            "request_type_id": REQUEST_TYPE_ID,
            "location_id": LOCATION_ID,
            "priority": 3,
            "organization_id": OTHER,
        }),
        None,
    )

    assert denied["statusCode"] == 403
    assert len(requests.puts) == 1


def test_unknown_inactive_and_foreign_request_types_are_rejected(monkeypatch):
    wire(monkeypatch, [request_type()])
    missing = request_handler.lambda_handler(
        event({"request_id": "PILOT-REQ-002", "request_type_id": "RQ-MISSING", "location_id": LOCATION_ID, "priority": 3}),
        None,
    )
    wire(monkeypatch, [request_type(status="INACTIVE")])
    inactive = request_handler.lambda_handler(
        event({"request_id": "PILOT-REQ-002", "request_type_id": REQUEST_TYPE_ID, "location_id": LOCATION_ID, "priority": 3}),
        None,
    )
    wire(monkeypatch, [request_type(organization_id=OTHER)])
    foreign = request_handler.lambda_handler(
        event({"request_id": "PILOT-REQ-002", "request_type_id": REQUEST_TYPE_ID, "location_id": LOCATION_ID, "priority": 3}),
        None,
    )

    assert missing["statusCode"] == 404
    assert inactive["statusCode"] == 404
    assert foreign["statusCode"] == 404
    assert body_of(missing)["message"] == "Request type not found"


def test_location_must_belong_to_the_organization(monkeypatch):
    wire(monkeypatch, [request_type()], locations=[])
    missing = request_handler.lambda_handler(
        event({"request_id": "PILOT-REQ-002", "request_type_id": REQUEST_TYPE_ID, "location_id": LOCATION_ID, "priority": 3}),
        None,
    )
    wire(
        monkeypatch,
        [request_type()],
        locations=[{
            "organization_id": OTHER,
            "location_id": LOCATION_ID,
            "name": "Other center",
            "status": "ACTIVE",
        }],
    )
    foreign = request_handler.lambda_handler(
        event({"request_id": "PILOT-REQ-002", "request_type_id": REQUEST_TYPE_ID, "location_id": LOCATION_ID, "priority": 3}),
        None,
    )

    assert missing["statusCode"] == 404
    assert foreign["statusCode"] == 404
    assert body_of(missing)["message"] == "Location not found"
