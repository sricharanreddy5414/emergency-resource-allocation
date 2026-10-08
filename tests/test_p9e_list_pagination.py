"""Signed one-page lists for emergency requests and allocations."""

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [
    str(ROOT / "src" / "shared"),
    str(ROOT / "src" / "organization"),
]

import access
from api_views import ALLOCATION_FIELDS, REQUEST_FIELDS

def load_module(name, relative):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


tokens = load_module("emergency_page_token_p9e", "src/shared/emergency_page_token.py")
allocation_service = load_module("allocation_service_p9e", "src/allocation/service.py")

ORG = "ORG-A"
OTHER = "ORG-B"
USER = "user-p9e"
SECRET = "synthetic-emergency-page-token-secret-v1"
PREVIOUS = "synthetic-emergency-page-token-secret-v0"
NOW = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)


def event(path, query=None, organization_id=ORG, role_status="ACTIVE"):
    parameters = {"organization_id": organization_id}
    if query:
        parameters.update(query)
    return {
        "httpMethod": "GET",
        "path": path,
        "queryStringParameters": parameters,
        "requestContext": {"authorizer": {"claims": {"sub": USER}}},
        "_role_status": role_status,
    }


def body_of(result):
    return json.loads(result["body"])


class PageTable:
    def __init__(self, items, identity):
        self.items = [dict(item) for item in items]
        self.identity = identity
        self.calls = []

    def query(self, **kwargs):
        self.calls.append(kwargs)
        start = 0
        exclusive = kwargs.get("ExclusiveStartKey")
        if exclusive:
            ids = [item[self.identity] for item in self.items]
            start = ids.index(exclusive[self.identity]) + 1
        limit = kwargs["Limit"]
        page = self.items[start:start + limit]
        result = {"Items": page}
        if start + limit < len(self.items) and page:
            last = page[-1]
            result["LastEvaluatedKey"] = {
                "organization_id": last["organization_id"],
                "location_id": last["location_id"],
                self.identity: last[self.identity],
            }
        return result


class LocationTable:
    def __init__(self, locations):
        self.locations = locations

    def get_item(self, Key):
        for item in self.locations:
            if item["organization_id"] == Key["organization_id"] and item["location_id"] == Key["location_id"]:
                return {"Item": dict(item)}
        return {}


def use_member(monkeypatch, status="ACTIVE"):
    monkeypatch.setattr(
        access,
        "list_memberships",
        lambda *args, **kwargs: [
            {
                "organization_id": ORG,
                "name": ORG,
                "role": "MEMBER",
                "status": status,
            }
        ],
    )
    monkeypatch.setattr(
        allocation_service,
        "load_emergency_page_secret",
        lambda: (SECRET, [PREVIOUS]),
    )


def requests_for(count, organization_id=ORG, location_id="LOC-A"):
    return [
        {
            "request_id": f"Q{index}",
            "organization_id": organization_id,
            "location_id": location_id,
            "status": "PENDING",
            "ResourceType": "AMBULANCE",
            "created_by": "hidden-sub",
            "pk": "SECRET",
        }
        for index in range(count)
    ]


def allocations_for(count, organization_id=ORG, location_id="LOC-A"):
    return [
        {
            "allocation_id": f"ALLOC-{index}",
            "organization_id": organization_id,
            "location_id": location_id,
            "request_id": f"Q{index}",
            "resource_id": f"R{index}",
            "status": "ALLOCATED",
            "created_by": "hidden-sub",
        }
        for index in range(count)
    ]


def install_requests(monkeypatch, items):
    table = PageTable(items, "request_id")
    monkeypatch.setattr(allocation_service, "requests_table", lambda: table)
    monkeypatch.setattr(allocation_service, "allocations_table", lambda: PageTable([], "allocation_id"))
    monkeypatch.setattr(
        allocation_service,
        "locations_table",
        lambda: LocationTable([
            {"organization_id": ORG, "location_id": "LOC-A", "status": "ACTIVE"},
            {"organization_id": ORG, "location_id": "LOC-B", "status": "ACTIVE"},
        ]),
    )
    return table


def install_allocations(monkeypatch, items):
    table = PageTable(items, "allocation_id")
    monkeypatch.setattr(allocation_service, "allocations_table", lambda: table)
    monkeypatch.setattr(allocation_service, "requests_table", lambda: PageTable([], "request_id"))
    monkeypatch.setattr(
        allocation_service,
        "locations_table",
        lambda: LocationTable([
            {"organization_id": ORG, "location_id": "LOC-A", "status": "ACTIVE"},
        ]),
    )
    return table


def test_request_pages_resume_and_stop(monkeypatch):
    use_member(monkeypatch)
    table = install_requests(monkeypatch, requests_for(3))
    first = body_of(allocation_service.lambda_handler(event("/requests", {"limit": "2"}), None))
    assert first["count"] == 2
    assert [item["request_id"] for item in first["requests"]] == ["Q0", "Q1"]
    assert first["next_token"]
    assert "created_by" not in json.dumps(first["requests"])
    assert "pk" not in json.dumps(first["requests"])
    assert table.calls[0]["Limit"] == 2
    assert "ExclusiveStartKey" not in table.calls[0]
    assert ".scan(" not in (ROOT / "src" / "allocation" / "service.py").read_text(encoding="utf-8")

    second = body_of(
        allocation_service.lambda_handler(
            event("/requests", {"limit": "2", "page_token": first["next_token"]}),
            None,
        )
    )
    assert second["count"] == 1
    assert second["requests"][0]["request_id"] == "Q2"
    assert "next_token" not in second
    assert table.calls[1]["ExclusiveStartKey"]["request_id"] == "Q1"
    assert len(table.calls) == 2


def test_request_default_limit_is_one_hundred(monkeypatch):
    use_member(monkeypatch)
    table = install_requests(monkeypatch, requests_for(1))
    body_of(allocation_service.lambda_handler(event("/requests"), None))
    assert table.calls[0]["Limit"] == 100


@pytest.mark.parametrize("limit", ["0", "101", "nope", "-1"])
def test_request_page_size_is_bounded(monkeypatch, limit):
    use_member(monkeypatch)
    install_requests(monkeypatch, requests_for(1))
    result = allocation_service.lambda_handler(event("/requests", {"limit": limit}), None)
    assert result["statusCode"] == 400
    assert body_of(result)["message"] == "Page size is invalid"


def test_allocation_pages_match_the_request_contract(monkeypatch):
    use_member(monkeypatch)
    table = install_allocations(monkeypatch, allocations_for(2))
    first = body_of(allocation_service.lambda_handler(event("/allocate/allocations", {"limit": "1"}), None))
    assert first["count"] == 1
    assert first["allocations"][0]["allocation_id"] == "ALLOC-0"
    assert set(first["allocations"][0]) <= set(ALLOCATION_FIELDS)
    second = body_of(
        allocation_service.lambda_handler(
            event("/allocate/allocations", {"limit": "1", "page_token": first["next_token"]}),
            None,
        )
    )
    assert second["allocations"][0]["allocation_id"] == "ALLOC-1"
    assert "next_token" not in second
    assert table.calls[0]["Limit"] == 1
    assert table.calls[1]["ExclusiveStartKey"]["allocation_id"] == "ALLOC-0"


def test_tokens_do_not_cross_endpoints_or_tenants_or_locations(monkeypatch):
    use_member(monkeypatch)
    request_table = PageTable(requests_for(2), "request_id")
    allocation_table = PageTable(allocations_for(2), "allocation_id")
    monkeypatch.setattr(allocation_service, "requests_table", lambda: request_table)
    monkeypatch.setattr(allocation_service, "allocations_table", lambda: allocation_table)
    monkeypatch.setattr(
        allocation_service,
        "locations_table",
        lambda: LocationTable([
            {"organization_id": ORG, "location_id": "LOC-A", "status": "ACTIVE"},
            {"organization_id": ORG, "location_id": "LOC-B", "status": "ACTIVE"},
        ]),
    )
    request_page = body_of(allocation_service.lambda_handler(event("/requests", {"limit": "1"}), None))
    allocation_page = body_of(
        allocation_service.lambda_handler(event("/allocate/allocations", {"limit": "1"}), None)
    )
    crossed = allocation_service.lambda_handler(
        event("/allocate/allocations", {"limit": "1", "page_token": request_page["next_token"]}),
        None,
    )
    assert crossed["statusCode"] == 400
    assert body_of(crossed)["message"] == "Invalid page token"
    reverse = allocation_service.lambda_handler(
        event("/requests", {"limit": "1", "page_token": allocation_page["next_token"]}),
        None,
    )
    assert reverse["statusCode"] == 400
    resized = allocation_service.lambda_handler(
        event("/requests", {"limit": "2", "page_token": request_page["next_token"]}),
        None,
    )
    assert resized["statusCode"] == 400
    moved = allocation_service.lambda_handler(
        event(
            "/requests",
            {"limit": "1", "location_id": "LOC-B", "page_token": request_page["next_token"]},
        ),
        None,
    )
    assert moved["statusCode"] == 400
    foreign = tokens.sign_emergency_page_token(
        {
            "organization_id": OTHER,
            "location_id": "LOC-A",
            "request_id": "Q0",
        },
        endpoint=tokens.REQUEST_ENDPOINT,
        organization_id=OTHER,
        location_id="",
        limit=1,
        secret=SECRET,
        now=NOW,
    )
    denied = allocation_service.lambda_handler(
        event("/requests", {"limit": "1", "page_token": foreign}),
        None,
    )
    assert denied["statusCode"] == 400
    assert OTHER not in body_of(denied)["message"]


def test_request_list_skips_other_organizations(monkeypatch):
    use_member(monkeypatch)
    items = requests_for(1) + requests_for(1, organization_id=OTHER)
    items[1]["request_id"] = "Q-OTHER"
    install_requests(monkeypatch, items)
    # The fake page returns both rows. The handler still drops the other tenant.
    body = body_of(allocation_service.lambda_handler(event("/requests", {"limit": "2"}), None))
    assert [item["request_id"] for item in body["requests"]] == ["Q0"]
    assert set(body["requests"][0]) <= set(REQUEST_FIELDS)


def test_malformed_expired_future_and_bad_signature_tokens(monkeypatch):
    use_member(monkeypatch)
    install_requests(monkeypatch, requests_for(1))
    cursor = {
        "organization_id": ORG,
        "location_id": "LOC-A",
        "request_id": "Q0",
    }
    valid = tokens.sign_emergency_page_token(
        cursor,
        endpoint=tokens.REQUEST_ENDPOINT,
        organization_id=ORG,
        location_id="",
        limit=100,
        secret=SECRET,
        now=NOW,
    )
    expired = tokens.sign_emergency_page_token(
        cursor,
        endpoint=tokens.REQUEST_ENDPOINT,
        organization_id=ORG,
        location_id="",
        limit=100,
        secret=SECRET,
        now=NOW - timedelta(seconds=tokens.TTL_SECONDS + 5),
    )
    future = tokens.sign_emergency_page_token(
        cursor,
        endpoint=tokens.REQUEST_ENDPOINT,
        organization_id=ORG,
        location_id="",
        limit=100,
        secret=SECRET,
        now=NOW + timedelta(seconds=tokens.CLOCK_SKEW_SECONDS + 5),
    )
    signed_with_previous = tokens.sign_emergency_page_token(
        cursor,
        endpoint=tokens.REQUEST_ENDPOINT,
        organization_id=ORG,
        location_id="",
        limit=100,
        secret=PREVIOUS,
        now=NOW,
    )
    assert tokens.read_emergency_page_token(
        signed_with_previous,
        endpoint=tokens.REQUEST_ENDPOINT,
        organization_id=ORG,
        location_id="",
        limit=100,
        secrets=[SECRET, PREVIOUS],
        now=NOW,
    )["request_id"] == "Q0"
    with pytest.raises(tokens.AccessError, match="Invalid page token"):
        tokens.read_emergency_page_token(
            future,
            endpoint=tokens.REQUEST_ENDPOINT,
            organization_id=ORG,
            location_id="",
            limit=100,
            secrets=[SECRET],
            now=NOW,
        )
    with pytest.raises(tokens.AccessError, match="Invalid page token"):
        tokens.read_emergency_page_token(
            expired,
            endpoint=tokens.REQUEST_ENDPOINT,
            organization_id=ORG,
            location_id="",
            limit=100,
            secrets=[SECRET],
            now=NOW,
        )
    for supplied in (valid + "x", "not-a-token", expired, future):
        result = allocation_service.lambda_handler(
            event("/requests", {"page_token": supplied}),
            None,
        )
        assert result["statusCode"] == 400
        assert body_of(result)["message"] == "Invalid page token"
        assert "secret" not in result["body"].lower()
        assert "organization_id" not in body_of(result)["message"]


def test_inactive_membership_is_still_denied(monkeypatch):
    use_member(monkeypatch, status="INACTIVE")
    install_requests(monkeypatch, requests_for(1))
    result = allocation_service.lambda_handler(event("/requests"), None)
    assert result["statusCode"] == 403


def test_missing_secret_does_not_describe_itself(monkeypatch):
    use_member(monkeypatch)

    def unavailable():
        from access import AccessError

        raise AccessError(500, "Unable to process request")

    monkeypatch.setattr(allocation_service, "load_emergency_page_secret", unavailable)
    install_requests(monkeypatch, requests_for(2))
    result = allocation_service.lambda_handler(event("/requests", {"limit": "1"}), None)
    assert result["statusCode"] == 500
    assert "page-token" not in result["body"]
    assert "Secret" not in result["body"]


def test_allocation_default_and_invalid_page_size(monkeypatch):
    use_member(monkeypatch)
    table = install_allocations(monkeypatch, allocations_for(1))
    body_of(allocation_service.lambda_handler(event("/allocate/allocations"), None))
    assert table.calls[0]["Limit"] == 100
    result = allocation_service.lambda_handler(
        event("/allocate/allocations", {"limit": "101", "page_token": "not-a-token"}),
        None,
    )
    assert result["statusCode"] == 400
    assert body_of(result)["message"] == "Page size is invalid"
    malformed = allocation_service.lambda_handler(
        event("/allocate/allocations", {"page_token": "not-a-token"}),
        None,
    )
    assert malformed["statusCode"] == 400
    assert body_of(malformed)["message"] == "Invalid page token"


def test_page_token_module_is_only_in_the_allocation_package():
    sys.path.insert(0, str(ROOT / "scripts"))
    from lambda_manifest import package_map

    packages = package_map()
    assert packages["emergency-resource-allocation"]["emergency_page_token.py"].endswith(
        "emergency_page_token.py"
    )
    for name, mapping in packages.items():
        if name == "emergency-resource-allocation":
            continue
        assert "emergency_page_token.py" not in mapping


def test_list_source_does_not_drain_the_partition():
    source = (ROOT / "src" / "allocation" / "service.py").read_text(encoding="utf-8")
    page = source[source.index("def _query_page"):source.index("def _list_response")]
    assert "LastEvaluatedKey" in page
    assert "while" not in page
    assert ".scan(" not in source
