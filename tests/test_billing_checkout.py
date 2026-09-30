import json
import socket
import sys
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

import pytest
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [
    str(ROOT / "src"),
    str(ROOT / "src" / "shared"),
    str(ROOT / "src" / "organization"),
]

from billing import checkout_handler
import membership
from billing.checkout import create_checkout
from billing.errors import BillingError
from billing.models import access_when_subscription_missing
from billing.plans import PLANS
from billing.provider.razorpay import (
    RAZORPAY_PLAN_LINKS,
    RazorpaySubscriptionProvider,
    load_test_secret,
    provider_plan,
)
from test_organization import condition_pairs


OWNER = "owner-sub"
OTHER = "other-owner"
ORG_A = "ORG-A"
ORG_B = "ORG-B"
NOW = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
LINKS = {
    "MONTHLY": {"razorpay_plan_id": "plan_TestOnly000001", "total_count": 1},
    "YEARLY": {"razorpay_plan_id": "plan_TestOnly000002", "total_count": 1},
}


class Members:
    def __init__(self, rows):
        self.rows = rows

    def query(self, **kwargs):
        rows = list(self.rows)
        for name, value in condition_pairs(kwargs.get("KeyConditionExpression")):
            rows = [row for row in rows if row.get(name) == value]
        return {"Items": rows}

    def get_item(self, Key):
        for row in self.rows:
            if (
                row.get("organization_id") == Key.get("organization_id")
                and row.get("user_sub") == Key.get("user_sub")
            ):
                return {"Item": dict(row)}
        return {}


class Orgs:
    def __init__(self, items):
        self.items = items

    def get_item(self, Key):
        item = self.items.get(Key["organization_id"])
        return {"Item": dict(item)} if item else {}


class SubscriptionTable:
    def __init__(self, item=None, conflict=False):
        self.items = {}
        self.conflict = conflict
        if item:
            self.items[item["organization_id"]] = dict(item)

    def get_item(self, Key):
        item = self.items.get(Key["organization_id"])
        return {"Item": dict(item)} if item else {}

    def update_item(self, Key, UpdateExpression, ConditionExpression, ExpressionAttributeValues):
        if "SET subscription_status" in UpdateExpression or "current_period_" in UpdateExpression:
            raise AssertionError(UpdateExpression)

        item = self.items.get(Key["organization_id"])
        values = ExpressionAttributeValues

        if item is None or item.get("subscription_status") != values[":status"] or self.conflict:
            raise ClientError(
                {"Error": {"Code": "ConditionalCheckFailedException", "Message": "conflict"}},
                "UpdateItem",
            )

        current = item.get("provider_subscription_id") or ""
        previous = values.get(":previous") or ""

        if current and values[":status"] != "CANCELLED" and current != previous:
            raise ClientError(
                {"Error": {"Code": "ConditionalCheckFailedException", "Message": "conflict"}},
                "UpdateItem",
            )

        item["provider"] = values[":provider"]
        item["provider_subscription_id"] = values[":sid"]
        item["pending_plan_id"] = values[":pending"]
        item["updated_at"] = values[":updated"]
        item["plan_id"] = item.get("plan_id") or "FREE_TRIAL"


class FakeProvider:
    def __init__(self):
        self.calls = []

    def create_subscription(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "provider": "razorpay",
            "provider_subscription_id": "sub_TestCheckout01",
            "public_key_id": "rzp_test_public",
            "key_secret": "must-not-leak",
        }


def purchasable_plans(*plan_ids):
    catalog = {}
    for plan_id in plan_ids:
        plan = dict(PLANS[plan_id])
        plan["purchasable"] = True
        catalog[plan_id] = plan
    return catalog


def row(status="TRIALING", organization_id=ORG_A, provider_subscription_id=""):
    return {
        "organization_id": organization_id,
        "subscription_status": status,
        "plan_id": "FREE_TRIAL",
        "provider": "",
        "provider_customer_id": "",
        "provider_subscription_id": provider_subscription_id,
        "name_marker": "leave-operational-data",
    }


def event(body, subject=OWNER):
    payload = {
        "httpMethod": "POST",
        "body": json.dumps(body),
        "requestContext": {"authorizer": {"claims": {"sub": subject}}},
    }
    return payload


def wire(monkeypatch, role="OWNER", subject=OWNER, subscription=None, provider=None):
    organizations = {
        ORG_A: {"organization_id": ORG_A, "name": "Alpha", "status": "ACTIVE"},
        ORG_B: {"organization_id": ORG_B, "name": "Beta", "status": "ACTIVE", "marker": "untouched"},
    }
    before = json.dumps(organizations)
    monkeypatch.setattr(membership, "organizations_table", lambda: Orgs(organizations))
    monkeypatch.setattr(
        membership,
        "members_table",
        lambda: Members(
            [
                {
                    "organization_id": ORG_A,
                    "user_sub": subject,
                    "role": role,
                    "status": "ACTIVE",
                }
            ]
        ),
    )
    table = SubscriptionTable(subscription)
    provider = provider or FakeProvider()
    monkeypatch.setattr(checkout_handler, "subscriptions_table", lambda: table)
    monkeypatch.setattr(checkout_handler, "build_provider", lambda: provider)
    return table, provider, organizations, before


def body_of(result):
    return json.loads(result["body"])


def test_owner_monthly_checkout_sends_the_authorization_limit(monkeypatch):
    table, provider, _organizations, _before = wire(monkeypatch, subscription=row())

    result = checkout_handler.lambda_handler(event({"plan_id": "MONTHLY"}), None)

    assert result["statusCode"] == 200
    assert provider.calls == [{
        "razorpay_plan_id": "plan_ThiWT35Gf1jyio",
        "total_count": 468,
        "organization_id": ORG_A,
    }]
    assert table.items[ORG_A]["subscription_status"] == "TRIALING"
    assert table.items[ORG_A]["pending_plan_id"] == "MONTHLY"
    assert table.items[ORG_A]["plan_id"] == "FREE_TRIAL"
    assert "plan_Thi" not in result["body"]


@pytest.mark.parametrize("role", ["ADMIN", "OPERATOR", "MEMBER"])
def test_non_owner_cannot_checkout(monkeypatch, role):
    _table, provider, _organizations, _before = wire(monkeypatch, role=role, subscription=row())

    result = checkout_handler.lambda_handler(event({"plan_id": "MONTHLY"}, subject=OWNER), None)

    assert result["statusCode"] == 403
    assert provider.calls == []


def test_unauthenticated_checkout_is_rejected(monkeypatch):
    _table, provider, _organizations, _before = wire(monkeypatch, subscription=row())
    anonymous = event({"plan_id": "MONTHLY"})
    anonymous["requestContext"] = {}

    result = checkout_handler.lambda_handler(anonymous, None)

    assert result["statusCode"] == 401
    assert provider.calls == []


def test_other_organization_cannot_be_checked_out(monkeypatch):
    table, provider, organizations, before = wire(monkeypatch, subscription=row())
    foreign = row(organization_id=ORG_B)
    table.items[ORG_B] = foreign

    result = checkout_handler.lambda_handler(
        event({"plan_id": "MONTHLY", "organization_id": ORG_B}),
        None,
    )

    assert result["statusCode"] == 403
    assert provider.calls == []
    assert table.items[ORG_B] == foreign
    assert json.dumps(organizations) == before


def test_missing_subscription_stays_grandfathered_and_is_not_created(monkeypatch):
    table, provider, _organizations, _before = wire(monkeypatch)

    refused = checkout_handler.lambda_handler(event({"plan_id": "MONTHLY"}), None)

    assert refused["statusCode"] == 409
    assert access_when_subscription_missing() == "grandfathered"
    assert table.items == {}
    assert provider.calls == []


def test_missing_subscription_checkout_raises(monkeypatch):
    with pytest.raises(BillingError) as error:
        create_checkout(
            {"plan_id": "MONTHLY"},
            ORG_A,
            SubscriptionTable(),
            lambda: FakeProvider(),
            links=LINKS,
            plans=purchasable_plans("MONTHLY"),
            now=NOW,
        )

    assert error.value.status_code == 409
    assert error.value.message == "Organization billing is not ready for checkout"


@pytest.mark.parametrize("status", ["TRIALING", "EXPIRED", "CANCELLED", "GRANDFATHERED"])
def test_allowed_status_can_checkout_when_plan_is_purchasable(status):
    table = SubscriptionTable(row(status, provider_subscription_id="sub_OldCancelled1" if status == "CANCELLED" else ""))
    provider = FakeProvider()

    result = create_checkout(
        {"plan_id": "MONTHLY"},
        ORG_A,
        table,
        lambda: provider,
        links=LINKS,
        plans=purchasable_plans("MONTHLY"),
        now=NOW,
    )

    saved = table.items[ORG_A]
    assert result["provider"] == "razorpay"
    assert result["provider_subscription_id"] == "sub_TestCheckout01"
    assert result["public_key_id"] == "rzp_test_public"
    assert "key_secret" not in result
    assert "must-not-leak" not in json.dumps(result)
    assert saved["subscription_status"] == status
    assert saved["plan_id"] == "FREE_TRIAL"
    assert saved["pending_plan_id"] == "MONTHLY"
    assert saved["provider"] == "razorpay"
    assert saved["provider_subscription_id"] == "sub_TestCheckout01"
    assert saved["provider_customer_id"] == ""
    assert "current_period_start" not in saved
    assert provider.calls[0]["organization_id"] == ORG_A
    assert "amount" not in provider.calls[0]


@pytest.mark.parametrize("status,message", [
    ("ACTIVE", "This organization already has an active subscription"),
    ("PAST_DUE", "The existing subscription requires recovery"),
])
def test_open_subscription_does_not_start_another(status, message):
    table = SubscriptionTable(row(status, provider_subscription_id="sub_Existing0001"))
    provider = FakeProvider()

    with pytest.raises(BillingError) as error:
        create_checkout(
            {"plan_id": "YEARLY"},
            ORG_A,
            table,
            lambda: provider,
            links=LINKS,
            plans=purchasable_plans("YEARLY"),
            now=NOW,
        )

    assert error.value.status_code == 409
    assert error.value.message == message
    assert provider.calls == []
    assert table.items[ORG_A]["provider_subscription_id"] == "sub_Existing0001"
    assert "pending_plan_id" not in table.items[ORG_A]


def test_provider_failure_does_not_store_a_pending_plan():
    table = SubscriptionTable(row())

    class Failing(FakeProvider):
        def create_subscription(self, **kwargs):
            self.calls.append(kwargs)
            raise BillingError(502, "Billing provider rejected the request")

    with pytest.raises(BillingError):
        create_checkout(
            {"plan_id": "MONTHLY"},
            ORG_A,
            table,
            lambda: Failing(),
            links=LINKS,
            plans=purchasable_plans("MONTHLY"),
            now=NOW,
        )

    assert table.items[ORG_A]["plan_id"] == "FREE_TRIAL"
    assert table.items[ORG_A]["subscription_status"] == "TRIALING"
    assert "pending_plan_id" not in table.items[ORG_A]


def test_unknown_plan_is_rejected():
    with pytest.raises(BillingError) as error:
        create_checkout(
            {"plan_id": "WEEKLY"},
            ORG_A,
            SubscriptionTable(row()),
            lambda: FakeProvider(),
            now=NOW,
        )

    assert error.value.status_code == 400


def test_owner_yearly_checkout_sends_the_authorization_limit(monkeypatch):
    table, provider, _organizations, _before = wire(monkeypatch, subscription=row())

    result = checkout_handler.lambda_handler(event({"plan_id": "YEARLY"}), None)

    assert result["statusCode"] == 200
    assert provider.calls == [{
        "razorpay_plan_id": "plan_ThiWTXOzBHl2Qb",
        "total_count": 39,
        "organization_id": ORG_A,
    }]
    assert table.items[ORG_A]["pending_plan_id"] == "YEARLY"
    assert table.items[ORG_A]["subscription_status"] == "TRIALING"
    assert PLANS["YEARLY"]["purchasable"] is True
    assert PLANS["YEARLY"]["amount_minor"] == 999900
    assert RAZORPAY_PLAN_LINKS["YEARLY"]["razorpay_plan_id"].startswith("plan_")
    assert "plan_Thi" not in result["body"]


def test_missing_provider_plan_is_rejected():
    provider = FakeProvider()

    with pytest.raises(BillingError) as error:
        create_checkout(
            {"plan_id": "MONTHLY"},
            ORG_A,
            SubscriptionTable(row()),
            lambda: provider,
            links={"MONTHLY": {"razorpay_plan_id": None, "total_count": None}},
            plans=purchasable_plans("MONTHLY"),
            now=NOW,
        )

    assert error.value.status_code == 409
    assert provider.calls == []


@pytest.mark.parametrize("extra,message", [
    ({"amount": 100}, "Amount cannot be supplied"),
    ({"price": 100}, "Amount cannot be supplied"),
    ({"currency": "INR"}, "Currency cannot be supplied"),
])
def test_client_cannot_supply_money(extra, message):
    with pytest.raises(BillingError) as error:
        create_checkout(
            {"plan_id": "MONTHLY", **extra},
            ORG_A,
            SubscriptionTable(row()),
            lambda: FakeProvider(),
            links=LINKS,
            plans=purchasable_plans("MONTHLY"),
            now=NOW,
        )

    assert error.value.message == message


def test_pending_provider_subscription_is_not_overwritten():
    table = SubscriptionTable(row(provider_subscription_id="sub_AlreadyOpen01"))
    provider = FakeProvider()

    with pytest.raises(BillingError) as error:
        create_checkout(
            {"plan_id": "MONTHLY"},
            ORG_A,
            table,
            lambda: provider,
            links=LINKS,
            plans=purchasable_plans("MONTHLY"),
            now=NOW,
        )

    assert error.value.status_code == 409
    assert provider.calls == []
    assert table.items[ORG_A]["provider_subscription_id"] == "sub_AlreadyOpen01"
    assert table.items[ORG_A]["name_marker"] == "leave-operational-data"


def test_created_provider_subscription_can_be_replaced_without_activation():
    table = SubscriptionTable(row(provider_subscription_id="sub_AlreadyOpen01"))
    provider = FakeProvider()
    provider.subscription_status = lambda subscription_id: "created" if subscription_id == "sub_AlreadyOpen01" else ""

    result = create_checkout(
        {"plan_id": "MONTHLY"},
        ORG_A,
        table,
        lambda: provider,
        links=LINKS,
        plans=purchasable_plans("MONTHLY"),
        now=NOW,
    )

    saved = table.items[ORG_A]
    assert result["provider_subscription_id"] == "sub_TestCheckout01"
    assert saved["subscription_status"] == "TRIALING"
    assert saved["plan_id"] == "FREE_TRIAL"
    assert saved["provider_subscription_id"] == "sub_TestCheckout01"
    assert saved["pending_plan_id"] == "MONTHLY"
    assert provider.calls[0]["organization_id"] == ORG_A


def test_authenticated_provider_subscription_is_not_replaced():
    table = SubscriptionTable(row(provider_subscription_id="sub_AlreadyOpen01"))
    provider = FakeProvider()
    provider.subscription_status = lambda subscription_id: "authenticated"

    with pytest.raises(BillingError) as error:
        create_checkout(
            {"plan_id": "MONTHLY"},
            ORG_A,
            table,
            lambda: provider,
            links=LINKS,
            plans=purchasable_plans("MONTHLY"),
            now=NOW,
        )

    assert error.value.status_code == 409
    assert provider.calls == []
    assert table.items[ORG_A]["subscription_status"] == "TRIALING"
    assert table.items[ORG_A]["provider_subscription_id"] == "sub_AlreadyOpen01"


def test_conditional_conflict_does_not_activate(monkeypatch):
    table = SubscriptionTable(row(), conflict=True)
    provider = FakeProvider()

    with pytest.raises(BillingError) as error:
        create_checkout(
            {"plan_id": "MONTHLY"},
            ORG_A,
            table,
            lambda: provider,
            links=LINKS,
            plans=purchasable_plans("MONTHLY"),
            now=NOW,
        )

    assert error.value.status_code == 409
    assert table.items[ORG_A]["subscription_status"] == "TRIALING"
    assert table.items[ORG_A]["provider_subscription_id"] == ""


def test_razorpay_request_uses_the_documented_subscription_call():
    seen = {}

    class Response:
        def read(self):
            return json.dumps({"id": "sub_Created000001", "status": "created", "current_start": None}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def urlopen(request, timeout):
        seen["url"] = request.full_url
        seen["timeout"] = timeout
        seen["body"] = json.loads(request.data.decode())
        seen["authorization"] = request.get_header("Authorization")
        return Response()

    result = RazorpaySubscriptionProvider(
        lambda: ("rzp_test_public", "test-secret-value"),
        urlopen=urlopen,
    ).create_subscription(
        razorpay_plan_id="plan_TestOnly000001",
        total_count=1,
        organization_id=ORG_A,
    )

    assert seen["url"] == "https://api.razorpay.com/v1/subscriptions"
    assert seen["timeout"] == 10
    assert seen["body"]["plan_id"] == "plan_TestOnly000001"
    assert seen["body"]["total_count"] == 1
    assert seen["body"]["quantity"] == 1
    assert seen["body"]["customer_notify"] is False
    assert "amount" not in seen["body"]
    assert "currency" not in seen["body"]
    assert "customer_id" not in seen["body"]
    assert result == {
        "provider": "razorpay",
        "provider_subscription_id": "sub_Created000001",
        "public_key_id": "rzp_test_public",
    }
    assert "test-secret-value" not in json.dumps(result)


@pytest.mark.parametrize(
    "short_url,expected",
    [
        ("https://rzp.io/i/testcheckout", "https://rzp.io/i/testcheckout"),
        ("http://rzp.io/i/testcheckout", ""),
        ("javascript:alert(1)", ""),
        ("https://user:secret@rzp.io/i/testcheckout", ""),
    ],
)
def test_hosted_checkout_url_is_https_only(short_url, expected):
    class Response:
        def read(self):
            return json.dumps({"id": "sub_Created000001", "short_url": short_url}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    result = RazorpaySubscriptionProvider(
        lambda: ("rzp_test_public", "test-secret-value"),
        urlopen=lambda request, timeout: Response(),
    ).create_subscription(
        razorpay_plan_id="plan_TestOnly000001",
        total_count=1,
        organization_id=ORG_A,
    )

    assert result.get("hosted_checkout_url", "") == expected
    assert "test-secret-value" not in json.dumps(result)


@pytest.mark.parametrize(
    "plan_id,total_count",
    [("MONTHLY", 468), ("YEARLY", 39)],
)
def test_production_checkout_body_stays_inside_the_authorization_limit(plan_id, total_count):
    seen = {}

    class Response:
        def read(self):
            return json.dumps({"id": "sub_Created000001"}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def urlopen(request, timeout):
        seen["body"] = json.loads(request.data.decode())
        seen["authorization"] = request.get_header("Authorization")
        return Response()

    class SecretClient:
        def get_secret_value(self, SecretId):
            assert SecretId == "erap/billing/razorpay/test"
            return {"SecretString": json.dumps({
                "key_id": "rzp_test_public",
                "key_secret": "test-secret-value",
            })}

    razorpay_plan_id, configured_count = provider_plan(plan_id)
    RazorpaySubscriptionProvider(
        lambda: load_test_secret(SecretClient()),
        urlopen=urlopen,
    ).create_subscription(
        razorpay_plan_id=razorpay_plan_id,
        total_count=configured_count,
        organization_id=ORG_A,
    )

    assert configured_count == total_count
    assert seen["body"] == {
        "plan_id": RAZORPAY_PLAN_LINKS[plan_id]["razorpay_plan_id"],
        "total_count": total_count,
        "quantity": 1,
        "customer_notify": False,
        "notes": {"organization_id": ORG_A},
    }
    assert seen["authorization"].startswith("Basic ")
    assert "test-secret-value" not in json.dumps(seen["body"])


def test_subscription_status_returns_only_the_provider_status():
    seen = {}

    class Response:
        def read(self):
            return json.dumps({
                "id": "sub_Created000001",
                "status": "created",
                "customer_id": "cust_hidden",
            }).encode()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def urlopen(request, timeout):
        seen["method"] = request.get_method()
        seen["url"] = request.full_url
        return Response()

    status = RazorpaySubscriptionProvider(
        lambda: ("rzp_test_public", "test-secret-value"),
        urlopen=urlopen,
    ).subscription_status("sub_Created000001")

    assert status == "created"
    assert seen["method"] == "GET"
    assert seen["url"].endswith("/subscriptions/sub_Created000001")
    assert "cust_hidden" not in status
    assert "test-secret-value" not in status


def test_live_key_is_rejected_before_the_subscription_call():
    def urlopen(request, timeout):
        raise AssertionError("live key must not call Razorpay")

    provider = RazorpaySubscriptionProvider(lambda: ("rzp_live_secret", "test-secret-value"), urlopen=urlopen)

    with pytest.raises(BillingError) as error:
        provider.create_subscription(
            razorpay_plan_id="plan_ThiWT35Gf1jyio",
            total_count=468,
            organization_id=ORG_A,
        )

    assert error.value.status_code == 500
    assert "test-secret-value" not in error.value.message
    assert "rzp_live" not in error.value.message


def test_razorpay_errors_are_controlled():
    def http_error(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 400, "bad", hdrs=None, fp=None)

    def timeout(request, timeout):
        raise socket.timeout("timed out")

    rejected = RazorpaySubscriptionProvider(lambda: ("rzp_test_public", "test-secret-value"), urlopen=http_error)
    timed = RazorpaySubscriptionProvider(lambda: ("rzp_test_public", "test-secret-value"), urlopen=timeout)

    with pytest.raises(BillingError) as provider_error:
        rejected.create_subscription(razorpay_plan_id="plan_TestOnly000001", total_count=1, organization_id=ORG_A)

    with pytest.raises(BillingError) as timeout_error:
        timed.create_subscription(razorpay_plan_id="plan_TestOnly000001", total_count=1, organization_id=ORG_A)

    assert provider_error.value.status_code == 502
    assert provider_error.value.message == "Billing provider rejected the request"
    assert timeout_error.value.status_code == 503
    assert timeout_error.value.message == "Billing provider is unavailable"
    assert "test-secret-value" not in provider_error.value.message


def test_live_key_and_wrong_secret_id_are_refused():
    called = {"count": 0}

    def urlopen(request, timeout):
        called["count"] += 1
        raise AssertionError("network")

    provider = RazorpaySubscriptionProvider(lambda: ("rzp_live_example", "test-secret-value"), urlopen=urlopen)

    with pytest.raises(BillingError) as live_key:
        provider.create_subscription(razorpay_plan_id="plan_TestOnly000001", total_count=1, organization_id=ORG_A)

    with pytest.raises(BillingError) as wrong_secret:
        load_test_secret(client=object(), secret_id="erap/billing/razorpay/live")

    assert live_key.value.status_code == 500
    assert wrong_secret.value.status_code == 500
    assert called["count"] == 0


def test_unconfigured_provider_plan_helper_fails_closed():
    plan_id, total_count = provider_plan("MONTHLY")
    assert plan_id == "plan_ThiWT35Gf1jyio"
    assert total_count == 468

    with pytest.raises(BillingError):
        provider_plan("MONTHLY", links={"MONTHLY": {"razorpay_plan_id": None, "total_count": None}})

    with pytest.raises(BillingError):
        provider_plan("FREE_TRIAL")
