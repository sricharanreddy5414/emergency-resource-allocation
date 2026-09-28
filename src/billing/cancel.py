"""Cancellation request. The webhook applies ACTIVE to CANCELLED."""

from datetime import datetime, timezone

from botocore.exceptions import ClientError

from .errors import BillingError
from .models import format_utc, lifecycle_keys
from .summary import billing_summary
from .transitions import change_subscription_status


ALLOWED_FIELDS = {"organization_id"}
UNAVAILABLE = "Cancellation is not available"


def request_cancellation(body, organization_id, subscriptions, provider_factory, now=None):
    """Record cancel-at-period-end after Razorpay accepts it.

    organization_id is the membership from authorize. The body cannot set
    the status, the timestamps, or the provider subscription id.
    """
    if not isinstance(body, dict) or set(body) - ALLOWED_FIELDS:
        raise BillingError(400, "Invalid cancellation request")

    current = _load(subscriptions, organization_id)
    _require_cancellable(current)

    if current.get("cancel_at_period_end") is True:
        return billing_summary(organization_id, current)

    provider = provider_factory()
    provider.cancel_subscription(provider_subscription_id=current["provider_subscription_id"])
    saved = _mark_period_end(
        subscriptions,
        organization_id,
        current,
        now or datetime.now(timezone.utc),
    )
    return billing_summary(organization_id, saved)


def _load(subscriptions, organization_id):
    try:
        item = subscriptions.get_item(Key={"organization_id": organization_id}).get("Item")
    except ClientError as error:
        print("Cancellation read failed:", error.response["Error"]["Code"])
        raise BillingError(500, "Unable to save cancellation")

    if not item:
        raise BillingError(409, UNAVAILABLE)

    return item


def _require_cancellable(current):
    try:
        change_subscription_status(current.get("subscription_status"), "CANCELLED")
    except BillingError:
        raise BillingError(409, UNAVAILABLE)

    provider_subscription_id = current.get("provider_subscription_id") or ""

    if current.get("provider") != "razorpay" or not provider_subscription_id:
        raise BillingError(409, UNAVAILABLE)


def _mark_period_end(subscriptions, organization_id, current, now):
    values = {
        ":flag": True,
        ":updated": format_utc(now),
        ":status": current.get("subscription_status"),
        ":sid": current.get("provider_subscription_id"),
        ":open": False,
    }
    sets = ["cancel_at_period_end = :flag", "updated_at = :updated"]
    indexed = lifecycle_keys(
        organization_id,
        "ACTIVE",
        "",
        True,
        current.get("current_period_end") or "",
    )
    removes = []

    if indexed["lifecycle_partition"]:
        sets.append("lifecycle_partition = :lifecycle_partition")
        sets.append("lifecycle_due_at = :lifecycle_due_at")
        values[":lifecycle_partition"] = indexed["lifecycle_partition"]
        values[":lifecycle_due_at"] = indexed["lifecycle_due_at"]
    else:
        removes = ["lifecycle_partition", "lifecycle_due_at"]

    expression = "SET " + ", ".join(sets)

    if removes:
        expression += " REMOVE " + ", ".join(removes)

    try:
        subscriptions.update_item(
            Key={"organization_id": organization_id},
            UpdateExpression=expression,
            ConditionExpression=(
                "subscription_status = :status AND "
                "provider_subscription_id = :sid AND "
                "cancel_at_period_end = :open"
            ),
            ExpressionAttributeValues=values,
        )
    except ClientError as error:
        code = error.response["Error"]["Code"]

        if code != "ConditionalCheckFailedException":
            print("Cancellation save failed:", code)
            raise BillingError(500, "Unable to save cancellation")

        fresh = subscriptions.get_item(Key={"organization_id": organization_id}).get("Item") or {}

        if fresh.get("cancel_at_period_end") is True and fresh.get("subscription_status") == current.get("subscription_status"):
            return fresh

        raise BillingError(409, UNAVAILABLE)

    saved = dict(current)
    saved["cancel_at_period_end"] = True
    saved["updated_at"] = values[":updated"]
    return saved
