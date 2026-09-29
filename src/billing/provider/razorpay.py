"""Razorpay test-mode subscription creation.

Official create call: POST https://api.razorpay.com/v1/subscriptions
Authentication is HTTP Basic with the test key id and key secret.
Required body fields are plan_id and total_count. A customer id is not
required; Razorpay fills customer_id only after the payer authorises.
This endpoint does not document an idempotency key, so ERAP does not
invent one. Duplicate checkout is refused before this call.
"""

import hashlib
import hmac
import json
import os
import socket
import urllib.error
import urllib.parse
import urllib.request
from base64 import b64encode
from datetime import datetime, timezone

from ..errors import BillingError


SUBSCRIPTIONS_URL = "https://api.razorpay.com/v1/subscriptions"
TEST_SECRET_ID = "erap/billing/razorpay/test"
TIMEOUT_SECONDS = 10

# Test plan ids are not secrets. total_count stays unset because Razorpay
# requires a finite billing-cycle count or an end_at, and ERAP does not
# invent a subscription duration. provider_plan fails closed until that
# decision exists, so checkout does not call Razorpay.
RAZORPAY_PLAN_LINKS = {
    "MONTHLY": {"razorpay_plan_id": "plan_ThiWT35Gf1jyio", "total_count": None},
    "YEARLY": {"razorpay_plan_id": "plan_ThiWTXOzBHl2Qb", "total_count": None},
}


def provider_plan(plan_id, links=None):
    chosen = RAZORPAY_PLAN_LINKS if links is None else links
    link = chosen.get(plan_id) or {}
    razorpay_plan_id = link.get("razorpay_plan_id")
    total_count = link.get("total_count")

    if not isinstance(razorpay_plan_id, str) or not razorpay_plan_id.startswith("plan_"):
        raise BillingError(409, "Plan is not currently available for purchase")

    if isinstance(total_count, bool) or not isinstance(total_count, int) or total_count < 1:
        raise BillingError(409, "Plan is not currently available for purchase")

    return razorpay_plan_id, total_count


def signatures_match(raw_body, supplied, secret):
    """Razorpay signs the raw body with HMAC-SHA256 and hex-encodes it."""
    if not isinstance(raw_body, bytes) or not isinstance(supplied, str) or not isinstance(secret, str):
        return False

    supplied = supplied.strip().lower()
    digest = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()

    if len(supplied) != len(digest):
        return False

    return hmac.compare_digest(digest, supplied)


def load_webhook_secret(client=None, secret_id=None):
    """Return only the webhook secret. Callers must not log it."""
    secret_id = secret_id or os.environ.get("RAZORPAY_SECRET_ID") or TEST_SECRET_ID

    if secret_id != TEST_SECRET_ID or client is None:
        raise BillingError(500, "Billing is not configured")

    try:
        response = client.get_secret_value(SecretId=secret_id)
        parsed = json.loads(response.get("SecretString") or "")
    except BillingError:
        raise
    except Exception:
        raise BillingError(500, "Billing is not configured")

    secret = parsed.get("webhook_secret") if isinstance(parsed, dict) else None

    if not isinstance(secret, str) or not secret:
        raise BillingError(500, "Billing is not configured")

    return secret


_EVENT_TARGETS = {
    "subscription.activated": "ACTIVE",
    "subscription.charged": "ACTIVE",
    "payment.failed": "PAST_DUE",
    "subscription.pending": "PAST_DUE",
    "subscription.halted": "PAST_DUE",
    "subscription.cancelled": "CANCELLED",
}

_PAYMENT_STATES = {
    "subscription.activated": "PAID",
    "subscription.charged": "PAID",
    "payment.failed": "FAILED",
    "subscription.pending": "PENDING",
    "subscription.halted": "FAILED",
}


def parse_webhook(payload):
    """Extract the few provider fields ERAP stores. Notes are not a tenant key."""
    if not isinstance(payload, dict):
        raise BillingError(400, "Invalid webhook request")

    event_type = str(payload.get("event") or "").strip()

    if not event_type:
        raise BillingError(400, "Invalid webhook request")

    subscription = _entity(payload, "subscription")
    payment = _entity(payload, "payment")
    subscription_id = subscription.get("id") or payment.get("subscription_id") or ""

    if not isinstance(subscription_id, str):
        subscription_id = ""

    payment_id = payment.get("id") if isinstance(payment.get("id"), str) else ""
    cancel_flag = subscription.get("cancel_at_cycle_end")

    if not isinstance(cancel_flag, bool):
        cancel_flag = None

    return {
        "event_type": event_type,
        "provider_subscription_id": subscription_id.strip(),
        "provider_payment_id": payment_id.strip(),
        "period_start": _unix(subscription.get("current_start")),
        "period_end": _unix(subscription.get("current_end")),
        "cancel_at_period_end": cancel_flag,
        "cancelled_at": _unix(subscription.get("ended_at")),
        "event_time": _unix(payload.get("created_at")),
        "target_status": _EVENT_TARGETS.get(event_type),
        "payment_state": _PAYMENT_STATES.get(event_type, ""),
    }


def _entity(payload, name):
    container = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
    section = container.get(name) if isinstance(container, dict) else None

    if not isinstance(section, dict):
        return {}

    entity = section.get("entity")

    return entity if isinstance(entity, dict) else {}


def _unix(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return ""

    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def load_test_secret(client=None, secret_id=None):
    """Read the test secret. The returned pair must not be logged."""
    secret_id = secret_id or os.environ.get("RAZORPAY_SECRET_ID") or TEST_SECRET_ID

    if secret_id != TEST_SECRET_ID:
        raise BillingError(500, "Billing is not configured")

    if client is None:
        raise BillingError(500, "Billing is not configured")

    try:
        response = client.get_secret_value(SecretId=secret_id)
        parsed = json.loads(response.get("SecretString") or "")
    except BillingError:
        raise
    except Exception:
        raise BillingError(500, "Billing is not configured")

    key_id = parsed.get("key_id") if isinstance(parsed, dict) else None
    key_secret = parsed.get("key_secret") if isinstance(parsed, dict) else None

    if not isinstance(key_id, str) or not isinstance(key_secret, str) or not key_id or not key_secret:
        raise BillingError(500, "Billing is not configured")

    return key_id, key_secret


class RazorpaySubscriptionProvider:
    def __init__(self, secret_loader, urlopen=None, timeout=TIMEOUT_SECONDS):
        self.secret_loader = secret_loader
        self.urlopen = urlopen or urllib.request.urlopen
        self.timeout = timeout

    def create_subscription(self, *, razorpay_plan_id, total_count, organization_id):
        key_id, key_secret = self.secret_loader()

        if not str(key_id).startswith("rzp_test_"):
            raise BillingError(500, "Billing is not configured")

        payload = {
            "plan_id": razorpay_plan_id,
            "total_count": total_count,
            "quantity": 1,
            "customer_notify": False,
            "notes": {"organization_id": organization_id},
        }
        token = b64encode(f"{key_id}:{key_secret}".encode("utf-8")).decode("ascii")
        request = urllib.request.Request(
            SUBSCRIPTIONS_URL,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Basic {token}",
            },
        )

        try:
            with self.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as error:
            _discard(error)
            if getattr(error, "code", 0) >= 500:
                raise BillingError(503, "Billing provider is unavailable")
            raise BillingError(502, "Billing provider rejected the request")
        except (TimeoutError, socket.timeout):
            raise BillingError(503, "Billing provider is unavailable")
        except urllib.error.URLError:
            raise BillingError(503, "Billing provider is unavailable")

        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError, AttributeError):
            raise BillingError(502, "Billing provider rejected the request")

        subscription_id = parsed.get("id") if isinstance(parsed, dict) else None

        if not isinstance(subscription_id, str) or not subscription_id.startswith("sub_"):
            raise BillingError(502, "Billing provider rejected the request")

        return {
            "provider": "razorpay",
            "provider_subscription_id": subscription_id,
            "public_key_id": key_id,
        }

    def cancel_subscription(self, *, provider_subscription_id):
        """Ask Razorpay to cancel at the end of the current cycle.

        Official parameter: cancel_at_cycle_end true. The subscription status
        becomes cancelled only when that cycle ends. This method does not
        decide the ERAP status.
        """
        key_id, key_secret = self.secret_loader()

        if not str(key_id).startswith("rzp_test_"):
            raise BillingError(500, "Billing is not configured")

        if not isinstance(provider_subscription_id, str) or not provider_subscription_id.startswith("sub_"):
            raise BillingError(409, "Cancellation is not available")

        subscription_id = urllib.parse.quote(provider_subscription_id, safe="")
        payload = {"cancel_at_cycle_end": True}
        token = b64encode(f"{key_id}:{key_secret}".encode("utf-8")).decode("ascii")
        request = urllib.request.Request(
            f"{SUBSCRIPTIONS_URL}/{subscription_id}/cancel",
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Basic {token}",
            },
        )
        parsed = _read_json(self.urlopen, request, self.timeout)
        returned = parsed.get("id") if isinstance(parsed, dict) else None

        if returned != provider_subscription_id:
            raise BillingError(502, "Billing provider rejected the request")

        return {"accepted": True}


def _read_json(urlopen, request, timeout):
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as error:
        _discard(error)
        if getattr(error, "code", 0) >= 500:
            raise BillingError(503, "Billing provider is unavailable")
        raise BillingError(502, "Billing provider rejected the request")
    except (TimeoutError, socket.timeout):
        raise BillingError(503, "Billing provider is unavailable")
    except urllib.error.URLError:
        raise BillingError(503, "Billing provider is unavailable")

    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError, AttributeError):
        raise BillingError(502, "Billing provider rejected the request")

    return parsed


def _discard(error):
    try:
        error.read()
    except Exception:
        return
