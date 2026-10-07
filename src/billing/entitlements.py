"""Subscription access. Organizations.status is not a billing state."""

from datetime import datetime, timezone


KNOWN_STATUSES = {
    "TRIALING",
    "ACTIVE",
    "PAST_DUE",
    "CANCELLED",
    "EXPIRED",
    "GRANDFATHERED",
}
WRITABLE_STATUSES = {"TRIALING", "ACTIVE", "PAST_DUE", "GRANDFATHERED"}


def subscription_status(item):
    """Label for a stored row. A missing row uses the legacy display label.

    That label does not grant operational writes. Writes require a stored
    status in WRITABLE_STATUSES.
    """
    if not item:
        return "GRANDFATHERED"

    return str(item.get("subscription_status") or "").strip().upper()


def is_operational_read_allowed(item):
    if not item:
        return True

    return subscription_status(item) in KNOWN_STATUSES


def is_billing_access_allowed(item):
    """Billing stays reachable so an owner can recover an expired organization."""
    del item
    return True


def cancellation_window_open(item, now):
    """True while an ACTIVE subscription is set to end at the current period."""
    if subscription_status(item) != "ACTIVE" or item.get("cancel_at_period_end") is not True:
        return False

    period_end = _period_end(item.get("current_period_end"))

    if period_end is None or now is None or now.tzinfo is None:
        return False

    return now.astimezone(timezone.utc) < period_end


def is_operational_write_allowed(item, now=None):
    """EXPIRED and CANCELLED block writes. A missing or unknown row does too.

    ACTIVE stays writable until the webhook moves it. An explicit
    GRANDFATHERED row remains writable. A missing row is not that state.
    """
    del now

    if not item:
        return False

    return subscription_status(item) in WRITABLE_STATUSES


def is_normal_access_allowed(item, now=None):
    """Reads and writes. A still-ACTIVE cancellation waits for the webhook."""
    return is_operational_read_allowed(item) and is_operational_write_allowed(item, now)


def _period_end(value):
    if not isinstance(value, str) or not value.strip():
        return None

    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None

    if parsed.tzinfo is None:
        return None

    return parsed.astimezone(timezone.utc)
