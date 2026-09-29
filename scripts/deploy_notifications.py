"""Create/update erap-notifications with dedicated least-privilege role and live alias."""

from __future__ import annotations

import json
import subprocess
import time

from aws_cli import aws
from lambda_manifest import ACCOUNT, ALIAS, API_ID, NOTIFICATION_PACKAGES, REGION, ROOT
from package_lambdas import build_zip, check_zip

FUNCTION = "erap-notifications"
ROLE_NAME = "ERAP-Notifications-Lambda-Role"
POLICY_NAME = "ERAP-Notifications-DynamoDB-Policy"
HANDLER = "handler.lambda_handler"
RUNTIME = "python3.14"
MEMORY = 256
TIMEOUT = 15
SOURCE_API = f"arn:aws:execute-api:{REGION}:{ACCOUNT}:{API_ID}/*/*"

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

POLICY = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "OrganizationsRead",
            "Effect": "Allow",
            "Action": ["dynamodb:GetItem"],
            "Resource": [f"arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/Organizations"],
        },
        {
            "Sid": "OrganizationMembersAuth",
            "Effect": "Allow",
            "Action": ["dynamodb:GetItem", "dynamodb:Query"],
            "Resource": [
                f"arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/OrganizationMembers",
                f"arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/OrganizationMembers/index/UserSubIndex",
            ],
        },
        {
            "Sid": "SubscriptionsEntitlementRead",
            "Effect": "Allow",
            "Action": ["dynamodb:GetItem"],
            "Resource": [
                f"arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/OrganizationSubscriptions"
            ],
        },
        {
            "Sid": "NotificationsReadWrite",
            "Effect": "Allow",
            "Action": [
                "dynamodb:GetItem",
                "dynamodb:Query",
                "dynamodb:UpdateItem",
            ],
            "Resource": [
                f"arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/Notifications",
                f"arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/Notifications/index/UnreadByUserIndex",
            ],
        },
        {
            "Sid": "CloudWatchLogs",
            "Effect": "Allow",
            "Action": [
                "logs:CreateLogGroup",
                "logs:CreateLogStream",
                "logs:PutLogEvents",
            ],
            "Resource": [f"arn:aws:logs:{REGION}:{ACCOUNT}:*"],
        },
    ],
}


def git_commit():
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()


def ensure_role():
    try:
        aws(["iam", "get-role", "--role-name", ROLE_NAME])
        print(f"role exists {ROLE_NAME}")
    except SystemExit as error:
        if "NoSuchEntity" not in str(error):
            raise
        aws(
            [
                "iam",
                "create-role",
                "--role-name",
                ROLE_NAME,
                "--assume-role-policy-document",
                json.dumps(TRUST),
                "--description",
                "Least-privilege role for erap-notifications",
            ]
        )
        print(f"role created {ROLE_NAME}")
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
    print(f"policy attached {POLICY_NAME}")
    time.sleep(8)
    return f"arn:aws:iam::{ACCOUNT}:role/{ROLE_NAME}"


def ensure_function(role_arn):
    zip_path = ROOT / "dist" / f"{FUNCTION}.zip"
    build_zip(FUNCTION, zip_path)
    check_zip(zip_path, FUNCTION)
    try:
        aws(["lambda", "get-function", "--function-name", FUNCTION], region=REGION)
        print(f"updating {FUNCTION} code")
        aws(
            [
                "lambda",
                "update-function-code",
                "--function-name",
                FUNCTION,
                "--zip-file",
                f"fileb://{zip_path}",
            ],
            region=REGION,
        )
        waiter = 0
        while waiter < 30:
            conf = aws(
                ["lambda", "get-function-configuration", "--function-name", FUNCTION],
                region=REGION,
            )
            if conf.get("LastUpdateStatus") in {None, "Successful"}:
                break
            time.sleep(2)
            waiter += 1
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
                str(TIMEOUT),
                "--memory-size",
                str(MEMORY),
            ],
            region=REGION,
        )
    except SystemExit as error:
        if "ResourceNotFoundException" not in str(error):
            raise
        print(f"creating {FUNCTION}")
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
                "--timeout",
                str(TIMEOUT),
                "--memory-size",
                str(MEMORY),
                "--zip-file",
                f"fileb://{zip_path}",
            ],
            region=REGION,
        )
    time.sleep(5)


def publish_live(commit):
    conf = aws(
        ["lambda", "get-function-configuration", "--function-name", FUNCTION],
        region=REGION,
    )
    previous = None
    try:
        alias = aws(
            ["lambda", "get-alias", "--function-name", FUNCTION, "--name", ALIAS],
            region=REGION,
        )
        previous = str(alias.get("FunctionVersion"))
    except SystemExit:
        previous = None

    published = aws(
        [
            "lambda",
            "publish-version",
            "--function-name",
            FUNCTION,
            "--description",
            f"commit={commit}"[:256],
        ],
        region=REGION,
    )
    version = str(published["Version"])
    try:
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
                f"commit={commit}"[:256],
            ],
            region=REGION,
        )
    except SystemExit:
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
                f"commit={commit}"[:256],
            ],
            region=REGION,
        )
    return previous, version


def ensure_invoke_permission():
    statement_id = "apigateway-invoke-live"
    try:
        aws(
            [
                "lambda",
                "add-permission",
                "--function-name",
                FUNCTION,
                "--qualifier",
                ALIAS,
                "--statement-id",
                statement_id,
                "--action",
                "lambda:InvokeFunction",
                "--principal",
                "apigateway.amazonaws.com",
                "--source-arn",
                SOURCE_API,
            ],
            region=REGION,
        )
        print(f"permission {statement_id} added")
    except SystemExit as error:
        if "ResourceConflictException" in str(error):
            print(f"permission {statement_id} exists")
        else:
            raise


def main():
    if FUNCTION not in NOTIFICATION_PACKAGES:
        raise SystemExit("erap-notifications missing from NOTIFICATION_PACKAGES")
    (ROOT / "dist").mkdir(exist_ok=True)
    role_arn = ensure_role()
    ensure_function(role_arn)
    commit = git_commit()
    previous, version = publish_live(commit)
    ensure_invoke_permission()
    report = {
        "function": FUNCTION,
        "runtime": RUNTIME,
        "handler": HANDLER,
        "memory": MEMORY,
        "timeout": TIMEOUT,
        "role": role_arn,
        "previous_live_version": previous,
        "live_version": version,
        "alias": ALIAS,
        "commit": commit,
    }
    (ROOT / "dist" / "deploy-notifications.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
