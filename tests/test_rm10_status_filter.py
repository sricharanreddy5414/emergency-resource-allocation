"""RM-10: resource list filters on operational_status."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

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


resource_handler = load_module("resource_handler_rm10", "src/resource/handler.py")
resource_state = load_module("resource_state_rm10", "src/shared/resource_state.py")
resource_tokens = load_module("resource_page_token_rm10", "src/shared/resource_page_token.py")

PAGE_SECRET = "synthetic-resource-page-token-secret-v1"

ORG = "ORG-A"
OTHER = "ORG-B"
STATUSES = (
    "AVAILABLE",
    "RESERVED",
    "ALLOCATED",
    "IN_USE",
    "MAINTENANCE",
    "DAMAGED",
    "RETIRED",
)


def resource(resource_id, status, available=None, **extra):
    if available is None:
        available = status == "AVAILABLE"
    row = {
        "resource_id": resource_id,
        "organization_id": ORG,
        "location_id": "LOC-A",
        "name": resource_id,
        "Type": "Kit",
        "resource_type_id": "TYPE1",
        "Available": available,
        "operational_status": status,
        "tracking_mode": "INDIVIDUAL",
        "visibility": "PRIVATE",
    }
    row.update(extra)
    return row


class ListTable:
    def __init__(self, items):
        self.items = list(items)
        self.queries = []

    def query(self, **kwargs):
        self.queries.append(kwargs)
        rows = list(self.items)
        for name, value in _condition_pairs(kwargs["KeyConditionExpression"]):
            if name in {"organization_id", "location_id"}:
                rows = [row for row in rows if row.get(name) == value]
        start = kwargs.get("ExclusiveStartKey")
        if start:
            ids = [row["resource_id"] for row in rows]
            cursor = start["resource_id"]
            rows = rows[ids.index(cursor) + 1 :] if cursor in ids else []
        limit = kwargs["Limit"]
        page = rows[:limit]
        last = None
        if len(rows) > limit and page:
            last = {
                "organization_id": page[-1]["organization_id"],
                "location_id": page[-1]["location_id"],
                "resource_id": page[-1]["resource_id"],
            }
        return {"Items": page, "LastEvaluatedKey": last}


def listed(monkeypatch, items, query):
    table = ListTable(items)
    monkeypatch.setattr(resource_handler, "resources_table", lambda: table)
    monkeypatch.setattr(resource_handler, "locations_table", lambda: object())
    monkeypatch.setattr(resource_handler, "require_location", lambda *_args: None)
    monkeypatch.setattr(
        resource_handler,
        "load_resource_page_secret",
        lambda client=None, secret_id=None: (PAGE_SECRET, []),
    )
    result = resource_handler.list_resources({"queryStringParameters": query}, ORG)
    return result, json.loads(result["body"]), table


def ids_of(body):
    rows = body["resources"] if isinstance(body, dict) else body
    return [item["resource_id"] for item in rows]


def catalog():
    return [resource(f"R-{status}", status) for status in STATUSES]


def test_missing_status_keeps_the_full_list(monkeypatch):
    result, body, table = listed(monkeypatch, catalog(), {})
    assert result["statusCode"] == 200
    assert ids_of(body) == [f"R-{status}" for status in STATUSES]
    assert "FilterExpression" not in table.queries[0]
    assert table.queries[0]["IndexName"] == "OrganizationLocationIndex"


@pytest.mark.parametrize("status", STATUSES)
def test_status_filter_uses_operational_status(monkeypatch, status):
    rows = catalog()
    rows.append(resource("R-FLIPPED", status, available=status != "AVAILABLE"))
    result, body, _table = listed(monkeypatch, rows, {"status": status.lower()})
    assert result["statusCode"] == 200
    assert ids_of(body) == [f"R-{status}", "R-FLIPPED"]
    assert all(item["operational_status"] == status for item in body)


def test_invalid_status_is_rejected_before_query(monkeypatch):
    result, body, table = listed(monkeypatch, catalog(), {"status": "INVALID"})
    assert result["statusCode"] == 400
    assert body["message"] == "Status is invalid"
    assert table.queries == []


def test_comma_separated_status_is_not_accepted(monkeypatch):
    result, _body, table = listed(monkeypatch, catalog(), {"status": "AVAILABLE,RESERVED"})
    assert result["statusCode"] == 400
    assert table.queries == []


def test_operational_status_query_parameter_is_not_a_second_filter(monkeypatch):
    result, body, _table = listed(monkeypatch, catalog(), {"operational_status": "RETIRED"})
    assert result["statusCode"] == 200
    assert len(ids_of(body)) == len(STATUSES)


def test_valid_status_with_no_matches_is_an_empty_list(monkeypatch):
    result, body, _table = listed(monkeypatch, [resource("R1", "AVAILABLE")], {"status": "RETIRED"})
    assert result["statusCode"] == 200
    assert body == []


def test_location_type_visibility_and_status_combine(monkeypatch):
    rows = [
        resource("R-HIT", "MAINTENANCE", location_id="LOC-1", resource_type_id="TYPE-9", visibility="NETWORK"),
        resource("R-OTHER-LOC", "MAINTENANCE", location_id="LOC-2", resource_type_id="TYPE-9", visibility="NETWORK"),
        resource("R-OTHER-TYPE", "MAINTENANCE", location_id="LOC-1", resource_type_id="TYPE-1", visibility="NETWORK"),
        resource("R-PRIVATE", "MAINTENANCE", location_id="LOC-1", resource_type_id="TYPE-9", visibility="PRIVATE"),
        resource("R-AVAILABLE", "AVAILABLE", location_id="LOC-1", resource_type_id="TYPE-9", visibility="NETWORK"),
        resource("R-FOREIGN", "MAINTENANCE", organization_id=OTHER, location_id="LOC-1", resource_type_id="TYPE-9", visibility="NETWORK"),
    ]
    result, body, table = listed(
        monkeypatch,
        rows,
        {
            "location_id": "LOC-1",
            "resource_type_id": "TYPE-9",
            "visibility": "NETWORK",
            "status": "MAINTENANCE",
        },
    )
    assert result["statusCode"] == 200
    assert ids_of(body) == ["R-HIT"]
    pairs = _condition_pairs(table.queries[0]["KeyConditionExpression"])
    assert ("organization_id", ORG) in pairs
    assert ("location_id", "LOC-1") in pairs


def test_quantity_pool_matches_available_status_when_flag_is_false(monkeypatch):
    rows = [
        resource(
            "POOL",
            "AVAILABLE",
            available=False,
            tracking_mode="QUANTITY",
            quantity_available=6,
        ),
        resource("HELD", "RESERVED", available=True),
    ]
    result, body, _table = listed(monkeypatch, rows, {"status": "AVAILABLE"})
    assert result["statusCode"] == 200
    assert ids_of(body) == ["POOL"]
    assert body[0]["Available"] is False
    assert body[0]["operational_status"] == "AVAILABLE"
    assert body[0]["quantity_available"] == 6


def test_available_flag_does_not_decide_the_status_filter(monkeypatch):
    rows = [
        resource("FLAG-TRUE", "MAINTENANCE", available=True),
        resource("FLAG-FALSE", "AVAILABLE", available=False),
        resource("LEGACY", "AVAILABLE", available=True, operational_status=""),
    ]
    rows[-1].pop("operational_status")
    result, body, _table = listed(monkeypatch, rows, {"status": "AVAILABLE"})
    assert ids_of(body) == ["FLAG-FALSE"]


def test_status_pages_stay_on_one_filter(monkeypatch):
    rows = [
        resource("R1", "AVAILABLE"),
        resource("R2", "MAINTENANCE"),
        resource("R3", "AVAILABLE"),
    ]
    first, body, table = listed(monkeypatch, rows, {"status": "AVAILABLE", "limit": "2"})
    assert first["statusCode"] == 200
    assert ids_of(body) == ["R1"]
    assert "ExclusiveStartKey" not in table.queries[0]
    token = body["next_token"]
    cursor = resource_tokens.read_resource_page_token(
        token,
        organization_id=ORG,
        location_id="",
        status="AVAILABLE",
        limit=2,
        secrets=[PAGE_SECRET],
    )
    assert cursor["resource_id"] == "R2"
    assert "status" not in cursor

    second, next_body, table = listed(
        monkeypatch,
        rows,
        {"status": "AVAILABLE", "limit": "2", "page_token": token},
    )
    assert second["statusCode"] == 200
    assert ids_of(next_body) == ["R3"]
    assert table.queries[0]["ExclusiveStartKey"] == {
        "organization_id": ORG,
        "location_id": "LOC-A",
        "resource_id": "R2",
    }


def test_page_token_cannot_cross_status_filters(monkeypatch):
    rows = [resource("R1", "AVAILABLE"), resource("R2", "MAINTENANCE"), resource("R3", "AVAILABLE")]
    _first, body, _table = listed(monkeypatch, rows, {"status": "AVAILABLE", "limit": "2"})
    result, payload, table = listed(
        monkeypatch,
        rows,
        {"status": "MAINTENANCE", "limit": "1", "page_token": body["next_token"]},
    )
    assert result["statusCode"] == 400
    assert payload["message"] == "Invalid page token"
    assert table.queries == []


def test_unfiltered_token_cannot_continue_a_status_query(monkeypatch):
    from pages import encode_token

    token = encode_token(
        {"organization_id": ORG, "location_id": "LOC-A", "resource_id": "R1"}
    )
    result, _body, table = listed(
        monkeypatch,
        catalog(),
        {"status": "AVAILABLE", "page_token": token},
    )
    assert result["statusCode"] == 400
    assert table.queries == []


def test_status_response_stays_on_the_resource_allowlist(monkeypatch):
    from api_views import RESOURCE_FIELDS

    row = resource("R1", "RESERVED", available=False, reserved_by="cognito-sub", visibility_key="secret")
    row["internal_secret_field"] = "should-never-leak"
    result, body, _table = listed(monkeypatch, [row], {"status": "RESERVED"})
    assert result["statusCode"] == 200
    assert set(body[0]) <= set(RESOURCE_FIELDS)
    assert "reserved_by" not in body[0]
    assert "visibility_key" not in body[0]
    assert "should-never-leak" not in result["body"]


def test_another_tenant_is_not_listed_or_pageable(monkeypatch):
    rows = [
        resource("LOCAL", "DAMAGED"),
        resource("FOREIGN", "DAMAGED", organization_id=OTHER),
    ]
    result, body, _table = listed(monkeypatch, rows, {"status": "DAMAGED"})
    assert ids_of(body) == ["LOCAL"]
    assert "FOREIGN" not in result["body"]

    token = resource_tokens.sign_resource_page_token(
        {
            "organization_id": OTHER,
            "location_id": "LOC-A",
            "resource_id": "FOREIGN",
        },
        organization_id=OTHER,
        location_id="",
        status="DAMAGED",
        limit=100,
        secret=PAGE_SECRET,
    )
    rejected, payload, table = listed(
        monkeypatch,
        rows,
        {"status": "DAMAGED", "page_token": token},
    )
    assert rejected["statusCode"] == 400
    assert payload["message"] == "Invalid page token"
    assert table.queries == []


def test_lifecycle_reservation_and_emergency_rules_stay_put():
    resource_state.validate_transition("AVAILABLE", "RESERVED")
    with pytest.raises(resource_state.ResourceStateError):
        resource_state.validate_transition("RETIRED", "AVAILABLE")

    release = (ROOT / "src" / "shared" / "everyday_operations.py").read_text(encoding="utf-8")
    assert "reserved_by = :actor" in release

    assert resource_state.emergency_matchable(
        {"Available": True, "operational_status": "AVAILABLE", "tracking_mode": "INDIVIDUAL"}
    )
    assert not resource_state.emergency_matchable(
        {"Available": False, "operational_status": "AVAILABLE", "tracking_mode": "QUANTITY"}
    )
    assert not resource_state.emergency_matchable(
        {"Available": True, "operational_status": "MAINTENANCE", "tracking_mode": "INDIVIDUAL"}
    )

    public = (ROOT / "src" / "public" / "handler.py").read_text(encoding="utf-8")
    listing = (ROOT / "src" / "resource" / "handler.py").read_text(encoding="utf-8")
    assert 'query.get("status")' not in public
    assert 'status == "AVAILABLE" and not is_available' not in listing
    assert "StatusIndex" not in listing


def _condition_pairs(expression):
    values = getattr(expression, "_values", ())
    name = getattr(values[0], "name", None) if values else None
    if name and len(values) == 2 and not hasattr(values[1], "_values"):
        return [(name, values[1])]
    found = []
    for value in values:
        if hasattr(value, "_values"):
            found.extend(_condition_pairs(value))
    return found
