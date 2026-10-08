"""RM-07: resource and allocation reads stay on existing tenant indexes."""

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [
    str(ROOT / "src" / "shared"),
    str(ROOT / "src" / "organization"),
    str(ROOT / "src"),
]


def load_module(name, relative):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


allocation_service = load_module("allocation_service_rm07", "src/allocation/service.py")
resource_handler = load_module("resource_handler_rm07", "src/resource/handler.py")
auto_release = load_module("auto_release_rm07", "src/auto_release/handler.py")

ORG = "ORG-A"
OTHER = "ORG-B"


def resource(resource_id, location_id, organization_id=ORG, available=True, status="AVAILABLE"):
    return {
        "resource_id": resource_id,
        "organization_id": organization_id,
        "location_id": location_id,
        "Type": "Kit",
        "Available": available,
        "operational_status": status,
        "tracking_mode": "INDIVIDUAL",
        "name": resource_id,
    }


def request(location_id="LOC-A", prefer_same=True):
    return {
        "request_id": "Q1",
        "organization_id": ORG,
        "location_id": location_id,
        "ResourceType": "Kit",
        "matching_config": {"same_location_preferred": prefer_same},
    }


def allocation(allocation_id, resource_id, location_id, organization_id=ORG, status="ALLOCATED", allocated_at="2026-10-08T00:00:00+00:00"):
    return {
        "allocation_id": allocation_id,
        "organization_id": organization_id,
        "resource_id": resource_id,
        "location_id": location_id,
        "request_id": "Q-" + allocation_id,
        "status": status,
        "allocated_at": allocated_at,
    }


def install_query(monkeypatch, module, rows):
    calls = []
    monkeypatch.setattr(module, "resources_table", lambda: object())
    monkeypatch.setattr(module, "allocations_table", lambda: object())

    def query(table, organization_id, location_id=None, index_name="OrganizationLocationIndex"):
        calls.append(
            {
                "organization_id": organization_id,
                "location_id": location_id,
                "index": index_name,
            }
        )
        matched = [dict(row) for row in rows if row.get("organization_id") == organization_id]
        if location_id:
            matched = [row for row in matched if row.get("location_id") == location_id]
        return matched

    monkeypatch.setattr(module, "query_by_organization", query)
    return calls


def test_resource_query_is_tenant_scoped(monkeypatch):
    rows = [resource("R1", "LOC-A"), resource("FOREIGN", "LOC-A", organization_id=OTHER)]
    calls = install_query(monkeypatch, allocation_service, rows)

    loaded = allocation_service._load_org_resources(ORG, "LOC-A")

    assert calls == [{"organization_id": ORG, "location_id": "LOC-A", "index": "OrganizationLocationIndex"}]
    assert [item["resource_id"] for item in loaded] == ["R1"]


def test_other_tenant_resource_is_not_selectable(monkeypatch):
    rows = [
        resource("FOREIGN", "LOC-A", organization_id=OTHER),
        resource("R-LOCAL", "LOC-A"),
    ]

    def leak(table, organization_id, location_id=None, index_name=None):
        return [dict(row) for row in rows]

    monkeypatch.setattr(allocation_service, "resources_table", lambda: object())
    monkeypatch.setattr(allocation_service, "query_by_organization", leak)
    loaded = allocation_service._resources_for_match(ORG, request(), {})

    assert [item["resource_id"] for item in loaded] == ["R-LOCAL"]
    assert allocation_service.choose_resource(loaded, request())["resource_id"] == "R-LOCAL"


def test_matching_reads_the_request_location_before_the_whole_organization(monkeypatch):
    rows = [
        resource("R-A", "LOC-OTHER"),
        resource("R-B", "LOC-A"),
    ]
    calls = install_query(monkeypatch, allocation_service, rows)

    pool = allocation_service._resources_for_match(ORG, request(), {})
    chosen = allocation_service.choose_resource(pool, request())

    assert calls == [{"organization_id": ORG, "location_id": "LOC-A", "index": "OrganizationLocationIndex"}]
    assert chosen["resource_id"] == "R-B"


def test_matching_falls_back_to_the_organization_and_keeps_selection_order(monkeypatch):
    rows = [
        resource("R-A", "LOC-OTHER"),
        resource("R-B", "LOC-A", available=False, status="ALLOCATED"),
    ]
    calls = install_query(monkeypatch, allocation_service, rows)

    pool = allocation_service._resources_for_match(ORG, request(), {})
    chosen = allocation_service.choose_resource(pool, request())

    assert [call["location_id"] for call in calls] == ["LOC-A", None]
    assert chosen["resource_id"] == "R-A"

    both = install_query(
        monkeypatch,
        allocation_service,
        [resource("R-A", "LOC-OTHER"), resource("R-B", "LOC-A")],
    )
    anywhere = request(prefer_same=False)
    pool = allocation_service._resources_for_match(ORG, anywhere, {})
    chosen = allocation_service.choose_resource(pool, anywhere)

    assert both == [{"organization_id": ORG, "location_id": None, "index": "OrganizationLocationIndex"}]
    assert chosen["resource_id"] == "R-A"


def test_release_uses_the_location_key_when_the_allocation_is_there(monkeypatch):
    rows = [
        allocation("A-OTHER", "R9", "LOC-OTHER"),
        allocation("A-LOCAL", "R1", "LOC-A", allocated_at="2026-10-08T02:00:00+00:00"),
        allocation("A-OLDER", "R1", "LOC-A", allocated_at="2026-10-08T01:00:00+00:00"),
        allocation("A-FOREIGN", "R1", "LOC-A", organization_id=OTHER),
    ]
    calls = install_query(monkeypatch, resource_handler, rows)

    found = resource_handler._allocated_for_resource(ORG, "R1", "LOC-A")

    assert calls == [{"organization_id": ORG, "location_id": "LOC-A", "index": "OrganizationLocationIndex"}]
    assert [item["allocation_id"] for item in found] == ["A-LOCAL", "A-OLDER"]


def test_release_falls_back_when_the_allocation_is_at_another_location(monkeypatch):
    rows = [allocation("A-MOVED", "R1", "LOC-OLD")]
    calls = install_query(monkeypatch, resource_handler, rows)

    found = resource_handler._allocated_for_resource(ORG, "R1", "LOC-A")

    assert [call["location_id"] for call in calls] == ["LOC-A", None]
    assert [item["allocation_id"] for item in found] == ["A-MOVED"]


def test_auto_release_still_uses_precise_reads():
    source = (ROOT / "src" / "auto_release" / "handler.py").read_text(encoding="utf-8")
    assert "AllocationStatusIndex" in source
    assert 'get_item(Key={"resource_id": resource_id})' in source
    assert 'get_item(Key={"request_id": request_id})' in source
    assert ".scan(" not in source
    decision = auto_release.plan_release(
        {"allocation_id": "A1", "status": "RELEASED", "organization_id": ORG, "resource_id": "R1", "request_id": "Q1"},
        {"resource_id": "R1", "organization_id": ORG},
        {"request_id": "Q1", "organization_id": ORG},
        [],
    )
    assert decision["action"] == "skip"
    assert decision["reason"] == "already released"


def test_release_still_commits_with_the_conditional_transaction():
    source = (ROOT / "src" / "resource" / "handler.py").read_text(encoding="utf-8")
    transaction = (ROOT / "src" / "shared" / "emergency_release.py").read_text(encoding="utf-8")
    assert "commit_emergency_release(" in source
    assert "TransactWriteItems" in transaction or "transact_write_items" in transaction
    assert "operational_status" in transaction


class ListTable:
    def __init__(self, items, token=None):
        self.items = items
        self.token = token
        self.queries = []
        self.scans = 0

    def query(self, **kwargs):
        self.queries.append(kwargs)
        return {"Items": list(self.items), "LastEvaluatedKey": self.token}

    def scan(self, **kwargs):
        self.scans += 1
        raise AssertionError("resource list must not scan")


def test_resource_pagination_stays_on_the_organization_index(monkeypatch):
    table = ListTable(
        [
            resource("R1", "LOC-A"),
            resource("FOREIGN", "LOC-A", organization_id=OTHER),
        ],
        token={"organization_id": ORG, "location_id": "LOC-A", "resource_id": "R1"},
    )
    monkeypatch.setattr(resource_handler, "resources_table", lambda: table)
    monkeypatch.setattr(
        resource_handler,
        "load_resource_page_secret",
        lambda client=None, secret_id=None: ("synthetic-resource-page-token-secret-v1", []),
    )

    result = resource_handler.list_resources({"queryStringParameters": {"limit": "10"}}, ORG)
    body = json.loads(result["body"])

    assert result["statusCode"] == 200
    assert table.scans == 0
    assert table.queries[0]["IndexName"] == "OrganizationLocationIndex"
    assert table.queries[0]["Limit"] == 10
    assert [item["resource_id"] for item in body["resources"]] == ["R1"]
    assert body["next_token"]
    assert "FOREIGN" not in result["body"]
    assert "internal_secret_field" not in result["body"]


def test_resource_list_allowlist_still_drops_unknown_fields(monkeypatch):
    item = resource("R1", "LOC-A")
    item["internal_secret_field"] = "should-never-leak"
    item["reserved_by"] = "cognito-sub"
    table = ListTable([item])
    monkeypatch.setattr(resource_handler, "resources_table", lambda: table)

    result = resource_handler.list_resources({"queryStringParameters": {}}, ORG)
    body = json.loads(result["body"])

    assert body[0]["resource_id"] == "R1"
    assert body[0]["Type"] == "Kit"
    assert "internal_secret_field" not in body[0]
    assert "reserved_by" not in body[0]
    assert "should-never-leak" not in result["body"]


def test_resource_management_reads_do_not_scan():
    paths = [
        "src/resource/handler.py",
        "src/allocation/service.py",
        "src/shared/matching.py",
        "src/shared/emergency_release.py",
        "src/auto_release/handler.py",
        "src/shared/everyday_operations.py",
        "src/public/handler.py",
        "src/shared/access.py",
    ]
    for relative in paths:
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert ".scan(" not in source


def test_bounded_queries_keep_the_organization_key():
    access_source = (ROOT / "src" / "shared" / "access.py").read_text(encoding="utf-8")
    assert 'Key("organization_id").eq(organization_id)' in access_source
    assert "LastEvaluatedKey" in access_source
    matching_source = (ROOT / "src" / "allocation" / "service.py").read_text(encoding="utf-8")
    release_source = (ROOT / "src" / "resource" / "handler.py").read_text(encoding="utf-8")
    assert "_load_org_resources(organization_id, location_id)" in matching_source
    assert "_load_org_resources(organization_id)" in matching_source
    assert "query_by_organization(allocations_table(), organization_id, location_id)" in release_source
    assert "query_by_organization(allocations_table(), organization_id)" in release_source
