"""Server-side plan configuration."""

from .errors import BillingError


CURRENCIES = {"INR"}
INTERVALS = {"none", "month", "year"}
ENTITLEMENT_KEYS = {
    "MAX_MEMBERS",
    "MAX_LOCATIONS",
    "MAX_RESOURCES",
    "MAX_REQUESTS",
    "ADVANCED_FEATURES",
    "AUDIT_HISTORY",
    "API_ACCESS",
}
NUMERIC_ENTITLEMENTS = {
    "MAX_MEMBERS",
    "MAX_LOCATIONS",
    "MAX_RESOURCES",
    "MAX_REQUESTS",
}
FLAG_ENTITLEMENTS = {"ADVANCED_FEATURES", "AUDIT_HISTORY", "API_ACCESS"}

# amount_minor is paise. None means no commercial price has been set.
PLAN_RULES = {
    "FREE_TRIAL": {
        "billing_interval": "none",
        "amount_minor": 0,
        "purchasable": False,
    },
    "GRANDFATHERED": {
        "billing_interval": "none",
        "amount_minor": 0,
        "purchasable": False,
    },
    "MONTHLY": {
        "billing_interval": "month",
        "amount_minor": 99900,
        "purchasable": True,
    },
    "YEARLY": {
        "billing_interval": "year",
        "amount_minor": 999900,
        "purchasable": True,
    },
}


def _amount(value, allow_unset):
    if value is None and allow_unset:
        return None

    if isinstance(value, bool) or not isinstance(value, int):
        raise BillingError(400, "Amount must be an integer number of minor units")

    if value < 0:
        raise BillingError(400, "Amount cannot be negative")

    return value


def validate_entitlements(entitlements):
    """Accept a future limit map. Phase A does not enforce the numbers."""
    if entitlements is None:
        return {}

    if not isinstance(entitlements, dict):
        raise BillingError(400, "Entitlements are invalid")

    cleaned = {}

    for key, value in entitlements.items():
        if key not in ENTITLEMENT_KEYS:
            raise BillingError(400, "Entitlement is invalid")

        if key in NUMERIC_ENTITLEMENTS:
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise BillingError(400, "Entitlement limit is invalid")
        elif not isinstance(value, bool):
            raise BillingError(400, "Entitlement flag is invalid")

        cleaned[key] = value

    return cleaned


def validate_plan_record(plan):
    if not isinstance(plan, dict):
        raise BillingError(400, "Plan is invalid")

    plan_id = plan.get("plan_id")
    rules = PLAN_RULES.get(plan_id)

    if rules is None:
        raise BillingError(400, "Plan is invalid")

    interval = plan.get("billing_interval")

    if interval not in INTERVALS or interval != rules["billing_interval"]:
        raise BillingError(400, "Billing interval does not match the plan")

    currency = plan.get("currency")

    if currency not in CURRENCIES:
        raise BillingError(400, "Currency is invalid")

    purchasable = plan.get("purchasable")

    if not isinstance(purchasable, bool) or purchasable != rules["purchasable"]:
        raise BillingError(400, "Plan purchase setting is invalid")

    amount = _amount(plan.get("amount_minor"), allow_unset=rules["amount_minor"] is None)

    if amount != rules["amount_minor"]:
        raise BillingError(400, "Plan amount does not match the configuration")

    display_name = str(plan.get("display_name") or "").strip()

    if not display_name or len(display_name) > 80:
        raise BillingError(400, "Plan name is invalid")

    return {
        "plan_id": plan_id,
        "display_name": display_name,
        "billing_interval": interval,
        "amount_minor": amount,
        "currency": currency,
        "purchasable": purchasable,
        "entitlements": validate_entitlements(plan.get("entitlements")),
    }


PLANS = {
    "FREE_TRIAL": validate_plan_record(
        {
            "plan_id": "FREE_TRIAL",
            "display_name": "15-day trial",
            "billing_interval": "none",
            "amount_minor": 0,
            "currency": "INR",
            "purchasable": False,
            "entitlements": {},
        }
    ),
    "GRANDFATHERED": validate_plan_record(
        {
            "plan_id": "GRANDFATHERED",
            "display_name": "Existing access",
            "billing_interval": "none",
            "amount_minor": 0,
            "currency": "INR",
            "purchasable": False,
            "entitlements": {},
        }
    ),
    "MONTHLY": validate_plan_record(
        {
            "plan_id": "MONTHLY",
            "display_name": "Monthly",
            "billing_interval": "month",
            "amount_minor": 99900,
            "currency": "INR",
            "purchasable": True,
            "entitlements": {},
        }
    ),
    "YEARLY": validate_plan_record(
        {
            "plan_id": "YEARLY",
            "display_name": "Yearly",
            "billing_interval": "year",
            "amount_minor": 999900,
            "currency": "INR",
            "purchasable": True,
            "entitlements": {},
        }
    ),
}


def get_plan(plan_id):
    plan = PLANS.get(plan_id)

    if plan is None:
        raise BillingError(400, "Plan is invalid")

    return dict(plan)


def customer_plans():
    """Plans a billing page may show. Provider ids and entitlements stay internal."""
    listed = []

    for plan_id in ("MONTHLY", "YEARLY"):
        plan = get_plan(plan_id)
        public = {
            "plan_id": plan["plan_id"],
            "display_name": plan["display_name"],
            "billing_interval": plan["billing_interval"],
            "currency": plan["currency"],
            "purchasable": plan["purchasable"],
        }

        if isinstance(plan.get("amount_minor"), int) and not isinstance(plan.get("amount_minor"), bool):
            public["amount_minor"] = plan["amount_minor"]

        listed.append(public)

    return listed


def require_purchasable(plan_id):
    """Checkout is refused until a plan is explicitly made purchasable."""
    plan = get_plan(plan_id)

    if plan["purchasable"] is not True:
        raise BillingError(409, "Plan is not currently available for purchase")

    return plan
