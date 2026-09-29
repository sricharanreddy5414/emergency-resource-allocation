"""Checkout rules. Creating a provider subscription does not activate it."""

from datetime import datetime, timezone

from botocore.exceptions import ClientError

from .errors import BillingError
from .models import format_utc
from .plans import get_plan
from .provider.razorpay import provider_plan


CHECKOUT_STATUSES = {"TRIALING", "EXPIRED", "CANCELLED", "GRANDFATHERED"}
ALLOWED_FIELDS = {"plan_id", "organization_id"}
UNAVAILABLE = "Plan is not currently available for purchase"


def create_checkout(
    body,
    organization_id,
    subscriptions,
    provider_factory,
    links=None,
    plans=None,
    now=None,
):
    """Return the public checkout reference for one organization.

    organization_id must already be the membership selected by authorize.
    plans and links are injection points for tests. Production prices are
    purchasable, but total_count is unset, so provider_plan refuses the
    call before Razorpay.
    """
    plan_id = _plan_id(body)
    plan = _plan(plan_id, plans)

    if plan.get("purchasable") is not True:
        raise BillingError(409, UNAVAILABLE)

    razorpay_plan_id, total_count = provider_plan(plan_id, links)
    current = _load(subscriptions, organization_id)
    _require_checkout_state(current)
    provider = provider_factory()
    created = provider.create_subscription(
        razorpay_plan_id=razorpay_plan_id,
        total_count=total_count,
        organization_id=organization_id,
    )
    _save_reference(
        subscriptions,
        organization_id,
        created["provider_subscription_id"],
        current.get("subscription_status"),
        plan["plan_id"],
        now or datetime.now(timezone.utc),
    )
    return {
        "provider": "razorpay",
        "provider_subscription_id": created["provider_subscription_id"],
        "public_key_id": created["public_key_id"],
    }


def _plan_id(body):
    if not isinstance(body, dict):
        raise BillingError(400, "Invalid checkout request")

    if "amount" in body or "price" in body:
        raise BillingError(400, "Amount cannot be supplied")

    if "currency" in body:
        raise BillingError(400, "Currency cannot be supplied")

    if set(body) - ALLOWED_FIELDS:
        raise BillingError(400, "Invalid checkout request")

    plan_id = body.get("plan_id")

    if not isinstance(plan_id, str) or not plan_id.strip():
        raise BillingError(400, "Plan is invalid")

    return plan_id.strip()


def _plan(plan_id, plans):
    if plans is None:
        return get_plan(plan_id)

    plan = plans.get(plan_id)

    if plan is None:
        raise BillingError(400, "Plan is invalid")

    return dict(plan)


def _load(subscriptions, organization_id):
    item = subscriptions.get_item(Key={"organization_id": organization_id}).get("Item")

    if not item:
        raise BillingError(409, "Organization billing is not ready for checkout")

    return item


def _require_checkout_state(current):
    status = str(current.get("subscription_status") or "")

    if status == "ACTIVE":
        raise BillingError(409, "This organization already has an active subscription")

    if status == "PAST_DUE":
        raise BillingError(409, "The existing subscription requires recovery")

    if status not in CHECKOUT_STATUSES:
        raise BillingError(409, "Checkout is not available for this subscription")

    provider_subscription_id = current.get("provider_subscription_id") or ""

    if provider_subscription_id and status != "CANCELLED":
        raise BillingError(409, "A checkout is already in progress")


def _save_reference(subscriptions, organization_id, provider_subscription_id, status, plan_id, now):
    """Remember the provider subscription and the plan waiting for confirmation.

    plan_id on the row stays unchanged until a verified webhook activates it.
    """
    try:
        subscriptions.update_item(
            Key={"organization_id": organization_id},
            UpdateExpression=(
                "SET provider = :provider, "
                "provider_subscription_id = :sid, "
                "pending_plan_id = :pending, "
                "updated_at = :updated"
            ),
            ConditionExpression=(
                "subscription_status = :status AND "
                "(attribute_not_exists(provider_subscription_id) OR "
                "provider_subscription_id = :empty OR "
                "subscription_status = :cancelled)"
            ),
            ExpressionAttributeValues={
                ":provider": "razorpay",
                ":sid": provider_subscription_id,
                ":pending": plan_id,
                ":updated": format_utc(now),
                ":status": status,
                ":empty": "",
                ":cancelled": "CANCELLED",
            },
        )
    except ClientError as error:
        code = error.response["Error"]["Code"]

        if code == "ConditionalCheckFailedException":
            raise BillingError(409, "A checkout is already in progress")

        print("Checkout save failed:", code)
        raise BillingError(500, "Unable to save checkout")
