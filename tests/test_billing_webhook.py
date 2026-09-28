import base64
import hashlib
import hmac
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [
    str(ROOT / "src"),
    str(ROOT / "src" / "shared"),
    str(ROOT / "src" / "organization"),
]

from billing import webhook_handler
from billing.errors import BillingError
from billing.models import new_trial_subscription
from billing.provider.razorpay import load_webhook_secret, signatures_match
from billing.webhook import process_webhook
from test_organization import condition_pairs


SECRET = "test-webhook-secret"
NOW = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
ORG_A = "ORG-A"
ORG_B = "ORG-B"
SUB_A = "sub_TestWebhook0001"
SUB_B = "sub_TestWebhook0002"


def sign(body):
    return hmac.new(SECRET.encode("utf-8"), body, hashlib.sha256).hexdigest()


def subscription(organization_id, provider_subscription_id, status="TRIALING"):
    item = new_trial_subscription(organization_id, datetime(2026, 9, 1, tzinfo=timezone.utc))
    item["provider"] = "razorpay"
    item["provider_subscription_id"] = provider_subscription_id
    item["subscription_status"] = status
    if status == "ACTIVE":
        item["plan_id"] = "MONTHLY"
        item["billing_interval"] = "month"
        item["trial_start"] = ""
        item["trial_end"] = ""
    if status == "PAST_DUE":
        item["plan_id"] = "MONTHLY"
        item["billing_interval"] = "month"
        item["trial_start"] = ""
        item["trial_end"] = ""
    if status == "CANCELLED":
        item["plan_id"] = "MONTHLY"
        item["billing_interval"] = "month"
        item["trial_start"] = ""
        item["trial_end"] = ""
        item["cancelled_at"] = datetime.fromtimestamp(1_700_000_200, timezone.utc).isoformat()
    return item


def payload(event_name, subscription_id, created_at=1_700_000_300, **extra):
    entity = {
        "id": subscription_id,
        "notes": {"organization_id": extra.get("organization_id", "ORG-EVIL")},
        "subscription_status": extra.get("subscription_status", "REFUNDED"),
        "card_number": "4111111111111111",
    }
    if "period" in extra:
        entity["current_start"], entity["current_end"] = extra["period"]
    if "ended_at" in extra:
        entity["ended_at"] = extra["ended_at"]
    if "cancel_at_cycle_end" in extra:
        entity["cancel_at_cycle_end"] = extra["cancel_at_cycle_end"]
    body = {
        "event": event_name,
        "created_at": created_at,
        "payload": {"subscription": {"entity": entity}},
    }
    if extra.get("payment_id"):
        body["payload"]["payment"] = {
            "entity": {"id": extra["payment_id"], "subscription_id": subscription_id}
        }
    return json.dumps(body, separators=(",", ":")).encode("utf-8")


def request(body, event_id="evt_1", signature=None, include_signature=True):
    headers = {}
    if include_signature:
        headers["X-Razorpay-Signature"] = sign(body) if signature is None else signature
    if event_id is not None:
        headers["X-Razorpay-Event-Id"] = event_id
    return {
        "httpMethod": "POST",
        "body": body.decode("utf-8"),
        "headers": headers,
        "queryStringParameters": {"organization_id": "ORG-EVIL"},
    }


class Subscriptions:
    def __init__(self, rows):
        self.rows = {row["organization_id"]: dict(row) for row in rows}
        self.updates = 0
        self.fail = None

    def query(self, **kwargs):
        pairs = condition_pairs(kwargs.get("KeyConditionExpression"))
        matched = list(self.rows.values())
        for name, value in pairs:
            matched = [row for row in matched if row.get(name) == value]
        return {"Items": [dict(row) for row in matched]}

    def get_item(self, Key):
        row = self.rows.get(Key["organization_id"])
        return {"Item": dict(row)} if row else {}

    def update_item(self, **kwargs):
        self.updates += 1
        if self.fail:
            raise self.fail
        row = self.rows[kwargs["Key"]["organization_id"]]
        values = kwargs["ExpressionAttributeValues"]
        if (
            row.get("subscription_status") != values[":expected"]
            or row.get("provider_subscription_id") != values[":sid"]
        ):
            raise ClientError(
                {"Error": {"Code": "ConditionalCheckFailedException", "Message": "conflict"}},
                "UpdateItem",
            )
        expression = kwargs.get("UpdateExpression") or ""
        if " REMOVE " in expression:
            for name in expression.split(" REMOVE ", 1)[1].split(","):
                row.pop(name.strip(), None)
        if ":status" in values:
            row["subscription_status"] = values[":status"]
        row["updated_at"] = values[":updated"]
        if ":plan_id" in values:
            row["plan_id"] = values[":plan_id"]
            row["billing_interval"] = values[":interval"]
            row["pending_plan_id"] = values[":pending_cleared"]
        if ":lifecycle_partition" in values:
            row["lifecycle_partition"] = values[":lifecycle_partition"]
            row["lifecycle_due_at"] = values[":lifecycle_due_at"]
        for field, token in (
            ("current_period_start", ":period_start"),
            ("current_period_end", ":period_end"),
            ("cancelled_at", ":cancelled_at"),
            ("cancel_at_period_end", ":cancel_at_period_end"),
        ):
            if token in values:
                row[field] = values[token]


class Events:
    def __init__(self):
        self.rows = {}
        self.fail_put = None
        self.fail_update = None

    def get_item(self, Key):
        row = self.rows.get(Key["provider_event_id"])
        return {"Item": dict(row)} if row else {}

    def put_item(self, Item, ConditionExpression=None):
        if self.fail_put:
            raise self.fail_put
        if Item["provider_event_id"] in self.rows:
            raise ClientError(
                {"Error": {"Code": "ConditionalCheckFailedException", "Message": "exists"}},
                "PutItem",
            )
        self.rows[Item["provider_event_id"]] = dict(Item)

    def update_item(self, **kwargs):
        if self.fail_update:
            raise self.fail_update
        row = self.rows[kwargs["Key"]["provider_event_id"]]
        values = kwargs["ExpressionAttributeValues"]
        if row["processing_status"] != values[":received"]:
            raise ClientError(
                {"Error": {"Code": "ConditionalCheckFailedException", "Message": "finished"}},
                "UpdateItem",
            )
        row["processing_status"] = values[":status"]
        row["processed_at"] = values[":processed"]
        if values.get(":organization"):
            row["organization_id"] = values[":organization"]
        row["payment_state"] = values[":payment"]
        row["provider_payment_id"] = values[":payment_id"]


def deliver(body, rows, event_id="evt_1", **kwargs):
    subscriptions = Subscriptions(rows)
    events = Events()
    status, response = process_webhook(
        request(body, event_id, **kwargs),
        subscriptions,
        events,
        SECRET,
        now=NOW,
    )
    return status, response, subscriptions, events


def test_valid_signature_activates_trial(capsys):
    body = payload(
        "subscription.activated",
        SUB_A,
        period=(1_700_000_300, 1_700_259_100),
        payment_id="pay_Test0001",
    )
    status, response, subscriptions, events = deliver(body, [subscription(ORG_A, SUB_A)])

    assert status == 200
    assert response == {"message": "OK"}
    row = subscriptions.rows[ORG_A]
    assert row["subscription_status"] == "ACTIVE"
    assert row["current_period_start"].startswith("2023-11-14")
    assert row["trial_start"].startswith("2026-09-01")
    stored = events.rows["evt_1"]
    assert stored["processing_status"] == "PROCESSED"
    assert stored["organization_id"] == ORG_A
    assert stored["payment_state"] == "PAID"
    assert stored["provider_payment_id"] == "pay_Test0001"
    assert "card_number" not in stored
    logged = capsys.readouterr().out
    assert SECRET not in logged
    assert SECRET not in json.dumps(response)
    assert "4111111111111111" not in logged
    assert sign(body) not in logged


def test_invalid_and_missing_signatures_are_rejected():
    body = payload("subscription.activated", SUB_A)
    rows = [subscription(ORG_A, SUB_A)]
    status, response, subscriptions, events = deliver(body, rows, signature="ab" * 32)
    assert status == 401
    assert subscriptions.rows[ORG_A]["subscription_status"] == "TRIALING"
    assert events.rows == {}
    assert SECRET not in json.dumps(response)
    assert sign(body) not in json.dumps(response)

    status, _, subscriptions, events = deliver(body, rows, include_signature=False)
    assert status == 401
    assert events.rows == {}
    assert subscriptions.rows[ORG_A]["subscription_status"] == "TRIALING"


def test_altered_body_and_signature_are_rejected():
    body = payload("subscription.activated", SUB_A)
    altered = body[:-1] + b" "
    status, _, subscriptions, events = deliver(altered, [subscription(ORG_A, SUB_A)], signature=sign(body))
    assert status == 401
    assert events.rows == {}
    assert subscriptions.updates == 0

    flipped = sign(body)[:-1] + ("0" if sign(body)[-1] != "0" else "1")
    status, _, _, events = deliver(body, [subscription(ORG_A, SUB_A)], signature=flipped)
    assert status == 401
    assert events.rows == {}


def test_constant_time_comparison_is_used(monkeypatch):
    seen = {}
    real = hmac.compare_digest

    def wrapped(left, right):
        seen["used"] = True
        return real(left, right)

    monkeypatch.setattr("billing.provider.razorpay.hmac.compare_digest", wrapped)
    body = b"{}"
    assert signatures_match(body, sign(body).upper(), SECRET) is True
    assert seen["used"] is True


def test_missing_event_id_is_rejected_without_a_row():
    body = payload("subscription.activated", SUB_A)
    status, _, subscriptions, events = deliver(body, [subscription(ORG_A, SUB_A)], event_id=None)
    assert status == 400
    assert events.rows == {}
    assert subscriptions.updates == 0


def test_duplicate_after_processed_does_not_apply_again():
    body = payload("subscription.charged", SUB_A, payment_id="pay_Test0002")
    subscriptions = Subscriptions([subscription(ORG_A, SUB_A)])
    events = Events()
    first, _, = _send(body, subscriptions, events)
    processed_at = events.rows["evt_1"]["processed_at"]
    updated_at = subscriptions.rows[ORG_A]["updated_at"]
    second, response = _send(body, subscriptions, events)

    assert first == 200 and second == 200
    assert response == {"message": "OK"}
    assert subscriptions.updates == 1
    assert len(events.rows) == 1
    assert events.rows["evt_1"]["processed_at"] == processed_at
    assert subscriptions.rows[ORG_A]["updated_at"] == updated_at
    assert subscriptions.rows[ORG_A]["subscription_status"] == "ACTIVE"


def test_duplicate_after_ignored_does_not_create_another_event():
    body = payload("invoice.paid", SUB_A)
    subscriptions = Subscriptions([subscription(ORG_A, SUB_A)])
    events = Events()
    assert _send(body, subscriptions, events)[0] == 200
    processed_at = events.rows["evt_1"]["processed_at"]
    assert _send(body, subscriptions, events)[0] == 200
    assert events.rows["evt_1"]["processing_status"] == "IGNORED"
    assert events.rows["evt_1"]["processed_at"] == processed_at
    assert subscriptions.updates == 0
    assert len(events.rows) == 1


def test_unknown_subscription_is_ignored():
    body = payload("subscription.activated", "sub_Unknown000001")
    status, _, subscriptions, events = deliver(body, [subscription(ORG_A, SUB_A)])
    assert status == 200
    assert subscriptions.updates == 0
    assert ORG_B not in subscriptions.rows
    assert events.rows["evt_1"]["processing_status"] == "IGNORED"
    assert not events.rows["evt_1"].get("organization_id")


def test_malformed_signed_body_is_rejected():
    body = b"{"
    status, _, subscriptions, events = deliver(body, [subscription(ORG_A, SUB_A)])
    assert status == 400
    assert events.rows == {}
    assert subscriptions.updates == 0


def test_conflicting_organization_id_is_ignored():
    body = payload("subscription.charged", SUB_A, organization_id=ORG_B)
    rows = [subscription(ORG_A, SUB_A), subscription(ORG_B, SUB_B)]
    status, _, subscriptions, events = deliver(body, rows)
    assert status == 200
    assert subscriptions.rows[ORG_A]["subscription_status"] == "ACTIVE"
    assert subscriptions.rows[ORG_B]["subscription_status"] == "TRIALING"
    assert events.rows["evt_1"]["organization_id"] == ORG_A


def test_payment_failure_pending_and_halted_move_to_past_due():
    cases = (
        ("payment.failed", "FAILED"),
        ("subscription.pending", "PENDING"),
        ("subscription.halted", "FAILED"),
    )
    for event_name, payment_state in cases:
        body = payload(event_name, SUB_A, payment_id="pay_Fail0001")
        _, _, subscriptions, events = deliver(
            body,
            [subscription(ORG_A, SUB_A, "ACTIVE")],
            event_id="evt_" + event_name,
        )
        assert subscriptions.rows[ORG_A]["subscription_status"] == "PAST_DUE"
        assert events.rows["evt_" + event_name]["payment_state"] == payment_state


def test_past_due_recovers_to_active():
    body = payload("subscription.charged", SUB_A)
    _, _, subscriptions, _ = deliver(body, [subscription(ORG_A, SUB_A, "PAST_DUE")])
    assert subscriptions.rows[ORG_A]["subscription_status"] == "ACTIVE"


def test_active_cancellation_sets_provider_time():
    ended = 1_700_000_400
    body = payload("subscription.cancelled", SUB_A, created_at=ended, ended_at=ended, cancel_at_cycle_end=True)
    _, _, subscriptions, events = deliver(body, [subscription(ORG_A, SUB_A, "ACTIVE")])
    row = subscriptions.rows[ORG_A]
    assert row["subscription_status"] == "CANCELLED"
    assert row["cancelled_at"] == datetime.fromtimestamp(ended, timezone.utc).isoformat()
    assert row["cancel_at_period_end"] is True
    assert events.rows["evt_1"]["payment_state"] == ""


def test_past_due_cancellation_is_ignored():
    body = payload("subscription.cancelled", SUB_A)
    _, _, subscriptions, events = deliver(body, [subscription(ORG_A, SUB_A, "PAST_DUE")])
    assert subscriptions.rows[ORG_A]["subscription_status"] == "PAST_DUE"
    assert subscriptions.rows[ORG_A]["cancelled_at"] == ""
    assert events.rows["evt_1"]["processing_status"] == "IGNORED"


def test_older_charge_does_not_reactivate_a_cancellation():
    cancel = payload("subscription.cancelled", SUB_A, created_at=1_700_000_200, ended_at=1_700_000_200)
    charge = payload("subscription.charged", SUB_A, created_at=1_700_000_100)
    subscriptions = Subscriptions([subscription(ORG_A, SUB_A, "ACTIVE")])
    events = Events()
    assert _send(cancel, subscriptions, events, "evt_cancel")[0] == 200
    assert _send(charge, subscriptions, events, "evt_old")[0] == 200
    assert subscriptions.rows[ORG_A]["subscription_status"] == "CANCELLED"
    assert events.rows["evt_old"]["processing_status"] == "IGNORED"


def test_activation_promotes_only_a_pending_commercial_plan():
    current = subscription(ORG_A, SUB_A)
    current["pending_plan_id"] = "MONTHLY"
    body = payload("subscription.activated", SUB_A, period=(1_700_000_300, 1_700_259_100))
    _, _, subscriptions, events = deliver(body, [current])
    row = subscriptions.rows[ORG_A]

    assert row["subscription_status"] == "ACTIVE"
    assert row["plan_id"] == "MONTHLY"
    assert row["billing_interval"] == "month"
    assert row["pending_plan_id"] == ""
    assert events.rows["evt_1"]["processing_status"] == "PROCESSED"


def test_activation_without_a_pending_plan_does_not_invent_one():
    current = subscription(ORG_A, SUB_A)
    body = payload("subscription.activated", SUB_A)
    _, _, subscriptions, _ = deliver(body, [current])

    assert subscriptions.rows[ORG_A]["plan_id"] == "FREE_TRIAL"
    assert subscriptions.rows[ORG_A]["billing_interval"] == "none"


def test_grandfathered_activation_does_not_invent_a_commercial_plan():
    current = subscription(ORG_A, SUB_A, "GRANDFATHERED")
    current["plan_id"] = "GRANDFATHERED"
    current["billing_interval"] = "none"
    current["pending_plan_id"] = ""
    body = payload("subscription.charged", SUB_A, created_at=1_700_000_300)
    _, _, subscriptions, _ = deliver(body, [current])
    row = subscriptions.rows[ORG_A]

    assert row["subscription_status"] == "ACTIVE"
    assert row["plan_id"] == "GRANDFATHERED"
    assert row["billing_interval"] == "none"


def test_failed_payment_does_not_activate_a_pending_plan():
    current = subscription(ORG_A, SUB_A)
    current["pending_plan_id"] = "YEARLY"
    body = payload("payment.failed", SUB_A, payment_id="pay_Fail0001")
    _, _, subscriptions, events = deliver(body, [current])
    row = subscriptions.rows[ORG_A]

    assert row["subscription_status"] == "TRIALING"
    assert row["plan_id"] == "FREE_TRIAL"
    assert row["pending_plan_id"] == "YEARLY"
    assert events.rows["evt_1"]["processing_status"] == "IGNORED"


def test_past_due_charge_recovers_without_replacing_the_plan():
    current = subscription(ORG_A, SUB_A, "PAST_DUE")
    current["plan_id"] = "YEARLY"
    current["billing_interval"] = "year"
    body = payload("subscription.charged", SUB_A, period=(1_700_259_100, 1_732_795_100))
    _, _, subscriptions, _ = deliver(body, [current])
    row = subscriptions.rows[ORG_A]

    assert row["subscription_status"] == "ACTIVE"
    assert row["plan_id"] == "YEARLY"
    assert row["billing_interval"] == "year"


def test_renewal_while_active_updates_the_period_without_clearing_cancellation():
    body = payload(
        "subscription.charged",
        SUB_A,
        period=(1_700_259_100, 1_700_518_500),
        created_at=1_700_259_100,
    )
    current = subscription(ORG_A, SUB_A, "ACTIVE")
    current["cancel_at_period_end"] = True
    current["current_period_end"] = "2023-11-14T22:13:20+00:00"
    _, _, subscriptions, events = deliver(body, [current], event_id="evt_renew")
    row = subscriptions.rows[ORG_A]

    assert row["subscription_status"] == "ACTIVE"
    assert row["plan_id"] == "MONTHLY"
    assert row["billing_interval"] == "month"
    assert row["cancel_at_period_end"] is True
    assert row["current_period_end"] == datetime.fromtimestamp(1_700_518_500, timezone.utc).isoformat()
    assert events.rows["evt_renew"]["processing_status"] == "PROCESSED"


def test_reactivation_clears_a_scheduled_cancellation():
    current = subscription(ORG_A, SUB_A, "CANCELLED")
    current["cancel_at_period_end"] = True
    current["cancelled_at"] = datetime.fromtimestamp(1_700_000_100, timezone.utc).isoformat()
    body = payload("subscription.charged", SUB_A, created_at=1_700_000_300)
    _, _, subscriptions, events = deliver(body, [current])
    row = subscriptions.rows[ORG_A]

    assert row["subscription_status"] == "ACTIVE"
    assert row["cancel_at_period_end"] is False
    assert events.rows["evt_1"]["processing_status"] == "PROCESSED"


def test_newer_charge_can_reactivate_after_cancellation():
    cancel = payload("subscription.cancelled", SUB_A, created_at=1_700_000_200, ended_at=1_700_000_200)
    charge = payload("subscription.charged", SUB_A, created_at=1_700_000_300)
    subscriptions = Subscriptions([subscription(ORG_A, SUB_A, "ACTIVE")])
    events = Events()
    _send(cancel, subscriptions, events, "evt_cancel")
    _send(charge, subscriptions, events, "evt_new")
    assert subscriptions.rows[ORG_A]["subscription_status"] == "ACTIVE"


def test_database_failure_returns_500_and_retry_converges():
    body = payload("subscription.activated", SUB_A, period=(1_700_000_300, 1_700_259_100))
    subscriptions = Subscriptions([subscription(ORG_A, SUB_A)])
    events = Events()
    subscriptions.fail = ClientError(
        {"Error": {"Code": "ProvisionedThroughputExceededException", "Message": "busy"}},
        "UpdateItem",
    )
    status, _ = _send(body, subscriptions, events)
    assert status == 500
    assert subscriptions.rows[ORG_A]["subscription_status"] == "TRIALING"
    assert events.rows["evt_1"]["processing_status"] == "RECEIVED"

    subscriptions.fail = None
    events.fail_update = ClientError(
        {"Error": {"Code": "ProvisionedThroughputExceededException", "Message": "busy"}},
        "UpdateItem",
    )
    status, _ = _send(body, subscriptions, events)
    assert status == 500
    assert subscriptions.rows[ORG_A]["subscription_status"] == "ACTIVE"
    assert events.rows["evt_1"]["processing_status"] == "RECEIVED"
    updates = subscriptions.updates

    events.fail_update = None
    status, _ = _send(body, subscriptions, events)
    assert status == 200
    assert subscriptions.updates == updates
    assert events.rows["evt_1"]["processing_status"] == "PROCESSED"
    assert subscriptions.rows[ORG_A]["current_period_start"].startswith("2023-11-14")


def test_base64_body_uses_decoded_bytes():
    body = payload("subscription.activated", SUB_A)
    event = request(body)
    event["body"] = base64.b64encode(body).decode("ascii")
    event["isBase64Encoded"] = True
    subscriptions = Subscriptions([subscription(ORG_A, SUB_A)])
    status, _ = process_webhook(
        event,
        subscriptions,
        Events(),
        SECRET,
        now=NOW,
    )
    assert status == 200
    assert subscriptions.rows[ORG_A]["subscription_status"] == "ACTIVE"


def test_webhook_secret_loader_returns_only_the_webhook_secret():
    class Client:
        def get_secret_value(self, SecretId):
            return {
                "SecretString": json.dumps(
                    {
                        "key_id": "rzp_test_public",
                        "key_secret": "key-secret-value",
                        "webhook_secret": SECRET,
                    }
                )
            }

    assert load_webhook_secret(Client()) == SECRET
    with pytest.raises(BillingError, match="Billing is not configured") as error:
        load_webhook_secret(Client(), secret_id="erap/billing/razorpay/live")
    assert "key-secret-value" not in str(error.value)


def test_handler_does_not_use_cognito(monkeypatch):
    source = (ROOT / "src" / "billing" / "webhook_handler.py").read_text(encoding="utf-8")
    assert "authorize(" not in source
    subscriptions = Subscriptions([subscription(ORG_A, SUB_A)])
    events = Events()
    monkeypatch.setattr(webhook_handler, "webhook_secret", lambda: SECRET)
    monkeypatch.setattr(webhook_handler, "subscriptions_table", lambda: subscriptions)
    monkeypatch.setattr(webhook_handler, "events_table", lambda: events)
    response = webhook_handler.lambda_handler({"httpMethod": "GET"}, None)
    assert response["statusCode"] == 405
    body = payload("subscription.activated", SUB_A)
    response = webhook_handler.lambda_handler(request(body), None)
    assert response["statusCode"] == 200
    assert SECRET not in response["body"]
    assert subscriptions.rows[ORG_A]["subscription_status"] == "ACTIVE"


def test_route_specs_keep_checkout_authenticated_and_webhook_public():
    checkout = json.loads((ROOT / "infra" / "billing-checkout.json").read_text(encoding="utf-8"))
    tables = json.loads((ROOT / "infra" / "billing-tables.json").read_text(encoding="utf-8"))
    assert checkout["applied"] is False
    assert checkout["route"]["authorization"] == "COGNITO_USER_POOLS"
    assert checkout["webhook"]["authorization"] == "NONE"
    assert checkout["webhook"]["path"] == "/billing/webhook"
    actions = json.dumps(checkout["webhook_iam"])
    assert "dynamodb:DeleteItem" not in actions
    assert "dynamodb:Scan" not in actions
    assert "EmergencyRequests" not in actions
    assert tables["applied"] is False
    index = tables["tables"][0]["GlobalSecondaryIndexes"][0]
    assert index["IndexName"] == "ProviderSubscriptionIndex"


def _send(body, subscriptions, events, event_id="evt_1"):
    return process_webhook(request(body, event_id), subscriptions, events, SECRET, now=NOW)
