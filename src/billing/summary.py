"""Billing summary for one organization. This does not create a row."""

from botocore.exceptions import ClientError

from .errors import BillingError
from .models import access_when_subscription_missing


def read_billing(subscriptions, organization_id):
    try:
        item = subscriptions.get_item(Key={"organization_id": organization_id}).get("Item")
    except ClientError as error:
        print("Billing read failed:", error.response["Error"]["Code"])
        raise BillingError(500, "Unable to read billing")

    if not item:
        if access_when_subscription_missing() != "grandfathered":
            raise BillingError(500, "Unable to read billing")

        return billing_summary(organization_id, None)

    return billing_summary(organization_id, item)


def billing_summary(organization_id, item):
    subscription = _grandfathered() if item is None else _subscription(item)

    return {
        "organization_id": organization_id,
        "subscription": subscription,
        "next_action": next_action(
            subscription["subscription_status"],
            subscription["cancel_at_period_end"],
        ),
    }


def next_action(status, cancel_at_period_end):
    if status == "PAST_DUE":
        return "payment_required"

    if status == "ACTIVE" and not cancel_at_period_end:
        return "manage_subscription"

    if status in {"TRIALING", "EXPIRED", "CANCELLED"}:
        return "subscribe"

    return "none"


def _grandfathered():
    return {
        "plan_id": "GRANDFATHERED",
        "billing_interval": "none",
        "subscription_status": "GRANDFATHERED",
        "trial_start": None,
        "trial_end": None,
        "current_period_start": None,
        "current_period_end": None,
        "cancel_at_period_end": False,
        "cancelled_at": None,
        "pending_plan_id": None,
    }


def _subscription(item):
    return {
        "plan_id": item.get("plan_id") or "",
        "billing_interval": item.get("billing_interval") or "",
        "subscription_status": item.get("subscription_status") or "",
        "trial_start": _present(item.get("trial_start")),
        "trial_end": _present(item.get("trial_end")),
        "current_period_start": _present(item.get("current_period_start")),
        "current_period_end": _present(item.get("current_period_end")),
        "cancel_at_period_end": item.get("cancel_at_period_end") is True,
        "cancelled_at": _present(item.get("cancelled_at")),
        "pending_plan_id": _present(item.get("pending_plan_id")),
    }


def _present(value):
    if value is None:
        return None

    text = str(value).strip()

    return text or None
