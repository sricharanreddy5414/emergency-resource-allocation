"""Create the GitHub OIDC provider and the two deploy roles. Safe to run again."""

import json
import sys
from pathlib import Path

from aws_cli import aws
from lambda_manifest import ACCOUNT, DEPLOY_ROLE, PRODUCTION_ROLE, ROOT


PROVIDER_URL = "https://token.actions.githubusercontent.com"
PROVIDER_ARN = f"arn:aws:iam::{ACCOUNT}:oidc-provider/token.actions.githubusercontent.com"
THUMBPRINT = "6938fd4d98bab03faadb97b34396831e3780aea1"


def provider_exists():
    listed = aws(["iam", "list-open-id-connect-providers"], region="us-east-1")
    return any(item["Arn"] == PROVIDER_ARN for item in listed.get("OpenIDConnectProviderList", []))


def ensure_provider():
    if provider_exists():
        print("oidc provider exists")
        return
    aws(
        [
            "iam",
            "create-open-id-connect-provider",
            "--url",
            PROVIDER_URL,
            "--client-id-list",
            "sts.amazonaws.com",
            "--thumbprint-list",
            THUMBPRINT,
        ],
        region="us-east-1",
    )
    print("oidc provider created")


def file_uri(path):
    return "file://" + Path(path).resolve().as_posix()


def ensure_role(name, trust_path):
    json.loads(trust_path.read_text(encoding="utf-8"))
    policy_path = ROOT / "infra" / "github-deploy-policy.json"
    json.loads(policy_path.read_text(encoding="utf-8"))
    try:
        aws(["iam", "get-role", "--role-name", name], region="us-east-1")
    except SystemExit as error:
        if "NoSuchEntity" not in str(error):
            raise
        aws(
            [
                "iam",
                "create-role",
                "--role-name",
                name,
                "--assume-role-policy-document",
                file_uri(trust_path),
                "--description",
                "GitHub Actions deploy role for sricharanreddy5414/emergency-resource-allocation",
                "--max-session-duration",
                "3600",
            ],
            region="us-east-1",
        )
        print(f"created {name}")
    else:
        aws(
            [
                "iam",
                "update-assume-role-policy",
                "--role-name",
                name,
                "--policy-document",
                file_uri(trust_path),
            ],
            region="us-east-1",
        )
        print(f"updated trust {name}")
    aws(
        [
            "iam",
            "put-role-policy",
            "--role-name",
            name,
            "--policy-name",
            "ERAP-Deploy-Policy",
            "--policy-document",
            file_uri(policy_path),
        ],
        region="us-east-1",
    )
    print(f"policy attached {name}")


def main():
    ensure_provider()
    ensure_role(DEPLOY_ROLE, ROOT / "infra" / "github-deploy-trust.json")
    ensure_role(PRODUCTION_ROLE, ROOT / "infra" / "github-production-trust.json")
    print(f"arn:aws:iam::{ACCOUNT}:role/{DEPLOY_ROLE}")
    print(f"arn:aws:iam::{ACCOUNT}:role/{PRODUCTION_ROLE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
