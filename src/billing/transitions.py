"""Legal subscription status changes. Callers cannot set an arbitrary status."""

from .errors import BillingError


SUBSCRIPTION_STATUSES = {
    "TRIALING",
    "ACTIVE",
    "PAST_DUE",
    "CANCELLED",
    "EXPIRED",
    "GRANDFATHERED",
}

# None means no subscription row yet.
TRANSITIONS = {
    None: {"TRIALING", "GRANDFATHERED"},
    "TRIALING": {"ACTIVE", "EXPIRED"},
    "ACTIVE": {"PAST_DUE", "CANCELLED"},
    "PAST_DUE": {"ACTIVE", "EXPIRED"},
    "CANCELLED": {"ACTIVE", "EXPIRED"},
    "EXPIRED": {"ACTIVE"},
    "GRANDFATHERED": {"ACTIVE"},
}


def _current(status):
    if status is None:
        return None

    text = str(status).strip().upper()

    if text in {"", "NONE"}:
        return None

    if text not in SUBSCRIPTION_STATUSES:
        raise BillingError(400, "Subscription status is invalid")

    return text


def _target(status):
    text = str(status or "").strip().upper()

    if text not in SUBSCRIPTION_STATUSES:
        raise BillingError(400, "Subscription status is invalid")

    return text


def change_subscription_status(current, target):
    """Return the next status, or raise when the change is not allowed."""
    source = _current(current)
    destination = _target(target)

    if destination not in TRANSITIONS.get(source, set()):
        raise BillingError(409, "Subscription status cannot change that way")

    return destination
