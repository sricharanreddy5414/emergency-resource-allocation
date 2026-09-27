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
import matching
from access import AccessError


def load_module(name, relative):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


resource_handler = load_module("resource_handler", "src/resource/handler.py")
request_handler = load_module("request_handler", "src/request/handler.py")
allocation_service = load_module("allocation_service", "src/allocation/service.py")


ORG_A = "ORG-A"
ORG_B = "ORG-B"
USER = "user-1"


def event(method="GET", body=None, subject=USER, organization_id=ORG_A, path="/allocate/resources"):
    payload = {
        "httpMethod": method,
        "path": path,
        "queryStringParameters": {"organization_id": organization_id} if organization_id else {},
        "requestContext": {"authorizer": {"claims": {"sub": subject}}},
    }

    if body is not None:
        payload["body"] = json.dumps(body)

    return payload


def body_of(result):
    return json.loads(result["body"])


def memberships_for(*organizations):
    return [
        {
            "organization_id": organization_id,
            "name": organization_id,
            "role": role,
            "status": "ACTIVE",
        }
        for organization_id, role in organizations
    ]


def use_memberships(monkeypatch, records):
    monkeypatch.setattr(access, "list_memberships", lambda *args, **kwargs: records)


class Store:
    def __init__(self, items=None):
        self.items = list(items or [])
        self.puts = []
        self.updates = []
        self.scans = 0

    def query(self, **kwargs):
        return {"Items": list(self.items)}

    def get_item(self, Key):
        for item in self.items:
            if all(item.get(key) == value for key, value in Key.items()):
                return {"Item": item}

        return {}

    def put_item(self, Item, ConditionExpression=None):
        self.puts.append(Item)
        self.items.append(Item)

    def update_item(self, **kwargs):
        self.updates.append(kwargs)

    def scan(self, **kwargs):
        self.scans += 1
        raise AssertionError("tenant API must not scan")


def test_unauthorized_user():
    with pytest.raises(AccessError) as error:
        access.authorize({"requestContext": {}}, {})

    assert error.value.status_code == 401


def test_non_member_is_rejected(monkeypatch):
    use_memberships(monkeypatch, [])

    with pytest.raises(AccessError) as error:
        access.authorize(event(), {})

    assert error.value.status_code == 403


def test_wrong_organization_is_rejected(monkeypatch):
    use_memberships(monkeypatch, memberships_for((ORG_A, "OWNER")))

    with pytest.raises(AccessError) as error:
        access.authorize(event(organization_id=ORG_B), {"organization_id": ORG_B, "user_sub": "forged", "role": "OWNER"})

    assert error.value.status_code == 403


def test_member_role_cannot_operate(monkeypatch):
    use_memberships(monkeypatch, memberships_for((ORG_A, "MEMBER")))

    with pytest.raises(AccessError) as error:
        access.authorize(event(method="POST"), {}, allowed_roles=access.OPERATE_ROLES)

    assert error.value.status_code == 403


def test_wrong_location_is_rejected():
    locations = Store(
        [
            {
                "organization_id": ORG_B,
                "location_id": "LOC-B",
                "status": "ACTIVE",
            }
        ]
    )

    with pytest.raises(AccessError) as error:
        access.require_location(locations, ORG_A, "LOC-B")

    assert error.value.status_code == 404


def test_resource_list_stays_inside_organization(monkeypatch):
    use_memberships(monkeypatch, memberships_for((ORG_A, "MEMBER")))
    resources = Store(
        [
            {
                "resource_id": "R1",
                "organization_id": ORG_A,
                "location_id": "LOC-A",
                "Type": "ICU_BED",
                "Available": True,
            }
        ]
    )
    monkeypatch.setattr(resource_handler, "resources_table", lambda: resources)

    result = resource_handler.lambda_handler(event(), None)

    assert result["statusCode"] == 200
    assert body_of(result)[0]["organization_id"] == ORG_A
    assert resources.scans == 0


def test_resource_detail_rejects_other_organization(monkeypatch):
    use_memberships(monkeypatch, memberships_for((ORG_A, "MEMBER")))
    resources = Store(
        [
            {
                "resource_id": "R9",
                "organization_id": ORG_B,
                "location_id": "LOC-B",
            }
        ]
    )
    monkeypatch.setattr(resource_handler, "resources_table", lambda: resources)
    monkeypatch.setattr(resource_handler, "history_table", lambda: Store())

    result = resource_handler.lambda_handler(
        event(path="/allocate/resources/history", organization_id=ORG_A),
        None,
    )
    result["query"] = None
    payload = event(path="/allocate/resources/history")
    payload["queryStringParameters"]["resource_id"] = "R9"

    result = resource_handler.lambda_handler(payload, None)

    assert result["statusCode"] == 404
    assert resources.scans == 0


def test_request_creation_ignores_client_identity(monkeypatch):
    use_memberships(monkeypatch, memberships_for((ORG_A, "MEMBER")))
    locations = Store(
        [
            {
                "organization_id": ORG_A,
                "location_id": "LOC-A",
                "name": "North",
                "status": "ACTIVE",
            }
        ]
    )
    requests = Store()
    monkeypatch.setattr(request_handler, "locations_table", lambda: locations)
    monkeypatch.setattr(request_handler, "requests_table", lambda: requests)

    result = request_handler.lambda_handler(
        event(
            "POST",
            {
                "request_id": "REQ-1",
                "resource_type": "ICU_BED",
                "location_id": "LOC-A",
                "priority": 1,
                "organization_id": ORG_A,
                "user_sub": "forged",
                "role": "OWNER",
            },
            path="/requests",
        ),
        None,
    )

    assert result["statusCode"] == 201
    assert requests.puts[0]["organization_id"] == ORG_A
    assert "forged" not in json.dumps(requests.puts[0])


def test_cross_tenant_allocation_is_rejected(monkeypatch):
    use_memberships(monkeypatch, memberships_for((ORG_A, "OPERATOR")))
    locations = Store(
        [
            {
                "organization_id": ORG_A,
                "location_id": "LOC-A",
                "name": "North",
                "status": "ACTIVE",
            }
        ]
    )
    requests = Store(
        [
            {
                "request_id": "REQ-OTHER",
                "organization_id": ORG_B,
                "location_id": "LOC-B",
                "Status": "PENDING",
            }
        ]
    )
    monkeypatch.setattr(allocation_service, "locations_table", lambda: locations)
    monkeypatch.setattr(allocation_service, "requests_table", lambda: requests)
    monkeypatch.setattr(allocation_service, "resources_table", lambda: Store())
    monkeypatch.setattr(allocation_service, "allocations_table", lambda: Store())

    result = allocation_service.lambda_handler(
        event(
            "POST",
            {
                "request_id": "REQ-OTHER",
                "resource_type": "ICU_BED",
                "location_id": "LOC-A",
                "priority": 1,
                "organization_id": ORG_A,
            },
            path="/allocate",
        ),
        None,
    )

    assert result["statusCode"] == 404
    assert requests.scans == 0


def test_same_location_is_preferred():
    request = {
        "organization_id": ORG_A,
        "location_id": "LOC-A",
        "ResourceType": "ICU_BED",
    }
    resources = [
        {
            "resource_id": "R-OTHER",
            "organization_id": ORG_A,
            "location_id": "LOC-B",
            "Type": "ICU_BED",
            "Available": True,
        },
        {
            "resource_id": "R-SAME",
            "organization_id": ORG_A,
            "location_id": "LOC-A",
            "Type": "ICU_BED",
            "Available": True,
        },
    ]

    assert matching.choose_resource(resources, request)["resource_id"] == "R-SAME"


def test_alternate_location_in_same_organization():
    request = {
        "organization_id": ORG_A,
        "location_id": "LOC-A",
        "ResourceType": "ICU_BED",
    }
    resources = [
        {
            "resource_id": "R-B",
            "organization_id": ORG_A,
            "location_id": "LOC-B",
            "Type": "ICU_BED",
            "Available": True,
        }
    ]

    assert matching.choose_resource(resources, request)["resource_id"] == "R-B"


def test_cross_organization_resource_is_not_matched():
    request = {
        "organization_id": ORG_A,
        "location_id": "LOC-A",
        "ResourceType": "ICU_BED",
    }
    resources = [
        {
            "resource_id": "R-B",
            "organization_id": ORG_B,
            "location_id": "LOC-A",
            "Type": "ICU_BED",
            "Available": True,
        }
    ]

    assert matching.choose_resource(resources, request) is None
