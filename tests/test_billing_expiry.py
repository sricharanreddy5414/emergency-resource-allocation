"""Local lifecycle completion. The job queries due dates and updates conditionally."""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [
    str(ROOT / "src"),
    str(ROOT / "src" / "shared"),
    str(ROOT / "src" / "organization"),
    str(ROOT / "scripts"),
]

from billing.cancel import _mark_period_end
from billing.expiry import run_expiry
from billing.expiry_handler import lambda_handler
from billing.models import lifecycle_shard, new_trial_subscription
from lambda_manifest import BILLING_PACKAGES, PACKAGES


UTC = timezone.utc
NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
ORG = "ORG-A"


def comparisons(expression):
    if expression is None or not hasattr(expression, "get_expression"):
        return []

    data = expression.get_expression()
    values = data.get("values", ())
    operator = data.get("operator")

    if operator in {"=", "<="} and len(values) == 2:
        return [(operator, getattr(values[0], "name", None), values[1])]

    found = []

    for value in values:
        found.extend(comparisons(value))

    return found


class Table:
    def __init__(self, rows):
        self.rows = {row["organization_id"]: dict(row) for row in rows}
        self.updates = 0
        self.stale = None

    def query(self, **kwargs):
        matched = list(self.stale) if self.stale is not None else list(self.rows.values())

        for operator, name, value in comparisons(kwargs.get("KeyConditionExpression")):
            if operator == "=":
                matched = [row for row in matched if row.get(name) == value]
            elif operator == "<=":
                matched = [row for row in matched if (row.get(name) or "") <= value]

        pages = [dict(row) for row in matched]
        start = kwargs.get("ExclusiveStartKey")

        if start:
            pages = pages[1:]

        if len(pages) > 1 and not start:
            first = pages[0]
            return {
                "Items": [first],
                "LastEvaluatedKey": {"organization_id": first["organization_id"]},
            }

        return {"Items": pages}

    def update_item(self, **kwargs):
        self.updates += 1
        row = self.rows[kwargs["Key"]["organization_id"]]
        values = kwargs["ExpressionAttributeValues"]
        expression = kwargs["UpdateExpression"]

        if row.get("subscription_status") != values.get(":expected"):
            raise ClientError(
                {"Error": {"Code": "ConditionalCheckFailedException", "Message": "status"}},
                "UpdateItem",
            )

        if ":trial_end" in values and row.get("trial_end") != values[":trial_end"]:
            raise ClientError(
                {"Error": {"Code": "ConditionalCheckFailedException", "Message": "trial"}},
                "UpdateItem",
            )

        if ":period_end" in values and row.get("current_period_end") != values[":period_end"]:
            raise ClientError(
                {"Error": {"Code": "ConditionalCheckFailedException", "Message": "period"}},
                "UpdateItem",
            )

        if ":due" in values and row.get("lifecycle_due_at") != values[":due"]:
            raise ClientError(
                {"Error": {"Code": "ConditionalCheckFailedException", "Message": "due"}},
                "UpdateItem",
            )

        row["subscription_status"] = values[":status"]
        row["updated_at"] = values[":updated"]

        if ":cancelled_at" in values:
            row["cancelled_at"] = values[":cancelled_at"]

        if "REMOVE " in expression:
            row.pop("lifecycle_partition", None)
            row.pop("lifecycle_due_at", None)

    def scan(self, **kwargs):
        raise AssertionError("scan")


def _part(kind, organization_id):
    return kind + "#" + f"{lifecycle_shard(organization_id):02d}"


def trial(trial_end, status="TRIALING", organization_id=ORG):
    return {
        "organization_id": organization_id,
        "subscription_status": status,
        "plan_id": "FREE_TRIAL",
        "trial_end": trial_end,
        "cancel_at_period_end": False,
        "current_period_end": "",
        "lifecycle_partition": _part("TRIAL", organization_id),
        "lifecycle_due_at": trial_end,
        "webhook_secret": "must-not-log",
    }


def scheduled(period_end, status="ACTIVE", cancel=True, organization_id=ORG):
    return {
        "organization_id": organization_id,
        "subscription_status": status,
        "plan_id": "MONTHLY",
        "billing_interval": "month",
        "trial_end": "",
        "cancel_at_period_end": cancel,
        "current_period_end": period_end,
        "lifecycle_partition": _part("CANCEL", organization_id),
        "lifecycle_due_at": period_end,
        "webhook_secret": "must-not-log",
    }


def test_new_trial_is_indexed_on_its_end_date():
    item = new_trial_subscription(ORG, datetime(2026, 9, 1, tzinfo=UTC))

    assert item["trial_end"].startswith("2026-09-16")
    assert item["lifecycle_partition"] == _part("TRIAL", ORG)
    assert item["lifecycle_due_at"] == item["trial_end"]
    assert item["pending_plan_id"] == ""


def test_trial_before_the_end_is_unchanged():
    end = (NOW + timedelta(hours=2)).isoformat()
    table = Table([trial(end)])
    result = run_expiry(table, NOW)

    assert result == {"expired": 0, "cancelled": 0, "skipped": 0}
    assert table.rows[ORG]["subscription_status"] == "TRIALING"
    assert table.updates == 0


@pytest.mark.parametrize("end", [NOW.isoformat(), (NOW - timedelta(minutes=1)).isoformat()])
def test_due_trial_expires(end):
    table = Table([trial(end)])
    result = run_expiry(table, NOW)
    row = table.rows[ORG]

    assert result["expired"] == 1
    assert row["subscription_status"] == "EXPIRED"
    assert "lifecycle_partition" not in row
    assert row["plan_id"] == "FREE_TRIAL"


def test_already_expired_trial_is_unchanged(capsys):
    end = (NOW - timedelta(days=1)).isoformat()
    table = Table([trial(end, status="EXPIRED")])
    result = run_expiry(table, NOW)

    assert result["skipped"] == 1
    assert result["expired"] == 0
    assert table.updates == 0
    assert table.rows[ORG]["subscription_status"] == "EXPIRED"
    assert "must-not-log" not in capsys.readouterr().out


def test_activation_wins_the_race_with_trial_expiry():
    end = (NOW - timedelta(minutes=5)).isoformat()
    original = trial(end)
    table = Table([original])
    table.stale = [dict(original)]
    table.rows[ORG]["subscription_status"] = "ACTIVE"
    table.rows[ORG]["plan_id"] = "MONTHLY"
    result = run_expiry(table, NOW)

    assert result["expired"] == 0
    assert result["skipped"] == 1
    assert table.updates == 1
    assert table.rows[ORG]["subscription_status"] == "ACTIVE"
    assert table.rows[ORG]["plan_id"] == "MONTHLY"


def test_scheduled_cancellation_before_the_period_end_is_unchanged():
    end = (NOW + timedelta(days=1)).isoformat()
    table = Table([scheduled(end)])
    result = run_expiry(table, NOW)

    assert result["cancelled"] == 0
    assert table.rows[ORG]["subscription_status"] == "ACTIVE"
    assert table.updates == 0


@pytest.mark.parametrize("end", [NOW.isoformat(), (NOW - timedelta(days=2)).isoformat()])
def test_scheduled_cancellation_completes(end):
    table = Table([scheduled(end)])
    result = run_expiry(table, NOW)
    row = table.rows[ORG]

    assert result["cancelled"] == 1
    assert row["subscription_status"] == "CANCELLED"
    assert row["cancelled_at"] == NOW.isoformat()
    assert row["plan_id"] == "MONTHLY"
    assert "lifecycle_due_at" not in row


def test_active_without_scheduled_cancellation_is_unchanged():
    end = (NOW - timedelta(days=1)).isoformat()
    row = scheduled(end, cancel=False)
    row.pop("lifecycle_partition")
    row.pop("lifecycle_due_at")
    table = Table([row])
    result = run_expiry(table, NOW)

    assert result == {"expired": 0, "cancelled": 0, "skipped": 0}
    assert table.rows[ORG]["subscription_status"] == "ACTIVE"


def test_already_cancelled_row_is_unchanged():
    end = (NOW - timedelta(days=1)).isoformat()
    table = Table([scheduled(end, status="CANCELLED")])
    result = run_expiry(table, NOW)

    assert result["cancelled"] == 0
    assert result["skipped"] == 1
    assert table.updates == 0


def test_renewed_period_prevents_local_cancellation():
    old_end = (NOW - timedelta(days=1)).isoformat()
    new_end = (NOW + timedelta(days=30)).isoformat()
    original = scheduled(old_end)
    table = Table([original])
    table.stale = [dict(original)]
    table.rows[ORG]["current_period_end"] = new_end
    table.rows[ORG]["lifecycle_due_at"] = new_end
    result = run_expiry(table, NOW)

    assert result["cancelled"] == 0
    assert result["skipped"] == 1
    assert table.updates == 1
    assert table.rows[ORG]["subscription_status"] == "ACTIVE"
    assert table.rows[ORG]["current_period_end"] == new_end


def test_repeat_invocation_is_a_no_op():
    end = (NOW - timedelta(hours=1)).isoformat()
    table = Table([trial(end)])
    first = run_expiry(table, NOW)
    updates = table.updates
    second = run_expiry(table, NOW)

    assert first["expired"] == 1
    assert second == {"expired": 0, "cancelled": 0, "skipped": 0}
    assert table.updates == updates


def test_a_long_outage_still_expires_an_old_due_trial():
    stale = (NOW - timedelta(days=400)).isoformat()
    recent = (NOW - timedelta(days=3)).isoformat()
    table = Table([
        trial(stale, organization_id="ORG-OLD"),
        trial(recent, organization_id="ORG-NEW"),
    ])
    result = run_expiry(table, NOW)

    assert result["expired"] == 2
    assert table.rows["ORG-OLD"]["subscription_status"] == "EXPIRED"
    assert table.rows["ORG-NEW"]["subscription_status"] == "EXPIRED"
    assert "lifecycle_partition" not in table.rows["ORG-OLD"]


def test_handler_does_not_use_cognito(monkeypatch):
    source = (ROOT / "src" / "billing" / "expiry_handler.py").read_text(encoding="utf-8")
    expiry_source = (ROOT / "src" / "billing" / "expiry.py").read_text(encoding="utf-8")
    assert "authorize(" not in source
    assert "razorpay" not in expiry_source.lower()
    assert "scan(" not in expiry_source

    class Context:
        aws_request_id = "exp-1"

    monkeypatch.setattr(
        "billing.expiry_handler.subscriptions_table",
        lambda: Table([trial((NOW - timedelta(hours=1)).isoformat())]),
    )
    monkeypatch.setattr("billing.expiry_handler.datetime", type("Clock", (), {"now": staticmethod(lambda tz=None: NOW)}))
    result = lambda_handler({}, Context())

    assert result["expired"] == 1
    assert "erap-billing-expiry" in BILLING_PACKAGES
    assert "erap-billing-expiry" not in PACKAGES


def test_cancel_replaces_or_removes_the_lifecycle_entry():
    class Store:
        def __init__(self, row):
            self.row = dict(row)

        def update_item(self, **kwargs):
            values = kwargs["ExpressionAttributeValues"]
            expression = kwargs["UpdateExpression"]
            self.row["cancel_at_period_end"] = values[":flag"]
            self.row["updated_at"] = values[":updated"]

            if " REMOVE " in expression:
                self.row.pop("lifecycle_partition", None)
                self.row.pop("lifecycle_due_at", None)

            if ":lifecycle_partition" in values:
                self.row["lifecycle_partition"] = values[":lifecycle_partition"]
                self.row["lifecycle_due_at"] = values[":lifecycle_due_at"]

        def get_item(self, Key):
            return {"Item": dict(self.row)}

    stale = trial((NOW - timedelta(days=1)).isoformat())
    stale["subscription_status"] = "ACTIVE"
    stale["provider"] = "razorpay"
    stale["provider_subscription_id"] = "sub_Cancel0000001"
    open_row = Store(stale)
    _mark_period_end(open_row, ORG, dict(stale), NOW)

    assert "lifecycle_partition" not in open_row.row

    period_end = (NOW + timedelta(days=10)).isoformat()
    current = dict(stale)
    current["current_period_end"] = period_end
    scheduled_row = Store(current)
    _mark_period_end(scheduled_row, ORG, dict(current), NOW)

    assert scheduled_row.row["lifecycle_partition"] == _part("CANCEL", ORG)
    assert scheduled_row.row["lifecycle_due_at"] == period_end
    assert scheduled_row.row["cancel_at_period_end"] is True


def test_expiry_specification_matches_the_live_schedule():
    spec = json.loads((ROOT / "infra" / "billing-expiry.json").read_text(encoding="utf-8"))
    tables = json.loads((ROOT / "infra" / "billing-tables.json").read_text(encoding="utf-8"))
    indexes = tables["tables"][0]["GlobalSecondaryIndexes"]
    policy = json.dumps(spec["iam"])

    assert spec["applied"] is True
    assert spec["schedule"]["state"] == "ENABLED"
    assert spec["schedule"]["timezone"] == "UTC"
    assert spec["schedule"]["schedule_expression"] == "cron(0 2 * * ? *)"
    assert indexes[1]["IndexName"] == "LifecycleDueIndex"
    assert "dynamodb:Scan" not in policy
    assert "dynamodb:DeleteItem" not in policy
    assert "dynamodb:PutItem" not in policy
    assert "secretsmanager" not in policy
    assert "Organizations" not in policy
