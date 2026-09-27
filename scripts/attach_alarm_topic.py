"""Give the five silent ERAP alarms an SNS topic. No email is subscribed."""

import json
import sys

from aws_cli import aws
from lambda_manifest import ACCOUNT, REGION, ROOT


TOPIC_NAME = "ERAP-Production-Alarms"
ALARMS = [
    "ERAP-ApiGateway-5XX",
    "ERAP-Allocation-Throttles",
    "ERAP-AutoRelease-Errors",
    "ERAP-Public-Lambda-Errors",
    "ERAP-Resources-SystemErrors",
]


def topic_arn():
    created = aws(["sns", "create-topic", "--name", TOPIC_NAME], region=REGION)
    arn = created["TopicArn"]
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "AllowCloudWatchAlarms",
                "Effect": "Allow",
                "Principal": {"Service": "cloudwatch.amazonaws.com"},
                "Action": "sns:Publish",
                "Resource": arn,
                "Condition": {"StringEquals": {"AWS:SourceAccount": ACCOUNT}},
            }
        ],
    }
    aws(
        [
            "sns",
            "set-topic-attributes",
            "--topic-arn",
            arn,
            "--attribute-name",
            "Policy",
            "--attribute-value",
            json.dumps(policy),
        ],
        region=REGION,
    )
    return arn


def put_alarm(alarm, arn):
    aws(
        [
            "cloudwatch",
            "put-metric-alarm",
            "--alarm-name",
            alarm["AlarmName"],
            "--alarm-description",
            alarm.get("AlarmDescription") or "ERAP production alarm",
            "--metric-name",
            alarm["MetricName"],
            "--namespace",
            alarm["Namespace"],
            "--statistic",
            alarm.get("Statistic") or "Sum",
            "--period",
            str(alarm["Period"]),
            "--evaluation-periods",
            str(alarm["EvaluationPeriods"]),
            "--threshold",
            str(alarm["Threshold"]),
            "--comparison-operator",
            alarm["ComparisonOperator"],
            "--treat-missing-data",
            alarm.get("TreatMissingData") or "notBreaching",
            "--dimensions",
            *[
                f"Name={item['Name']},Value={item['Value']}"
                for item in alarm.get("Dimensions") or []
            ],
            "--alarm-actions",
            arn,
        ],
        region=REGION,
    )


def main():
    arn = topic_arn()
    described = aws(["cloudwatch", "describe-alarms", "--alarm-names", *ALARMS], region=REGION)
    found = {item["AlarmName"]: item for item in described.get("MetricAlarms", [])}
    for name in ALARMS:
        alarm = found.get(name)
        if not alarm:
            raise SystemExit(f"missing alarm {name}")
        if arn in (alarm.get("AlarmActions") or []):
            print(f"already attached {name}")
            continue
        put_alarm(alarm, arn)
        print(f"attached {name}")
    record = {"topic_arn": arn, "subscriptions": 0}
    listed = aws(["sns", "list-subscriptions-by-topic", "--topic-arn", arn], region=REGION)
    record["subscriptions"] = len(listed.get("Subscriptions") or [])
    (ROOT / "dist").mkdir(exist_ok=True)
    (ROOT / "dist" / "alarm-topic.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(json.dumps(record))
    return 0


if __name__ == "__main__":
    sys.exit(main())
