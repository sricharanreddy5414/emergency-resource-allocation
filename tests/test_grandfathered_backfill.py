"""One-organization grandfathered billing backfill. No provider calls."""

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]

from backfill_grandfathered_billing import (
    PILOT_ORGANIZATION_ID,
    BackfillError,
    execute,
    prepare,
    require_pilot,
    stored_grandfathered_item,
)
from billing.checkout import CHECKOUT_STATUSES, create_checkout
from billing.models import SUBSCRIPTION_INDEX_ATTRIBUTES


NOW = datetime(2026, 9, 29, 6, 0, tzinfo=timezone.utc)
OTHER = "ORG-OTHER0000001"


def organization(status="ACTIVE"):
    return {
        "organization_id": PILOT_ORGANIZATION_ID,
        "name": "ERAP Pilot Operations",
        "status": status,
    }


class Gateway:
    def __init__(self, organization_item=None, subscription_item=None, race=False):
        self.organization_item = organization_item
        self.subscription_item = subscription_item
        self.race = race
        self.puts = []

    def organizations_get(self, organization_id):
        assert organization_id == PILOT_ORGANIZATION_ID
        return self.organization_item

    def subscriptions_get(self, organization_id):
        assert organization_id == PILOT_ORGANIZATION_ID
        return self.subscription_item

    def subscriptions_put(self, item):
        if self.race:
            raise ClientError(
                {"Error": {"Code": "ConditionalCheckFailedException", "Message": "exists"}},
                "PutItem",
            )
        self.puts.append(dict(item))
        return "CREATED"


def test_wrong_organization_is_rejected():
    with pytest.raises(BackfillError):
        require_pilot(OTHER)

    with pytest.raises(BackfillError):
        require_pilot("*")

    with pytest.raises(BackfillError):
        require_pilot(PILOT_ORGANIZATION_ID + "," + OTHER)


def test_missing_organization_is_rejected():
    with pytest.raises(BackfillError, match="not found"):
        prepare(PILOT_ORGANIZATION_ID, None, None, NOW)


def test_dry_run_performs_no_write():
    gateway = Gateway(organization())
    result = execute(
        PILOT_ORGANIZATION_ID,
        False,
        gateway.organizations_get,
        gateway.subscriptions_get,
        gateway.subscriptions_put,
        NOW,
    )

    assert result["result"] == "would create GRANDFATHERED subscription for " + PILOT_ORGANIZATION_ID
    assert result["subscription_row"] == "ABSENT"
    assert gateway.puts == []


def test_absent_row_creates_exactly_one_grandfathered_row():
    gateway = Gateway(organization())
    result = execute(
        PILOT_ORGANIZATION_ID,
        True,
        gateway.organizations_get,
        gateway.subscriptions_get,
        gateway.subscriptions_put,
        NOW,
    )
    item = gateway.puts[0]

    assert result["result"] == "CREATED"
    assert len(gateway.puts) == 1
    assert item["organization_id"] == PILOT_ORGANIZATION_ID
    assert item["subscription_status"] == "GRANDFATHERED"
    assert item["plan_id"] == "GRANDFATHERED"
    assert item["billing_interval"] == "none"
    assert item["provider"] == ""
    assert item["cancel_at_period_end"] is False
    assert "provider_subscription_id" not in item
    assert "lifecycle_partition" not in item
    assert "lifecycle_due_at" not in item


def test_existing_row_is_not_overwritten():
    existing = stored_grandfathered_item(PILOT_ORGANIZATION_ID, NOW)
    gateway = Gateway(organization(), existing)
    result = execute(
        PILOT_ORGANIZATION_ID,
        True,
        gateway.organizations_get,
        gateway.subscriptions_get,
        gateway.subscriptions_put,
        NOW,
    )

    assert result["result"] == "ALREADY_EXISTS"
    assert gateway.puts == []


def test_conditional_race_is_not_overwritten():
    gateway = Gateway(organization(), race=True)
    result = execute(
        PILOT_ORGANIZATION_ID,
        True,
        gateway.organizations_get,
        gateway.subscriptions_get,
        gateway.subscriptions_put,
        NOW,
    )

    assert result["result"] == "CONCURRENT_CREATE"
    assert gateway.puts == []


def test_stored_row_has_no_lifecycle_or_provider_subscription():
    item = stored_grandfathered_item(PILOT_ORGANIZATION_ID, NOW)

    for name in SUBSCRIPTION_INDEX_ATTRIBUTES:
        assert name not in item

    assert item["provider_customer_id"] == ""
    assert item["pending_plan_id"] == ""
    assert item["trial_start"] == ""
    assert item["trial_end"] == ""
    assert item["current_period_start"] == ""
    assert item["current_period_end"] == ""
    assert item["cancelled_at"] == ""


def test_backfill_script_does_not_call_a_provider_or_scan():
    source = (ROOT / "scripts" / "backfill_grandfathered_billing.py").read_text(encoding="utf-8")

    assert "razorpay" not in source.lower()
    assert "scan(" not in source
    assert "DeleteItem" not in source
    assert "UpdateItem" not in source
    assert "BatchWriteItem" not in source


def test_grandfathered_row_passes_the_checkout_row_prerequisite():
    item = stored_grandfathered_item(PILOT_ORGANIZATION_ID, NOW)

    class Table:
        def get_item(self, Key):
            return {"Item": dict(item)}

        def update_item(self, **kwargs):
            self.saved = kwargs

    class Provider:
        def __init__(self):
            self.calls = []

        def create_subscription(self, **kwargs):
            self.calls.append(kwargs)
            return {
                "provider": "razorpay",
                "provider_subscription_id": "sub_TestCheckout01",
                "public_key_id": "rzp_test_public",
            }

    provider = Provider()
    table = Table()
    result = create_checkout(
        {"plan_id": "MONTHLY"},
        PILOT_ORGANIZATION_ID,
        table,
        lambda: provider,
        now=NOW,
    )

    assert item["subscription_status"] in CHECKOUT_STATUSES
    assert provider.calls[0]["total_count"] == 468
    assert provider.calls[0]["razorpay_plan_id"].startswith("plan_")
    assert result["provider_subscription_id"] == "sub_TestCheckout01"
