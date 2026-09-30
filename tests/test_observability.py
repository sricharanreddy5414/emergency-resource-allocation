"""Phase 11B operational logging. Business responses stay unchanged."""

from __future__ import annotations

import importlib
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHARED = ROOT / "src" / "shared"
sys.path.insert(0, str(SHARED))
sys.path.insert(0, str(ROOT / "src"))

import observability


def _reload():
    return importlib.reload(observability)


def _events(capsys):
    text = capsys.readouterr().out
    events = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("{"):
            events.append(json.loads(line))
    return events


def test_correlation_id_reuses_api_gateway_request_id():
    mod = _reload()
    request_id = mod.begin_request({"requestContext": {"requestId": "gw-req-1"}, "httpMethod": "GET", "path": "/resources"})
    assert request_id == "gw-req-1"
    assert mod.current_request_id() == "gw-req-1"


def test_missing_request_id_is_opaque_and_not_a_token():
    mod = _reload()
    request_id = mod.begin_request({"headers": {"Authorization": "Bearer a.b.cccccccccccccccc"}})
    assert request_id
    assert "Bearer" not in request_id
    assert request_id.count(".") != 2
    again = mod.begin_request({})
    assert again != request_id


def test_structured_log_redacts_secrets_and_hashes_actor(capsys):
    mod = _reload()
    mod.begin_request({"requestContext": {"requestId": "corr-1"}})
    payload = mod.log_event(
        "INFO",
        "exchange",
        "offer.create",
        "committed",
        organization_id="ORG-A66B0A1E4F96",
        actor_sub="user-sub-1",
        exchange_request_id="EXREQ-1",
        authorization="Bearer a.b.cccccccccccccccc",
        password="secret-pass",
        qr_payload="erap-hq.v1.raw-token",
        signature="razorpay-signature",
        webhook_secret="whsec",
        mfa="123456",
    )
    assert payload["correlation_id"] == "corr-1"
    assert payload["level"] == "INFO"
    assert payload["actor_sub_hash"] == mod.actor_hash("user-sub-1")
    assert "user-sub-1" not in json.dumps(payload)
    blob = json.dumps(payload)
    for secret in ("Bearer", "secret-pass", "erap-hq", "razorpay-signature", "whsec", "123456"):
        assert secret not in blob
    logged = _events(capsys)[0]
    assert logged["organization_id"] == "ORG-A66B0A1E4F96"
    assert logged["exchange_request_id"] == "EXREQ-1"


def test_actor_hash_is_stable_and_not_the_sub():
    mod = _reload()
    assert mod.actor_hash("abc") == mod.actor_hash("abc")
    assert mod.actor_hash("abc") != "abc"
    assert mod.actor_hash("") == ""


def test_authorization_denied_is_logged_without_tokens(monkeypatch, capsys):
    import access

    monkeypatch.setattr(
        access,
        "list_memberships",
        lambda *_args, **_kwargs: [
            {"organization_id": "ORG-A66B0A1E4F96", "role": "MEMBER", "status": "ACTIVE", "user_sub": "sub-1"}
        ],
    )
    monkeypatch.setattr(
        access,
        "get_user_sub",
        lambda event: event["requestContext"]["authorizer"]["claims"]["sub"],
    )

    try:
        access.authorize(
            {
                "requestContext": {"authorizer": {"claims": {"sub": "sub-1"}}, "requestId": "corr-auth"},
                "headers": {"Authorization": "Bearer a.b.cccccccccccccccc"},
            },
            {"organization_id": "ORG-A66B0A1E4F96"},
            allowed_roles={"OWNER"},
        )
    except access.AccessError as error:
        assert error.status_code == 403
    else:
        raise AssertionError("expected denial")
    blob = capsys.readouterr().out
    assert "role_denied" in blob
    assert "Bearer" not in blob
    assert "sub-1" not in blob
    assert observability.actor_hash("sub-1") in blob


def test_billing_webhook_log_omits_body_and_signature(capsys):
    import billing.webhook as webhook

    webhook._log("evt-1", "subscription.charged", "ORG-A66B0A1E4F96", "sub_test", "IGNORED")
    blob = capsys.readouterr().out
    event = json.loads(blob.strip().splitlines()[-1])
    assert event["service"] == "billing"
    assert event["outcome"] == "ignored"
    assert event["billing_event_id"] == "evt-1"
    assert "signature" not in event
    assert "payload" not in event


def test_qr_log_omits_token(capsys):
    import handover_qr

    importlib.reload(handover_qr)
    handover_qr.log_qr("issued", "SESS-1", "EXREQ-1")
    blob = capsys.readouterr().out
    assert "qr_result" in blob
    assert "erap-hq" not in blob
    assert "token_hash" not in blob


def test_expiry_summary_shape(capsys):
    saved = list(sys.path)
    sys.path.insert(0, str(ROOT / "src" / "exchange"))
    path = ROOT / "src" / "exchange" / "lifecycle.py"
    spec = importlib.util.spec_from_file_location("erap_exchange_lifecycle_obs", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["erap_exchange_lifecycle_obs"] = module
    try:
        spec.loader.exec_module(module)
        module.json_log("inv-1", "2026-09-30T00:00:00+00:00", {"expired": 1, "skipped": 2, "idempotent": 3, "conflict": 0})
    finally:
        sys.path[:] = saved
    event = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert event["service"] == "exchange-expiry"
    assert event["items_considered"] == 6
    assert event["items_expired"] == 1
    assert "pk" not in event


def test_no_custom_metric_dimensions():
    text = (SHARED / "observability.py").read_text(encoding="utf-8")
    assert "put_metric_data" not in text
    assert "PutMetricData" not in text
