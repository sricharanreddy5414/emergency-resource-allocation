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
PRODUCTION_SECRET_ID = "erap/billing/razorpay/production"
TIMEOUT_SECONDS = 10

# Razorpay rejects authorization when expire_at is more than 40 years away.
# 468 monthly cycles and 39 yearly cycles stay inside that limit.
# A customer can cancel earlier. This is not unlimited.
RAZORPAY_PLAN_LINKS = {
    "MONTHLY": {"razorpay_plan_id": "plan_ThiWT35Gf1jyio", "total_count": 468},
    "YEARLY": {"razorpay_plan_id": "plan_ThiWTXOzBHl2Qb", "total_count": 39},
}
TEST_PLAN_IDS = frozenset(link["razorpay_plan_id"] for link in RAZORPAY_PLAN_LINKS.values())


def billing_mode(mode=None):
    """Backend chooses test or production. A client value is never accepted."""
    chosen = mode if mode is not None else os.environ.get("ERAP_BILLING_MODE") or "test"
    chosen = str(chosen).strip().lower()

    if chosen not in {"test", "production"}:
        raise BillingError(500, "Billing is not configured")

    return chosen


def secret_id_for_mode(mode=None):
    return TEST_SECRET_ID if billing_mode(mode) == "test" else PRODUCTION_SECRET_ID


def key_prefix_for_mode(mode=None):
    return "rzp_test_" if billing_mode(mode) == "test" else "rzp_live_"


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


def load_billing_config(client=None, mode=None, secret_id=None, require_webhook=False):
    """Read one mode's secret. The returned mapping must not be logged."""
    mode = billing_mode(mode)
    expected = secret_id_for_mode(mode)
    secret_id = secret_id or os.environ.get("RAZORPAY_SECRET_ID") or expected

    if secret_id != expected or client is None:
        raise BillingError(500, "Billing is not configured")

    try:
        response = client.get_secret_value(SecretId=secret_id)
        parsed = json.loads(response.get("SecretString") or "")
    except BillingError:
        raise
    except Exception:
        raise BillingError(500, "Billing is not configured")

    if not isinstance(parsed, dict):
        raise BillingError(500, "Billing is not configured")

    key_id = parsed.get("key_id")
    key_secret = parsed.get("key_secret")
    webhook_secret = parsed.get("webhook_secret")
    prefix = key_prefix_for_mode(mode)

    if not isinstance(key_id, str) or not key_id.startswith(prefix):
        raise BillingError(500, "Billing is not configured")

    if not isinstance(key_secret, str) or not key_secret:
        raise BillingError(500, "Billing is not configured")

    if not isinstance(webhook_secret, str):
        webhook_secret = ""

    if (require_webhook or mode == "production") and not webhook_secret:
        raise BillingError(500, "Billing is not configured")

    links = RAZORPAY_PLAN_LINKS if mode == "test" else _production_links(parsed)

    return {
        "mode": mode,
        "secret_id": expected,
        "key_id": key_id,
        "key_secret": key_secret,
        "webhook_secret": webhook_secret,
        "links": links,
    }


def checkout_links(client=None, mode=None, client_factory=None):
    """Test checkout keeps the in-code test plans. Production plans come from its secret."""
    mode = billing_mode(mode)

    if mode == "test":
        return None

    if client is None and client_factory is not None:
        client = client_factory()

    if client is None:
        raise BillingError(500, "Billing is not configured")

    return load_billing_config(client, mode)["links"]


def checkout_binding(client_factory, provider_factory, mode=None):
    """Return the provider factory and plan map for checkout.

    Test mode returns the injected factory immediately. It does not call
    client_factory, so a unit test never opens Secrets Manager or Razorpay.
    Production loads only the production secret and never substitutes test plans.
    """
    if billing_mode(mode) == "test":
        return provider_factory, None

    if client_factory is None:
        raise BillingError(500, "Billing is not configured")

    provider, links = open_provider(client_factory(), mode)
    return (lambda: provider), links


def open_provider(client, mode=None):
    """Return the provider and the plan map for the backend-selected mode."""
    config = load_billing_config(client, mode)
    provider = RazorpaySubscriptionProvider(
        lambda: (config["key_id"], config["key_secret"]),
        mode=config["mode"],
    )
    return provider, config["links"]


def load_webhook_secret(client=None, secret_id=None, mode=None):
    """Return only the webhook secret. Callers must not log it."""
    secret = load_billing_config(client, mode, secret_id, require_webhook=True)["webhook_secret"]

    if not secret:
        raise BillingError(500, "Billing is not configured")

    return secret


# subscription.completed is unmapped. Razorpay sends it when every invoice
# in total_count has been generated. ACTIVE cannot become EXPIRED, and
# treating completion as CANCELLED would end the last paid period early.
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


def _production_links(parsed):
    monthly = parsed.get("monthly_plan_id")
    yearly = parsed.get("yearly_plan_id")
    links = {
        "MONTHLY": {"razorpay_plan_id": monthly, "total_count": 468},
        "YEARLY": {"razorpay_plan_id": yearly, "total_count": 39},
    }

    for link in links.values():
        plan_id = link["razorpay_plan_id"]

        if not isinstance(plan_id, str) or not plan_id.startswith("plan_") or plan_id in TEST_PLAN_IDS:
            raise BillingError(500, "Billing is not configured")

    if links["MONTHLY"]["razorpay_plan_id"] == links["YEARLY"]["razorpay_plan_id"]:
        raise BillingError(500, "Billing is not configured")

    return links


def load_test_secret(client=None, secret_id=None):
    """Read the test secret. The returned pair must not be logged."""
    config = load_billing_config(client, "test", secret_id)
    return config["key_id"], config["key_secret"]


class RazorpaySubscriptionProvider:
    def __init__(self, secret_loader, urlopen=None, timeout=TIMEOUT_SECONDS, mode="test"):
        self.secret_loader = secret_loader
        self.urlopen = urlopen or urllib.request.urlopen
        self.timeout = timeout
        self.mode = billing_mode(mode)

    def _require_mode_key(self, key_id):
        if not str(key_id).startswith(key_prefix_for_mode(self.mode)):
            raise BillingError(500, "Billing is not configured")

    def create_subscription(self, *, razorpay_plan_id, total_count, organization_id):
        key_id, key_secret = self.secret_loader()
        self._require_mode_key(key_id)

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

        result = {
            "provider": "razorpay",
            "provider_subscription_id": subscription_id,
            "public_key_id": key_id,
        }
        hosted = _hosted_checkout_url(parsed.get("short_url") if isinstance(parsed, dict) else None)

        if hosted:
            result["hosted_checkout_url"] = hosted

        return result

    def subscription_status(self, provider_subscription_id):
        """Return only the provider status. The response body is not stored."""
        key_id, key_secret = self.secret_loader()
        self._require_mode_key(key_id)

        if not isinstance(provider_subscription_id, str) or not provider_subscription_id.startswith("sub_"):
            raise BillingError(409, "A checkout is already in progress")

        subscription_id = urllib.parse.quote(provider_subscription_id, safe="")
        token = b64encode(f"{key_id}:{key_secret}".encode("utf-8")).decode("ascii")
        request = urllib.request.Request(
            f"{SUBSCRIPTIONS_URL}/{subscription_id}",
            method="GET",
            headers={"Authorization": f"Basic {token}"},
        )
        parsed = _read_json(self.urlopen, request, self.timeout)
        status = parsed.get("status") if isinstance(parsed, dict) else None

        if not isinstance(status, str) or not status.strip():
            raise BillingError(502, "Billing provider rejected the request")

        return status.strip()

    def cancel_subscription(self, *, provider_subscription_id):
        """Ask Razorpay to cancel at the end of the current cycle.

        Official parameter: cancel_at_cycle_end true. The subscription status
        becomes cancelled only when that cycle ends. This method does not
        decide the ERAP status.
        """
        key_id, key_secret = self.secret_loader()
        self._require_mode_key(key_id)

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


def _hosted_checkout_url(value):
    """Keep only an https payment page. Card data and secrets never qualify."""
    if not isinstance(value, str):
        return ""

    parsed = urllib.parse.urlparse(value.strip())

    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        return ""

    return parsed.geturl()


def _discard(error):
    try:
        error.read()
    except Exception:
        return
