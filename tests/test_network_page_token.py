"""Signed exchange network pagination tokens. Tests use a synthetic key only."""

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [
    str(ROOT / "src"),
    str(ROOT / "src" / "shared"),
    str(ROOT / "src" / "exchange"),
]

from access import AccessError
import network_page_token as tokens
from pages import decode_token


SECRET = "synthetic-network-page-token-secret-v1"
OTHER_SECRET = "synthetic-network-page-token-secret-v2"
NOW = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
ORG = "ORG-NETWORK-VIEWER"
OTHER_ORG = "ORG-OTHER-VIEWER"
CURSOR = {
    "network_list_key": "OPEN",
    "created_at": "2026-10-07T00:00:00Z",
    "pk": "EXREQ#EXREQ-OPEN",
    "sk": "META",
}
PRIVATE_CURSOR = {
    "requester_organization_id": ORG,
    "created_at": "2026-10-07T00:00:00Z",
    "pk": "EXREQ#EXREQ-MINE",
    "sk": "META",
}


def _sign(**overrides):
    values = {
        "cursor": CURSOR,
        "organization_id": ORG,
        "limit": 25,
        "secret": SECRET,
        "now": NOW,
    }
    values.update(overrides)
    return tokens.sign_network_page_token(**values)


def _read(token, **overrides):
    values = {
        "token": token,
        "organization_id": ORG,
        "limit": 25,
        "secrets": [SECRET],
        "now": NOW,
    }
    values.update(overrides)
    return tokens.read_network_page_token(**values)


def _reject(token, **overrides):
    with pytest.raises(AccessError) as caught:
        _read(token, **overrides)
    assert caught.value.status_code == 400
    assert caught.value.message == "Invalid page token"
    assert "signature" not in caught.value.message.lower()
    assert SECRET not in caught.value.message
    assert OTHER_SECRET not in caught.value.message


def _service():
    path = ROOT / "src" / "exchange" / "service.py"
    spec = importlib.util.spec_from_file_location("network_page_service", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _QueryTable:
    def __init__(self, items=None, last_key=None):
        self.items = items or []
        self.last_key = last_key
        self.queries = []

    def query(self, **kwargs):
        self.queries.append(kwargs)
        body = {"Items": list(self.items)}
        if self.last_key:
            body["LastEvaluatedKey"] = self.last_key
        return body


def test_valid_token_is_accepted_and_can_be_reused_for_the_same_query():
    token = _sign()
    assert _read(token) == CURSOR
    assert _read(token) == CURSOR


def test_modified_payload_is_rejected():
    token = _sign()
    payload, signature = token.split(".")
    flipped = ("A" if payload[0] != "A" else "B") + payload[1:]
    _reject(flipped + "." + signature)


def test_modified_signature_is_rejected():
    token = _sign()
    payload, signature = token.split(".")
    flipped = ("A" if signature[0] != "A" else "B") + signature[1:]
    _reject(payload + "." + flipped)


def test_wrong_secret_is_rejected():
    _reject(_sign(), secrets=[OTHER_SECRET])


def test_token_for_another_endpoint_is_rejected():
    body = {
        "v": tokens.VERSION,
        "e": "exchange.mine",
        "o": ORG,
        "l": 25,
        "c": CURSOR,
        "iat": int(NOW.timestamp()),
        "exp": int(NOW.timestamp()) + tokens.TTL_SECONDS,
    }
    raw = json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")
    import hashlib
    import hmac

    signature = hmac.new(SECRET.encode("utf-8"), raw, hashlib.sha256).digest()
    token = tokens._b64encode(raw) + "." + tokens._b64encode(signature)
    _reject(token)


def test_token_for_another_query_is_rejected():
    token = _sign()
    _reject(token, organization_id=OTHER_ORG)
    _reject(token, limit=10)
    other_limit = _sign(limit=10)
    _reject(other_limit, limit=25)


def test_expired_token_is_rejected():
    token = _sign()
    _reject(token, now=NOW + timedelta(seconds=tokens.TTL_SECONDS))


def test_unsupported_version_is_rejected():
    body = {
        "v": 2,
        "e": tokens.ENDPOINT,
        "o": ORG,
        "l": 25,
        "c": CURSOR,
        "iat": int(NOW.timestamp()),
        "exp": int(NOW.timestamp()) + tokens.TTL_SECONDS,
    }
    raw = json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")
    import hashlib
    import hmac

    signature = hmac.new(SECRET.encode("utf-8"), raw, hashlib.sha256).digest()
    _reject(tokens._b64encode(raw) + "." + tokens._b64encode(signature))


@pytest.mark.parametrize(
    "token",
    ["", "not-a-token", "abc", "@@@.@@@", "a.b.c", "aaaa.bbbb"],
)
def test_malformed_token_is_rejected_and_missing_token_is_first_page(token):
    if token == "":
        assert tokens.read_network_page_token(
            None,
            organization_id=ORG,
            limit=25,
            secrets=[SECRET],
            now=NOW,
        ) is None
        assert _read("") is None
        return
    _reject(token)


def test_network_pagination_returns_the_next_page(monkeypatch):
    service = _service()
    table = _QueryTable(
        items=[
            {
                "entity_type": "EXCHANGE_REQUEST",
                "status": "OPEN",
                "requester_organization_id": OTHER_ORG,
                "requester_organization_display_name": "Other",
                "exchange_request_id": "EXREQ-OPEN",
                "resource_type_name": "Water",
                "tracking_mode": "QUANTITY",
                "quantity_requested": 2,
                "destination_city": "Hyderabad",
                "destination_state": "Telangana",
                "expires_at": "2026-10-08T00:00:00Z",
                "created_at": CURSOR["created_at"],
                "network_list_key": "OPEN",
            }
        ],
        last_key=dict(CURSOR),
    )
    monkeypatch.setattr(service, "_require_active_organization", lambda org: {"status": "ACTIVE"})
    monkeypatch.setattr(service, "_network_page_secrets", lambda: [SECRET])
    monkeypatch.setattr(service, "exchanges_table", lambda: table)

    first = service.list_network_requests(ORG, {"limit": "25"})
    assert "ExclusiveStartKey" not in table.queries[0]
    assert table.queries[0]["IndexName"] == "NetworkOpenRequestIndex"
    assert first["items"][0]["exchange_request_id"] == "EXREQ-OPEN"
    assert first["items"][0]["requester_organization_display_name"] == "Other"
    assert "requester_organization_id" not in first["items"][0]
    second_token = first["next_token"]
    assert _read(second_token, now=datetime.now(timezone.utc)) == CURSOR

    service.list_network_requests(ORG, {"limit": "25", "page_token": second_token})
    assert table.queries[1]["ExclusiveStartKey"] == CURSOR


def test_private_exchange_pagination_stays_unsigned(monkeypatch):
    service = _service()
    table = _QueryTable(last_key=dict(PRIVATE_CURSOR))
    monkeypatch.setattr(service, "_require_active_organization", lambda org: {"status": "ACTIVE"})
    monkeypatch.setattr(service, "exchanges_table", lambda: table)
    listed = service.list_my_requests(ORG, {})
    assert "." not in listed["next_token"]
    assert decode_token(
        listed["next_token"],
        ["requester_organization_id", "created_at", "pk", "sk"],
    )["pk"] == "EXREQ#EXREQ-MINE"


def test_cross_tenant_network_discovery_stays_open(monkeypatch):
    service = _service()
    own = {
        "entity_type": "EXCHANGE_REQUEST",
        "status": "OPEN",
        "requester_organization_id": ORG,
        "exchange_request_id": "EXREQ-OWN",
        "resource_type_name": "Blankets",
        "tracking_mode": "QUANTITY",
        "quantity_requested": 1,
        "destination_city": "Pune",
        "destination_state": "Maharashtra",
        "expires_at": "2026-10-08T00:00:00Z",
        "created_at": "2026-10-07T01:00:00Z",
        "network_list_key": "OPEN",
        "requester_organization_display_name": "Viewer",
    }
    other = dict(own)
    other["requester_organization_id"] = OTHER_ORG
    other["exchange_request_id"] = "EXREQ-OTHER"
    other["requester_organization_display_name"] = "Neighbor"
    table = _QueryTable(items=[own, other])
    monkeypatch.setattr(service, "_require_active_organization", lambda org: {"status": "ACTIVE"})
    monkeypatch.setattr(service, "exchanges_table", lambda: table)
    listed = service.list_network_requests(ORG, {})
    ids = [item["exchange_request_id"] for item in listed["items"]]
    assert ids == ["EXREQ-OTHER"]
    assert listed["next_token"] is None
    expression = table.queries[0]["KeyConditionExpression"].get_expression()
    assert expression["values"][0].name == "network_list_key"
    assert expression["values"][1] == "OPEN"
    assert ORG not in json.dumps(expression, default=str)


def test_token_contains_no_credentials_or_signing_secret():
    token = _sign()
    payload = json.loads(tokens._b64decode(token.split(".", 1)[0]).decode("utf-8"))
    assert SECRET not in token
    assert "Bearer" not in token
    assert set(payload) == {"v", "e", "o", "l", "c", "iat", "exp"}
    assert "authorization" not in payload
    assert "user_sub" not in payload


def test_logs_do_not_print_the_raw_token(capsys):
    import observability

    token = _sign()
    observability.log_event(
        "WARNING",
        "exchange",
        "network.list",
        "invalid_page_token",
        page_token=token,
        signature=token.split(".", 1)[1],
        secret=SECRET,
        token_present=True,
    )
    logged = capsys.readouterr().out
    assert token not in logged
    assert SECRET not in logged
    assert "token_present" in logged
    assert "invalid_page_token" in logged


def test_client_cannot_change_the_query_binding():
    token = _sign()
    payload, signature = token.split(".")
    body = json.loads(tokens._b64decode(payload).decode("utf-8"))
    body["o"] = OTHER_ORG
    raw = json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")
    _reject(tokens._b64encode(raw) + "." + signature)


def test_previous_key_still_verifies_and_current_key_signs():
    old = _sign(secret=OTHER_SECRET)
    assert _read(old, secrets=[SECRET, OTHER_SECRET]) == CURSOR
    current = _sign()
    assert _read(current, secrets=[SECRET, OTHER_SECRET]) == CURSOR
    _reject(current, secrets=[OTHER_SECRET])


def test_secret_material_parser_rejects_short_values_and_keeps_previous():
    current, previous = tokens.parse_secret_material(
        json.dumps({"current": SECRET, "previous": OTHER_SECRET})
    )
    assert current == SECRET
    assert previous == [OTHER_SECRET]
    with pytest.raises(AccessError) as caught:
        tokens.parse_secret_material("too-short")
    assert caught.value.status_code == 500
    assert "too-short" not in caught.value.message


def test_secret_loader_uses_the_supplied_client_and_does_not_cache_it():
    tokens.reset_secret_cache()

    class FakeSecrets:
        def __init__(self, text):
            self.text = text
            self.calls = 0

        def get_secret_value(self, SecretId):
            self.calls += 1
            assert SecretId == tokens.SECRET_ID
            return {"SecretString": self.text}

    client = FakeSecrets(SECRET)
    assert tokens.load_network_page_secret(client=client) == (SECRET, [])
    assert tokens.load_network_page_secret(client=client) == (SECRET, [])
    assert client.calls == 2
    tokens.reset_secret_cache()


def test_exchange_package_contains_the_token_module():
    sys.path.insert(0, str(ROOT / "scripts"))
    from lambda_manifest import EXCHANGE_PACKAGES

    assert EXCHANGE_PACKAGES["erap-exchange"]["network_page_token.py"].endswith(
        "network_page_token.py"
    )


def test_frontend_exchange_network_treats_tokens_as_opaque():
    app = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
    start = app.index("async function loadExchangeWorkspace")
    end = app.index("\nasync function ", start + 1)
    chunk = app[start:end]
    assert 'scope: "network"' in chunk
    assert "page_token" not in chunk
    assert "atob" not in chunk
    assert "next_token" not in chunk
