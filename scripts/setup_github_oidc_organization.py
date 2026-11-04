"""Create only ERAP-GitHub-Production-Organization.

This does not create the OIDC provider. It does not update the shared
GitHub deploy or production roles. It attaches only the narrow
organization/location Lambda policy.
"""

import json
import sys
from pathlib import Path

from aws_cli import aws
from lambda_manifest import ACCOUNT, ROOT


ROLE_NAME = "ERAP-GitHub-Production-Organization"
POLICY_NAME = "ERAP-Organization-Release-Policy"
TRUST_PATH = ROOT / "infra" / "github-production-organization-trust.json"
POLICY_PATH = ROOT / "infra" / "github-production-organization-policy.json"


def file_uri(path):
    return "file://" + Path(path).resolve().as_posix()


def main():
    trust = json.loads(TRUST_PATH.read_text(encoding="utf-8"))
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    refs = trust["Statement"][0]["Condition"]["StringEquals"][
        "token.actions.githubusercontent.com:job_workflow_ref"
    ]
    expected_refs = [
        "sricharanreddy5414/emergency-resource-allocation/.github/workflows/release-organization.yml@refs/heads/main",
        "sricharanreddy5414/emergency-resource-allocation/.github/workflows/rollback-organization.yml@refs/heads/main",
    ]
    if refs != expected_refs:
        raise SystemExit("organization trust workflow refs are not the two approved files")
    actions = []
    resources = []
    for statement in policy["Statement"]:
        action = statement["Action"]
        resource = statement["Resource"]
        actions.extend(action if isinstance(action, list) else [action])
        resources.extend(resource if isinstance(resource, list) else [resource])
    if "iam:PassRole" in actions or "lambda:*" in actions:
        raise SystemExit("organization policy is too broad")
    try:
        aws(["iam", "get-role", "--role-name", ROLE_NAME], region="us-east-1")
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
                file_uri(TRUST_PATH),
                "--description",
                "GitHub Actions organization/location release role",
                "--max-session-duration",
                "3600",
            ],
            region="us-east-1",
        )
        print(f"created {ROLE_NAME}")
    else:
        aws(
            [
                "iam",
                "update-assume-role-policy",
                "--role-name",
                ROLE_NAME,
                "--policy-document",
                file_uri(TRUST_PATH),
            ],
            region="us-east-1",
        )
        print(f"updated trust {ROLE_NAME}")
    aws(
        [
            "iam",
            "put-role-policy",
            "--role-name",
            ROLE_NAME,
            "--policy-name",
            POLICY_NAME,
            "--policy-document",
            file_uri(POLICY_PATH),
        ],
        region="us-east-1",
    )
    print(f"policy attached {ROLE_NAME}")
    print(f"arn:aws:iam::{ACCOUNT}:role/{ROLE_NAME}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
