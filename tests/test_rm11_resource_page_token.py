"""RM-11: resource list continuation tokens are signed and bound."""

import hashlib
import hmac
import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [
    str(ROOT / "src" / "shared"),
    str(ROOT / "src" / "exchange"),
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


tokens = load_module("resource_page_token_rm11", "src/shared/resource_page_token.py")
network = load_module("network_page_token_rm11", "src/exchange/network_page_token.py")
resource_handler = load_module("resource_handler_rm11", "src/resource/handler.py")
observability = load_module("observability_rm11", "src/shared/observability.py")

from access import AccessError
from api_views import RESOURCE_FIELDS
from pages import encode_token

SECRET = "synthetic-resource-page-token-secret-v1"
PREVIOUS = "synthetic-resource-page-token-secret-v0"
OTHER_SECRET = "synthetic-resource-page-token-secret-v2"
NOW = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
ORG = "ORG-A"
OTHER = "ORG-B"
CURSOR = {
    "organization_id": ORG,
    "location_id": "LOC-A",
    "resource_id": "R1",
}


def _sign(**overrides):
    values = {
        "cursor": CURSOR,
        "organization_id": ORG,
        "location_id": "",
        "status": "",
        "limit": 10,
        "secret": SECRET,
        "now": NOW,
    }
    values.update(overrides)
    return tokens.sign_resource_page_token(**values)


def _read(token, **overrides):
    values = {
        "token": token,
        "organization_id": ORG,
        "location_id": "",
        "status": "",
        "limit": 10,
        "secrets": [SECRET, PREVIOUS],
        "now": NOW,
    }
    values.update(overrides)
    return tokens.read_resource_page_token(**values)


def _reject(token, **overrides):
    with pytest.raises(AccessError) as caught:
        _read(token, **overrides)
    assert caught.value.status_code == 400
    assert caught.value.message == "Invalid page token"
    assert SECRET not in caught.value.message
    assert "signature" not in caught.value.message.lower()


def _forge(payload, secret=SECRET):
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    return tokens._b64encode(body) + "." + tokens._b64encode(signature)


def _payload(token=None):
    token = token or _sign()
    body = tokens._b64decode(token.split(".", 1)[0])
    return json.loads(body.decode("utf-8"))


class ListTable:
    def __init__(self, items):
        self.items = list(items)
        self.queries = []

    def query(self, **kwargs):
        self.queries.append(kwargs)
        rows = list(self.items)
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


def _resource(resource_id, status="AVAILABLE", **extra):
    row = {
        "resource_id": resource_id,
        "organization_id": ORG,
        "location_id": "LOC-A",
        "name": resource_id,
        "Type": "Kit",
        "Available": status == "AVAILABLE",
        "operational_status": status,
        "tracking_mode": "INDIVIDUAL",
        "visibility": "PRIVATE",
    }
    row.update(extra)
    return row


def _listed(monkeypatch, items, query, loads):
    table = ListTable(items)
    monkeypatch.setattr(resource_handler, "resources_table", lambda: table)
    monkeypatch.setattr(resource_handler, "locations_table", lambda: object())
    monkeypatch.setattr(resource_handler, "require_location", lambda *_args: None)

    def load(client=None, secret_id=None):
        loads.append(secret_id)
        return SECRET, [PREVIOUS]

    monkeypatch.setattr(resource_handler, "load_resource_page_secret", load)
    result = resource_handler.list_resources({"queryStringParameters": query}, ORG)
    return result, json.loads(result["body"]), table


def test_first_page_without_a_token_does_not_load_a_secret(monkeypatch):
    loads = []
    result, body, table = _listed(monkeypatch, [_resource("R1")], {}, loads)
    assert result["statusCode"] == 200
    assert body[0]["resource_id"] == "R1"
    assert loads == []
    assert "ExclusiveStartKey" not in table.queries[0]
    assert _read(None) is None


def test_signed_token_continues_and_can_be_replayed(monkeypatch):
    loads = []
    rows = [_resource("R1"), _resource("R2", "MAINTENANCE"), _resource("R3")]
    first, body, _table = _listed(monkeypatch, rows, {"status": "AVAILABLE", "limit": "2"}, loads)
    assert [item["resource_id"] for item in body["resources"]] == ["R1"]
    token = body["next_token"]
    assert token.count(".") == 1
    current = datetime.now(timezone.utc)
    cursor = _read(token, status="AVAILABLE", limit=2, now=current)
    assert cursor["resource_id"] == "R2"
    assert _read(token, status="AVAILABLE", limit=2, now=current) == cursor

    second, next_body, table = _listed(
        monkeypatch,
        rows,
        {"status": "AVAILABLE", "limit": "2", "page_token": token},
        [],
    )
    assert second["statusCode"] == 200
    assert [item["resource_id"] for item in next_body["resources"]] == ["R3"]
    assert table.queries[0]["ExclusiveStartKey"]["resource_id"] == "R2"
    assert "st" not in table.queries[0]["ExclusiveStartKey"]
    assert next_body["next_token"] is None


def test_tampered_payload_and_signature_fail():
    token = _sign()
    payload, signature = token.split(".")
    flipped_payload = ("A" if payload[0] != "A" else "B") + payload[1:]
    flipped_signature = ("A" if signature[0] != "A" else "B") + signature[1:]
    _reject(flipped_payload + "." + signature)
    _reject(payload + "." + flipped_signature)
    _reject(_sign(), secrets=[OTHER_SECRET])


@pytest.mark.parametrize(
    "token",
    [
        "",
        "   ",
        "not-a-token",
        "@@@.@@@",
        "aaaa.bbbb.cccc",
        "a" * 2001,
        "abc",
    ],
)
def test_malformed_and_empty_tokens_fail(token):
    _reject(token)


def test_expired_future_and_skew_follow_the_continuation_window():
    _reject(_sign(now=NOW - timedelta(seconds=tokens.TTL_SECONDS)))
    _reject(_sign(now=NOW + timedelta(seconds=tokens.CLOCK_SKEW_SECONDS + 1)))
    within = _sign(now=NOW + timedelta(seconds=tokens.CLOCK_SKEW_SECONDS))
    assert _read(within) == CURSOR


def test_context_binding_rejects_mismatched_request_fields():
    token = _sign(location_id="LOC-A", status="MAINTENANCE")
    _reject(token, organization_id=OTHER, location_id="LOC-A", status="MAINTENANCE")
    _reject(token, location_id="LOC-B", status="MAINTENANCE")
    _reject(token, location_id="LOC-A", status="AVAILABLE")
    _reject(token, location_id="LOC-A", status="MAINTENANCE", limit=11)
    assert _read(token, location_id="LOC-A", status="MAINTENANCE") == CURSOR


def test_invalid_cursor_version_and_field_types_fail():
    payload = _payload()
    payload["v"] = 2
    _reject(_forge(payload))
    payload = _payload()
    payload["e"] = "exchange.network"
    _reject(_forge(payload))
    payload = _payload()
    del payload["iat"]
    _reject(_forge(payload))
    payload = _payload()
    payload["l"] = "10"
    _reject(_forge(payload))
    payload = _payload()
    payload["l"] = True
    _reject(_forge(payload))
    payload = _payload()
    payload["iat"] = -100
    payload["exp"] = -100 + tokens.TTL_SECONDS
    _reject(_forge(payload))
    payload = _payload()
    payload["exp"] = payload["iat"] + 999999
    _reject(_forge(payload))
    payload = _payload()
    payload["c"] = {"organization_id": ORG}
    _reject(_forge(payload))
    payload = _payload()
    payload["c"]["organization_id"] = OTHER
    _reject(_forge(payload))
    payload = _payload()
    payload["c"]["resource_id"] = ""
    _reject(_forge(payload))


def test_resource_and_exchange_tokens_are_not_interchangeable():
    resource_token = _sign()
    with pytest.raises(AccessError) as caught:
        network.read_network_page_token(
            resource_token,
            organization_id=ORG,
            limit=10,
            secrets=[SECRET],
            now=NOW,
        )
    assert caught.value.message == "Invalid page token"

    exchange_token = network.sign_network_page_token(
        {
            "network_list_key": "OPEN",
            "created_at": "2026-10-08T00:00:00Z",
            "pk": "EXREQ#EXREQ-OPEN",
            "sk": "META",
        },
        organization_id=ORG,
        limit=10,
        secret=SECRET,
        now=NOW,
    )
    _reject(exchange_token)


def test_unsigned_token_is_rejected():
    unsigned = encode_token(CURSOR)
    _reject(unsigned)


def test_previous_key_still_validates_and_new_tokens_use_the_current_key():
    older = _sign(secret=PREVIOUS)
    assert _read(older) == CURSOR
    current = _sign(secret=SECRET)
    assert _read(current, secrets=[SECRET]) == CURSOR
    _reject(current, secrets=[PREVIOUS])


def test_token_contents_and_logs_do_not_carry_secrets(capsys):
    token = _sign()
    payload = _payload(token)
    assert set(payload) == tokens._PAYLOAD_KEYS
    assert "authorization" not in payload
    assert "email" not in payload
    raw = json.dumps(payload)
    assert "Bearer" not in raw
    assert SECRET not in raw

    observability.log_event(
        "WARNING",
        "api",
        "resource",
        "denied",
        page_token=token,
        next_token=token,
        token=token,
        signature=token.split(".", 1)[1],
        secret=SECRET,
        authorization="Bearer cognito-token",
    )
    logged = capsys.readouterr().out
    assert token not in logged
    assert SECRET not in logged
    assert "Bearer" not in logged
    assert "cognito-token" not in logged


def test_handler_rejects_a_bad_token_without_logging_it(monkeypatch, capsys):
    token = _sign()
    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
    result, body, table = _listed(
        monkeypatch,
        [_resource("R1"), _resource("FOREIGN", organization_id=OTHER)],
        {"page_token": tampered, "limit": "10"},
        [],
    )
    assert result["statusCode"] == 400
    assert body["message"] == "Invalid page token"
    assert table.queries == []
    logged = capsys.readouterr().out
    assert tampered not in logged
    assert SECRET not in logged
    assert "FOREIGN" not in result["body"]


def test_wrong_limit_does_not_query(monkeypatch):
    token = _sign(limit=10)
    result, body, table = _listed(
        monkeypatch,
        [_resource("R1")],
        {"page_token": token, "limit": "9"},
        [],
    )
    assert result["statusCode"] == 400
    assert body["message"] == "Invalid page token"
    assert table.queries == []


def test_list_response_stays_on_the_allowlist(monkeypatch):
    row = _resource("R1")
    row["reserved_by"] = "cognito-sub"
    row["internal_secret_field"] = "should-never-leak"
    result, body, _table = _listed(monkeypatch, [row], {"status": "AVAILABLE"}, [])
    assert result["statusCode"] == 200
    assert set(body[0]) <= set(RESOURCE_FIELDS)
    assert "reserved_by" not in result["body"]
    assert "should-never-leak" not in result["body"]


def test_source_does_not_keep_an_unsigned_resource_continuation():
    handler = (ROOT / "src" / "resource" / "handler.py").read_text(encoding="utf-8")
    module = (ROOT / "src" / "shared" / "resource_page_token.py").read_text(encoding="utf-8")
    listing = handler.split("def list_resources", 1)[1].split("def resource_history", 1)[0]
    assert "encode_token(" not in listing
    assert "decode_token(" not in listing
    assert "hmac.compare_digest" in module
    assert "sha256" in module
    assert SECRET not in module
    assert "erap/resource/page-token" in module
    app = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
    start = app.index("async function loadResources")
    end = app.index("RESOURCE PAGE FILTER")
    resource_load = app[start:end]
    assert "page_token" not in resource_load
    assert "atob(" not in resource_load
