"""Phase 8B notification backend tests."""

from __future__ import annotations

import importlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[1]
SHARED = ROOT / "src" / "shared"
NOTIF_API = ROOT / "src" / "notification_api"
ORG = ROOT / "src" / "organization"
BILLING = ROOT / "src"


def _ensure_paths():
    for path in (SHARED, NOTIF_API, ORG, BILLING, ROOT / "src"):
        text = str(path)
        if text not in sys.path:
            sys.path.insert(0, text)


def _load_notification_service():
    _ensure_paths()
    path = NOTIF_API / "service.py"
    name = "notification_api_service"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_notification_handler():
    _ensure_paths()
    svc = _load_notification_service()
    sys.modules["service"] = svc
    path = NOTIF_API / "handler.py"
    name = "notification_api_handler"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_ensure_paths()


class FakeTable:
    def __init__(self, name="Notifications"):
        self.name = name
        self.table_name = name
        self.items = {}
        self.queries = []
        self.puts = []
        self.updates = []

    def _key(self, key):
        return (key.get("pk"), key.get("sk"))

    def put_item(self, Item=None, ConditionExpression=None, **_kwargs):
        item = dict(Item or {})
        key = self._key(item)
        self.puts.append(item)
        if ConditionExpression and "attribute_not_exists" in ConditionExpression:
            if key in self.items:
                raise ClientError(
                    {"Error": {"Code": "ConditionalCheckFailedException", "Message": "exists"}},
                    "PutItem",
                )
        self.items[key] = item
        return {}

    def get_item(self, Key=None, **_kwargs):
        item = self.items.get(self._key(Key or {}))
        return {"Item": dict(item)} if item else {}

    def update_item(
        self,
        Key=None,
        UpdateExpression=None,
        ConditionExpression=None,
        ExpressionAttributeValues=None,
        **_kwargs,
    ):
        key = self._key(Key or {})
        item = self.items.get(key)
        values = ExpressionAttributeValues or {}
        self.updates.append({"key": Key, "expr": UpdateExpression})
        if ConditionExpression:
            if "attribute_exists(unread_key)" in ConditionExpression and (
                not item or not item.get("unread_key")
            ):
                raise ClientError(
                    {"Error": {"Code": "ConditionalCheckFailedException", "Message": "cond"}},
                    "UpdateItem",
                )
            if "attribute_not_exists(read_at) OR read_at = :empty" in (ConditionExpression or ""):
                if item and item.get("read_at"):
                    raise ClientError(
                        {"Error": {"Code": "ConditionalCheckFailedException", "Message": "cond"}},
                        "UpdateItem",
                    )
        if item is None:
            raise ClientError(
                {"Error": {"Code": "ConditionalCheckFailedException", "Message": "missing"}},
                "UpdateItem",
            )
        updated = dict(item)
        if UpdateExpression and "read_at = :now" in UpdateExpression:
            updated["read_at"] = values.get(":now") or values.get(":complete")
        if UpdateExpression and "REMOVE unread_key" in UpdateExpression:
            updated.pop("unread_key", None)
        if UpdateExpression and "fanout_status = :complete" in UpdateExpression:
            updated["fanout_status"] = values.get(":complete")
            updated["recipient_count"] = values.get(":count")
        self.items[key] = updated
        return {}

    def query(self, **kwargs):
        self.queries.append(kwargs)
        index = kwargs.get("IndexName")
        limit = kwargs.get("Limit")
        items = []
        if index == "UnreadByUserIndex":
            # Approximate Key unread_key from Expression - Fake uses scan of items
            for item in self.items.values():
                if item.get("unread_key") and item.get("entity_type") == "NOTIFICATION":
                    items.append(item)
            items.sort(key=lambda row: row.get("created_at") or "", reverse=True)
        else:
            # Inbox query by pk
            for item in self.items.values():
                if item.get("entity_type") == "NOTIFICATION" and str(item.get("sk", "")).startswith(
                    "AT#"
                ):
                    items.append(item)
            items.sort(key=lambda row: row.get("created_at") or "", reverse=True)
            if kwargs.get("FilterExpression") is not None:
                # Filter by event_id when Attr used — tests set matching items only
                pass
        if kwargs.get("Select") == "COUNT":
            count = len(items)
            if limit:
                count = min(count, limit)
            return {"Count": count, "Items": []}
        if limit:
            items = items[:limit]
        return {"Items": [dict(item) for item in items]}


class MembersTable:
    def __init__(self, rows):
        self.rows = list(rows)

    def query(self, **_kwargs):
        return {"Items": list(self.rows)}


class OrganizationsTable:
    def __init__(self, active_ids):
        self.active_ids = set(active_ids)

    def get_item(self, Key=None, **_kwargs):
        org = (Key or {}).get("organization_id")
        if org in self.active_ids:
            return {"Item": {"organization_id": org, "status": "ACTIVE", "name": org}}
        return {"Item": {"organization_id": org, "status": "INACTIVE"}}


@pytest.fixture
def notifications_mod(monkeypatch):
    _ensure_paths()
    import notifications as mod

    importlib.reload(mod)
    return mod


def test_event_id_deterministic(notifications_mod):
    a = notifications_mod.event_id_for(
        "exchange.offer.received", "EXREQ-1", "ORG-A"
    )
    b = notifications_mod.event_id_for(
        "exchange.offer.received", "EXREQ-1", "ORG-A"
    )
    assert a == b
    assert a == "exchange.offer.received#EXREQ-1#ORG-A"


def test_resolve_recipients_roles_and_actor(notifications_mod):
    members = MembersTable(
        [
            {"organization_id": "ORG-A", "user_sub": "owner-1", "role": "OWNER", "status": "ACTIVE"},
            {"organization_id": "ORG-A", "user_sub": "admin-1", "role": "ADMIN", "status": "ACTIVE"},
            {"organization_id": "ORG-A", "user_sub": "ops-1", "role": "OPERATOR", "status": "ACTIVE"},
            {"organization_id": "ORG-A", "user_sub": "member-1", "role": "MEMBER", "status": "ACTIVE"},
            {"organization_id": "ORG-A", "user_sub": "inactive-1", "role": "ADMIN", "status": "INACTIVE"},
            {"organization_id": "ORG-A", "user_sub": "invite-x", "role": "OPERATOR", "status": "PENDING"},
            {"organization_id": "ORG-A", "user_sub": "actor-1", "role": "OWNER", "status": "ACTIVE"},
        ]
    )
    orgs = OrganizationsTable({"ORG-A"})
    got = notifications_mod.resolve_recipients(
        "ORG-A", "actor-1", members=members, organizations=orgs
    )
    assert "actor-1" not in got
    assert "member-1" not in got
    assert "inactive-1" not in got
    assert "invite-x" not in got
    assert set(got) == {"owner-1", "admin-1", "ops-1"}


def test_emit_creates_event_and_inbox_idempotent(notifications_mod):
    table = FakeTable()
    members = MembersTable(
        [
            {"organization_id": "ORG-R", "user_sub": "recv-1", "role": "ADMIN", "status": "ACTIVE"},
            {"organization_id": "ORG-R", "user_sub": "actor-p", "role": "OWNER", "status": "ACTIVE"},
        ]
    )
    orgs = OrganizationsTable({"ORG-R"})
    kwargs = dict(
        event_code=notifications_mod.EVENT_OFFER_RECEIVED,
        subject_id="EXREQ-ABC",
        recipient_organization_id="ORG-R",
        actor_sub="actor-p",
        payload={"exchange_request_id": "EXREQ-ABC", "resource_type_name": "Pump", "quantity": 2},
        table=table,
        members=members,
        organizations=orgs,
    )
    notifications_mod.emit_notification_event(**kwargs)
    notifications_mod.emit_notification_event(**kwargs)
    events = [item for item in table.items.values() if item.get("entity_type") == "NOTIFICATION_EVENT"]
    inbox = [item for item in table.items.values() if item.get("entity_type") == "NOTIFICATION"]
    assert len(events) == 1
    assert len(inbox) == 1
    assert inbox[0]["user_sub"] == "recv-1"
    assert inbox[0]["unread_key"]
    assert inbox[0]["expires_at"]
    assert "Pump" in inbox[0]["body"]


def test_emit_failure_swallowed(notifications_mod):
    class Boom(FakeTable):
        def put_item(self, **kwargs):
            raise RuntimeError("dynamo down")

    notifications_mod.emit_notification_event(
        event_code=notifications_mod.EVENT_OFFER_RECEIVED,
        subject_id="EXREQ-Z",
        recipient_organization_id="ORG-R",
        actor_sub="actor",
        table=Boom(),
        members=MembersTable([]),
        organizations=OrganizationsTable({"ORG-R"}),
    )


def test_api_list_unread_mark_read(monkeypatch):
    _ensure_paths()
    import notifications as nmod

    importlib.reload(nmod)
    api = _load_notification_service()

    table = FakeTable()
    members = MembersTable(
        [{"organization_id": "ORG-A", "user_sub": "u1", "role": "OPERATOR", "status": "ACTIVE"}]
    )
    orgs = OrganizationsTable({"ORG-A"})
    nmod.emit_notification_event(
        event_code=nmod.EVENT_TRANSFER_STARTED,
        subject_id="EXREQ-1",
        recipient_organization_id="ORG-A",
        actor_sub="other",
        payload={"exchange_request_id": "EXREQ-1"},
        table=table,
        members=members,
        organizations=orgs,
    )
    membership = {"organization_id": "ORG-A", "role": "OPERATOR", "status": "ACTIVE"}
    listed = api.list_notifications("ORG-A", "u1", membership, table=table)
    assert len(listed["notifications"]) == 1
    nid = listed["notifications"][0]["notification_id"]
    count = api.unread_count("ORG-A", "u1", membership, table=table)
    assert count["unread_count"] == 1
    marked = api.mark_read("ORG-A", "u1", membership, nid, table=table)
    assert marked["notification"]["read_at"]
    # idempotent
    marked2 = api.mark_read("ORG-A", "u1", membership, nid, table=table)
    assert marked2["notification"]["read_at"]
    count2 = api.unread_count("ORG-A", "u1", membership, table=table)
    assert count2["unread_count"] == 0


def test_member_forbidden(monkeypatch):
    api = _load_notification_service()
    from access import AccessError

    with pytest.raises(AccessError):
        api.list_notifications(
            "ORG-A",
            "m1",
            {"organization_id": "ORG-A", "role": "MEMBER", "status": "ACTIVE"},
            table=FakeTable(),
        )


def test_cross_tenant_mark_read_404():
    api = _load_notification_service()
    with pytest.raises(api.NotificationOperationError) as err:
        api.mark_read(
            "ORG-A",
            "u1",
            {"organization_id": "ORG-A", "role": "ADMIN", "status": "ACTIVE"},
            "exchange.offer.received#EXREQ-1#ORG-B#u2",
            table=FakeTable(),
        )
    assert err.value.status_code == 404


def test_handler_routes(monkeypatch):
    h = _load_notification_handler()
    api = _load_notification_service()

    calls = {}

    def fake_authorize(event, body=None, allowed_roles=None, access=None):
        calls["roles"] = allowed_roles
        return "user-1", {"organization_id": "ORG-A", "role": "ADMIN", "status": "ACTIVE"}

    monkeypatch.setattr(h, "authorize", fake_authorize)
    monkeypatch.setattr(
        h.notification_service,
        "list_notifications",
        lambda *a, **k: {"organization_id": "ORG-A", "notifications": [], "next_page_token": None},
    )
    monkeypatch.setattr(
        h.notification_service,
        "unread_count",
        lambda *a, **k: {"organization_id": "ORG-A", "unread_count": 0},
    )
    monkeypatch.setattr(
        h.notification_service,
        "mark_all_read",
        lambda *a, **k: {"organization_id": "ORG-A", "marked_read": 0},
    )
    monkeypatch.setattr(
        h.notification_service,
        "mark_read",
        lambda *a, **k: {"message": "ok", "notification": {"notification_id": "x"}},
    )

    event = {
        "httpMethod": "GET",
        "path": "/notifications",
        "queryStringParameters": {"organization_id": "ORG-A"},
        "requestContext": {"authorizer": {"claims": {"sub": "user-1"}}},
        "body": None,
    }
    resp = h.lambda_handler(event, None)
    assert resp["statusCode"] == 200

    resp = h.lambda_handler(
        {**event, "path": "/notifications/unread-count"}, None
    )
    assert resp["statusCode"] == 200

    resp = h.lambda_handler(
        {**event, "httpMethod": "POST", "path": "/notifications/read-all", "body": "{}"},
        None,
    )
    assert resp["statusCode"] == 200

    resp = h.lambda_handler(
        {
            **event,
            "httpMethod": "POST",
            "path": "/notifications/exchange.offer.received%23EXREQ-1%23ORG-A%23user-1/read",
            "body": "{}",
        },
        None,
    )
    assert resp["statusCode"] == 200
    assert "OPERATOR" in calls["roles"] or "ADMIN" in calls["roles"]


def test_exchange_notify_bridge(monkeypatch):
    _ensure_paths()
    import exchange_notify as bridge
    import notifications as nmod

    importlib.reload(nmod)
    importlib.reload(bridge)

    seen = []

    def capture(**kwargs):
        seen.append(kwargs)

    monkeypatch.setattr(bridge, "emit_notification_event", capture)
    monkeypatch.setattr(bridge, "emit_for_orgs", lambda **kwargs: seen.append(kwargs))

    meta = {
        "exchange_request_id": "EXREQ-1",
        "requester_organization_id": "ORG-R",
        "accepted_provider_organization_id": "ORG-P",
        "resource_type_name": "Kit",
    }
    offer = {"offer_id": "EXOFF-1", "provider_organization_id": "ORG-P", "quantity_offered": 3}
    bridge.notify_offer_received(meta, offer, "provider-actor")
    bridge.notify_offer_accepted(meta, offer, "req-actor")
    bridge.notify_offer_rejected(meta, offer, "req-actor")
    bridge.notify_offer_withdrawn(meta, offer, "prov-actor")
    bridge.notify_offer_superseded(meta, offer, "req-actor")
    bridge.notify_transfer_started(meta, offer, "prov-actor")
    bridge.notify_handover_completed(meta, offer, "req-actor", destination_mode="CREATE")
    bridge.notify_request_cancelled(meta, "req-actor", previous_status="ACCEPTED")
    bridge.notify_request_cancelled(meta, "req-actor", previous_status="OPEN")
    bridge.notify_request_expired(meta, "system", previous_status="OPEN")
    bridge.notify_request_expired(meta, "system", previous_status="TRANSFER_PENDING")
    assert len(seen) >= 8


def test_package_includes_notifications():
    sys.path.insert(0, str(ROOT / "scripts"))
    from lambda_manifest import package_map

    mapping = package_map()
    assert "erap-notifications" in mapping
    assert "notifications.py" in mapping["erap-exchange"]
    assert "exchange_notify.py" in mapping["erap-exchange"]
    assert "handler.py" in mapping["erap-notifications"]


def test_infra_notifications_spec():
    data = json.loads((ROOT / "infra" / "notifications-table.json").read_text(encoding="utf-8"))
    table = data["tables"][0]
    assert table["TableName"] == "Notifications"
    assert table["BillingMode"] == "PAY_PER_REQUEST"
    assert table["DeletionProtectionEnabled"] is True
    gsis = {item["IndexName"] for item in table["GlobalSecondaryIndexes"]}
    assert gsis == {"UnreadByUserIndex"}
    assert table["TimeToLiveSpecification"]["AttributeName"] == "expires_at"
