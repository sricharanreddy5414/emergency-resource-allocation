"""Razorpay test-mode subscription creation.

Official create call: POST https://api.razorpay.com/v1/subscriptions
Authentication is HTTP Basic with the test key id and key secret.
Required body fields are plan_id and total_count. A customer id is not
required; Razorpay fills customer_id only after the payer authorises.
This endpoint does not document an idempotency key, so ERAP does not
invent one. Duplicate checkout is refused before this call.
"""

import json
import os
import socket
import urllib.error
import urllib.request
from base64 import b64encode

from ..errors import BillingError


SUBSCRIPTIONS_URL = "https://api.razorpay.com/v1/subscriptions"
TEST_SECRET_ID = "erap/billing/razorpay/test"
TIMEOUT_SECONDS = 10

# Commercial Razorpay plans are not created yet. None fails closed.
RAZORPAY_PLAN_LINKS = {
    "MONTHLY": {"razorpay_plan_id": None, "total_count": None},
    "YEARLY": {"razorpay_plan_id": None, "total_count": None},
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


def _discard(error):
    try:
        error.read()
    except Exception:
        return
