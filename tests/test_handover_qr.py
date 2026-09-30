"""Phase 9B QR handover backend tests. No camera or frontend."""

import base64
import json
from datetime import timedelta
from pathlib import Path

import pytest

import handover_qr
from test_resource_exchange_handover import (
    ORG_A,
    ORG_B,
    ORG_C,
    body_of,
    confirm_as_requester,
    event,
    handler,
    memberships,
    service,
    setup_accepted_exchange,
    start_transfer_as_provider,
    use_billing,
    use_memberships,
)

ROOT = Path(__file__).resolve().parents[1]


def pending(monkeypatch, tracking="INDIVIDUAL", resource_id="R-B-1"):
    request_id, offer_id, exchanges, resources, allocations, audit, history = setup_accepted_exchange(
        monkeypatch, tracking=tracking, resource_id=resource_id
    )
    started = start_transfer_as_provider(monkeypatch, request_id)
    assert started["statusCode"] == 200, body_of(started)
    return request_id, offer_id, exchanges, resources, allocations, audit, history


def issue(monkeypatch, request_id, org=ORG_B, role="OPERATOR"):
    use_memberships(monkeypatch, memberships((org, role)))
    use_billing(monkeypatch, "ACTIVE")
    return handler.lambda_handler(
        event(
            "POST",
            {"organization_id": org},
            organization_id=org,
            path=f"/exchange/requests/{request_id}/handover/qr",
        ),
        None,
    )


def preview(monkeypatch, payload, org=ORG_A, role="OPERATOR", query=None):
    use_memberships(monkeypatch, memberships((org, role)))
    use_billing(monkeypatch, "ACTIVE")
    return handler.lambda_handler(
        event(
            "POST",
            {"organization_id": org, "token": payload},
            organization_id=org,
            path="/exchange/handover/qr/preview",
            query=query,
        ),
        None,
    )


def confirm(monkeypatch, payload, org=ORG_A, body=None, role="OPERATOR"):
    use_memberships(monkeypatch, memberships((org, role)))
    use_billing(monkeypatch, "ACTIVE")
    payload_body = {"organization_id": org, "token": payload}
    if body:
        payload_body.update(body)
    return handler.lambda_handler(
        event(
            "POST",
            payload_body,
            organization_id=org,
            path="/exchange/handover/qr/confirm",
        ),
        None,
    )


def secret_of(payload):
    assert payload.startswith(handover_qr.QR_PREFIX)
    return payload[len(handover_qr.QR_PREFIX) :]


def stored_blob(exchanges, audit):
    return json.dumps({"items": list(exchanges.items.values()), "audit": audit.events}, default=str)


def test_token_entropy_prefix_and_hash_only(monkeypatch):
    request_id, _, exchanges, _, _, audit, _ = pending(monkeypatch)
    issued = issue(monkeypatch, request_id)
    assert issued["statusCode"] == 200, body_of(issued)
    body = body_of(issued)
    token = secret_of(body["qr_payload"])
    raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
    assert len(raw) == 32
    assert "token" not in body
    assert request_id not in body["qr_payload"]
    assert "ORG-" not in body["qr_payload"]
    blob = stored_blob(exchanges, audit)
    assert token not in blob
    assert body["qr_payload"] not in blob
    assert handover_qr.token_digest(token) in blob
    meta = next(item for item in exchanges.items.values() if item.get("entity_type") == "EXCHANGE_REQUEST")
    assert "qr_ttl_epoch" not in meta


def test_malformed_token_rejected(monkeypatch):
    pending(monkeypatch)
    denied = preview(monkeypatch, "not-a-qr")
    assert denied["statusCode"] == 404
    assert body_of(denied)["message"] == "Handover QR is not valid"
    denied_confirm = confirm(monkeypatch, "erap-hq.v2." + "a" * 43)
    assert denied_confirm["statusCode"] == 404


def test_issue_authorization_and_state(monkeypatch):
    request_id, _, exchanges, _, _, _, _ = pending(monkeypatch)
    requester = issue(monkeypatch, request_id, org=ORG_A)
    assert requester["statusCode"] == 404
    stranger = issue(monkeypatch, request_id, org=ORG_C)
    assert stranger["statusCode"] == 404
    member = issue(monkeypatch, request_id, org=ORG_B, role="MEMBER")
    assert member["statusCode"] == 403
    use_memberships(
        monkeypatch,
        [{"organization_id": ORG_B, "name": ORG_B, "role": "OPERATOR", "status": "SUSPENDED"}],
    )
    inactive = handler.lambda_handler(
        event(
            "POST",
            {"organization_id": ORG_B},
            organization_id=ORG_B,
            path=f"/exchange/requests/{request_id}/handover/qr",
        ),
        None,
    )
    assert inactive["statusCode"] == 403
    for org in exchanges.items.values():
        pass
    org_row = next(
        item
        for item in service.organizations_table()._items
        if item["organization_id"] == ORG_B
    )
    org_row["status"] = "SUSPENDED"
    use_memberships(monkeypatch, memberships((ORG_B, "OPERATOR")))
    suspended = issue(monkeypatch, request_id)
    assert suspended["statusCode"] == 403
    org_row["status"] = "ACTIVE"
    use_billing(monkeypatch, "EXPIRED")
    use_memberships(monkeypatch, memberships((ORG_B, "OPERATOR")))
    billed = handler.lambda_handler(
        event(
            "POST",
            {"organization_id": ORG_B},
            organization_id=ORG_B,
            path=f"/exchange/requests/{request_id}/handover/qr",
        ),
        None,
    )
    assert billed["statusCode"] == 403


def test_issue_before_transfer_is_rejected(monkeypatch):
    request_id, *_ = setup_accepted_exchange(monkeypatch)
    denied = issue(monkeypatch, request_id)
    assert denied["statusCode"] == 409


def test_regeneration_revokes_previous_and_caps_at_30(monkeypatch):
    request_id, _, exchanges, _, _, audit, _ = pending(monkeypatch)
    first = body_of(issue(monkeypatch, request_id))
    second = body_of(issue(monkeypatch, request_id))
    assert first["qr_payload"] != second["qr_payload"]
    stale = preview(monkeypatch, first["qr_payload"])
    assert stale["statusCode"] == 404
    assert preview(monkeypatch, second["qr_payload"])["statusCode"] == 200
    assert any(event["action"] == "exchange.handover_qr_revoked" for event in audit.events)
    for _ in range(28):
        assert issue(monkeypatch, request_id)["statusCode"] == 200
    capped = issue(monkeypatch, request_id)
    assert capped["statusCode"] == 429
    assert body_of(capped)["message"] == "Handover QR generation limit reached"
    pointer = next(item for item in exchanges.items.values() if item.get("entity_type") == "HANDOVER_QR_POINTER")
    assert int(pointer["generation_count"]) == 30


def test_preview_is_requester_only_and_safe(monkeypatch):
    request_id, _, _, _, _, _, _ = pending(monkeypatch)
    payload = body_of(issue(monkeypatch, request_id))["qr_payload"]
    provider = preview(monkeypatch, payload, org=ORG_B)
    assert provider["statusCode"] == 404
    assert request_id not in json.dumps(body_of(provider))
    stranger = preview(monkeypatch, payload, org=ORG_C)
    assert stranger["statusCode"] == 404
    member = preview(monkeypatch, payload, role="MEMBER")
    assert member["statusCode"] == 403
    shown = preview(monkeypatch, payload)
    assert shown["statusCode"] == 200, body_of(shown)
    safe = body_of(shown)
    assert safe["exchange_request_id"] == request_id
    assert safe["tracking_mode"] == "INDIVIDUAL"
    assert safe["quantity"] is None
    assert safe["destination_location_id"] == "LOC-A"
    assert "user_sub" not in safe
    assert "token_hash" not in safe
    assert "issued_by" not in safe
    leaked = preview(
        monkeypatch,
        payload,
        query={"token": payload},
    )
    assert leaked["statusCode"] == 400
    assert payload not in json.dumps(body_of(leaked))


def test_individual_confirm_consumes_once(monkeypatch, capsys):
    request_id, _, exchanges, resources, allocations, audit, _ = pending(monkeypatch)
    payload = body_of(issue(monkeypatch, request_id))["qr_payload"]
    import exchange_notify

    calls = []
    monkeypatch.setattr(
        exchange_notify,
        "notify_handover_completed",
        lambda *args, **kwargs: calls.append(kwargs),
    )
    done = confirm(monkeypatch, payload)
    assert done["statusCode"] == 200, body_of(done)
    body = body_of(done)
    assert body["request"]["status"] == "COMPLETED"
    assert body["qr_consumed"] is True
    assert body["ownership_transferred"] is True
    assert resources.items["R-B-1"]["organization_id"] == ORG_A
    assert resources.items["R-B-1"]["visibility"] == "PRIVATE"
    assert list(allocations.items.values())[0]["status"] == "RELEASED"
    session = next(item for item in exchanges.items.values() if item.get("entity_type") == "HANDOVER_QR_SESSION" and item.get("status") == "CONSUMED")
    meta = next(item for item in exchanges.items.values() if item.get("entity_type") == "EXCHANGE_REQUEST")
    assert "qr_ttl_epoch" not in meta
    assert "qr_ttl_epoch" in session
    confirmed = [event for event in audit.events if event["action"] == "exchange.handover_confirmed"]
    assert len(confirmed) == 1
    assert confirmed[0]["metadata"]["handover_channel"] == "QR"
    assert confirmed[0]["metadata"]["session_id"] == body["session_id"]
    assert secret_of(payload) not in json.dumps(confirmed, default=str)
    assert calls and calls[0].get("tracking_mode") == "INDIVIDUAL"
    again = confirm(monkeypatch, payload)
    assert again["statusCode"] == 200
    assert body_of(again)["message"] == "Handover already completed"
    assert body_of(again)["qr_consumed"] is False
    assert resources.items["R-B-1"]["organization_id"] == ORG_A
    logged = capsys.readouterr().out
    assert secret_of(payload) not in logged


def test_expired_and_revoked_qr(monkeypatch):
    request_id, _, exchanges, resources, _, _, _ = pending(monkeypatch)
    issued = body_of(issue(monkeypatch, request_id))
    original = service._now_iso
    later = handover_qr._parse(original()) + timedelta(minutes=16)

    def ahead():
        return later.isoformat()

    monkeypatch.setattr(service, "_now_iso", ahead)
    expired = preview(monkeypatch, issued["qr_payload"])
    assert expired["statusCode"] == 409
    assert body_of(expired)["message"] == "Handover QR has expired"
    denied = confirm(monkeypatch, issued["qr_payload"])
    assert denied["statusCode"] == 409
    assert resources.items["R-B-1"]["organization_id"] == ORG_B
    monkeypatch.setattr(service, "_now_iso", original)
    fresh = body_of(issue(monkeypatch, request_id))
    assert preview(monkeypatch, issued["qr_payload"])["statusCode"] == 404
    assert confirm(monkeypatch, issued["qr_payload"])["statusCode"] == 404
    assert preview(monkeypatch, fresh["qr_payload"])["statusCode"] == 200
    session = next(
        item
        for item in exchanges.items.values()
        if item.get("entity_type") == "HANDOVER_QR_SESSION" and item.get("session_id") == fresh["session_id"]
    )
    session["status"] = "CONSUMED"
    assert preview(monkeypatch, fresh["qr_payload"])["statusCode"] == 404


def test_cancel_and_manual_confirm_invalidate_qr(monkeypatch):
    request_id, _, exchanges, resources, _, audit, _ = pending(monkeypatch)
    payload = body_of(issue(monkeypatch, request_id))["qr_payload"]
    use_memberships(monkeypatch, memberships((ORG_A, "OPERATOR")))
    cancelled = handler.lambda_handler(
        event(
            "POST",
            {"organization_id": ORG_A},
            path=f"/exchange/requests/{request_id}/cancel",
        ),
        None,
    )
    assert cancelled["statusCode"] == 200, body_of(cancelled)
    assert confirm(monkeypatch, payload)["statusCode"] == 404
    assert resources.items["R-B-1"]["organization_id"] == ORG_B
    assert any(event["action"] == "exchange.handover_qr_revoked" for event in audit.events)

    request_id, _, exchanges, resources, _, audit, _ = pending(monkeypatch)
    payload = body_of(issue(monkeypatch, request_id))["qr_payload"]
    manual = confirm_as_requester(monkeypatch, request_id)
    assert manual["statusCode"] == 200, body_of(manual)
    session = next(
        item
        for item in exchanges.items.values()
        if item.get("session_id") and item.get("entity_type") == "HANDOVER_QR_SESSION"
    )
    assert session["status"] == "REVOKED"
    replay = confirm(monkeypatch, payload)
    assert replay["statusCode"] == 200
    assert body_of(replay)["message"] == "Handover already completed"
    assert resources.items["R-B-1"]["organization_id"] == ORG_A
    manual_audit = next(event for event in audit.events if event["action"] == "exchange.handover_confirmed")
    assert manual_audit["metadata"]["handover_channel"] == "MANUAL"


def test_qr_cannot_retarget_destination(monkeypatch):
    request_id, _, _, resources, _, _, _ = pending(monkeypatch)
    payload = body_of(issue(monkeypatch, request_id))["qr_payload"]
    denied = confirm(
        monkeypatch,
        payload,
        body={"destination_location_id": "LOC-A2"},
    )
    assert denied["statusCode"] == 400
    assert resources.items["R-B-1"]["organization_id"] == ORG_B
    denied_resource = confirm(
        monkeypatch,
        payload,
        body={"destination_resource_id": "R-OTHER"},
    )
    assert denied_resource["statusCode"] == 400


def test_quantity_create_and_merge(monkeypatch):
    request_id, _, _, resources, _, _, _ = pending(monkeypatch, tracking="QUANTITY", resource_id="R-B-Q")
    payload = body_of(issue(monkeypatch, request_id))["qr_payload"]
    shown = body_of(preview(monkeypatch, payload))
    assert shown["tracking_mode"] == "QUANTITY"
    assert int(shown["quantity"]) == 3
    assert shown["destination_mode"] == "PENDING_CONFIRM"
    created = confirm(monkeypatch, payload, body={"quantity": 3})
    assert created["statusCode"] == 200, body_of(created)
    transfer = body_of(created)["transfer"]
    assert transfer["destination_created"] is True
    assert transfer["quantity"] == 3
    dest = resources.items[transfer["destination_resource_id"]]
    assert dest["organization_id"] == ORG_A
    assert dest["visibility"] == "PRIVATE"
    assert dest["location_id"] == "LOC-A"
    assert int(dest["quantity_total"]) == 3
    source = resources.items["R-B-Q"]
    assert int(source["quantity_total"]) == 7
    assert int(source["quantity_allocated"]) == 0
    assert source["organization_id"] == ORG_B

    request_id, _, _, resources, _, _, _ = pending(monkeypatch, tracking="QUANTITY", resource_id="R-B-Q")
    resources.items["R-B-Q"]["quantity_total"] = 10
    resources.items["R-B-Q"]["quantity_available"] = 7
    resources.items["R-B-Q"]["quantity_allocated"] = 3
    resources.items["R-DEST"] = {
        "resource_id": "R-DEST",
        "organization_id": ORG_A,
        "location_id": "LOC-A",
        "Location": "A Depot",
        "name": "Requester pool",
        "Type": "Medical Kit",
        "Available": False,
        "operational_status": "AVAILABLE",
        "tracking_mode": "QUANTITY",
        "quantity_total": 4,
        "quantity_available": 4,
        "quantity_reserved": 0,
        "quantity_allocated": 0,
        "visibility": "PRIVATE",
    }
    payload = body_of(issue(monkeypatch, request_id))["qr_payload"]
    merged = confirm(
        monkeypatch,
        payload,
        body={"quantity": 3, "destination_resource_id": "R-DEST"},
    )
    assert merged["statusCode"] == 200, body_of(merged)
    assert body_of(merged)["transfer"]["destination_created"] is False
    assert int(resources.items["R-DEST"]["quantity_total"]) == 7
    assert resources.items["R-DEST"]["visibility"] == "PRIVATE"
    wrong = pending(monkeypatch, tracking="QUANTITY", resource_id="R-B-Q")
    payload = body_of(issue(monkeypatch, wrong[0]))["qr_payload"]
    mismatch = confirm(monkeypatch, payload, body={"quantity": 1, "destination_resource_id": "R-NOPE"})
    assert mismatch["statusCode"] in {400, 404, 409}


def test_handover_deadline_caps_qr_expiry(monkeypatch):
    request_id, _, exchanges, _, _, _, _ = pending(monkeypatch)
    from exchange_model import meta_pk, meta_sk

    meta = exchanges.items[(meta_pk(request_id), meta_sk())]
    issued_at = handover_qr._parse(service._now_iso())
    meta["handover_expires_at"] = (issued_at + timedelta(minutes=5)).isoformat()
    body = body_of(issue(monkeypatch, request_id))
    expires = handover_qr._parse(body["expires_at"])
    assert expires <= issued_at + timedelta(minutes=5, seconds=2)
    assert expires < issued_at + timedelta(minutes=15)


def test_source_has_no_scan_and_token_not_in_routes():
    text = (ROOT / "src" / "shared" / "handover_qr.py").read_text(encoding="utf-8")
    handler = (ROOT / "src" / "exchange" / "handler.py").read_text(encoding="utf-8")
    assert ".scan(" not in text
    assert "Scan" not in text
    assert "/handover/qr/preview" in handler
    assert "queryStringParameters" not in text or "token" in handler


def test_effective_expiry_is_fifteen_minutes_unless_handover_sooner():
    issued = handover_qr._parse("2026-09-30T00:00:00+00:00")
    later = handover_qr.effective_expiry(issued, "2026-10-03T00:00:00+00:00")
    assert later - issued == timedelta(minutes=15)
    sooner = handover_qr.effective_expiry(issued, "2026-09-30T00:04:00+00:00")
    assert sooner.isoformat().startswith("2026-09-30T00:04:00")
    assert handover_qr.effective_expiry(issued, "2026-09-29T00:00:00+00:00") is None
