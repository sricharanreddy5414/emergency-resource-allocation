import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [
    str(ROOT / "src"),
    str(ROOT / "src" / "shared"),
    str(ROOT / "src" / "organization"),
]

import access
import membership
from billing import api_handler
from billing.entitlements import (
    cancellation_window_open,
    is_billing_access_allowed,
    is_normal_access_allowed,
    is_operational_read_allowed,
    is_operational_write_allowed,
    subscription_status,
)

ORG_A = "ORG-A"
ORG_B = "ORG-B"
OWNER = "owner-sub"
NOW = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
SECRET = "secret-must-not-leak"


def load_module(name, relative):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


locations_handler = load_module("locations_handler_enforcement", "src/locations/handler.py")
request_handler = load_module("request_handler_enforcement", "src/request/handler.py")


def row(status, organization_id=ORG_A, **extra):
    item = {
        "organization_id": organization_id,
        "subscription_status": status,
        "cancel_at_period_end": False,
        "current_period_end": "",
        "provider_subscription_id": "sub_ServerOnly0001",
        "webhook_secret": SECRET,
    }
    item.update(extra)
    return item


class Subscriptions:
    def __init__(self, rows=None, fail=None):
        self.items = {item["organization_id"]: dict(item) for item in rows or []}
        self.fail = fail
        self.puts = 0
        self.reads = 0

    def get_item(self, Key):
        self.reads += 1
        if self.fail:
            raise self.fail
        item = self.items.get(Key["organization_id"])
        return {"Item": dict(item)} if item else {}

    def put_item(self, *args, **kwargs):
        self.puts += 1
        raise AssertionError("authorization must not create a subscription")


class Store:
    def __init__(self, items=None):
        self.items = list(items or [])
        self.puts = []

    def query(self, **kwargs):
        return {"Items": list(self.items)}

    def get_item(self, Key):
        for item in self.items:
            if all(item.get(key) == value for key, value in Key.items()):
                return {"Item": item}
        return {}

    def put_item(self, Item, ConditionExpression=None):
        self.puts.append(Item)
        self.items.append(Item)


def use_members(monkeypatch, records):
    monkeypatch.setattr(access, "list_memberships", lambda *args, **kwargs: records)


def member(organization_id, role, subject=OWNER):
    return {
        "organization_id": organization_id,
        "name": organization_id,
        "role": role,
        "status": "ACTIVE",
    }


def use_subscriptions(monkeypatch, subscriptions):
    monkeypatch.setattr(access, "subscriptions_table", lambda: subscriptions)


def location_event(method, role_subject=OWNER, organization_id=ORG_A, body=None):
    payload = {
        "httpMethod": method,
        "path": "/locations",
        "queryStringParameters": {"organization_id": organization_id},
        "requestContext": {"authorizer": {"claims": {"sub": role_subject}}},
    }
    if body is not None:
        payload["body"] = json.dumps(body)
    return payload


def wire_locations(monkeypatch):
    store = Store()
    monkeypatch.setattr(locations_handler, "locations_table", lambda: store)
    monkeypatch.setattr(locations_handler, "audit_table", lambda: None)
    return store


def request_event(role, organization_id=ORG_A, body_role="OWNER"):
    return {
        "httpMethod": "POST",
        "path": "/requests",
        "queryStringParameters": {"organization_id": organization_id},
        "requestContext": {"authorizer": {"claims": {"sub": role}}},
        "body": json.dumps(
            {
                "request_id": "REQ-1",
                "resource_type": "ICU_BED",
                "location_id": "LOC-A",
                "priority": 1,
                "organization_id": organization_id,
                "user_sub": "forged",
                "role": body_role,
            }
        ),
    }


def wire_requests(monkeypatch):
    requests = Store()
    locations = Store(
        [{
            "organization_id": ORG_A,
            "location_id": "LOC-A",
            "name": "North",
            "status": "ACTIVE",
        }]
    )
    monkeypatch.setattr(request_handler, "requests_table", lambda: requests)
    monkeypatch.setattr(request_handler, "locations_table", lambda: locations)
    monkeypatch.setattr(request_handler, "audit_table", lambda: None)
    return requests


def test_entitlement_matrix():
    future = (NOW + timedelta(days=3)).isoformat()
    past = (NOW - timedelta(days=1)).isoformat()
    active_window = row("ACTIVE", cancel_at_period_end=True, current_period_end=future)
    active_after = row("ACTIVE", cancel_at_period_end=True, current_period_end=past)
    expired = row("EXPIRED", cancel_at_period_end=True, current_period_end=future)

    assert subscription_status(None) == "GRANDFATHERED"
    for status in ("TRIALING", "ACTIVE", "PAST_DUE", "GRANDFATHERED"):
        item = row(status)
        assert is_operational_read_allowed(item)
        assert is_operational_write_allowed(item, NOW)
        assert is_normal_access_allowed(item, NOW)
        assert is_billing_access_allowed(item)

    assert is_operational_read_allowed(None)
    assert is_operational_write_allowed(None, NOW)
    assert cancellation_window_open(active_window, NOW)
    assert is_operational_write_allowed(active_window, NOW)
    assert is_operational_write_allowed(active_after, NOW)
    assert cancellation_window_open(active_after, NOW) is False
    assert is_operational_read_allowed(expired)
    assert is_operational_write_allowed(expired, NOW) is False
    assert is_billing_access_allowed(expired)
    cancelled = row("CANCELLED", cancel_at_period_end=True, current_period_end=future)
    assert is_operational_read_allowed(cancelled)
    assert is_operational_write_allowed(cancelled, NOW) is False
    assert is_operational_write_allowed(row("UNKNOWN"), NOW) is False


@pytest.mark.parametrize("status", ["TRIALING", "ACTIVE", "PAST_DUE", "GRANDFATHERED"])
def test_writable_states_can_create_a_location(monkeypatch, status):
    subscriptions = Subscriptions([row(status)])
    use_subscriptions(monkeypatch, subscriptions)
    use_members(monkeypatch, [member(ORG_A, "OWNER")])
    store = wire_locations(monkeypatch)

    result = locations_handler.lambda_handler(
        location_event("POST", body={"name": "North", "organization_id": ORG_A}),
        None,
    )

    assert result["statusCode"] == 201
    assert store.puts[0]["organization_id"] == ORG_A
    assert subscriptions.puts == 0


def test_active_cancellation_before_period_end_still_allows_writes(monkeypatch):
    future = (NOW + timedelta(days=2)).isoformat()
    subscriptions = Subscriptions([
        row("ACTIVE", cancel_at_period_end=True, current_period_end=future),
    ])
    use_subscriptions(monkeypatch, subscriptions)
    use_members(monkeypatch, [member(ORG_A, "ADMIN")])
    store = wire_locations(monkeypatch)

    result = locations_handler.lambda_handler(
        location_event("POST", body={"name": "North"}),
        None,
    )

    assert result["statusCode"] == 201
    assert store.puts


def test_missing_subscription_allows_writes_and_creates_no_row(monkeypatch):
    subscriptions = Subscriptions()
    use_subscriptions(monkeypatch, subscriptions)
    use_members(monkeypatch, [member(ORG_A, "OWNER")])
    store = wire_locations(monkeypatch)

    result = locations_handler.lambda_handler(
        location_event("POST", body={"name": "North"}),
        None,
    )

    assert result["statusCode"] == 201
    assert subscriptions.items == {}
    assert subscriptions.puts == 0
    assert store.puts


@pytest.mark.parametrize("role", ["OWNER", "ADMIN", "OPERATOR", "MEMBER"])
def test_expired_blocks_request_writes_for_every_role(monkeypatch, role):
    subscriptions = Subscriptions([row("EXPIRED")])
    use_subscriptions(monkeypatch, subscriptions)
    use_members(monkeypatch, [member(ORG_A, role)])
    requests = wire_requests(monkeypatch)

    result = request_handler.lambda_handler(request_event(OWNER), None)
    body = json.loads(result["body"])

    assert result["statusCode"] == 403
    assert body["error"]["code"] == "BILLING_REQUIRED"
    assert body["message"] == access.BILLING_REQUIRED
    assert body["error"]["request_id"]
    assert SECRET not in result["body"]
    assert "sub_ServerOnly0001" not in result["body"]
    assert requests.puts == []
    assert subscriptions.puts == 0


def test_expired_location_read_remains_available(monkeypatch):
    subscriptions = Subscriptions([row("EXPIRED")])
    use_subscriptions(monkeypatch, subscriptions)
    use_members(monkeypatch, [member(ORG_A, "MEMBER")])
    store = wire_locations(monkeypatch)
    store.items.append({
        "organization_id": ORG_A,
        "location_id": "LOC-A",
        "name": "North",
        "status": "ACTIVE",
    })

    result = locations_handler.lambda_handler(location_event("GET"), None)

    assert result["statusCode"] == 200
    assert json.loads(result["body"])["locations"][0]["name"] == "North"
    assert subscriptions.reads == 0


def test_cancelled_write_is_blocked_without_local_transition(monkeypatch):
    subscriptions = Subscriptions([
        row("CANCELLED", cancel_at_period_end=True, current_period_end=(NOW + timedelta(days=1)).isoformat()),
    ])
    use_subscriptions(monkeypatch, subscriptions)
    use_members(monkeypatch, [member(ORG_A, "OWNER")])
    store = wire_locations(monkeypatch)

    result = locations_handler.lambda_handler(location_event("POST", body={"name": "North"}), None)

    assert result["statusCode"] == 403
    assert json.loads(result["body"])["error"]["code"] == "BILLING_REQUIRED"
    assert subscriptions.items[ORG_A]["subscription_status"] == "CANCELLED"
    assert store.puts == []


def test_other_organization_selector_cannot_bypass_expiry(monkeypatch):
    subscriptions = Subscriptions([
        row("EXPIRED", ORG_A),
        row("TRIALING", ORG_B),
    ])
    use_subscriptions(monkeypatch, subscriptions)
    use_members(monkeypatch, [member(ORG_A, "OWNER")])
    store = wire_locations(monkeypatch)

    denied = locations_handler.lambda_handler(
        location_event("POST", organization_id=ORG_B, body={"name": "North", "organization_id": ORG_B}),
        None,
    )

    assert denied["statusCode"] == 403
    assert json.loads(denied["body"])["error"]["code"] == "FORBIDDEN"
    assert store.puts == []
    use_members(monkeypatch, [member(ORG_A, "OWNER"), member(ORG_B, "OWNER")])
    allowed = locations_handler.lambda_handler(
        location_event("POST", organization_id=ORG_B, body={"name": "North", "organization_id": ORG_B}),
        None,
    )
    blocked = locations_handler.lambda_handler(
        location_event("POST", organization_id=ORG_A, body={"name": "South", "organization_id": ORG_A}),
        None,
    )

    assert allowed["statusCode"] == 201
    assert store.puts[0]["organization_id"] == ORG_B
    assert blocked["statusCode"] == 403
    assert json.loads(blocked["body"])["error"]["code"] == "BILLING_REQUIRED"


def test_billing_endpoints_remain_available_when_expired(monkeypatch):
    monkeypatch.setattr(membership, "organizations_table", lambda: Store([
        {"organization_id": ORG_A, "name": "Alpha", "status": "ACTIVE"},
    ]))
    monkeypatch.setattr(membership, "members_table", lambda: _Members([
        {"organization_id": ORG_A, "user_sub": OWNER, "role": "OWNER", "status": "ACTIVE"},
    ]))
    billing_rows = Subscriptions([row("EXPIRED", plan_id="MONTHLY")])
    monkeypatch.setattr(api_handler, "subscriptions_table", lambda: billing_rows)
    monkeypatch.setattr(api_handler, "events_table", lambda: Store())
    monkeypatch.setattr(api_handler, "build_provider", lambda: (_ for _ in ()).throw(AssertionError("provider")))
    event = {
        "httpMethod": "GET",
        "path": "/billing",
        "requestContext": {"authorizer": {"claims": {"sub": OWNER}}},
    }

    read = api_handler.lambda_handler(event, None)
    event["httpMethod"] = "POST"
    event["path"] = "/billing/checkout"
    event["body"] = json.dumps({"plan_id": "MONTHLY"})
    checkout = api_handler.lambda_handler(event, None)
    checkout_body = json.loads(checkout["body"])

    assert read["statusCode"] == 200
    assert json.loads(read["body"])["subscription"]["subscription_status"] == "EXPIRED"
    assert checkout["statusCode"] == 409
    assert checkout_body["message"] == "Plan is not currently available for purchase"
    assert checkout_body["error"]["code"] != "BILLING_REQUIRED"
    assert billing_rows.puts == 0


def test_billing_lookup_failure_does_not_grandfather_a_write(monkeypatch):
    subscriptions = Subscriptions(fail=ClientError(
        {"Error": {"Code": "ProvisionedThroughputExceededException", "Message": "busy"}},
        "GetItem",
    ))
    use_subscriptions(monkeypatch, subscriptions)
    use_members(monkeypatch, [member(ORG_A, "OWNER")])
    store = wire_locations(monkeypatch)

    result = locations_handler.lambda_handler(location_event("POST", body={"name": "North"}), None)
    body = json.loads(result["body"])

    assert result["statusCode"] == 500
    assert body["message"] == "Unable to verify billing"
    assert "ProvisionedThroughputExceededException" not in result["body"]
    assert store.puts == []


class _Members:
    def __init__(self, rows):
        self.rows = rows
        self.writes = 0

    def query(self, **kwargs):
        return {"Items": list(self.rows)}

    def put_item(self, **kwargs):
        self.writes += 1
        raise AssertionError("invitation acceptance must not write when billing blocks it")

    def update_item(self, **kwargs):
        self.writes += 1
        raise AssertionError("invitation acceptance must not write when billing blocks it")


def test_expired_organization_blocks_invitation_acceptance(monkeypatch):
    import member_admin

    email = "member@example.com"
    members = _Members([
        {
            "organization_id": ORG_A,
            "user_sub": member_admin.invite_subject(email),
            "role": "MEMBER",
            "status": "PENDING",
            "email": email,
        }
    ])
    monkeypatch.setattr(membership, "members_table", lambda: members)
    use_subscriptions(monkeypatch, Subscriptions([row("EXPIRED")]))
    event = {
        "requestContext": {
            "authorizer": {
                "claims": {
                    "sub": "new-member",
                    "email": email,
                    "email_verified": "true",
                }
            }
        }
    }

    result = member_admin.handle_member_operation(
        event,
        {"operation": "accept_invitation", "organization_id": ORG_A},
        "new-member",
        None,
    )
    body = json.loads(result["body"])

    assert result["statusCode"] == 403
    assert body["error"]["code"] == "BILLING_REQUIRED"
    assert members.writes == 0
