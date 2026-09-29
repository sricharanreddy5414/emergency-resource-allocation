import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from billing.errors import BillingError
from billing.models import (
    access_when_subscription_missing,
    grandfathered_subscription,
    new_trial_subscription,
    trial_bounds,
    trial_window_for_request,
    validate_billing_event,
    validate_subscription,
)
from billing.plans import PLANS, get_plan, require_purchasable, validate_plan_record
from billing.transitions import SUBSCRIPTION_STATUSES, change_subscription_status


UTC = timezone.utc
START = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)


LEGAL = [
    (None, "TRIALING"),
    ("", "GRANDFATHERED"),
    ("NONE", "TRIALING"),
    ("TRIALING", "ACTIVE"),
    ("TRIALING", "EXPIRED"),
    ("ACTIVE", "PAST_DUE"),
    ("ACTIVE", "CANCELLED"),
    ("PAST_DUE", "ACTIVE"),
    ("PAST_DUE", "EXPIRED"),
    ("CANCELLED", "ACTIVE"),
    ("CANCELLED", "EXPIRED"),
    ("EXPIRED", "ACTIVE"),
    ("GRANDFATHERED", "ACTIVE"),
]

ILLEGAL = [
    ("TRIALING", "CANCELLED"),
    ("TRIALING", "PAST_DUE"),
    ("EXPIRED", "TRIALING"),
    ("GRANDFATHERED", "TRIALING"),
    ("ACTIVE", "TRIALING"),
    ("CANCELLED", "TRIALING"),
    ("NONE", "ACTIVE"),
    ("ACTIVE", "EXPIRED"),
    ("PAST_DUE", "CANCELLED"),
]


def test_trial_lasts_fifteen_utc_days():
    start, end = trial_bounds(START)

    assert start == "2026-01-01T00:00:00+00:00"
    assert end == "2026-01-16T00:00:00+00:00"


def test_trial_converts_offset_to_utc():
    india = timezone(timedelta(hours=5, minutes=30))
    start, end = trial_bounds(datetime(2026, 1, 15, 5, 30, tzinfo=india))

    assert start == "2026-01-15T00:00:00+00:00"
    assert end == "2026-01-30T00:00:00+00:00"


def test_naive_trial_clock_is_rejected():
    with pytest.raises(BillingError) as error:
        trial_bounds(datetime(2026, 1, 1, 0, 0))

    assert error.value.status_code == 400


def test_retried_creation_keeps_the_original_trial():
    original = new_trial_subscription("ORG-1", START)
    later = START + timedelta(days=3)
    start, end = trial_window_for_request(original, later)

    assert start == original["trial_start"]
    assert end == original["trial_end"]


@pytest.mark.parametrize("current,target", LEGAL)
def test_legal_transition(current, target):
    assert change_subscription_status(current, target) == target


@pytest.mark.parametrize("current,target", ILLEGAL)
def test_illegal_transition(current, target):
    with pytest.raises(BillingError) as error:
        change_subscription_status(current, target)

    assert error.value.status_code == 409


def test_refunded_is_not_a_subscription_status():
    assert "REFUNDED" not in SUBSCRIPTION_STATUSES

    with pytest.raises(BillingError) as error:
        change_subscription_status("ACTIVE", "REFUNDED")

    assert error.value.status_code == 400


def test_plan_configuration():
    assert PLANS["FREE_TRIAL"]["billing_interval"] == "none"
    assert PLANS["FREE_TRIAL"]["amount_minor"] == 0
    assert PLANS["FREE_TRIAL"]["currency"] == "INR"
    assert PLANS["FREE_TRIAL"]["purchasable"] is False

    assert PLANS["GRANDFATHERED"]["billing_interval"] == "none"
    assert PLANS["GRANDFATHERED"]["amount_minor"] == 0
    assert PLANS["GRANDFATHERED"]["purchasable"] is False

    assert PLANS["MONTHLY"]["billing_interval"] == "month"
    assert PLANS["MONTHLY"]["amount_minor"] == 99900
    assert PLANS["MONTHLY"]["currency"] == "INR"
    assert PLANS["MONTHLY"]["purchasable"] is True

    assert PLANS["YEARLY"]["billing_interval"] == "year"
    assert PLANS["YEARLY"]["amount_minor"] == 999900
    assert PLANS["YEARLY"]["purchasable"] is True


def test_free_trial_cannot_use_a_paid_interval():
    plan = dict(PLANS["FREE_TRIAL"])
    plan["billing_interval"] = "month"

    with pytest.raises(BillingError):
        validate_plan_record(plan)


def test_grandfathered_cannot_be_purchasable():
    plan = dict(PLANS["GRANDFATHERED"])
    plan["purchasable"] = True

    with pytest.raises(BillingError):
        validate_plan_record(plan)


def test_monthly_cannot_use_the_yearly_interval():
    plan = dict(PLANS["MONTHLY"])
    plan["billing_interval"] = "year"

    with pytest.raises(BillingError):
        validate_plan_record(plan)


@pytest.mark.parametrize("plan_id", ["FREE_TRIAL", "GRANDFATHERED"])
def test_non_commercial_plans_are_not_purchasable(plan_id):
    with pytest.raises(BillingError) as error:
        require_purchasable(plan_id)

    assert error.value.status_code == 409
    assert get_plan(plan_id)["purchasable"] is False


@pytest.mark.parametrize("plan_id,amount", [("MONTHLY", 99900), ("YEARLY", 999900)])
def test_commercial_plans_are_purchasable(plan_id, amount):
    plan = require_purchasable(plan_id)

    assert plan["purchasable"] is True
    assert plan["amount_minor"] == amount
    assert plan["currency"] == "INR"


def test_negative_amount_is_rejected():
    plan = dict(PLANS["FREE_TRIAL"])
    plan["amount_minor"] = -1

    with pytest.raises(BillingError) as error:
        validate_plan_record(plan)

    assert "negative" in error.value.message


def test_float_amount_is_rejected():
    plan = dict(PLANS["FREE_TRIAL"])
    plan["amount_minor"] = 1.5

    with pytest.raises(BillingError):
        validate_plan_record(plan)


def test_invalid_billing_interval_is_rejected():
    plan = dict(PLANS["MONTHLY"])
    plan["billing_interval"] = "week"

    with pytest.raises(BillingError):
        validate_plan_record(plan)


def test_subscription_interval_must_match_plan():
    item = new_trial_subscription("ORG-1", START)
    item["billing_interval"] = "month"

    with pytest.raises(BillingError):
        validate_subscription(item)


def test_trial_end_before_start_is_rejected():
    item = new_trial_subscription("ORG-1", START)
    item["trial_end"] = "2025-12-01T00:00:00+00:00"

    with pytest.raises(BillingError) as error:
        validate_subscription(item)

    assert error.value.status_code == 400


def test_invalid_subscription_status_is_rejected():
    item = new_trial_subscription("ORG-1", START)
    item["subscription_status"] = "SUSPENDED"

    with pytest.raises(BillingError):
        validate_subscription(item)


def test_organization_status_is_not_part_of_the_subscription():
    item = new_trial_subscription("ORG-1", START)
    item["status"] = "ACTIVE"

    with pytest.raises(BillingError):
        validate_subscription(item)


def test_grandfathered_record_has_no_provider_or_trial():
    item = grandfathered_subscription("ORG-EXISTING", START)

    assert item["subscription_status"] == "GRANDFATHERED"
    assert item["plan_id"] == "GRANDFATHERED"
    assert item["provider"] == ""
    assert item["provider_subscription_id"] == ""
    assert item["trial_start"] == ""
    assert item["trial_end"] == ""


def test_invalid_processing_status_is_rejected():
    with pytest.raises(BillingError):
        validate_billing_event(_event(processing_status="DONE"))


def test_invalid_payment_state_is_rejected():
    with pytest.raises(BillingError):
        validate_billing_event(_event(payment_state="CHARGED"))


def test_payment_instrument_is_rejected():
    with pytest.raises(BillingError) as error:
        validate_billing_event(_event(card_number="4242424242424242", cvv="123"))

    assert error.value.status_code == 400


def test_missing_subscription_means_legacy_access_without_a_local_state_machine():
    assert access_when_subscription_missing() == "grandfathered"
    source = (ROOT / "src" / "shared" / "access.py").read_text(encoding="utf-8")
    assert "TRANSITIONS" not in source
    assert "is_operational_write_allowed" in source


def _event(**extra):
    item = {
        "provider_event_id": "evt-1",
        "provider": "razorpay",
        "event_type": "subscription.charged",
        "organization_id": "ORG-1",
        "provider_payment_id": "pay-1",
        "payment_state": "PAID",
        "received_at": "2026-01-16T00:00:00+00:00",
        "processed_at": "",
        "processing_status": "RECEIVED",
    }
    item.update(extra)
    return item
