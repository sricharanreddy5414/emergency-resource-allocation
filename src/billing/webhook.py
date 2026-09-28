"""Verified provider events. A browser return never reaches this module."""

import json
from datetime import datetime, timezone

from botocore.exceptions import ClientError

from .errors import BillingError
from .models import COMMERCIAL_PLAN_IDS, format_utc, item_for_storage, lifecycle_keys, validate_billing_event
from .plans import get_plan
from .provider.razorpay import parse_webhook, signatures_match
from .transitions import change_subscription_status


PROVIDER_INDEX = "ProviderSubscriptionIndex"


def process_webhook(event, subscriptions, events, secret, now=None):
    now = now or datetime.now(timezone.utc)
    raw = _raw_body(event)
    signature = _header(event, "x-razorpay-signature")
    event_id = _header(event, "x-razorpay-event-id")

    if not signatures_match(raw, signature or "", secret or ""):
        _log(event_id or "", "", "", "", "REJECTED")
        return 401, {"message": "Authentication required"}

    if not event_id:
        return 400, {"message": "Invalid webhook request"}

    try:
        payload = json.loads(raw.decode("utf-8"))
        parsed = parse_webhook(payload)
    except (UnicodeError, json.JSONDecodeError, BillingError):
        return 400, {"message": "Invalid webhook request"}

    try:
        existing = _begin(events, event_id, parsed, now)
    except ClientError as error:
        print("Webhook record failed:", error.response["Error"]["Code"])
        return 500, {"message": "Unable to record billing event"}

    if existing in {"PROCESSED", "IGNORED"}:
        _log(event_id, parsed["event_type"], "", parsed["provider_subscription_id"], existing)
        return 200, {"message": "OK"}

    try:
        outcome = _apply(subscriptions, parsed, resume=existing == "resume")
    except ClientError as error:
        print("Webhook subscription update failed:", error.response["Error"]["Code"])
        return 500, {"message": "Unable to record billing event"}

    try:
        _finish(events, event_id, parsed, outcome, now)
    except ClientError as error:
        print("Webhook record failed:", error.response["Error"]["Code"])
        return 500, {"message": "Unable to record billing event"}

    _log(
        event_id,
        parsed["event_type"],
        outcome["organization_id"],
        parsed["provider_subscription_id"],
        outcome["processing_status"],
    )
    return 200, {"message": "OK"}


def _begin(events, event_id, parsed, now):
    current = events.get_item(Key={"provider_event_id": event_id}).get("Item")

    if current and current.get("processing_status") in {"PROCESSED", "IGNORED"}:
        return current["processing_status"]

    if current:
        return "resume"

    try:
        events.put_item(
            Item=_event_item(event_id, parsed, "", "RECEIVED", now, ""),
            ConditionExpression="attribute_not_exists(provider_event_id)",
        )
    except ClientError as error:
        if error.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise

        current = events.get_item(Key={"provider_event_id": event_id}).get("Item")

        if current and current.get("processing_status") in {"PROCESSED", "IGNORED"}:
            return current["processing_status"]

    return "created"


def _apply(subscriptions, parsed, resume=False):
    subscription_id = parsed["provider_subscription_id"]
    current = _find_subscription(subscriptions, subscription_id) if subscription_id else None

    if current is None:
        return {"organization_id": "", "processing_status": "IGNORED", "changed": False}

    decision = _decision(current, parsed, resume)

    if decision == "ignore":
        return {
            "organization_id": current["organization_id"],
            "processing_status": "IGNORED",
            "changed": False,
        }

    if decision == "unchanged":
        return {
            "organization_id": current["organization_id"],
            "processing_status": "PROCESSED",
            "changed": False,
        }

    _save_transition(subscriptions, current, parsed, changing=decision == "apply")
    return {
        "organization_id": current["organization_id"],
        "processing_status": "PROCESSED",
        "changed": True,
    }


def _decision(current, parsed, resume):
    target = parsed["target_status"]

    if not target:
        return "ignore"

    if _stale(current, parsed, target):
        return "ignore"

    if current.get("subscription_status") == target:
        return "unchanged" if resume else "refresh"

    try:
        change_subscription_status(current.get("subscription_status"), target)
    except BillingError:
        return "ignore"

    return "apply"


def _stale(current, parsed, target):
    """An older activation must not undo a cancellation of the same subscription."""
    if current.get("subscription_status") != "CANCELLED" or target != "ACTIVE":
        return False

    cancelled_at = current.get("cancelled_at") or ""
    event_time = parsed.get("event_time") or ""

    if not cancelled_at or not event_time:
        return True

    return event_time <= cancelled_at


def _find_subscription(subscriptions, provider_subscription_id):
    from boto3.dynamodb.conditions import Key

    result = subscriptions.query(
        IndexName=PROVIDER_INDEX,
        KeyConditionExpression=Key("provider_subscription_id").eq(provider_subscription_id),
    )
    items = result.get("Items") or []

    if len(items) != 1:
        if len(items) > 1:
            raise ClientError(
                {"Error": {"Code": "ValidationException", "Message": "ambiguous subscription"}},
                "Query",
            )
        return None

    return items[0]


def _save_transition(subscriptions, current, parsed, changing):
    target = parsed["target_status"]
    names = {"#status": "subscription_status"}
    values = {
        ":expected": current.get("subscription_status"),
        ":sid": current.get("provider_subscription_id"),
        ":updated": parsed["event_time"] or format_utc(datetime.now(timezone.utc)),
    }
    sets = ["updated_at = :updated"]

    if changing:
        sets.append("#status = :status")
        values[":status"] = target

    if parsed["period_start"]:
        sets.append("current_period_start = :period_start")
        values[":period_start"] = parsed["period_start"]

    if parsed["period_end"]:
        sets.append("current_period_end = :period_end")
        values[":period_end"] = parsed["period_end"]

    cancel_flag = parsed["cancel_at_period_end"]
    resulting_cancel = current.get("cancel_at_period_end") is True

    if changing and target == "CANCELLED":
        sets.append("cancelled_at = :cancelled_at")
        values[":cancelled_at"] = parsed["cancelled_at"] or parsed["event_time"]

        if cancel_flag is not None:
            sets.append("cancel_at_period_end = :cancel_at_period_end")
            values[":cancel_at_period_end"] = cancel_flag
            resulting_cancel = cancel_flag
    elif changing and target == "ACTIVE":
        resulting_cancel = False if cancel_flag is None else cancel_flag
        sets.append("cancel_at_period_end = :cancel_at_period_end")
        values[":cancel_at_period_end"] = resulting_cancel
        _apply_pending_plan(current, sets, values)
    elif cancel_flag is not None:
        resulting_cancel = cancel_flag
        sets.append("cancel_at_period_end = :cancel_at_period_end")
        values[":cancel_at_period_end"] = cancel_flag

    resulting_status = target if changing else current.get("subscription_status")
    removes = _lifecycle_assignment(
        sets,
        values,
        resulting_status,
        current.get("trial_end") or "",
        resulting_cancel,
        parsed["period_end"] or current.get("current_period_end") or "",
    )
    expression = "SET " + ", ".join(sets)

    if removes:
        expression += " REMOVE " + ", ".join(removes)

    try:
        subscriptions.update_item(
            Key={"organization_id": current["organization_id"]},
            UpdateExpression=expression,
            ConditionExpression="#status = :expected AND provider_subscription_id = :sid",
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
        )
    except ClientError as error:
        if error.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise

        fresh = subscriptions.get_item(
            Key={"organization_id": current["organization_id"]}
        ).get("Item") or {}

        if fresh.get("subscription_status") != target:
            raise


def _apply_pending_plan(current, sets, values):
    """Promote a checkout selection only when the provider confirms ACTIVE."""
    pending = str(current.get("pending_plan_id") or "").strip()

    if pending not in COMMERCIAL_PLAN_IDS:
        return

    plan = get_plan(pending)
    sets.append("plan_id = :plan_id")
    sets.append("billing_interval = :interval")
    sets.append("pending_plan_id = :pending_cleared")
    values[":plan_id"] = plan["plan_id"]
    values[":interval"] = plan["billing_interval"]
    values[":pending_cleared"] = ""


def _lifecycle_assignment(sets, values, status, trial_end, cancel_at_period_end, period_end):
    indexed = lifecycle_keys(status, trial_end, cancel_at_period_end, period_end)

    if indexed["lifecycle_partition"]:
        sets.append("lifecycle_partition = :lifecycle_partition")
        sets.append("lifecycle_due_at = :lifecycle_due_at")
        values[":lifecycle_partition"] = indexed["lifecycle_partition"]
        values[":lifecycle_due_at"] = indexed["lifecycle_due_at"]
        return []

    return ["lifecycle_partition", "lifecycle_due_at"]


def _finish(events, event_id, parsed, outcome, now):
    sets = [
        "processing_status = :status",
        "processed_at = :processed",
        "payment_state = :payment",
        "provider_payment_id = :payment_id",
    ]
    values = {
        ":status": outcome["processing_status"],
        ":processed": format_utc(now),
        ":payment": parsed["payment_state"],
        ":payment_id": parsed["provider_payment_id"],
        ":received": "RECEIVED",
    }

    if outcome["organization_id"]:
        sets.append("organization_id = :organization")
        values[":organization"] = outcome["organization_id"]

    events.update_item(
        Key={"provider_event_id": event_id},
        UpdateExpression="SET " + ", ".join(sets),
        ConditionExpression="processing_status = :received",
        ExpressionAttributeValues=values,
    )


def _event_item(event_id, parsed, organization_id, status, now, processed_at):
    return item_for_storage(
        validate_billing_event(
            {
                "provider_event_id": event_id,
                "provider": "razorpay",
                "event_type": parsed["event_type"],
                "organization_id": organization_id,
                "provider_payment_id": parsed["provider_payment_id"],
                "payment_state": parsed["payment_state"],
                "received_at": format_utc(now),
                "processed_at": processed_at,
                "processing_status": status,
            }
        ),
        ("organization_id",),
    )


def _raw_body(event):
    body = (event or {}).get("body")

    if body is None:
        return b""

    if event.get("isBase64Encoded") and isinstance(body, str):
        import base64

        return base64.b64decode(body)

    if isinstance(body, bytes):
        return body

    return str(body).encode("utf-8")


def _header(event, name):
    headers = (event or {}).get("headers") or {}

    if not isinstance(headers, dict):
        return ""

    for key, value in headers.items():
        if str(key).lower() == name and isinstance(value, str) and value.strip():
            return value.strip()

    return ""


def _log(event_id, event_type, organization_id, provider_subscription_id, processing_status):
    print(json.dumps({
        "provider": "razorpay",
        "provider_event_id": event_id,
        "event_type": event_type,
        "organization_id": organization_id,
        "provider_subscription_id": provider_subscription_id,
        "processing_status": processing_status,
    }))
