"""Phase 11A notification hardening regressions."""

from __future__ import annotations

import importlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from botocore.exceptions import ClientError

import test_notifications as fixtures
from access import AccessError
from pages import encode_token

ROOT = Path(__file__).resolve().parents[1]
EVENTS = [
    "exchange.offer.received",
    "exchange.offer.accepted",
    "exchange.offer.rejected",
    "exchange.offer.withdrawn",
    "exchange.offer.superseded",
    "exchange.transfer.started",
    "exchange.handover.completed",
    "exchange.request.cancelled",
    "exchange.request.expired",
]


def _modules():
    fixtures._ensure_paths()
    import notifications as notifications_mod

    importlib.reload(notifications_mod)
    return notifications_mod, fixtures._load_notification_service()


def _orgs(*ids):
    return fixtures.OrganizationsTable(set(ids))


def _members():
    return fixtures.MembersTable(
        [
            {"organization_id": "ORG-A66B0A1E4F96", "user_sub": "owner-a", "role": "OWNER", "status": "ACTIVE"},
            {"organization_id": "ORG-A66B0A1E4F96", "user_sub": "admin-a", "role": "ADMIN", "status": "ACTIVE"},
            {"organization_id": "ORG-A66B0A1E4F96", "user_sub": "op-a", "role": "OPERATOR", "status": "ACTIVE"},
            {"organization_id": "ORG-A66B0A1E4F96", "user_sub": "member-a", "role": "MEMBER", "status": "ACTIVE"},
            {"organization_id": "ORG-A66B0A1E4F96", "user_sub": "inactive-a", "role": "ADMIN", "status": "INACTIVE"},
            {"organization_id": "ORG-A66B0A1E4F96", "user_sub": "actor-a", "role": "OWNER", "status": "ACTIVE"},
            {"organization_id": "ORG-17D0E2939B2D", "user_sub": "owner-b", "role": "OWNER", "status": "ACTIVE"},
            {"organization_id": "ORG-17D0E2939B2D", "user_sub": "actor-b", "role": "OPERATOR", "status": "ACTIVE"},
        ]
    )


def test_each_event_id_is_deterministic_and_duplicate_puts_do_not_fan_out_again():
    notifications_mod, _api = _modules()
    table = fixtures.FakeTable()
    members = _members()
    orgs = _orgs("ORG-A66B0A1E4F96", "ORG-17D0E2939B2D")
    for code in EVENTS:
        subject = "EXOFF-1" if "offer." in code and code != "exchange.offer.received" else "EXREQ-1"
        kwargs = dict(
            event_code=code,
            subject_id=subject,
            recipient_organization_id="ORG-A66B0A1E4F96",
            actor_sub="actor-a",
            payload={
                "exchange_request_id": "EXREQ-1",
                "offer_id": "EXOFF-1",
                "password": "nope",
                "qr_payload": "erapqr.secret",
                "href_kind": "https://evil.example/phish",
            },
            table=table,
            members=members,
            organizations=orgs,
        )
        notifications_mod.emit_notification_event(**kwargs)
        notifications_mod.emit_notification_event(**kwargs)
    events = [item for item in table.items.values() if item.get("entity_type") == "NOTIFICATION_EVENT"]
    inbox = [item for item in table.items.values() if item.get("entity_type") == "NOTIFICATION"]
    assert len(events) == len(EVENTS)
    assert len(inbox) == len(EVENTS) * 3
    blob = json.dumps(list(table.items.values()), default=str)
    assert "nope" not in blob
    assert "erapqr" not in blob
    assert "evil.example" not in blob
    assert "actor-a" not in {item["user_sub"] for item in inbox}
    assert "member-a" not in {item["user_sub"] for item in inbox}
    assert "inactive-a" not in {item["user_sub"] for item in inbox}
    assert "owner-b" not in {item["user_sub"] for item in inbox}
    for item in inbox:
        assert item["organization_id"] == "ORG-A66B0A1E4F96"
        assert item["href_kind"] == "exchange_request"
        created = datetime.fromisoformat(item["created_at"])
        expires = datetime.fromtimestamp(int(item["expires_at"]), tz=timezone.utc)
        assert timedelta(days=89) < expires - created < timedelta(days=91)
    assert all(item.get("expires_at") for item in events)


def test_fanout_cap_and_inactive_organization_emit_nothing():
    notifications_mod, _api = _modules()
    rows = [
        {"organization_id": "ORG-A66B0A1E4F96", "user_sub": f"op-{index}", "role": "OPERATOR", "status": "ACTIVE"}
        for index in range(60)
    ]
    rows.insert(0, {"organization_id": "ORG-A66B0A1E4F96", "user_sub": "owner-a", "role": "OWNER", "status": "ACTIVE"})
    table = fixtures.FakeTable()
    notifications_mod.emit_notification_event(
        event_code=notifications_mod.EVENT_REQUEST_EXPIRED,
        subject_id="EXREQ-CAP",
        recipient_organization_id="ORG-A66B0A1E4F96",
        actor_sub="",
        payload={"exchange_request_id": "EXREQ-CAP"},
        table=table,
        members=fixtures.MembersTable(rows),
        organizations=_orgs("ORG-A66B0A1E4F96"),
    )
    inbox = [item for item in table.items.values() if item.get("entity_type") == "NOTIFICATION"]
    assert len(inbox) == 50
    assert any(item["user_sub"] == "owner-a" for item in inbox)
    assert sum(1 for item in inbox if str(item["user_sub"]).startswith("op-")) == 49
    inbox_before = len(inbox)
    notifications_mod.emit_notification_event(
        event_code=notifications_mod.EVENT_REQUEST_EXPIRED,
        subject_id="EXREQ-DEAD",
        recipient_organization_id="ORG-A66B0A1E4F96",
        actor_sub="",
        table=table,
        members=fixtures.MembersTable(rows),
        organizations=fixtures.OrganizationsTable(set()),
    )
    inbox_after = [item for item in table.items.values() if item.get("entity_type") == "NOTIFICATION"]
    assert len(inbox_after) == inbox_before


def test_emit_failure_is_logged_without_raising_or_recording_secrets(capsys):
    notifications_mod, _api = _modules()

    class Boom(fixtures.FakeTable):
        def put_item(self, **kwargs):
            raise ClientError(
                {"Error": {"Code": "ProvisionedThroughputExceededException", "Message": "secret-token"}},
                "PutItem",
            )

    notifications_mod.emit_notification_event(
        event_code=notifications_mod.EVENT_OFFER_RECEIVED,
        subject_id="EXREQ-Z",
        recipient_organization_id="ORG-A66B0A1E4F96",
        actor_sub="actor-a",
        payload={"qr_payload": "erapqr.secret"},
        table=Boom(),
        members=_members(),
        organizations=_orgs("ORG-A66B0A1E4F96"),
    )
    logged = capsys.readouterr().out
    assert "NOTIFICATION_EMIT_FAILED" in logged
    assert "ProvisionedThroughputExceededException" in logged
    assert "erapqr" not in logged
    assert "secret-token" not in logged


def test_inbox_pages_do_not_overlap_or_cross_tenants():
    notifications_mod, api = _modules()
    table = fixtures.FakeTable()
    members = _members()
    orgs = _orgs("ORG-A66B0A1E4F96", "ORG-17D0E2939B2D")
    for index in range(25):
        notifications_mod.emit_notification_event(
            event_code=notifications_mod.EVENT_OFFER_RECEIVED,
            subject_id=f"EXREQ-{index}",
            recipient_organization_id="ORG-A66B0A1E4F96",
            actor_sub="actor-a",
            payload={"exchange_request_id": f"EXREQ-{index}"},
            table=table,
            members=members,
            organizations=orgs,
        )
    notifications_mod.emit_notification_event(
        event_code=notifications_mod.EVENT_OFFER_RECEIVED,
        subject_id="EXREQ-FOREIGN",
        recipient_organization_id="ORG-17D0E2939B2D",
        actor_sub="actor-b",
        payload={"exchange_request_id": "EXREQ-FOREIGN"},
        table=table,
        members=members,
        organizations=orgs,
    )
    membership = {"organization_id": "ORG-A66B0A1E4F96", "role": "ADMIN", "status": "ACTIVE"}
    page1 = api.list_notifications("ORG-A66B0A1E4F96", "owner-a", membership, limit=20, table=table)
    page2 = api.list_notifications(
        "ORG-A66B0A1E4F96",
        "owner-a",
        membership,
        limit=20,
        page_token=page1["next_page_token"],
        table=table,
    )
    ids1 = [item["notification_id"] for item in page1["notifications"]]
    ids2 = [item["notification_id"] for item in page2["notifications"]]
    assert len(ids1) == 20
    assert len(ids2) == 5
    assert not set(ids1) & set(ids2)
    assert page2["next_page_token"] is None
    assert all(item["href"]["exchange_request_id"].startswith("EXREQ-") for item in page1["notifications"])
    foreign = api.list_notifications(
        "ORG-17D0E2939B2D",
        "owner-b",
        {"organization_id": "ORG-17D0E2939B2D", "role": "OWNER", "status": "ACTIVE"},
        table=table,
    )
    assert [item["href"]["exchange_request_id"] for item in foreign["notifications"]] == ["EXREQ-FOREIGN"]
    with pytest.raises(api.NotificationOperationError) as bad:
        api.list_notifications(
            "ORG-A66B0A1E4F96",
            "owner-a",
            membership,
            page_token=encode_token({"pk": "INBOX#ORG-17D0E2939B2D#owner-b", "sk": "AT#x#y"}),
            table=table,
        )
    assert bad.value.status_code == 400
    with pytest.raises(AccessError):
        api.list_notifications(
            "ORG-A66B0A1E4F96",
            "owner-a",
            membership,
            page_token="not-a-token",
            table=table,
        )


def test_unread_mark_read_and_bounded_read_all_stay_in_one_inbox():
    notifications_mod, api = _modules()
    table = fixtures.FakeTable()
    members = _members()
    orgs = _orgs("ORG-A66B0A1E4F96", "ORG-17D0E2939B2D")
    for index in range(3):
        notifications_mod.emit_notification_event(
            event_code=notifications_mod.EVENT_TRANSFER_STARTED,
            subject_id=f"EXREQ-{index}",
            recipient_organization_id="ORG-A66B0A1E4F96",
            actor_sub="someone-else",
            payload={"exchange_request_id": f"EXREQ-{index}"},
            table=table,
            members=members,
            organizations=orgs,
        )
    notifications_mod.emit_notification_event(
        event_code=notifications_mod.EVENT_TRANSFER_STARTED,
        subject_id="EXREQ-B",
        recipient_organization_id="ORG-17D0E2939B2D",
        actor_sub="someone-else",
        payload={"exchange_request_id": "EXREQ-B"},
        table=table,
        members=members,
        organizations=orgs,
    )
    membership = {"organization_id": "ORG-A66B0A1E4F96", "role": "OPERATOR", "status": "ACTIVE"}
    assert api.unread_count("ORG-A66B0A1E4F96", "op-a", membership, table=table)["unread_count"] == 3
    listed = api.list_notifications("ORG-A66B0A1E4F96", "op-a", membership, table=table)
    nid = listed["notifications"][0]["notification_id"]
    api.mark_read("ORG-A66B0A1E4F96", "op-a", membership, nid, table=table)
    again = api.mark_read("ORG-A66B0A1E4F96", "op-a", membership, nid, table=table)
    assert again["message"] == "Notification already read"
    assert api.unread_count("ORG-A66B0A1E4F96", "op-a", membership, table=table)["unread_count"] == 2
    with pytest.raises(api.NotificationOperationError) as missing:
        api.mark_read(
            "ORG-17D0E2939B2D",
            "owner-b",
            {"organization_id": "ORG-17D0E2939B2D", "role": "OWNER", "status": "ACTIVE"},
            nid,
            table=table,
        )
    assert missing.value.status_code == 404
    other = api.unread_count(
        "ORG-17D0E2939B2D",
        "owner-b",
        {"organization_id": "ORG-17D0E2939B2D", "role": "OWNER", "status": "ACTIVE"},
        table=table,
    )
    assert other["unread_count"] == 1
    cleared = api.mark_all_read("ORG-A66B0A1E4F96", "op-a", membership, table=table)
    assert cleared["marked_read"] == 2
    assert cleared["truncated"] is False
    repeat = api.mark_all_read("ORG-A66B0A1E4F96", "op-a", membership, table=table)
    assert repeat["marked_read"] == 0
    assert api.unread_count("ORG-A66B0A1E4F96", "op-a", membership, table=table)["unread_count"] == 0
    assert other["unread_count"] == 1
    assert api.unread_count(
        "ORG-17D0E2939B2D",
        "owner-b",
        {"organization_id": "ORG-17D0E2939B2D", "role": "OWNER", "status": "ACTIVE"},
        table=table,
    )["unread_count"] == 1
    with pytest.raises(AccessError):
        api.unread_count(
            "ORG-A66B0A1E4F96",
            "member-a",
            {"organization_id": "ORG-A66B0A1E4F96", "role": "MEMBER", "status": "ACTIVE"},
            table=table,
        )


def test_read_all_reports_truncation_when_the_batch_limit_is_hit():
    notifications_mod, api = _modules()
    table = fixtures.FakeTable()
    members = fixtures.MembersTable(
        [{"organization_id": "ORG-A66B0A1E4F96", "user_sub": "op-a", "role": "OPERATOR", "status": "ACTIVE"}]
    )
    for index in range(3):
        notifications_mod.emit_notification_event(
            event_code=notifications_mod.EVENT_REQUEST_CANCELLED,
            subject_id=f"EXREQ-{index}",
            recipient_organization_id="ORG-A66B0A1E4F96",
            actor_sub="other",
            payload={"exchange_request_id": f"EXREQ-{index}"},
            table=table,
            members=members,
            organizations=_orgs("ORG-A66B0A1E4F96"),
        )
    membership = {"organization_id": "ORG-A66B0A1E4F96", "role": "OPERATOR", "status": "ACTIVE"}
    first = api.mark_all_read("ORG-A66B0A1E4F96", "op-a", membership, table=table, max_items=2)
    assert first["marked_read"] == 2
    assert first["truncated"] is True
    second = api.mark_all_read("ORG-A66B0A1E4F96", "op-a", membership, table=table, max_items=2)
    assert second["marked_read"] == 1
    assert second["truncated"] is False


def test_notification_sources_do_not_scan_or_add_qr_events():
    texts = []
    for relative in (
        "src/shared/notifications.py",
        "src/shared/exchange_notify.py",
        "src/notification_api/service.py",
        "src/shared/handover_qr.py",
    ):
        texts.append((ROOT / relative).read_text(encoding="utf-8"))
    combined = "\n".join(texts).lower()
    assert ".scan(" not in combined
    assert "batchwrite" not in combined
    assert "deleteitem" not in combined
    assert "qr." not in (ROOT / "src/shared/notifications.py").read_text(encoding="utf-8").lower()
    assert "notify_" not in (ROOT / "src/shared/handover_qr.py").read_text(encoding="utf-8")


def test_handler_denies_inactive_membership_and_member_role(monkeypatch):
    handler = fixtures._load_notification_handler()
    import access

    def inactive(event):
        return "user-1"

    def inactive_memberships(*_args, **_kwargs):
        return [
            {
                "organization_id": "ORG-A66B0A1E4F96",
                "user_sub": "user-1",
                "role": "ADMIN",
                "status": "INACTIVE",
            }
        ]

    monkeypatch.setattr(access, "get_user_sub", inactive)
    monkeypatch.setattr(access, "list_memberships", inactive_memberships)
    event = {
        "httpMethod": "GET",
        "path": "/notifications",
        "queryStringParameters": {"organization_id": "ORG-A66B0A1E4F96"},
        "requestContext": {"authorizer": {"claims": {"sub": "user-1"}}},
        "body": None,
    }
    denied = handler.lambda_handler(event, None)
    assert denied["statusCode"] == 403

    def member_rows(*_args, **_kwargs):
        return [
            {
                "organization_id": "ORG-A66B0A1E4F96",
                "user_sub": "user-1",
                "role": "MEMBER",
                "status": "ACTIVE",
            }
        ]

    monkeypatch.setattr(access, "list_memberships", member_rows)
    member = handler.lambda_handler(event, None)
    assert member["statusCode"] == 403
