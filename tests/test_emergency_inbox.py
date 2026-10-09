"""Emergency request and allocation inbox events."""

import importlib.util
import sys
from pathlib import Path

from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src" / "shared"), str(ROOT / "src" / "organization")]

import emergency_notify
from notifications import event_id_for, public_notification_view

ORG = "ORG-A"
OTHER = "ORG-B"
ACTOR = "operator-sub"
PEER = "admin-sub"


def _values(node):
    found = []
    if isinstance(node, str):
        found.append(node)
    for child in getattr(node, "_values", ()) or ():
        found.extend(_values(child))
    return found


class Notes:
    def __init__(self):
        self.items = {}

    def put_item(self, Item, ConditionExpression=None):
        key = (Item["pk"], Item["sk"])
        if ConditionExpression and key in self.items:
            raise ClientError(
                {"Error": {"Code": "ConditionalCheckFailedException", "Message": "exists"}},
                "PutItem",
            )
        self.items[key] = dict(Item)

    def get_item(self, Key):
        item = self.items.get((Key["pk"], Key["sk"]))
        return {"Item": dict(item)} if item else {}

    def update_item(self, Key, UpdateExpression=None, ExpressionAttributeValues=None, **kwargs):
        item = self.items[(Key["pk"], Key["sk"])]
        if UpdateExpression and "fanout_status" in UpdateExpression:
            item["fanout_status"] = ExpressionAttributeValues[":complete"]
            item["recipient_count"] = ExpressionAttributeValues[":count"]


class Members:
    def __init__(self, rows):
        self.rows = rows

    def query(self, **kwargs):
        wanted = _values(kwargs.get("KeyConditionExpression"))
        return {"Items": [row for row in self.rows if row["organization_id"] in wanted]}


class Orgs:
    def get_item(self, Key):
        org_id = Key["organization_id"]
        if org_id == ORG:
            return {"Item": {"organization_id": ORG, "status": "ACTIVE"}}
        return {"Item": {"organization_id": org_id, "status": "ACTIVE"}}


def members():
    return Members(
        [
            {"organization_id": ORG, "user_sub": ACTOR, "role": "OPERATOR", "status": "ACTIVE"},
            {"organization_id": ORG, "user_sub": PEER, "role": "ADMIN", "status": "ACTIVE"},
            {"organization_id": ORG, "user_sub": "member-sub", "role": "MEMBER", "status": "ACTIVE"},
            {"organization_id": ORG, "user_sub": "inactive-sub", "role": "OPERATOR", "status": "INACTIVE"},
            {"organization_id": OTHER, "user_sub": "foreign-sub", "role": "OWNER", "status": "ACTIVE"},
        ]
    )


def emit(func, **kwargs):
    notes = Notes()
    func(table=notes, members=members(), organizations=Orgs(), **kwargs)
    return notes


def inbox_rows(notes):
    return [item for item in notes.items.values() if item.get("entity_type") == "NOTIFICATION"]


def test_emergency_events_notify_only_other_operators_in_the_organization():
    notes = emit(
        emergency_notify.notify_request_created,
        organization_id=ORG,
        actor_sub=ACTOR,
        request_id="Q-A",
        resource_type="ICU bed",
        location="North",
    )
    rows = inbox_rows(notes)
    assert [row["user_sub"] for row in rows] == [PEER]
    assert rows[0]["organization_id"] == ORG
    assert rows[0]["event_code"] == "emergency.request.created"
    assert rows[0]["payload"]["request_id"] == "Q-A"
    assert OTHER not in str(rows[0])
    view = public_notification_view(rows[0])
    assert view["href"]["kind"] == "emergency_request"
    assert view["href"]["request_id"] == "Q-A"
    assert "exchange_request_id" not in view["href"]


def test_duplicate_emergency_event_does_not_add_another_inbox_row():
    notes = Notes()
    kwargs = {
        "organization_id": ORG,
        "actor_sub": ACTOR,
        "request_id": "Q-A",
        "allocation_id": "ALLOC-Q-A",
        "resource_id": "R1",
        "table": notes,
        "members": members(),
        "organizations": Orgs(),
    }
    emergency_notify.notify_allocation_created(**kwargs)
    emergency_notify.notify_allocation_created(**kwargs)
    rows = inbox_rows(notes)
    assert len(rows) == 1
    assert rows[0]["event_id"] == event_id_for(
        "emergency.allocation.created", "ALLOC-Q-A", ORG
    )


def test_unsafe_fields_are_dropped_and_failures_do_not_raise():
    notes = emit(
        emergency_notify.notify_allocation_released,
        organization_id=ORG,
        actor_sub=ACTOR,
        request_id="Q-A",
        allocation_id="ALLOC-Q-A",
        resource_id="R1",
        resource_type="ICU",
        location="http://evil.example/phish",
    )
    payload = inbox_rows(notes)[0]["payload"]
    assert "location" not in payload
    assert payload["href_kind"] == "emergency_request"

    class Boom:
        def put_item(self, **kwargs):
            raise RuntimeError("notifications down")

        def get_item(self, **kwargs):
            return {}

    emergency_notify.notify_request_cancelled(
        organization_id=ORG,
        actor_sub=ACTOR,
        request_id="Q-A",
        table=Boom(),
        members=members(),
        organizations=Orgs(),
    )


def test_payload_cannot_carry_another_organization():
    from notifications import EVENT_EMERGENCY_REQUEST_CREATED, _safe_payload

    payload = _safe_payload(
        {
            "request_id": "Q-A",
            "organization_id": OTHER,
            "location": "http://evil.example/phish",
            "resource_type": "ICU",
        },
        EVENT_EMERGENCY_REQUEST_CREATED,
    )
    assert payload == {
        "request_id": "Q-A",
        "resource_type": "ICU",
        "href_kind": "emergency_request",
    }
    notes = emit(
        emergency_notify.notify_request_created,
        organization_id=ORG,
        actor_sub=ACTOR,
        request_id="Q-A",
    )
    assert all(row["user_sub"] != "foreign-sub" for row in inbox_rows(notes))


def _p13a():
    spec = importlib.util.spec_from_file_location(
        "p13a_helpers_for_inbox",
        ROOT / "tests" / "test_p13a_allocation_preview.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_preview_does_not_notify_and_confirmation_survives_notification_failure(monkeypatch):
    helpers = _p13a()
    calls = []

    def explode(**kwargs):
        calls.append(kwargs)
        raise RuntimeError("inbox down")

    monkeypatch.setattr(helpers.allocation, "notify_allocation_created", explode)
    world = helpers.World([helpers.resource_row()], [helpers.request_row()])
    world.bind(monkeypatch)

    preview_status, preview = world.post(helpers.body(preview=True))
    assert preview_status == 200
    assert preview["preview"] is True
    assert calls == []
    assert world.allocations.items == []

    status, payload = world.post(helpers.body(confirm=True, resource_id="R1"))
    assert status == 200
    assert payload["status"] == "ALLOCATED"
    assert world.requests.items[0]["Status"] == "ALLOCATED"
    assert len(world.allocations.items) == 1
    assert calls[0]["organization_id"] == "ORG-A"
    assert calls[0]["allocation_id"] == "ALLOC-Q-A"
    assert calls[0]["resource_id"] == "R1"
