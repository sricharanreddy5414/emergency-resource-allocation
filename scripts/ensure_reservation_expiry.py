"""Create the reservation due-time index, worker role, and schedule.

Does nothing unless --apply is present. The general backend release does
not call this script.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = ROOT / "infra" / "reservation-expiry.json"
FUNCTION = "erap-reservation-expiry"
ROLE_NAME = "ERAP-Reservation-Expiry-Lambda-Role"
POLICY_NAME = "ERAP-Reservation-Expiry-Policy"
SCHEDULER_ROLE = "ERAP-Reservation-Expiry-Scheduler-Role"
INDEX_NAME = "ReservationDueIndex"


def _spec():
    return json.loads(SPEC_PATH.read_text(encoding="utf-8"))


def plan():
    spec = _spec()
    return {
        "applied": False,
        "function": spec["function"]["name"],
        "index": spec["index"]["name"],
        "schedule": spec["schedule"]["name"],
        "schedule_expression": spec["schedule"]["schedule_expression"],
    }


def _aws():
    from aws_cli import aws

    return aws


def ensure_index(spec):
    aws = _aws()
    from lambda_manifest import REGION

    table = spec["index"]["table"]
    described = aws(["dynamodb", "describe-table", "--table-name", table], region=REGION)
    existing = {
        index.get("IndexName")
        for index in (described.get("Table") or {}).get("GlobalSecondaryIndexes") or []
    }
    if INDEX_NAME in existing:
        print(f"index exists {INDEX_NAME}")
        return
    attribute_names = {
        item["AttributeName"]
        for item in (described.get("Table") or {}).get("AttributeDefinitions") or []
    }
    definitions = []
    for name in ("reservation_due_key", "reservation_expires_at"):
        if name not in attribute_names:
            definitions.append({"AttributeName": name, "AttributeType": "S"})
    args = [
        "dynamodb",
        "update-table",
        "--table-name",
        table,
        "--global-secondary-index-updates",
        json.dumps(
            [
                {
                    "Create": {
                        "IndexName": INDEX_NAME,
                        "KeySchema": [
                            {"AttributeName": "reservation_due_key", "KeyType": "HASH"},
                            {"AttributeName": "reservation_expires_at", "KeyType": "RANGE"},
                        ],
                        "Projection": {
                            "ProjectionType": "INCLUDE",
                            "NonKeyAttributes": spec["index"]["non_key_attributes"],
                        },
                    }
                }
            ]
        ),
    ]
    if definitions:
        args[4:4] = ["--attribute-definitions", json.dumps(definitions)]
    aws(args, region=REGION)
    for _ in range(60):
        current = aws(["dynamodb", "describe-table", "--table-name", table], region=REGION)
        indexes = (current.get("Table") or {}).get("GlobalSecondaryIndexes") or []
        match = next((index for index in indexes if index.get("IndexName") == INDEX_NAME), None)
        if match and match.get("IndexStatus") == "ACTIVE":
            print(f"index active {INDEX_NAME}")
            return
        time.sleep(5)
    raise SystemExit("ReservationDueIndex did not become active")


def ensure_role(spec):
    aws = _aws()
    trust = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "lambda.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }
        ],
    }
    try:
        role = aws(["iam", "get-role", "--role-name", ROLE_NAME])
        role_arn = role["Role"]["Arn"]
    except SystemExit as error:
        if "NoSuchEntity" not in str(error):
            raise
        role = aws(
            [
                "iam",
                "create-role",
                "--role-name",
                ROLE_NAME,
                "--assume-role-policy-document",
                json.dumps(trust),
            ]
        )
        role_arn = role["Role"]["Arn"]
    aws(
        [
            "iam",
            "put-role-policy",
            "--role-name",
            ROLE_NAME,
            "--policy-name",
            POLICY_NAME,
            "--policy-document",
            json.dumps(spec["iam"]),
        ]
    )
    return role_arn


def ensure_schedule(spec, function_arn):
    aws = _aws()
    from lambda_manifest import REGION

    scheduler_trust = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "scheduler.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }
        ],
    }
    try:
        role = aws(["iam", "get-role", "--role-name", SCHEDULER_ROLE])
        scheduler_arn = role["Role"]["Arn"]
    except SystemExit as error:
        if "NoSuchEntity" not in str(error):
            raise
        role = aws(
            [
                "iam",
                "create-role",
                "--role-name",
                SCHEDULER_ROLE,
                "--assume-role-policy-document",
                json.dumps(scheduler_trust),
            ]
        )
        scheduler_arn = role["Role"]["Arn"]
    aws(
        [
            "iam",
            "put-role-policy",
            "--role-name",
            SCHEDULER_ROLE,
            "--policy-name",
            "ERAP-Reservation-Expiry-Invoke",
            "--policy-document",
            json.dumps(spec["scheduler_iam"]),
        ]
    )
    time.sleep(5)
    target = {"Arn": function_arn, "RoleArn": scheduler_arn, "Input": "{}"}
    name = spec["schedule"]["name"]
    expression = spec["schedule"]["schedule_expression"]
    try:
        aws(["scheduler", "get-schedule", "--name", name], region=REGION)
        aws(
            [
                "scheduler",
                "update-schedule",
                "--name",
                name,
                "--schedule-expression",
                expression,
                "--flexible-time-window",
                '{"Mode":"OFF"}',
                "--target",
                json.dumps(target),
                "--state",
                "ENABLED",
            ],
            region=REGION,
        )
    except SystemExit as error:
        if "ResourceNotFoundException" not in str(error) and "NotFoundException" not in str(error):
            raise
        aws(
            [
                "scheduler",
                "create-schedule",
                "--name",
                name,
                "--schedule-expression",
                expression,
                "--flexible-time-window",
                '{"Mode":"OFF"}',
                "--target",
                json.dumps(target),
                "--state",
                "ENABLED",
            ],
            region=REGION,
        )


def apply_infrastructure():
    spec = _spec()
    if spec.get("applied") is not False:
        raise SystemExit("reservation-expiry.json must stay applied false until this command is used")
    ensure_index(spec)
    role_arn = ensure_role(spec)
    from lambda_manifest import ACCOUNT, REGION

    aws = _aws()
    function_arn = f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:{FUNCTION}:live"
    try:
        aws(["lambda", "get-function", "--function-name", FUNCTION], region=REGION)
        ensure_schedule(spec, function_arn)
    except SystemExit as error:
        if "ResourceNotFoundException" not in str(error):
            raise
        print("schedule waiting for erap-reservation-expiry")
    print(json.dumps({"applied": True, "role_arn": role_arn, "function": FUNCTION}))
    return role_arn


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if "--apply" not in args:
        print(json.dumps(plan()))
        return 0
    apply_infrastructure()
    return 0


if __name__ == "__main__":
    sys.exit(main())
