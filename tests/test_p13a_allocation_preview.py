"""Phase 13A: emergency allocation preview is read-only and confirmation rechecks the match."""

import copy
import importlib.util
import inspect
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
from transact_memory import apply_transact


def load_module(name, relative):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


allocation = load_module("allocation_service_p13a", "src/allocation/service.py")

ORG = "ORG-A"
OTHER = "ORG-B"
USER = "operator-sub"


def use_memberships(monkeypatch, role="OPERATOR", status="ACTIVE"):
    monkeypatch.setattr(
        access,
        "list_memberships",
        lambda *args, **kwargs: [
            {
                "organization_id": ORG,
                "name": ORG,
                "role": role,
                "status": status,
            }
        ],
    )


class Store:
    def __init__(self, items=None):
        self.items = [dict(item) for item in (items or [])]
        self.puts = []

    def query(self, **kwargs):
        return {"Items": [dict(item) for item in self.items]}

    def get_item(self, Key):
        for item in self.items:
            if all(item.get(key) == value for key, value in Key.items()):
                return {"Item": dict(item)}
        return {}

    def put_item(self, Item, ConditionExpression=None):
        self.puts.append(dict(Item))
        self.items.append(dict(Item))


class Events:
    def __init__(self):
        self.calls = []

    def put_events(self, **kwargs):
        self.calls.append(kwargs)
        return {}


def location(location_id="LOC-A", name="North"):
    return {
        "organization_id": ORG,
        "location_id": location_id,
        "name": name,
        "status": "ACTIVE",
    }


def request_row(request_id="Q-A", status="PENDING", organization_id=ORG, location_id="LOC-A"):
    return {
        "request_id": request_id,
        "organization_id": organization_id,
        "location_id": location_id,
        "Status": status,
        "ResourceType": "ICU_BED",
        "Priority": 1,
    }


def resource_row(
    resource_id="R1",
    organization_id=ORG,
    location_id="LOC-A",
    name="Ward bed",
    available=True,
    resource_type="ICU_BED",
):
    return {
        "resource_id": resource_id,
        "organization_id": organization_id,
        "location_id": location_id,
        "name": name,
        "Type": resource_type,
        "Available": available,
        "operational_status": "AVAILABLE" if available else "ALLOCATED",
        "tracking_mode": "INDIVIDUAL",
    }


def event(body):
    return {
        "httpMethod": "POST",
        "path": "/allocate",
        "queryStringParameters": {"organization_id": ORG},
        "requestContext": {"authorizer": {"claims": {"sub": USER}}},
        "body": json.dumps(body),
    }


def body(request_id="Q-A", **extra):
    payload = {
        "request_id": request_id,
        "resource_type": "ICU_BED",
        "location_id": "LOC-A",
        "priority": 1,
        "organization_id": ORG,
    }
    payload.update(extra)
    return payload


class World:
    def __init__(self, resources, requests, locations=None):
        self.resources = Store(resources)
        self.requests = Store(requests)
        self.allocations = Store()
        self.history = Store()
        self.audit = Store()
        self.events = Events()
        self.transact_calls = []
        self.locations = locations or [location()]

    def bind(self, monkeypatch, role="OPERATOR", status="ACTIVE"):
        use_memberships(monkeypatch, role, status)
        locations = self.locations
        monkeypatch.setattr(allocation, "locations_table", lambda: Store(locations))
        monkeypatch.setattr(allocation, "resources_table", lambda: self.resources)
        monkeypatch.setattr(allocation, "requests_table", lambda: self.requests)
        monkeypatch.setattr(allocation, "allocations_table", lambda: self.allocations)
        monkeypatch.setattr(allocation, "history_table", lambda: self.history)
        monkeypatch.setattr(allocation, "audit_table", lambda: self.audit)
        monkeypatch.setattr(allocation, "events_client", lambda: self.events)
        monkeypatch.setattr(allocation, "request_types_table", lambda: Store())

        def _transact(items):
            self.transact_calls.append(copy.deepcopy(items))
            apply_transact(
                {
                    "Resources": self.resources.items,
                    "Allocations": self.allocations.items,
                    "EmergencyRequests": self.requests.items,
                },
                items,
            )

        monkeypatch.setattr(allocation, "_transact_write", _transact)

    def post(self, payload):
        result = allocation.lambda_handler(event(payload), None)
        return result["statusCode"], json.loads(result["body"])

    def snapshot(self):
        return {
            "resources": copy.deepcopy(self.resources.items),
            "requests": copy.deepcopy(self.requests.items),
            "allocations": copy.deepcopy(self.allocations.items),
            "history": copy.deepcopy(self.history.puts),
            "audit": copy.deepcopy(self.audit.puts),
            "events": copy.deepcopy(self.events.calls),
            "transact": copy.deepcopy(self.transact_calls),
        }


def test_preview_selects_the_existing_matcher_candidate(monkeypatch):
    same_location = World(
        [resource_row("R2", name="Later bed"), resource_row("R1", name="Earlier bed")],
        [request_row()],
    )
    same_location.bind(monkeypatch)
    status, payload = same_location.post(body(preview=True))
    assert status == 200
    assert payload["preview"] is True
    assert payload["resource_id"] == "R1"
    assert payload["name"] == "Earlier bed"
    assert payload["resource_type"] == "ICU_BED"
    assert payload["location_id"] == "LOC-A"
    assert payload["location"] == "North"
    assert payload["available"] is True
    assert "same location" in payload["match"]

    preferred = World(
        [
            resource_row("R1", location_id="LOC-B", name="Other ward"),
            resource_row("R9", name="Same ward"),
        ],
        [request_row()],
        [location(), location("LOC-B", "South")],
    )
    preferred.bind(monkeypatch)
    status, payload = preferred.post(body(preview=True))
    assert status == 200
    assert payload["resource_id"] == "R9"
    assert payload["location"] == "North"
    assert "Other ward" not in json.dumps(payload)


def test_preview_does_not_mutate_records_or_counters(monkeypatch):
    world = World(
        [resource_row("R2"), resource_row("R1")],
        [request_row(), request_row("Q-B")],
    )
    world.bind(monkeypatch)
    before = world.snapshot()

    status, payload = world.post(body(preview=True, organization_id=OTHER, available=False))

    assert status == 200
    assert payload["resource_id"] == "R1"
    assert payload["available"] is True
    assert OTHER not in json.dumps(payload)
    assert world.snapshot() == before


def test_confirmation_claims_the_previewed_resource(monkeypatch):
    world = World([resource_row("R2"), resource_row("R1")], [request_row()])
    world.bind(monkeypatch)

    preview_status, preview = world.post(body(preview=True))
    status, payload = world.post(body(confirm=True, resource_id=preview["resource_id"]))

    assert preview_status == 200
    assert status == 200
    assert payload["status"] == "ALLOCATED"
    assert payload["resource_id"] == "R1"
    assert payload["allocation_id"] == "ALLOC-Q-A"
    assert world.requests.items[0]["Status"] == "ALLOCATED"
    assert world.resources.items[1]["operational_status"] == "ALLOCATED"
    assert world.resources.items[1]["Available"] is False
    assert world.resources.items[0]["Available"] is True
    assert [item["allocation_id"] for item in world.allocations.items] == ["ALLOC-Q-A"]
    assert len(world.transact_calls) == 1
    assert world.history.puts[0]["reason"] == "RESOURCE_ALLOCATED"
    assert world.audit.puts
    assert world.events.calls


def test_confirmation_rejects_a_resource_taken_by_a_competing_allocation(monkeypatch):
    world = World(
        [resource_row("R1"), resource_row("R2", name="Second bed")],
        [request_row("Q-A"), request_row("Q-B")],
    )
    world.bind(monkeypatch)

    first, first_body = world.post(body("Q-A", confirm=True, resource_id="R1"))
    second, second_body = world.post(body("Q-B", confirm=True, resource_id="R1"))

    assert first == 200
    assert first_body["resource_id"] == "R1"
    assert second == 409
    assert second_body["message"] == "The proposed resource is no longer available. Preview the match again."
    assert [item["request_id"] for item in world.allocations.items] == ["Q-A"]
    assert world.requests.items[0]["Status"] == "ALLOCATED"
    assert world.requests.items[1]["Status"] == "PENDING"
    assert world.resources.items[0]["operational_status"] == "ALLOCATED"
    assert world.resources.items[1]["Available"] is True
    assert world.resources.items[1]["operational_status"] == "AVAILABLE"
    assert len(world.transact_calls) == 1


def test_foreign_resource_id_cannot_bypass_tenant_isolation(monkeypatch):
    mixed = World(
        [resource_row("R1"), resource_row("F1", organization_id=OTHER, name="Foreign bed")],
        [request_row()],
    )
    mixed.bind(monkeypatch)
    before = mixed.snapshot()
    status, payload = mixed.post(body(confirm=True, resource_id="F1", organization_id=OTHER))
    assert status == 409
    assert payload["message"] == "The proposed resource is no longer available. Preview the match again."
    assert mixed.snapshot() == before

    foreign_only = World(
        [resource_row("F1", organization_id=OTHER, name="Foreign bed")],
        [request_row()],
    )
    foreign_only.bind(monkeypatch)
    before = foreign_only.snapshot()
    status, payload = foreign_only.post(body(confirm=True, resource_id="F1"))
    assert status == 404
    assert payload["message"] == "No suitable resource available"
    assert foreign_only.snapshot() == before


def test_other_organization_request_is_rejected(monkeypatch):
    world = World([resource_row()], [request_row(organization_id=OTHER)])
    world.bind(monkeypatch)
    before = world.snapshot()

    preview_status, preview = world.post(body(preview=True))
    confirm_status, confirm = world.post(body(confirm=True, resource_id="R1"))

    assert preview_status == 404
    assert preview["message"] == "Record not found"
    assert confirm_status == 404
    assert confirm["message"] == "Record not found"
    assert world.snapshot() == before


@pytest.mark.parametrize("status", ["SUSPENDED", "ARCHIVED", "INACTIVE"])
@pytest.mark.parametrize("mode", ["preview", "confirm"])
def test_inactive_membership_or_organization_is_denied(monkeypatch, status, mode):
    world = World([resource_row()], [request_row()])
    world.bind(monkeypatch, status=status)
    before = world.snapshot()
    payload = body(preview=True) if mode == "preview" else body(confirm=True, resource_id="R1")

    code, result = world.post(payload)

    assert code == 403
    assert result["message"] == "Organization access denied"
    assert world.snapshot() == before


@pytest.mark.parametrize("mode", ["preview", "confirm"])
def test_member_role_cannot_preview_or_confirm(monkeypatch, mode):
    world = World([resource_row()], [request_row()])
    world.bind(monkeypatch, role="MEMBER")
    before = world.snapshot()
    payload = body(preview=True) if mode == "preview" else body(confirm=True, resource_id="R1")

    code, result = world.post(payload)

    assert code == 403
    assert result["message"] == "You are not allowed to perform this action"
    assert world.snapshot() == before


def test_resource_unavailable_between_preview_and_confirmation_is_rejected(monkeypatch):
    world = World([resource_row(available=False)], [request_row()])
    seen = copy.deepcopy(world.resources.items)
    world.resources.query = lambda **kwargs: {"Items": [dict(resource_row(available=True))]}
    world.bind(monkeypatch)

    status, payload = world.post(body(confirm=True, resource_id="R1"))

    assert status == 404
    assert payload["message"] == "No suitable resource available"
    assert world.resources.items == seen
    assert world.allocations.items == []
    assert world.requests.items[0]["Status"] == "PENDING"
    assert world.history.puts == []
    assert world.audit.puts == []
    assert world.events.calls == []
    assert len(world.transact_calls) == 1


def test_no_eligible_resource_is_a_safe_response(monkeypatch):
    world = World([resource_row(resource_type="GENERAL_BED")], [request_row()])
    world.bind(monkeypatch)
    before = world.snapshot()

    status, payload = world.post(body(preview=True))

    assert status == 404
    assert payload["message"] == "No suitable resource available"
    assert "resource_id" not in payload
    assert world.snapshot() == before


def test_duplicate_confirmation_does_not_create_a_second_allocation(monkeypatch):
    world = World([resource_row("R1"), resource_row("R2")], [request_row()])
    world.bind(monkeypatch)

    first, first_body = world.post(body(confirm=True, resource_id="R1"))
    second, second_body = world.post(body(confirm=True, resource_id="R1"))

    assert first == 200
    assert first_body["allocation_id"] == "ALLOC-Q-A"
    assert second == 409
    assert second_body["message"] == "Request is not eligible for allocation"
    assert [item["allocation_id"] for item in world.allocations.items] == ["ALLOC-Q-A"]
    assert len(world.transact_calls) == 1
    assert world.resources.items[1]["Available"] is True


def test_unconfirmed_post_and_cancel_do_not_allocate(monkeypatch):
    world = World([resource_row()], [request_row()])
    world.bind(monkeypatch)
    before = world.snapshot()

    missing, missing_body = world.post(body())
    text, text_body = world.post(body(confirm="true", resource_id="R1"))
    both, both_body = world.post(body(preview=True, confirm=True, resource_id="R1"))

    assert missing == 200
    assert missing_body["preview"] is True
    assert "allocation_id" not in missing_body
    assert text == 200
    assert text_body["preview"] is True
    assert both == 400
    assert both_body["message"] == "Choose either a preview or a confirmation"
    assert world.snapshot() == before

    source = inspect.getsource(allocation.allocate)
    assert source.index("preview") < source.index("_commit_emergency_claim")


def test_frontend_preview_confirmation_states():
    script = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
    page = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    styles = (ROOT / "frontend" / "style.css").read_text(encoding="utf-8")

    cancel = script[script.index("function cancelAllocationPreview(") : script.index("async function postAllocation(")]
    confirm = script[script.index("async function confirmAllocation(") : script.index("async function submitAllocation(")]
    preview = script[script.index("async function submitAllocation(") : script.index("function getApiMessage(")]
    render = script[script.index("function renderAllocationPreview(") : script.index("function cancelAllocationPreview(")]

    assert "fetch(" not in cancel
    assert "API_URL" not in cancel
    assert "clearAllocationPreview" in cancel
    assert "window.confirm" not in preview
    assert "preview: true" in preview
    assert "Finding match..." in preview
    assert "No matching resource is available." in preview
    assert "confirm: true" in confirm
    assert "Allocating..." in confirm
    assert "The resource is no longer available. Preview the match again." in confirm
    assert "loadRequests()" in confirm
    assert "loadAllocations()" in confirm
    assert "loadResources()" in confirm
    assert 'result.status !== "ALLOCATED"' in confirm
    assert "escapeHtml" in render
    assert "allocationPreviewResourceId" in render
    assert "setAllocationBusy" in preview
    assert "setAllocationBusy" in confirm
    assert "Preview match" in page
    assert "Confirm allocation" in page
    assert "Cancel" in page
    assert 'id="allocationPreview"' in page
    assert "allocation-preview" in styles
    assert "prefers-reduced-motion" in styles
