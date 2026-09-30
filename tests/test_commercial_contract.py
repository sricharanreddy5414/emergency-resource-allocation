"""Commercial contract. These tests do not call AWS or a payment provider."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from billing.checkout import _plan_id
from billing.entitlements import is_operational_write_allowed
from billing.errors import BillingError
from billing.models import PAYMENT_STATES, TRIAL_DAYS, trial_bounds
from billing.plans import PLAN_RULES, PLANS, customer_plans
from billing.provider.razorpay import (
    RAZORPAY_PLAN_LINKS,
    TEST_SECRET_ID,
    _EVENT_TARGETS,
    _PAYMENT_STATES,
    load_webhook_secret,
)
from billing.summary import next_action
from billing.transitions import SUBSCRIPTION_STATUSES, TRANSITIONS

ROOT = Path(__file__).resolve().parents[1]


def test_prices_live_in_one_catalog():
    assert PLAN_RULES["MONTHLY"]["amount_minor"] == 99900
    assert PLAN_RULES["YEARLY"]["amount_minor"] == 999900
    assert PLANS["MONTHLY"]["amount_minor"] == 99900
    assert PLANS["YEARLY"]["amount_minor"] == 999900
    assert PLANS["FREE_TRIAL"]["amount_minor"] == 0
    assert PLANS["GRANDFATHERED"]["purchasable"] is False
    listed = {plan["plan_id"]: plan for plan in customer_plans()}
    assert set(listed) == {"MONTHLY", "YEARLY"}
    assert "razorpay_plan_id" not in listed["MONTHLY"]
    assert listed["MONTHLY"]["amount_minor"] == 99900


def test_provider_plan_ids_are_not_application_plans():
    assert set(RAZORPAY_PLAN_LINKS) == {"MONTHLY", "YEARLY"}
    assert RAZORPAY_PLAN_LINKS["MONTHLY"]["razorpay_plan_id"] == "plan_ThiWT35Gf1jyio"
    assert RAZORPAY_PLAN_LINKS["YEARLY"]["razorpay_plan_id"] == "plan_ThiWTXOzBHl2Qb"
    for link in RAZORPAY_PLAN_LINKS.values():
        assert link["razorpay_plan_id"] not in PLAN_RULES
    with pytest.raises(BillingError) as error:
        _plan_id({"plan_id": "MONTHLY", "razorpay_plan_id": "plan_ThiWT35Gf1jyio"})
    assert error.value.status_code == 400


def test_subscription_transitions_match_the_implemented_machine():
    assert TRANSITIONS == {
        None: {"TRIALING", "GRANDFATHERED"},
        "TRIALING": {"ACTIVE", "EXPIRED"},
        "ACTIVE": {"PAST_DUE", "CANCELLED"},
        "PAST_DUE": {"ACTIVE", "EXPIRED"},
        "CANCELLED": {"ACTIVE", "EXPIRED"},
        "EXPIRED": {"ACTIVE"},
        "GRANDFATHERED": {"ACTIVE"},
    }
    assert "EXPIRED" not in TRANSITIONS["ACTIVE"]
    assert "CANCELLED" not in TRANSITIONS["PAST_DUE"]


def test_payment_states_are_not_subscription_states():
    assert PAYMENT_STATES == {"PENDING", "PAID", "FAILED", "REFUNDED"}
    assert PAYMENT_STATES.isdisjoint(SUBSCRIPTION_STATUSES)
    assert _PAYMENT_STATES["subscription.charged"] == "PAID"
    assert _EVENT_TARGETS["subscription.charged"] == "ACTIVE"
    assert _EVENT_TARGETS["payment.failed"] == "PAST_DUE"
    assert not any("refund" in name for name in _EVENT_TARGETS)


def test_trial_is_fifteen_utc_days_and_writes_follow_status_not_payment():
    start, end = trial_bounds(datetime(2026, 9, 1, tzinfo=timezone.utc))
    assert TRIAL_DAYS == 15
    started = datetime.fromisoformat(start)
    ended = datetime.fromisoformat(end)
    assert ended - started == timedelta(days=15)
    for status in ("TRIALING", "ACTIVE", "PAST_DUE", "GRANDFATHERED"):
        assert is_operational_write_allowed({"subscription_status": status}) is True
    for status in ("CANCELLED", "EXPIRED"):
        assert is_operational_write_allowed({"subscription_status": status}) is False
    assert is_operational_write_allowed(None) is True


def test_limits_are_not_enforced_and_only_the_test_secret_is_accepted():
    assert PLANS["MONTHLY"]["entitlements"] == {}
    assert PLANS["YEARLY"]["entitlements"] == {}
    with pytest.raises(BillingError):
        load_webhook_secret(client=object(), secret_id="erap/billing/razorpay/live")
    assert TEST_SECRET_ID == "erap/billing/razorpay/test"


def test_next_action_follows_subscription_status():
    assert next_action("TRIALING", False) == "subscribe"
    assert next_action("ACTIVE", False) == "manage_subscription"
    assert next_action("ACTIVE", True) == "none"
    assert next_action("PAST_DUE", False) == "payment_required"
    assert next_action("GRANDFATHERED", False) == "none"
    assert "ARCHIVED" not in SUBSCRIPTION_STATUSES
    assert "SUSPENDED" not in SUBSCRIPTION_STATUSES


def test_help_does_not_say_purchase_is_unavailable():
    text = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    assert "Purchase stays unavailable until a plan is offered" not in text
    assert "Checkout opens the provider page and does not by itself mark the subscription active." in text
    assert "Razorpay test integration" in text


def test_commercial_doc_states_the_launch_boundary():
    text = (ROOT / "docs" / "saas-commercial-readiness.md").read_text(encoding="utf-8")
    assert "Production launch is NOT authorized by Phase 14." in text
    assert "ORG-D13B30D99127" not in text
    assert "sub_TiEekQFpwByhkU" not in text
    assert "sub_Thke6MCZ8oT1A2" not in text
