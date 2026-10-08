"""Define the four lifecycle error alarms and reservation-expiry log retention.

Does nothing unless --apply is present. The general backend release does not
call this script. Applying uses the existing ERAP-Production-Alarms topic and
does not create a topic or a subscription.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = ROOT / "infra" / "lifecycle-alarms.json"


def spec():
    return json.loads(SPEC_PATH.read_text(encoding="utf-8"))


def alarm_definitions():
    """Return the four CloudWatch alarm payloads. No AWS call."""
    document = spec()
    pattern = document["pattern"]
    alarms = []
    for item in document["alarms"]:
        alarms.append(
            {
                "AlarmName": item["alarm_name"],
                "AlarmDescription": pattern["alarm_description"],
                "MetricName": pattern["metric_name"],
                "Namespace": pattern["namespace"],
                "Statistic": pattern["statistic"],
                "Period": pattern["period"],
                "EvaluationPeriods": pattern["evaluation_periods"],
                "Threshold": pattern["threshold"],
                "ComparisonOperator": pattern["comparison_operator"],
                "TreatMissingData": pattern["treat_missing_data"],
                "Dimensions": [
                    {"Name": pattern["dimension_name"], "Value": item["function_name"]}
                ],
                "TopicName": document["topic_name"],
            }
        )
    return alarms


def plan():
    document = spec()
    return {
        "applied": False,
        "topic_name": document["topic_name"],
        "alarms": [item["AlarmName"] for item in alarm_definitions()],
        "functions": [
            item["Dimensions"][0]["Value"] for item in alarm_definitions()
        ],
        "log_group": document["log_retention"]["log_group"],
        "retention_in_days": document["log_retention"]["retention_in_days"],
    }


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    report = plan()
    if "--apply" not in args:
        print(json.dumps(report))
        return 0
    _apply(report)
    report["applied"] = True
    print(json.dumps(report))
    return 0


def _apply(report):
    from aws_cli import aws
    from lambda_manifest import REGION

    arn = _existing_topic_arn(aws, REGION, report["topic_name"])
    for alarm in alarm_definitions():
        _put_alarm(aws, REGION, alarm, arn)
    aws(
        [
            "logs",
            "put-retention-policy",
            "--log-group-name",
            report["log_group"],
            "--retention-in-days",
            str(report["retention_in_days"]),
        ],
        region=REGION,
    )


def _existing_topic_arn(aws, region, topic_name):
    listed = aws(["sns", "list-topics"], region=region)
    suffix = ":" + topic_name
    for topic in listed.get("Topics") or []:
        arn = topic.get("TopicArn") or ""
        if arn.endswith(suffix):
            return arn
    raise SystemExit(f"missing topic {topic_name}")


def _put_alarm(aws, region, alarm, arn):
    aws(
        [
            "cloudwatch",
            "put-metric-alarm",
            "--alarm-name",
            alarm["AlarmName"],
            "--alarm-description",
            alarm["AlarmDescription"],
            "--metric-name",
            alarm["MetricName"],
            "--namespace",
            alarm["Namespace"],
            "--statistic",
            alarm["Statistic"],
            "--period",
            str(alarm["Period"]),
            "--evaluation-periods",
            str(alarm["EvaluationPeriods"]),
            "--threshold",
            str(alarm["Threshold"]),
            "--comparison-operator",
            alarm["ComparisonOperator"],
            "--treat-missing-data",
            alarm["TreatMissingData"],
            "--dimensions",
            f"Name={alarm['Dimensions'][0]['Name']},Value={alarm['Dimensions'][0]['Value']}",
            "--alarm-actions",
            arn,
        ],
        region=region,
    )


if __name__ == "__main__":
    sys.exit(main())
