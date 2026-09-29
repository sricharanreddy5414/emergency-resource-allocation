"""Deploy erap-exchange-expiry with least-privilege role and hourly schedule.

Does not modify billing expiry. Dry infrastructure applies only when --apply.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from aws_cli import aws
from lambda_manifest import ACCOUNT, ALIAS, EXCHANGE_PACKAGES, REGION, ROOT
from package_lambdas import build_zip, check_zip

FUNCTION = "erap-exchange-expiry"
ROLE_NAME = "ERAP-Exchange-Expiry-Lambda-Role"
POLICY_NAME = "ERAP-Exchange-Expiry-Policy"
SCHEDULER_ROLE = "ERAP-Exchange-Expiry-Scheduler-Role"
SCHEDULE_NAME = "erap-exchange-expiry-hourly"
HANDLER = "handler.lambda_handler"
RUNTIME = "python3.14"

TRUST = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Principal": {"Service": "lambda.amazonaws.com"},
            "Action": "sts:AssumeRole",
        }
    ],
}

POLICY = json.loads(
    (ROOT / "infra" / "exchange-expiry.json").read_text(encoding="utf-8")
)["iam"]


def git_commit():
    import subprocess

    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()


def ensure_role():
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
                json.dumps(TRUST),
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
            json.dumps(POLICY),
        ]
    )
    time.sleep(8)
    return role_arn


def wait_ready(name):
    for _ in range(40):
        config = aws(
            ["lambda", "get-function-configuration", "--function-name", name],
            region=REGION,
        )
        status = config.get("LastUpdateStatus") or "Successful"
        if status == "Successful":
            return config
        if status == "Failed":
            raise SystemExit(config.get("LastUpdateStatusReason", "update failed"))
        time.sleep(3)
    raise SystemExit(f"{name} update did not finish")


def point_alias(version, commit):
    description = f"commit={commit}"[:256]
    try:
        aws(
            ["lambda", "get-alias", "--function-name", FUNCTION, "--name", ALIAS],
            region=REGION,
        )
        aws(
            [
                "lambda",
                "update-alias",
                "--function-name",
                FUNCTION,
                "--name",
                ALIAS,
                "--function-version",
                version,
                "--description",
                description,
            ],
            region=REGION,
        )
    except SystemExit as error:
        if "ResourceNotFoundException" not in str(error):
            raise
        aws(
            [
                "lambda",
                "create-alias",
                "--function-name",
                FUNCTION,
                "--name",
                ALIAS,
                "--function-version",
                version,
                "--description",
                description,
            ],
            region=REGION,
        )


def ensure_schedule(function_arn):
    try:
        role = aws(["iam", "get-role", "--role-name", SCHEDULER_ROLE])
        scheduler_arn = role["Role"]["Arn"]
    except SystemExit as error:
        if "NoSuchEntity" not in str(error):
            raise
        trust = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Service": "scheduler.amazonaws.com"},
                    "Action": "sts:AssumeRole",
                }
            ],
        }
        role = aws(
            [
                "iam",
                "create-role",
                "--role-name",
                SCHEDULER_ROLE,
                "--assume-role-policy-document",
                json.dumps(trust),
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
            "InvokeExchangeExpiry",
            "--policy-document",
            json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Action": "lambda:InvokeFunction",
                            "Resource": [
                                function_arn,
                                function_arn.replace(f":{ALIAS}", ""),
                            ],
                        }
                    ],
                }
            ),
        ]
    )
    time.sleep(5)
    target = {
        "Arn": function_arn,
        "RoleArn": scheduler_arn,
        "Input": "{}",
    }
    try:
        aws(
            [
                "scheduler",
                "get-schedule",
                "--name",
                SCHEDULE_NAME,
            ],
            region=REGION,
        )
        aws(
            [
                "scheduler",
                "update-schedule",
                "--name",
                SCHEDULE_NAME,
                "--schedule-expression",
                "cron(15 * * * ? *)",
                "--flexible-time-window",
                '{"Mode":"OFF"}',
                "--target",
                json.dumps(target),
                "--state",
                "ENABLED",
            ],
            region=REGION,
        )
        print(f"schedule updated {SCHEDULE_NAME}")
    except SystemExit as error:
        if "ResourceNotFoundException" not in str(error) and "NotFoundException" not in str(error):
            raise
        aws(
            [
                "scheduler",
                "create-schedule",
                "--name",
                SCHEDULE_NAME,
                "--schedule-expression",
                "cron(15 * * * ? *)",
                "--flexible-time-window",
                '{"Mode":"OFF"}',
                "--target",
                json.dumps(target),
                "--state",
                "ENABLED",
            ],
            region=REGION,
        )
        print(f"schedule created {SCHEDULE_NAME}")


def main():
    if FUNCTION not in EXCHANGE_PACKAGES:
        raise SystemExit("erap-exchange-expiry missing from EXCHANGE_PACKAGES")
    commit = git_commit()
    role_arn = ensure_role()
    folder = ROOT / "dist"
    folder.mkdir(exist_ok=True)
    archive = build_zip(FUNCTION, folder / f"{FUNCTION}.zip")
    check_zip(archive, FUNCTION)

    try:
        aws(
            ["lambda", "get-function-configuration", "--function-name", FUNCTION],
            region=REGION,
        )
        aws(
            [
                "lambda",
                "update-function-code",
                "--function-name",
                FUNCTION,
                "--zip-file",
                f"fileb://{archive}",
            ],
            region=REGION,
        )
        wait_ready(FUNCTION)
        aws(
            [
                "lambda",
                "update-function-configuration",
                "--function-name",
                FUNCTION,
                "--role",
                role_arn,
                "--handler",
                HANDLER,
                "--runtime",
                RUNTIME,
                "--timeout",
                "60",
                "--memory-size",
                "256",
                "--description",
                f"commit={commit}"[:256],
            ],
            region=REGION,
        )
        wait_ready(FUNCTION)
    except SystemExit as error:
        if "ResourceNotFoundException" not in str(error):
            raise
        aws(
            [
                "lambda",
                "create-function",
                "--function-name",
                FUNCTION,
                "--runtime",
                RUNTIME,
                "--role",
                role_arn,
                "--handler",
                HANDLER,
                "--zip-file",
                f"fileb://{archive}",
                "--timeout",
                "60",
                "--memory-size",
                "256",
                "--description",
                f"commit={commit}"[:256],
            ],
            region=REGION,
        )
        wait_ready(FUNCTION)

    published = aws(
        ["lambda", "publish-version", "--function-name", FUNCTION, "--description", commit[:256]],
        region=REGION,
    )
    version = str(published["Version"])
    point_alias(version, commit)
    alias_arn = (
        f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:{FUNCTION}:{ALIAS}"
    )
    ensure_schedule(alias_arn)
    print(json.dumps({"function": FUNCTION, "version": version, "alias": ALIAS, "commit": commit}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
