"""Create/update erap-exchange with a dedicated least-privilege role and live alias.

Does not modify other Lambda functions or shared production roles.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

from aws_cli import aws
from lambda_manifest import ACCOUNT, ALIAS, API_ID, EXCHANGE_PACKAGES, REGION, ROOT
from package_lambdas import build_zip, check_zip


FUNCTION = "erap-exchange"
ROLE_NAME = "ERAP-Exchange-Lambda-Role"
POLICY_NAME = "ERAP-Exchange-DynamoDB-Policy"
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

# Least privilege derived from src/exchange + shared access/membership/audit paths.
# No Scan, DeleteItem, BatchWriteItem, or Razorpay.
# Network pagination uses a separate GetSecretValue policy, not this document.
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
            "Sid": "OrganizationMembersRead",
            "Effect": "Allow",
            "Action": ["dynamodb:GetItem", "dynamodb:Query"],
            "Resource": [f"arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/OrganizationMembers"],
        },
        {
            "Sid": "OrganizationMembersUserSubIndex",
            "Effect": "Allow",
            "Action": ["dynamodb:Query"],
            "Resource": [
                f"arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/OrganizationMembers/index/UserSubIndex"
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
            "Sid": "LocationsRead",
            "Effect": "Allow",
            "Action": ["dynamodb:GetItem"],
            "Resource": [f"arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/Locations"],
        },
        {
            "Sid": "ResourceTypesRead",
            "Effect": "Allow",
            "Action": ["dynamodb:GetItem"],
            "Resource": [f"arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/ResourceTypes"],
        },
        {
            "Sid": "ResourcesReadUpdate",
            "Effect": "Allow",
            "Action": [
                "dynamodb:GetItem",
                "dynamodb:PutItem",
                "dynamodb:UpdateItem",
                "dynamodb:TransactWriteItems",
            ],
            "Resource": [f"arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/Resources"],
        },
        {
            "Sid": "ResourceExchangesItems",
            "Effect": "Allow",
            "Action": [
                "dynamodb:GetItem",
                "dynamodb:PutItem",
                "dynamodb:UpdateItem",
                "dynamodb:Query",
                "dynamodb:TransactWriteItems",
            ],
            "Resource": [f"arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/ResourceExchanges"],
        },
        {
            "Sid": "ResourceExchangesIndexes",
            "Effect": "Allow",
            "Action": ["dynamodb:Query"],
            "Resource": [
                f"arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/ResourceExchanges/index/NetworkOpenRequestIndex",
                f"arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/ResourceExchanges/index/RequesterOrgIndex",
                f"arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/ResourceExchanges/index/ProviderOrgOfferIndex",
                f"arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/ResourceExchanges/index/ExpiryDueIndex",
            ],
        },
        {
            "Sid": "AllocationsReadWrite",
            "Effect": "Allow",
            "Action": [
                "dynamodb:GetItem",
                "dynamodb:PutItem",
                "dynamodb:UpdateItem",
                "dynamodb:TransactWriteItems",
            ],
            "Resource": [f"arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/Allocations"],
        },
        {
            "Sid": "AuditEventsWrite",
            "Effect": "Allow",
            "Action": ["dynamodb:PutItem"],
            "Resource": [f"arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/AuditEvents"],
        },
        {
            "Sid": "NotificationsEmit",
            "Effect": "Allow",
            "Action": [
                "dynamodb:PutItem",
                "dynamodb:GetItem",
                "dynamodb:UpdateItem",
            ],
            "Resource": [f"arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/Notifications"],
        },
        {
            "Sid": "ResourceStatusHistoryWrite",
            "Effect": "Allow",
            "Action": ["dynamodb:PutItem"],
            "Resource": [f"arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/ResourceStatusHistory"],
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
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    return result.stdout.strip()


def ensure_role():
    role_arn = f"arn:aws:iam::{ACCOUNT}:role/{ROLE_NAME}"
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
                "Least-privilege role for erap-exchange",
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
    page_policy = json.loads(
        (ROOT / "infra" / "exchange-network-page-token-secret.json").read_text(encoding="utf-8")
    )
    aws(
        [
            "iam",
            "put-role-policy",
            "--role-name",
            ROLE_NAME,
            "--policy-name",
            page_policy["policy_name"],
            "--policy-document",
            json.dumps(page_policy["policy"]),
        ]
    )
    print(f"policy attached {page_policy['policy_name']}")
    # IAM role propagation
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


def function_exists():
    try:
        return aws(
            ["lambda", "get-function-configuration", "--function-name", FUNCTION],
            region=REGION,
        )
    except SystemExit as error:
        if "ResourceNotFoundException" in str(error):
            return None
        raise


def point_alias(version, commit):
    description = f"commit={commit}"[:256]
    try:
        aws(
            ["lambda", "get-alias", "--function-name", FUNCTION, "--name", ALIAS],
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
        return
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


def allow_api_invoke():
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
                "apigateway-invoke-live",
                "--action",
                "lambda:InvokeFunction",
                "--principal",
                "apigateway.amazonaws.com",
                "--source-arn",
                SOURCE_API,
            ],
            region=REGION,
        )
        print("permission apigateway-invoke-live created")
    except SystemExit as error:
        if "ResourceConflictException" not in str(error):
            raise
        print("permission apigateway-invoke-live exists")


def validate_iam():
    doc = aws(
        [
            "iam",
            "get-role-policy",
            "--role-name",
            ROLE_NAME,
            "--policy-name",
            POLICY_NAME,
        ]
    )["PolicyDocument"]
    text = json.dumps(doc)
    if "dynamodb:*" in text:
        raise SystemExit("IAM contains dynamodb:* wildcard")
    forbidden = {"dynamodb:Scan", "dynamodb:DeleteItem", "dynamodb:BatchWriteItem"}
    actions = set()
    for statement in doc.get("Statement") or []:
        action = statement.get("Action")
        if isinstance(action, str):
            actions.add(action)
        else:
            actions.update(action or [])
    bad = actions & forbidden
    if bad:
        raise SystemExit(f"IAM contains forbidden actions: {sorted(bad)}")
    return doc


def main():
    if FUNCTION not in EXCHANGE_PACKAGES:
        raise SystemExit("erap-exchange missing from EXCHANGE_PACKAGES")

    commit = git_commit()
    role_arn = ensure_role()
    folder = ROOT / "dist"
    folder.mkdir(exist_ok=True)
    archive = build_zip(FUNCTION, folder / f"{FUNCTION}.zip")
    check_zip(archive, FUNCTION)

    previous_version = ""
    existing = function_exists()
    if existing is None:
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
                "fileb://" + archive.resolve().as_posix(),
                "--description",
                "Resource Exchange Network API",
            ],
            region=REGION,
        )
        wait_ready(FUNCTION)
    else:
        try:
            alias = aws(
                ["lambda", "get-alias", "--function-name", FUNCTION, "--name", ALIAS],
                region=REGION,
            )
            previous_version = str(alias.get("FunctionVersion") or "")
        except SystemExit:
            previous_version = ""
        print(f"updating {FUNCTION} code")
        aws(
            [
                "lambda",
                "update-function-code",
                "--function-name",
                FUNCTION,
                "--zip-file",
                "fileb://" + archive.resolve().as_posix(),
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
                str(TIMEOUT),
                "--memory-size",
                str(MEMORY),
            ],
            region=REGION,
        )
        wait_ready(FUNCTION)

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
    wait_ready(FUNCTION)
    point_alias(version, commit)
    allow_api_invoke()
    policy = validate_iam()
    config = wait_ready(FUNCTION)

    report = {
        "function": FUNCTION,
        "runtime": config.get("Runtime"),
        "handler": config.get("Handler"),
        "memory": config.get("MemorySize"),
        "timeout": config.get("Timeout"),
        "environment": config.get("Environment") or {},
        "role": role_arn,
        "previous_live_version": previous_version,
        "live_version": version,
        "alias": ALIAS,
        "commit": commit,
        "iam_actions": sorted(
            {
                action
                for statement in policy.get("Statement") or []
                for action in (
                    [statement["Action"]]
                    if isinstance(statement.get("Action"), str)
                    else (statement.get("Action") or [])
                )
            }
        ),
    }
    (folder / "exchange-deploy.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
