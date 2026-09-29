import json
import sys
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

from billing import api_handler
from billing.errors import BillingError
from billing.plans import PLANS, get_plan
from billing.provider.razorpay import RAZORPAY_PLAN_LINKS, RazorpaySubscriptionProvider
from test_organization import condition_pairs
import membership


OWNER = "owner-sub"
ORG_A = "ORG-A"
ORG_B = "ORG-B"
SECRET = "webhook-secret-must-not-leak"
SUB_A = "sub_TestCancel0001"


def organizations():
    return {
        ORG_A: {"organization_id": ORG_A, "name": "Alpha", "status": "ACTIVE"},
        ORG_B: {"organization_id": ORG_B, "name": "Beta", "status": "ACTIVE"},
    }


def member(role, organization_id=ORG_A, subject=OWNER):
    return {
        "organization_id": organization_id,
        "user_sub": subject,
        "role": role,
        "status": "ACTIVE",
    }


class Members:
    def __init__(self, rows):
        self.rows = rows

    def query(self, **kwargs):
        rows = list(self.rows)
        for name, value in condition_pairs(kwargs.get("KeyConditionExpression")):
            rows = [row for row in rows if row.get(name) == value]
        return {"Items": rows}


class Orgs:
    def __init__(self, items):
        self.items = items

    def get_item(self, Key):
        item = self.items.get(Key["organization_id"])
        return {"Item": dict(item)} if item else {}


class Subscriptions:
    def __init__(self, rows=None):
        self.items = {row["organization_id"]: dict(row) for row in rows or []}
        self.reads = 0
        self.puts = 0

    def get_item(self, Key):
        self.reads += 1
        item = self.items.get(Key["organization_id"])
        return {"Item": dict(item)} if item else {}

    def put_item(self, **kwargs):
        self.puts += 1
        raise AssertionError("billing read must not create a row")

    def update_item(self, **kwargs):
        expression = kwargs["UpdateExpression"]
        if "subscription_status =" in expression or "current_period_" in expression:
            raise AssertionError(expression)
        item = self.items[kwargs["Key"]["organization_id"]]
        values = kwargs["ExpressionAttributeValues"]
        if ":flag" in values:
            if (
                item.get("subscription_status") != values[":status"]
                or item.get("provider_subscription_id") != values[":sid"]
                or item.get("cancel_at_period_end") is not False
            ):
                raise ClientError(
                    {"Error": {"Code": "ConditionalCheckFailedException", "Message": "conflict"}},
                    "UpdateItem",
                )
            item["cancel_at_period_end"] = values[":flag"]
            item["updated_at"] = values[":updated"]
            return
        item["provider"] = values[":provider"]
        item["provider_subscription_id"] = values[":sid"]
        item["pending_plan_id"] = values.get(":pending", "")
        item["updated_at"] = values[":updated"]


class Events:
    def __init__(self, rows):
        self.rows = [dict(row) for row in rows]
        self.queries = []

    def query(self, **kwargs):
        self.queries.append(kwargs)
        matched = list(self.rows)
        for name, value in condition_pairs(kwargs.get("KeyConditionExpression")):
            matched = [row for row in matched if row.get(name) == value]
        if kwargs.get("ScanIndexForward") is False:
            matched.sort(key=lambda row: row.get("received_at") or "", reverse=True)
        return {"Items": matched[: kwargs.get("Limit") or len(matched)]}

    def scan(self, **kwargs):
        raise AssertionError("scan")


class Provider:
    def __init__(self, fail=None):
        self.calls = []
        self.fail = fail

    def create_subscription(self, **kwargs):
        self.calls.append(("create", kwargs))
        if self.fail:
            raise self.fail
        return {
            "provider": "razorpay",
            "provider_subscription_id": "sub_TestCheckout01",
            "public_key_id": "rzp_test_public",
            "key_secret": SECRET,
        }

    def cancel_subscription(self, **kwargs):
        self.calls.append(("cancel", kwargs))
        if self.fail:
            raise self.fail
        return {"accepted": True, "key_secret": SECRET}


def subscription(**overrides):
    item = {
        "organization_id": ORG_A,
        "plan_id": "FREE_TRIAL",
        "billing_interval": "none",
        "subscription_status": "TRIALING",
        "trial_start": "2026-09-01T00:00:00+00:00",
        "trial_end": "2026-09-16T00:00:00+00:00",
        "current_period_start": "",
        "current_period_end": "",
        "cancel_at_period_end": False,
        "cancelled_at": "",
        "provider": "",
        "provider_customer_id": "",
        "provider_subscription_id": "",
        "webhook_secret": SECRET,
        "key_secret": SECRET,
    }
    item.update(overrides)
    return item


def event_row(organization_id, event_id, received_at):
    return {
        "provider_event_id": event_id,
        "provider": "razorpay",
        "event_type": "subscription.charged",
        "organization_id": organization_id,
        "provider_payment_id": "pay_Test0001",
        "received_at": received_at,
        "processed_at": received_at,
        "processing_status": "PROCESSED",
        "raw_body": "{\"card_number\":\"4111111111111111\"}",
        "signature": "signature-must-not-leak",
        "webhook_secret": SECRET,
        "key_secret": SECRET,
    }


def request(method, path, body=None, subject=OWNER, organization_id=None):
    payload = {
        "httpMethod": method,
        "path": path,
        "requestContext": {"authorizer": {"claims": {"sub": subject}}},
    }
    data = dict(body or {})
    if organization_id and method != "GET":
        data["organization_id"] = organization_id
    if method == "POST":
        payload["body"] = json.dumps(data)
    if organization_id and method == "GET":
        payload["queryStringParameters"] = {"organization_id": organization_id}
    return payload


def wire(monkeypatch, role="OWNER", rows=None, events=None, provider=None, memberships=None):
    monkeypatch.setattr(membership, "organizations_table", lambda: Orgs(organizations()))
    monkeypatch.setattr(
        membership,
        "members_table",
        lambda: Members(memberships or [member(role)]),
    )
    table = Subscriptions(rows)
    history = Events(events or [])
    provider = provider or Provider()
    monkeypatch.setattr(api_handler, "subscriptions_table", lambda: table)
    monkeypatch.setattr(api_handler, "events_table", lambda: history)
    monkeypatch.setattr(api_handler, "build_provider", lambda: provider)
    return table, history, provider


def call(monkeypatch, method, path, role="OWNER", body=None, organization_id=None, **kwargs):
    table, history, provider = wire(monkeypatch, role=role, **kwargs)
    response = api_handler.lambda_handler(
        request(method, path, body, organization_id=organization_id),
        None,
    )
    return response, json.loads(response["body"]), table, history, provider


def enable_purchase(monkeypatch):
    def opened(plan_id):
        plan = get_plan(plan_id)
        plan["purchasable"] = True
        return plan

    monkeypatch.setattr("billing.checkout.get_plan", opened)
    monkeypatch.setattr(
        "billing.checkout.provider_plan",
        lambda plan_id, links=None: ("plan_TestOnly000001", 1),
    )


def test_owner_and_admin_can_read_billing(monkeypatch):
    for role in ("OWNER", "ADMIN"):
        response, body, table, _history, _provider = call(
            monkeypatch,
            "GET",
            "/billing",
            role=role,
            rows=[subscription()],
            organization_id=ORG_A,
        )
        assert response["statusCode"] == 200
        assert body["organization_id"] == ORG_A
        assert body["subscription"]["subscription_status"] == "TRIALING"
        assert body["subscription"]["trial_start"] == "2026-09-01T00:00:00+00:00"
        assert body["subscription"]["cancelled_at"] is None
        assert body["next_action"] == "subscribe"
        assert "provider_subscription_id" not in response["body"]
        assert SECRET not in response["body"]
        assert table.puts == 0


def test_operator_and_member_cannot_read_billing(monkeypatch):
    for role in ("OPERATOR", "MEMBER"):
        response, body, _table, _history, _provider = call(monkeypatch, "GET", "/billing", role=role)
        assert response["statusCode"] == 403
        assert body["error"]["code"] == "FORBIDDEN"


def test_unauthenticated_billing_is_rejected(monkeypatch):
    wire(monkeypatch, rows=[subscription()])
    response = api_handler.lambda_handler({"httpMethod": "GET", "path": "/billing"}, None)
    body = json.loads(response["body"])
    assert response["statusCode"] == 401
    assert body["error"]["code"] == "UNAUTHENTICATED"


def test_other_organization_billing_is_denied(monkeypatch):
    response, body, table, _history, _provider = call(
        monkeypatch,
        "GET",
        "/billing",
        rows=[subscription(), subscription(organization_id=ORG_B, subscription_status="ACTIVE")],
        organization_id=ORG_B,
    )
    assert response["statusCode"] == 403
    assert table.reads == 0
    assert "ACTIVE" not in body["message"]


def test_single_organization_needs_no_selector(monkeypatch):
    response, body, _table, _history, _provider = call(
        monkeypatch,
        "GET",
        "/billing",
        rows=[subscription()],
    )
    assert response["statusCode"] == 200
    assert body["organization_id"] == ORG_A


def test_several_organizations_still_require_a_selector(monkeypatch):
    response, body, _table, _history, _provider = call(
        monkeypatch,
        "GET",
        "/billing",
        memberships=[member("OWNER", ORG_A), member("OWNER", ORG_B)],
    )
    assert response["statusCode"] == 400
    assert body["message"] == "Organization selection is required"


def test_missing_subscription_is_grandfathered_and_not_created(monkeypatch):
    response, body, table, _history, _provider = call(monkeypatch, "GET", "/billing")
    assert response["statusCode"] == 200
    assert body["subscription"]["subscription_status"] == "GRANDFATHERED"
    assert body["subscription"]["plan_id"] == "GRANDFATHERED"
    assert body["next_action"] == "none"
    assert table.items == {}
    assert table.puts == 0


def test_existing_subscription_state_is_preserved(monkeypatch):
    response, body, _table, _history, _provider = call(
        monkeypatch,
        "GET",
        "/billing",
        rows=[subscription(
            subscription_status="PAST_DUE",
            plan_id="MONTHLY",
            billing_interval="month",
            current_period_end="2026-10-01T00:00:00+00:00",
        )],
    )
    assert body["subscription"]["subscription_status"] == "PAST_DUE"
    assert body["subscription"]["current_period_end"] == "2026-10-01T00:00:00+00:00"
    assert body["next_action"] == "payment_required"
    assert response["statusCode"] == 200


def test_plans_follow_the_billing_role_policy(monkeypatch):
    response, body, table, _history, _provider = call(monkeypatch, "GET", "/billing/plans", role="ADMIN")
    assert response["statusCode"] == 200
    assert [plan["plan_id"] for plan in body["plans"]] == ["MONTHLY", "YEARLY"]
    monthly, yearly = body["plans"]
    assert monthly["display_name"] == "Monthly"
    assert yearly["display_name"] == "Yearly"
    assert monthly["purchasable"] is True
    assert yearly["purchasable"] is True
    assert monthly["amount_minor"] == 99900
    assert yearly["amount_minor"] == 999900
    assert monthly["currency"] == "INR"
    assert yearly["currency"] == "INR"
    rendered = json.dumps(body)
    assert RAZORPAY_PLAN_LINKS["MONTHLY"]["razorpay_plan_id"] not in rendered
    assert RAZORPAY_PLAN_LINKS["YEARLY"]["razorpay_plan_id"] not in rendered
    assert "rzp_" not in rendered
    assert "entitlements" not in rendered
    assert "razorpay" not in rendered
    assert table.reads == 0
    assert PLANS["MONTHLY"]["purchasable"] is True
    assert RAZORPAY_PLAN_LINKS["MONTHLY"]["razorpay_plan_id"].startswith("plan_")
    assert RAZORPAY_PLAN_LINKS["YEARLY"]["razorpay_plan_id"].startswith("plan_")
    assert RAZORPAY_PLAN_LINKS["MONTHLY"]["total_count"] == 1200
    assert RAZORPAY_PLAN_LINKS["YEARLY"]["total_count"] == 100
    assert "1200" not in rendered
    assert "100" not in rendered
    refused, _body, _table, _history, _provider = call(monkeypatch, "GET", "/billing/plans", role="MEMBER")
    assert refused["statusCode"] == 403


def test_checkout_permissions_and_unavailable_plan(monkeypatch):
    response, body, table, _history, provider = call(
        monkeypatch,
        "POST",
        "/billing/checkout",
        rows=[subscription()],
        body={"plan_id": "MONTHLY"},
        organization_id=ORG_A,
    )
    assert response["statusCode"] == 200
    assert provider.calls == [(
        "create",
        {
            "razorpay_plan_id": "plan_ThiWT35Gf1jyio",
            "total_count": 1200,
            "organization_id": ORG_A,
        },
    )]
    assert table.items[ORG_A]["subscription_status"] == "TRIALING"
    assert table.items[ORG_A]["pending_plan_id"] == "MONTHLY"
    assert "plan_Thi" not in response["body"]
    trial, trial_body, _table, _history, trial_provider = call(
        monkeypatch,
        "POST",
        "/billing/checkout",
        rows=[subscription()],
        body={"plan_id": "FREE_TRIAL"},
        organization_id=ORG_A,
    )
    assert trial["statusCode"] == 409
    assert trial_body["message"] == "Plan is not currently available for purchase"
    assert trial_provider.calls == []
    for role in ("ADMIN", "OPERATOR", "MEMBER"):
        refused, _body, _table, _history, role_provider = call(
            monkeypatch,
            "POST",
            "/billing/checkout",
            role=role,
            rows=[subscription()],
            body={"plan_id": "MONTHLY"},
        )
        assert refused["statusCode"] == 403
        assert role_provider.calls == []


def test_checkout_rejects_unknown_plan_money_and_other_tenants(monkeypatch):
    unknown, body, _table, _history, provider = call(
        monkeypatch,
        "POST",
        "/billing/checkout",
        rows=[subscription()],
        body={"plan_id": "WEEKLY"},
    )
    assert unknown["statusCode"] == 400
    assert body["message"] == "Plan is invalid"
    amount, body, _table, _history, provider = call(
        monkeypatch,
        "POST",
        "/billing/checkout",
        rows=[subscription()],
        body={"plan_id": "MONTHLY", "amount": 100},
    )
    assert amount["statusCode"] == 400
    assert body["message"] == "Amount cannot be supplied"
    currency, body, _table, _history, provider = call(
        monkeypatch,
        "POST",
        "/billing/checkout",
        rows=[subscription()],
        body={"plan_id": "MONTHLY", "currency": "INR"},
    )
    assert currency["statusCode"] == 400
    assert body["message"] == "Currency cannot be supplied"
    assert provider.calls == []
    cross, _body, table, _history, provider = call(
        monkeypatch,
        "POST",
        "/billing/checkout",
        rows=[subscription(), subscription(organization_id=ORG_B)],
        body={"plan_id": "MONTHLY"},
        organization_id=ORG_B,
    )
    assert cross["statusCode"] == 403
    assert table.items[ORG_B]["provider_subscription_id"] == ""


def test_active_checkout_is_rejected_before_the_provider(monkeypatch):
    enable_purchase(monkeypatch)
    response, body, table, _history, provider = call(
        monkeypatch,
        "POST",
        "/billing/checkout",
        rows=[subscription(subscription_status="ACTIVE", provider="razorpay", provider_subscription_id=SUB_A)],
        body={"plan_id": "MONTHLY"},
    )
    assert response["statusCode"] == 409
    assert body["message"] == "This organization already has an active subscription"
    assert provider.calls == []
    assert table.items[ORG_A]["subscription_status"] == "ACTIVE"


def test_checkout_provider_failure_is_controlled(monkeypatch):
    enable_purchase(monkeypatch)
    response, body, table, _history, provider = call(
        monkeypatch,
        "POST",
        "/billing/checkout",
        rows=[subscription()],
        body={"plan_id": "MONTHLY"},
        provider=Provider(fail=BillingError(502, "Billing provider rejected the request")),
    )
    assert response["statusCode"] == 502
    assert body["message"] == "Billing provider rejected the request"
    assert body["error"]["code"] == "REQUEST_FAILED"
    assert "Traceback" not in response["body"]
    assert SECRET not in response["body"]
    assert table.items[ORG_A]["provider_subscription_id"] == ""
    assert table.items[ORG_A]["subscription_status"] == "TRIALING"


def test_successful_checkout_stores_the_provider_id_and_stays_trialing(monkeypatch):
    enable_purchase(monkeypatch)
    response, body, table, _history, provider = call(
        monkeypatch,
        "POST",
        "/billing/checkout",
        rows=[subscription()],
        body={"plan_id": "MONTHLY"},
    )
    assert response["statusCode"] == 200
    assert body["provider_subscription_id"] == "sub_TestCheckout01"
    assert body["public_key_id"] == "rzp_test_public"
    assert SECRET not in response["body"]
    assert table.items[ORG_A]["subscription_status"] == "TRIALING"
    assert table.items[ORG_A]["provider_subscription_id"] == "sub_TestCheckout01"
    assert provider.calls[0][0] == "create"


def test_owner_cancellation_sets_period_end_without_changing_status(monkeypatch):
    response, body, table, _history, provider = call(
        monkeypatch,
        "POST",
        "/billing/cancel",
        rows=[subscription(
            subscription_status="ACTIVE",
            plan_id="MONTHLY",
            billing_interval="month",
            provider="razorpay",
            provider_subscription_id=SUB_A,
            current_period_end="2026-10-01T00:00:00+00:00",
        )],
        body={"provider_subscription_id": "sub_ClientSupplied1"},
    )
    assert response["statusCode"] == 400
    assert provider.calls == []
    response, body, table, _history, provider = call(
        monkeypatch,
        "POST",
        "/billing/cancel",
        rows=[subscription(
            subscription_status="ACTIVE",
            plan_id="MONTHLY",
            billing_interval="month",
            provider="razorpay",
            provider_subscription_id=SUB_A,
            current_period_end="2026-10-01T00:00:00+00:00",
        )],
        body={},
    )
    assert response["statusCode"] == 200
    assert body["subscription"]["subscription_status"] == "ACTIVE"
    assert body["subscription"]["cancel_at_period_end"] is True
    assert body["subscription"]["current_period_end"] == "2026-10-01T00:00:00+00:00"
    assert body["next_action"] == "none"
    assert "EXPIRED" not in response["body"]
    assert SUB_A not in response["body"]
    assert provider.calls == [("cancel", {"provider_subscription_id": SUB_A})]
    assert table.items[ORG_A]["subscription_status"] == "ACTIVE"


def test_cancel_is_owner_only_and_provider_failure_does_not_change_state(monkeypatch):
    refused, _body, table, _history, provider = call(
        monkeypatch,
        "POST",
        "/billing/cancel",
        role="ADMIN",
        rows=[subscription(subscription_status="ACTIVE", provider="razorpay", provider_subscription_id=SUB_A)],
    )
    assert refused["statusCode"] == 403
    assert provider.calls == []
    assert table.items[ORG_A]["cancel_at_period_end"] is False
    failed, body, table, _history, _provider = call(
        monkeypatch,
        "POST",
        "/billing/cancel",
        rows=[subscription(subscription_status="ACTIVE", provider="razorpay", provider_subscription_id=SUB_A)],
        provider=Provider(fail=BillingError(503, "Billing provider is unavailable")),
    )
    assert failed["statusCode"] == 503
    assert body["message"] == "Billing provider is unavailable"
    assert table.items[ORG_A]["subscription_status"] == "ACTIVE"
    assert table.items[ORG_A]["cancel_at_period_end"] is False


def test_repeated_cancellation_does_not_call_the_provider(monkeypatch):
    row = subscription(
        subscription_status="ACTIVE",
        provider="razorpay",
        provider_subscription_id=SUB_A,
        cancel_at_period_end=True,
    )
    _response, body, table, _history, provider = call(monkeypatch, "POST", "/billing/cancel", rows=[row])
    assert body["subscription"]["subscription_status"] == "ACTIVE"
    assert provider.calls == []
    assert table.items[ORG_A]["cancel_at_period_end"] is True


def test_billing_events_are_organization_scoped_and_redacted(monkeypatch):
    rows = [
        event_row(ORG_A, "evt_old", "2026-09-01T00:00:00+00:00"),
        event_row(ORG_A, "evt_new", "2026-09-02T00:00:00+00:00"),
        event_row(ORG_B, "evt_other", "2026-09-03T00:00:00+00:00"),
    ]
    response, body, _table, history, _provider = call(
        monkeypatch,
        "GET",
        "/billing/events",
        role="ADMIN",
        events=rows,
        organization_id=ORG_A,
    )
    assert response["statusCode"] == 200
    assert [item["provider_event_id"] for item in body["events"]] == ["evt_new", "evt_old"]
    assert history.queries[0]["IndexName"] == "OrganizationBillingEventsIndex"
    rendered = response["body"]
    assert "4111111111111111" not in rendered
    assert "signature-must-not-leak" not in rendered
    assert SECRET not in rendered
    assert "raw_body" not in rendered
    for role in ("OPERATOR", "MEMBER"):
        refused, _body, _table, history, _provider = call(
            monkeypatch,
            "GET",
            "/billing/events",
            role=role,
            events=rows,
        )
        assert refused["statusCode"] == 403
        assert history.queries == []
    cross, _body, _table, history, _provider = call(
        monkeypatch,
        "GET",
        "/billing/events",
        events=rows,
        organization_id=ORG_B,
    )
    assert cross["statusCode"] == 403
    assert history.queries == []


def test_cancel_request_uses_cycle_end():
    seen = {}

    class Response:
        def read(self):
            return json.dumps({"id": SUB_A, "status": "active"}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def urlopen(request, timeout):
        seen["url"] = request.full_url
        seen["body"] = json.loads(request.data.decode())
        seen["timeout"] = timeout
        return Response()

    result = RazorpaySubscriptionProvider(
        lambda: ("rzp_test_public", "test-secret-value"),
        urlopen=urlopen,
    ).cancel_subscription(provider_subscription_id=SUB_A)

    assert seen["url"] == f"https://api.razorpay.com/v1/subscriptions/{SUB_A}/cancel"
    assert seen["body"] == {"cancel_at_cycle_end": True}
    assert seen["timeout"] == 10
    assert result == {"accepted": True}
    assert "test-secret-value" not in json.dumps(result)


def test_route_specification_is_not_deployed():
    checkout = json.loads((ROOT / "infra" / "billing-checkout.json").read_text(encoding="utf-8"))
    tables = json.loads((ROOT / "infra" / "billing-tables.json").read_text(encoding="utf-8"))
    assert checkout["applied"] is False
    assert checkout["webhook"]["authorization"] == "NONE"
    paths = {(item["method"], item["path"], item["authorization"]) for item in checkout["routes"]}
    assert paths == {
        ("GET", "/billing", "COGNITO_USER_POOLS"),
        ("GET", "/billing/plans", "COGNITO_USER_POOLS"),
        ("POST", "/billing/checkout", "COGNITO_USER_POOLS"),
        ("POST", "/billing/cancel", "COGNITO_USER_POOLS"),
        ("GET", "/billing/events", "COGNITO_USER_POOLS"),
    }
    assert all(item["authorizerId"] == "y0hzhr" for item in checkout["routes"])
    policy = json.dumps(checkout["runtime_iam"])
    assert "dynamodb:Scan" not in policy
    assert "dynamodb:DeleteItem" not in policy
    assert "OrganizationBillingEventsIndex" in policy
    index = tables["tables"][1]["GlobalSecondaryIndexes"][0]["IndexName"]
    assert index == "OrganizationBillingEventsIndex"
    assert tables["applied"] is False
