"""Local trial and scheduled-cancellation completion.

This job does not call a payment provider and does not scan. It queries
LifecycleDueIndex for the UTC dates that are already due.
"""

import json
from datetime import datetime, timedelta, timezone

from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from .errors import BillingError
from .models import LIFECYCLE_INDEX, format_utc, parse_utc
from .transitions import change_subscription_status


LOOKBACK_DAYS = 62


def run_expiry(subscriptions, now=None, invocation_id="", lookback_days=LOOKBACK_DAYS):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise BillingError(400, "Expiry clock must be UTC")

    now = now.astimezone(timezone.utc)
    counts = {"expired": 0, "cancelled": 0, "skipped": 0}

    for partition in _partitions(now, lookback_days):
        for item in _due_items(subscriptions, partition, format_utc(now)):
            outcome = _advance(subscriptions, item, now, invocation_id)
            counts[outcome] += 1

    return counts


def _partitions(now, lookback_days):
    partitions = []

    for offset in range(lookback_days + 1):
        day = (now.date() - timedelta(days=offset)).isoformat()
        partitions.append("TRIAL#" + day)
        partitions.append("CANCEL#" + day)

    return partitions


def _due_items(subscriptions, partition, now_text):
    items = []
    start_key = None

    while True:
        kwargs = {
            "IndexName": LIFECYCLE_INDEX,
            "KeyConditionExpression": (
                Key("lifecycle_partition").eq(partition) & Key("lifecycle_due_at").lte(now_text)
            ),
        }

        if start_key:
            kwargs["ExclusiveStartKey"] = start_key

        result = subscriptions.query(**kwargs)
        items.extend(result.get("Items") or [])
        start_key = result.get("LastEvaluatedKey")

        if not start_key:
            return items


def _advance(subscriptions, item, now, invocation_id):
    status = str(item.get("subscription_status") or "")
    organization_id = item.get("organization_id") or ""

    if status == "TRIALING" and _reached(item.get("trial_end"), now):
        return _commit(
            subscriptions,
            item,
            now,
            invocation_id,
            action="trial_expired",
            target="EXPIRED",
            condition=(
                "subscription_status = :expected AND trial_end = :trial_end "
                "AND lifecycle_due_at = :due"
            ),
            condition_values={
                ":expected": "TRIALING",
                ":trial_end": item.get("trial_end"),
                ":due": item.get("lifecycle_due_at"),
            },
            extra_sets=[],
            extra_values={},
        )

    if (
        status == "ACTIVE"
        and item.get("cancel_at_period_end") is True
        and _reached(item.get("current_period_end"), now)
    ):
        return _commit(
            subscriptions,
            item,
            now,
            invocation_id,
            action="cancellation_completed",
            target="CANCELLED",
            condition=(
                "subscription_status = :expected AND cancel_at_period_end = :cancel "
                "AND current_period_end = :period_end AND lifecycle_due_at = :due"
            ),
            condition_values={
                ":expected": "ACTIVE",
                ":cancel": True,
                ":period_end": item.get("current_period_end"),
                ":due": item.get("lifecycle_due_at"),
            },
            extra_sets=["cancelled_at = :cancelled_at"],
            extra_values={":cancelled_at": format_utc(now)},
        )

    _log(invocation_id, "skipped", organization_id, status, status, now)
    return "skipped"


def _commit(
    subscriptions,
    item,
    now,
    invocation_id,
    action,
    target,
    condition,
    condition_values,
    extra_sets,
    extra_values,
):
    try:
        change_subscription_status(item.get("subscription_status"), target)
    except BillingError:
        _log(invocation_id, "skipped", item.get("organization_id"), item.get("subscription_status"), item.get("subscription_status"), now)
        return "skipped"

    names = {"#status": "subscription_status"}
    values = {
        ":status": target,
        ":updated": format_utc(now),
    }
    values.update(condition_values)
    values.update(extra_values)
    sets = ["#status = :status", "updated_at = :updated", *extra_sets]
    expression = "SET " + ", ".join(sets) + " REMOVE lifecycle_partition, lifecycle_due_at"

    try:
        subscriptions.update_item(
            Key={"organization_id": item["organization_id"]},
            UpdateExpression=expression,
            ConditionExpression=condition,
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
        )
    except ClientError as error:
        code = error.response["Error"]["Code"]

        if code == "ConditionalCheckFailedException":
            _log(
                invocation_id,
                "skipped",
                item.get("organization_id"),
                item.get("subscription_status"),
                item.get("subscription_status"),
                now,
            )
            return "skipped"

        print("Expiry update failed:", code)
        raise

    outcome = "expired" if target == "EXPIRED" else "cancelled"
    _log(invocation_id, action, item.get("organization_id"), item.get("subscription_status"), target, now)
    return outcome


def _reached(value, now):
    if not value:
        return False

    try:
        return parse_utc(value, "lifecycle_due_at") <= now
    except BillingError:
        return False


def _log(invocation_id, action, organization_id, before, after, now):
    print(json.dumps({
        "lifecycle_action": action,
        "organization_id": organization_id or "",
        "subscription_status_before": before or "",
        "subscription_status_after": after or "",
        "at": format_utc(now),
        "invocation_id": invocation_id or "",
    }))
