"""Subscription and billing-event records. No payment instruments."""

from datetime import datetime, timedelta, timezone

from .errors import BillingError
from .plans import INTERVALS, get_plan


TRIAL_DAYS = 15
LEGACY_ACCESS = "grandfathered"
PROVIDERS = {"", "razorpay"}
PROCESSING_STATUSES = {"RECEIVED", "PROCESSED", "IGNORED", "REJECTED"}
PAYMENT_STATES = {"PENDING", "PAID", "FAILED", "REFUNDED"}
FORBIDDEN_FIELDS = {
    "card_number",
    "card",
    "cvv",
    "cvc",
    "pan",
    "bank_account",
    "bank_credentials",
    "webhook_secret",
    "key_secret",
    "secret",
    "password",
    "authorization",
    "token",
}

SUBSCRIPTION_FIELDS = {
    "organization_id",
    "provider",
    "provider_customer_id",
    "provider_subscription_id",
    "plan_id",
    "pending_plan_id",
    "billing_interval",
    "subscription_status",
    "trial_start",
    "trial_end",
    "current_period_start",
    "current_period_end",
    "cancel_at_period_end",
    "cancelled_at",
    "created_at",
    "updated_at",
    "lifecycle_partition",
    "lifecycle_due_at",
}
LIFECYCLE_INDEX = "LifecycleDueIndex"
SUBSCRIPTION_INDEX_ATTRIBUTES = (
    "provider_subscription_id",
    "lifecycle_partition",
    "lifecycle_due_at",
)
COMMERCIAL_PLAN_IDS = {"MONTHLY", "YEARLY"}

EVENT_FIELDS = {
    "provider_event_id",
    "provider",
    "event_type",
    "organization_id",
    "provider_payment_id",
    "payment_state",
    "received_at",
    "processed_at",
    "processing_status",
}


def lifecycle_keys(status, trial_end, cancel_at_period_end, current_period_end):
    """Index entry for one due date. Blank values stay off LifecycleDueIndex.

    The partition is the kind plus the UTC date the row becomes due, so the
    daily job queries that day and a short lookback. It does not put every
    trialing organization in one partition.
    """
    if status == "TRIALING" and trial_end:
        return _lifecycle_entry("TRIAL", trial_end)

    if status == "ACTIVE" and cancel_at_period_end is True and current_period_end:
        return _lifecycle_entry("CANCEL", current_period_end)

    return {"lifecycle_partition": "", "lifecycle_due_at": ""}


def _lifecycle_entry(kind, due_at):
    due = format_utc(parse_utc(due_at, "lifecycle_due_at"))

    return {
        "lifecycle_partition": kind + "#" + due[:10],
        "lifecycle_due_at": due,
    }


def item_for_storage(item, index_attributes):
    """Drop blank global-index keys. DynamoDB rejects an empty string key.

    A missing provider subscription id keeps the subscription out of
    ProviderSubscriptionIndex. A billing event with no organization stays
    out of OrganizationBillingEventsIndex. Callers still use "" in memory.
    """
    stored = dict(item)

    for name in index_attributes:
        if stored.get(name) == "":
            del stored[name]

    return stored


def access_when_subscription_missing():
    """A missing OrganizationSubscriptions row keeps current access.

    Existing organizations are treated as grandfathered until a later
    backfill writes an explicit row. This helper does not read DynamoDB
    and does not change authorization.
    """
    return LEGACY_ACCESS


def parse_utc(value, field_name):
    if not isinstance(value, str) or not value.strip():
        raise BillingError(400, f"{field_name} is invalid")

    text = value.strip().replace("Z", "+00:00")

    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as error:
        raise BillingError(400, f"{field_name} is invalid") from error

    if parsed.tzinfo is None:
        raise BillingError(400, f"{field_name} must be UTC")

    return parsed.astimezone(timezone.utc)


def format_utc(value):
    if value.tzinfo is None:
        raise BillingError(400, "Timestamp must be UTC")

    return value.astimezone(timezone.utc).isoformat()


def trial_bounds(start):
    """Return UTC trial_start and trial_end, 15 days apart."""
    if not isinstance(start, datetime):
        raise BillingError(400, "Trial start is invalid")

    started = start.astimezone(timezone.utc) if start.tzinfo else None

    if started is None:
        raise BillingError(400, "Trial start must be UTC")

    return format_utc(started), format_utc(started + timedelta(days=TRIAL_DAYS))


def trial_window_for_request(existing, now):
    """Keep the original trial when the same creation request is retried."""
    if isinstance(existing, dict) and existing.get("trial_start") and existing.get("trial_end"):
        start = parse_utc(existing["trial_start"], "trial_start")
        end = parse_utc(existing["trial_end"], "trial_end")

        if end <= start:
            raise BillingError(400, "Trial end must be after trial start")

        return format_utc(start), format_utc(end)

    return trial_bounds(now)


def _reject_forbidden(item):
    found = FORBIDDEN_FIELDS.intersection(item)

    if found:
        raise BillingError(400, "Payment credentials cannot be stored")


def _text(item, field, required=False, limit=128):
    value = item.get(field, "")

    if value is None:
        value = ""

    if not isinstance(value, str):
        raise BillingError(400, f"{field} is invalid")

    value = value.strip()

    if required and not value:
        raise BillingError(400, f"{field} is required")

    if len(value) > limit:
        raise BillingError(400, f"{field} is invalid")

    return value


def _optional_time(item, field):
    value = item.get(field) or ""

    if value == "":
        return ""

    return format_utc(parse_utc(value, field))


def _ordered_period(start, end, start_name, end_name):
    if start and end and parse_utc(end, end_name) <= parse_utc(start, start_name):
        raise BillingError(400, f"{end_name} must be after {start_name}")


def validate_subscription(item):
    if not isinstance(item, dict):
        raise BillingError(400, "Subscription is invalid")

    _reject_forbidden(item)

    if set(item) - SUBSCRIPTION_FIELDS:
        raise BillingError(400, "Subscription contains an unsupported field")

    from .transitions import SUBSCRIPTION_STATUSES

    status = str(item.get("subscription_status") or "").strip().upper()

    if status not in SUBSCRIPTION_STATUSES:
        raise BillingError(400, "Subscription status is invalid")

    plan = get_plan(item.get("plan_id"))
    interval = item.get("billing_interval")

    if interval not in INTERVALS or interval != plan["billing_interval"]:
        raise BillingError(400, "Billing interval does not match the plan")

    provider = item.get("provider", "")

    if provider not in PROVIDERS:
        raise BillingError(400, "Billing provider is invalid")

    if not isinstance(item.get("cancel_at_period_end"), bool):
        raise BillingError(400, "Cancellation setting is invalid")

    trial_start = _optional_time(item, "trial_start")
    trial_end = _optional_time(item, "trial_end")
    period_start = _optional_time(item, "current_period_start")
    period_end = _optional_time(item, "current_period_end")
    cancelled_at = _optional_time(item, "cancelled_at")
    created_at = format_utc(parse_utc(item.get("created_at"), "created_at"))
    updated_at = format_utc(parse_utc(item.get("updated_at"), "updated_at"))

    if status == "TRIALING":
        if plan["plan_id"] != "FREE_TRIAL":
            raise BillingError(400, "Trial subscription plan is invalid")

        if not trial_start or not trial_end:
            raise BillingError(400, "Trial timestamps are required")

    if status == "GRANDFATHERED":
        if plan["plan_id"] != "GRANDFATHERED":
            raise BillingError(400, "Grandfathered subscription plan is invalid")

        if provider or _text(item, "provider_subscription_id"):
            raise BillingError(400, "Grandfathered access has no payment subscription")

        if trial_start or trial_end:
            raise BillingError(400, "Grandfathered access has no trial clock")

    if trial_start or trial_end:
        _ordered_period(trial_start, trial_end, "trial_start", "trial_end")

        if bool(trial_start) != bool(trial_end):
            raise BillingError(400, "Trial timestamps must both be set")

    _ordered_period(period_start, period_end, "current_period_start", "current_period_end")
    pending_plan_id = _pending_plan(item)
    indexed = lifecycle_keys(status, trial_end, item["cancel_at_period_end"], period_end)

    return {
        "organization_id": _text(item, "organization_id", required=True),
        "provider": provider,
        "provider_customer_id": _text(item, "provider_customer_id"),
        "provider_subscription_id": _text(item, "provider_subscription_id"),
        "plan_id": plan["plan_id"],
        "pending_plan_id": pending_plan_id,
        "billing_interval": interval,
        "subscription_status": status,
        "trial_start": trial_start,
        "trial_end": trial_end,
        "current_period_start": period_start,
        "current_period_end": period_end,
        "cancel_at_period_end": item["cancel_at_period_end"],
        "cancelled_at": cancelled_at,
        "created_at": created_at,
        "updated_at": updated_at,
        "lifecycle_partition": indexed["lifecycle_partition"],
        "lifecycle_due_at": indexed["lifecycle_due_at"],
    }


def _pending_plan(item):
    pending = _text(item, "pending_plan_id")

    if not pending:
        return ""

    if pending not in COMMERCIAL_PLAN_IDS:
        raise BillingError(400, "Pending plan is invalid")

    return get_plan(pending)["plan_id"]


def new_trial_subscription(organization_id, now):
    start, end = trial_bounds(now)

    return validate_subscription(
        {
            "organization_id": organization_id,
            "provider": "",
            "provider_customer_id": "",
            "provider_subscription_id": "",
            "plan_id": "FREE_TRIAL",
            "billing_interval": "none",
            "subscription_status": "TRIALING",
            "trial_start": start,
            "trial_end": end,
            "current_period_start": "",
            "current_period_end": "",
            "cancel_at_period_end": False,
            "cancelled_at": "",
            "created_at": start,
            "updated_at": start,
        }
    )


def grandfathered_subscription(organization_id, now):
    """Domain shape only. This does not write a row."""
    stamp = format_utc(now)

    return validate_subscription(
        {
            "organization_id": organization_id,
            "provider": "",
            "provider_customer_id": "",
            "provider_subscription_id": "",
            "plan_id": "GRANDFATHERED",
            "billing_interval": "none",
            "subscription_status": "GRANDFATHERED",
            "trial_start": "",
            "trial_end": "",
            "current_period_start": "",
            "current_period_end": "",
            "cancel_at_period_end": False,
            "cancelled_at": "",
            "created_at": stamp,
            "updated_at": stamp,
        }
    )


def validate_billing_event(item):
    if not isinstance(item, dict):
        raise BillingError(400, "Billing event is invalid")

    _reject_forbidden(item)

    if set(item) - EVENT_FIELDS:
        raise BillingError(400, "Billing event contains an unsupported field")

    provider = item.get("provider", "")

    if provider not in PROVIDERS:
        raise BillingError(400, "Billing provider is invalid")

    status = str(item.get("processing_status") or "").strip().upper()

    if status not in PROCESSING_STATUSES:
        raise BillingError(400, "Processing status is invalid")

    payment_state = item.get("payment_state", "")

    if payment_state not in {""} and str(payment_state).strip().upper() not in PAYMENT_STATES:
        raise BillingError(400, "Payment state is invalid")

    if payment_state:
        payment_state = str(payment_state).strip().upper()

    processed_at = _optional_time(item, "processed_at")

    return {
        "provider_event_id": _text(item, "provider_event_id", required=True),
        "provider": provider,
        "event_type": _text(item, "event_type", required=True),
        "organization_id": _text(item, "organization_id"),
        "provider_payment_id": _text(item, "provider_payment_id"),
        "payment_state": payment_state,
        "received_at": format_utc(parse_utc(item.get("received_at"), "received_at")),
        "processed_at": processed_at,
        "processing_status": status,
    }
