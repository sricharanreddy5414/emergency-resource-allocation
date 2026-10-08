"""Resource Management responses copy an explicit field allowlist."""

import importlib.util
import json
import sys
from pathlib import Path

import access
from api_views import RESOURCE_FIELDS, RESOURCE_HISTORY_FIELDS, resource_history_view, resource_view


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [
    str(ROOT / "src" / "shared"),
    str(ROOT / "src" / "organization"),
]


def load_module(name, relative):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


resource_handler = load_module("resource_handler_rm06", "src/resource/handler.py")

ORG_A = "ORG-A"
ORG_B = "ORG-B"
USER = "user-rm06"
SECRET = "should-never-leak"
LEAKS = (
    "pk",
    "sk",
    "visibility_key",
    "discovery_key",
    "public_type_name",
    "public_city",
    "public_state",
    "public_status",
    "reserved_by",
    "reserved_at",
    "created_by",
    "user_sub",
    "actor_sub",
    "actor_role",
    "idempotency_key",
    "updated_at",
    "created_at",
    "quantity_total",
    "quantity_reserved",
    "quantity_allocated",
    "serial_number",
    "asset_tag",
    "department",
    "responsible_team",
    "assigned_to",
    "description",
    "condition",
    "internal_secret_field",
    "notes",
    "history_id",
)


def body_of(result):
    return json.loads(result["body"])


def event(method="GET", body=None, path="/allocate/resources", query=None):
    payload = {
        "httpMethod": method,
        "path": path,
        "queryStringParameters": {"organization_id": ORG_A, **(query or {})},
        "requestContext": {"authorizer": {"claims": {"sub": USER}}},
    }
    if body is not None:
        payload["body"] = json.dumps(body)
    return payload


def use_member(monkeypatch, role="MEMBER"):
    monkeypatch.setattr(
        access,
        "list_memberships",
        lambda *args, **kwargs: [
            {"organization_id": ORG_A, "name": ORG_A, "role": role, "status": "ACTIVE"}
        ],
    )


def resource_item(**extra):
    item = {
        "resource_id": "R1",
        "organization_id": ORG_A,
        "name": "North kit",
        "Type": "Kit",
        "resource_type_id": "TYPE1",
        "Location": "HQ",
        "location_id": "LOC1",
        "Available": True,
        "visibility": "PUBLIC",
        "attributes": {"capacity": 2},
        "operational_status": "AVAILABLE",
        "tracking_mode": "QUANTITY",
        "quantity_total": 10,
        "quantity_available": 6,
        "quantity_reserved": 1,
        "quantity_allocated": 3,
        "public_name": "Public kit",
        "public_description": "Listed",
        "public_contact": "desk",
        "show_availability": True,
        "visibility_key": "PUBLIC",
        "discovery_key": "KIT#CITY#R1",
        "public_type_name": "Kit",
        "public_city": "City",
        "public_state": "State",
        "public_status": "AVAILABLE",
        "reserved_by": "cognito-sub-should-not-leak",
        "reserved_at": "2026-10-08T00:00:00+00:00",
        "created_by": "cognito-sub-should-not-leak",
        "actor_sub": "cognito-sub-should-not-leak",
        "serial_number": "SN-SECRET",
        "asset_tag": "AT-1",
        "assigned_to": "member-1",
        "department": "Ops",
        "responsible_team": "Night",
        "description": "Internal note",
        "condition": "GOOD",
        "updated_at": "2026-10-08T00:00:00+00:00",
        "pk": "RESOURCE#R1",
        "sk": "META",
        "internal_secret_field": SECRET,
    }
    item.update(extra)
    return item


def assert_resource_contract(view):
    assert set(view) <= set(RESOURCE_FIELDS)
    for name in LEAKS:
        assert name not in view
    assert SECRET not in json.dumps(view)
    assert "cognito-sub-should-not-leak" not in json.dumps(view)
    assert view["resource_id"] == "R1"
    assert view["organization_id"] == ORG_A
    assert view["name"] == "North kit"
    assert view["Type"] == "Kit"
    assert view["resource_type_id"] == "TYPE1"
    assert view["Location"] == "HQ"
    assert view["location_id"] == "LOC1"
    assert view["Available"] is True
    assert view["visibility"] == "PUBLIC"
    assert view["attributes"] == {"capacity": 2}
    assert view["operational_status"] == "AVAILABLE"
    assert view["tracking_mode"] == "QUANTITY"
    assert view["quantity_available"] == 6
    assert view["public_name"] == "Public kit"
    assert view["public_description"] == "Listed"
    assert view["public_contact"] == "desk"
    assert view["show_availability"] is True


class QueryTable:
    def __init__(self, items):
        self.items = [dict(item) for item in items]
        self.scans = 0

    def query(self, **kwargs):
        return {"Items": [dict(item) for item in self.items]}

    def get_item(self, Key):
        for item in self.items:
            if item.get("resource_id") == Key.get("resource_id"):
                return {"Item": dict(item)}
        return {}

    def scan(self, **kwargs):
        self.scans += 1
        raise AssertionError("resource list must not scan")


class PutTable:
    def __init__(self):
        self.puts = []

    def put_item(self, Item, ConditionExpression=None):
        self.puts.append(dict(Item))


class UpdateTable:
    def __init__(self, item):
        self.item = dict(item)
        self.updates = []

    def get_item(self, Key):
        if self.item.get("resource_id") == Key.get("resource_id"):
            return {"Item": dict(self.item)}
        return {}

    def update_item(self, **kwargs):
        self.updates.append(kwargs)


class Lookup:
    def __init__(self, item):
        self.item = item

    def get_item(self, Key):
        return {"Item": dict(self.item)}


def test_resource_view_drops_unapproved_and_keeps_screen_fields():
    assert_resource_contract(resource_view(resource_item()))


def test_resource_list_contains_only_allowlisted_fields(monkeypatch):
    use_member(monkeypatch)
    table = QueryTable([resource_item(), resource_item(resource_id="R-OTHER", organization_id=ORG_B, name="Foreign")])
    monkeypatch.setattr(resource_handler, "resources_table", lambda: table)

    result = resource_handler.lambda_handler(event(), None)

    assert result["statusCode"] == 200
    listed = body_of(result)
    assert [item["resource_id"] for item in listed] == ["R1"]
    assert_resource_contract(listed[0])
    assert table.scans == 0
    assert ORG_B not in result["body"]
    assert "Foreign" not in result["body"]


def test_resource_detail_uses_the_same_allowlist(monkeypatch):
    """The edit form reads the listed resource. There is no separate detail route."""
    table = QueryTable([resource_item()])
    monkeypatch.setattr(resource_handler, "resources_table", lambda: table)

    result = resource_handler.list_resources({"queryStringParameters": {}}, ORG_A)

    listed = body_of(result)
    assert len(listed) == 1
    assert_resource_contract(listed[0])


def test_resource_list_page_projects_each_item(monkeypatch):
    table = QueryTable([resource_item()])
    monkeypatch.setattr(resource_handler, "resources_table", lambda: table)

    result = resource_handler.list_resources(
        {"queryStringParameters": {"limit": "10"}},
        ORG_A,
    )

    payload = body_of(result)
    assert set(payload) == {"resources", "next_token"}
    assert_resource_contract(payload["resources"][0])


def test_create_response_contains_only_allowlisted_fields(monkeypatch):
    resources = PutTable()
    monkeypatch.setattr(resource_handler, "resources_table", lambda: resources)
    monkeypatch.setattr(resource_handler, "audit_table", lambda: PutTable())
    monkeypatch.setattr(
        resource_handler,
        "locations_table",
        lambda: Lookup(
            {
                "organization_id": ORG_A,
                "location_id": "LOC1",
                "name": "HQ",
                "status": "ACTIVE",
                "city": "City",
                "state": "State",
            }
        ),
    )
    monkeypatch.setattr(
        resource_handler,
        "resource_types_table",
        lambda: Lookup(
            {
                "organization_id": ORG_A,
                "resource_type_id": "TYPE1",
                "name": "Kit",
                "status": "ACTIVE",
                "attributes_schema": {"fields": []},
            }
        ),
    )

    result = resource_handler.register_resource(
        {
            "resource_id": "R-NEW",
            "resource_type_id": "TYPE1",
            "location_id": "LOC1",
            "name": "Created",
            "visibility": "PUBLIC",
            "public_name": "Created",
            "public_description": "Shown",
            "public_contact": "desk",
            "show_availability": True,
            "attributes": {},
            "reserved_by": "cognito-sub-should-not-leak",
            "actor_sub": "cognito-sub-should-not-leak",
            "internal_secret_field": SECRET,
            "organization_id": ORG_B,
        },
        ORG_A,
        USER,
        "OPERATOR",
    )

    assert result["statusCode"] == 201
    created = body_of(result)["resource"]
    stored = resources.puts[0]
    assert stored["organization_id"] == ORG_A
    assert stored["visibility_key"] == "PUBLIC"
    assert set(created) <= set(RESOURCE_FIELDS)
    assert "visibility_key" not in created
    assert "discovery_key" not in created
    assert "reserved_by" not in created
    assert "internal_secret_field" not in created
    assert SECRET not in json.dumps(created)
    assert created["resource_id"] == "R-NEW"
    assert created["organization_id"] == ORG_A
    assert created["name"] == "Created"
    assert created["Type"] == "Kit"
    assert created["location_id"] == "LOC1"
    assert created["visibility"] == "PUBLIC"
    assert created["public_name"] == "Created"
    assert created["show_availability"] is True
    assert created["operational_status"] == "AVAILABLE"
    assert created["tracking_mode"] == "INDIVIDUAL"
    assert created["Available"] is True


def test_update_response_contains_only_allowlisted_fields(monkeypatch):
    current = resource_item(visibility="PRIVATE", name="Old")
    table = UpdateTable(current)
    monkeypatch.setattr(resource_handler, "resources_table", lambda: table)
    monkeypatch.setattr(resource_handler, "audit_table", lambda: PutTable())
    monkeypatch.setattr(
        resource_handler,
        "locations_table",
        lambda: Lookup(
            {
                "organization_id": ORG_A,
                "location_id": "LOC1",
                "name": "HQ",
                "status": "ACTIVE",
                "city": "City",
                "state": "State",
            }
        ),
    )
    monkeypatch.setattr(
        resource_handler,
        "resource_types_table",
        lambda: Lookup(
            {
                "organization_id": ORG_A,
                "resource_type_id": "TYPE1",
                "name": "Kit",
                "status": "ACTIVE",
                "attributes_schema": {"fields": []},
            }
        ),
    )

    result = resource_handler.update_resource(
        {
            "resource_id": "R1",
            "name": "Renamed",
            "visibility": "PUBLIC",
            "public_name": "Renamed",
            "public_description": "Shown",
            "public_contact": "desk",
            "show_availability": True,
            "internal_secret_field": SECRET,
        },
        ORG_A,
        USER,
        "OPERATOR",
    )

    assert result["statusCode"] == 200
    updated = body_of(result)["resource"]
    assert set(updated) <= set(RESOURCE_FIELDS)
    assert updated["resource_id"] == "R1"
    assert updated["name"] == "Renamed"
    assert updated["visibility"] == "PUBLIC"
    assert updated["public_name"] == "Renamed"
    assert "visibility_key" not in updated
    assert "discovery_key" not in updated
    assert "reserved_by" not in updated
    assert "internal_secret_field" not in updated
    assert SECRET not in json.dumps(updated)
    written = json.dumps(table.updates[0]["ExpressionAttributeNames"])
    assert "visibility_key" in written
    assert table.item["reserved_by"] == "cognito-sub-should-not-leak"
    assert table.item["internal_secret_field"] == SECRET


def test_history_response_uses_its_own_allowlist(monkeypatch):
    history = {
        "history_id": "HIST-1",
        "resource_id": "R1",
        "organization_id": ORG_A,
        "location_id": "LOC1",
        "resource_type": "Kit",
        "location": "HQ",
        "previous_status": "AVAILABLE",
        "new_status": "RESERVED",
        "changed_at": "2026-10-08T00:00:00+00:00",
        "reason": "RESOURCE_RESERVED",
        "request_id": "Q1",
        "allocation_id": "ALLOC-1",
        "notes": "private note",
        "actor_sub": "cognito-sub-should-not-leak",
        "pk": "HISTORY#HIST-1",
        "sk": "META",
        "internal_secret_field": SECRET,
    }
    monkeypatch.setattr(resource_handler, "resources_table", lambda: QueryTable([resource_item()]))
    monkeypatch.setattr(resource_handler, "history_table", lambda: QueryTable([history]))

    result = resource_handler.resource_history(event(path="/allocate/resources/history", query={"resource_id": "R1"}), ORG_A)

    assert result["statusCode"] == 200
    rows = body_of(result)
    assert len(rows) == 1
    assert set(rows[0]) <= set(RESOURCE_HISTORY_FIELDS)
    assert rows[0] == {
        "previous_status": "AVAILABLE",
        "new_status": "RESERVED",
        "changed_at": "2026-10-08T00:00:00+00:00",
        "reason": "RESOURCE_RESERVED",
        "request_id": "Q1",
        "allocation_id": "ALLOC-1",
    }
    assert SECRET not in result["body"]
    assert "cognito-sub-should-not-leak" not in result["body"]
    assert "private note" not in result["body"]
    assert resource_history_view({"previous_status": "AVAILABLE"}) == {"previous_status": "AVAILABLE"}


def test_unknown_future_field_does_not_appear():
    viewed = resource_view({"resource_id": "R1", "internal_secret_field": SECRET, "future_field": "nope"})
    assert viewed == {"resource_id": "R1"}
    assert "internal_secret_field" not in viewed


def test_public_discovery_module_is_unchanged():
    source = (ROOT / "src" / "public" / "handler.py").read_text(encoding="utf-8")
    assert "resource_view" not in source
    assert "def public_view" in source
