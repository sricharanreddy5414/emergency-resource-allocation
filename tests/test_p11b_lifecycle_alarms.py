"""Phase 11B alarm and retention definitions. These tests do not call AWS."""

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import attach_alarm_topic
import ensure_lifecycle_alarms
import set_log_retention


SPEC_PATH = ROOT / "infra" / "lifecycle-alarms.json"

EXISTING_ALARMS = {
    "ERAP-ApiGateway-5XX",
    "ERAP-Allocation-Throttles",
    "ERAP-AutoRelease-Errors",
    "ERAP-Public-Lambda-Errors",
    "ERAP-Resources-SystemErrors",
    "EmergencyResourceAllocation-Lambda-Errors",
}

EXPECTED = {
    "ERAP-BillingWebhook-Errors": "erap-billing-webhook",
    "ERAP-BillingExpiry-Errors": "erap-billing-expiry",
    "ERAP-ExchangeExpiry-Errors": "erap-exchange-expiry",
    "ERAP-ReservationExpiry-Errors": "erap-reservation-expiry",
}


def test_four_error_alarms_target_the_existing_topic():
    document = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    alarms = ensure_lifecycle_alarms.alarm_definitions()
    assert document["applied"] is False
    assert document["topic_name"] == "ERAP-Production-Alarms"
    assert {item["AlarmName"]: item["Dimensions"][0]["Value"] for item in alarms} == EXPECTED
    for alarm in alarms:
        assert alarm["TopicName"] == "ERAP-Production-Alarms"
        assert alarm["Namespace"] == "AWS/Lambda"
        assert alarm["MetricName"] == "Errors"
        assert alarm["Statistic"] == "Sum"
        assert alarm["Period"] == 300
        assert alarm["EvaluationPeriods"] == 1
        assert alarm["Threshold"] == 1
        assert alarm["ComparisonOperator"] == "GreaterThanThreshold"
        assert alarm["TreatMissingData"] == "notBreaching"
        assert alarm["Dimensions"][0]["Name"] == "FunctionName"
        assert alarm["AlarmName"] not in EXISTING_ALARMS


def test_reservation_expiry_retention_is_thirty_days():
    document = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    retention = document["log_retention"]
    assert retention["function_name"] == "erap-reservation-expiry"
    assert retention["log_group"] == "/aws/lambda/erap-reservation-expiry"
    assert retention["retention_in_days"] == 30
    assert "erap-reservation-expiry" in set_log_retention.EXTRA_FUNCTIONS
    source = (ROOT / "scripts" / "set_log_retention.py").read_text(encoding="utf-8")
    assert '--retention-in-days", "30"' in source


def test_existing_alarms_and_topic_are_not_replaced(capsys):
    assert set(attach_alarm_topic.ALARMS) == {
        "ERAP-ApiGateway-5XX",
        "ERAP-Allocation-Throttles",
        "ERAP-AutoRelease-Errors",
        "ERAP-Public-Lambda-Errors",
        "ERAP-Resources-SystemErrors",
    }
    assert attach_alarm_topic.TOPIC_NAME == "ERAP-Production-Alarms"
    report = ensure_lifecycle_alarms.plan()
    assert report["applied"] is False
    assert set(report["alarms"]).isdisjoint(EXISTING_ALARMS)
    assert "create-topic" not in (ROOT / "scripts" / "ensure_lifecycle_alarms.py").read_text(encoding="utf-8")
    assert ensure_lifecycle_alarms.main([]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["applied"] is False
    assert printed["topic_name"] == "ERAP-Production-Alarms"
    assert printed["retention_in_days"] == 30


def test_deploy_policy_does_not_gain_alarm_write():
    policy = json.loads((ROOT / "infra" / "github-deploy-policy.json").read_text(encoding="utf-8"))
    actions = []
    for statement in policy["Statement"]:
        action = statement["Action"]
        actions.extend(action if isinstance(action, list) else [action])
    assert "cloudwatch:PutMetricAlarm" not in actions
    assert "cloudwatch:DescribeAlarms" in actions
    assert "logs:PutRetentionPolicy" not in actions


def test_release_workflows_do_not_apply_the_alarms():
    for name in ("ci.yml", "deploy-backend.yml", "release.yml", "rollback.yml"):
        text = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
        assert "ensure_lifecycle_alarms.py" not in text
        assert "set_log_retention.py" not in text
