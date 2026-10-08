"""Check the workflow files without deploying."""

import json
import sys
from pathlib import Path

from lambda_manifest import DEPLOY_ROLE, PRODUCTION_ROLE, REPO, ROOT


REQUIRED = {
    "ci.yml": ["pull_request", "pytest", "security_scan.py", "package_lambdas.py", "check_frontend.py"],
    "deploy-backend.yml": [
        "environment: development",
        "pytest",
        "security_scan.py",
        "package_lambdas.py",
        "does not publish alias live",
    ],
    "rollback.yml": ["workflow_dispatch", "environment: production", PRODUCTION_ROLE, "deploy_backend.py", "release_provenance.py", "refs/heads/main"],
    "release.yml": ["workflow_dispatch", "environment: production", PRODUCTION_ROLE, "deploy_backend.py", "release_provenance.py", "refs/heads/main"],
    "release-exchange.yml": [
        "workflow_dispatch",
        "environment: production",
        PRODUCTION_ROLE,
        "release_exchange.py",
        "release_provenance.py",
        "refs/heads/main",
        "security_scan.py",
    ],
    "rollback-exchange.yml": [
        "workflow_dispatch",
        "environment: production",
        PRODUCTION_ROLE,
        "rollback_exchange.py",
        "refs/heads/main",
        "security_scan.py",
    ],
    "release-resource.yml": [
        "workflow_dispatch",
        "environment: production",
        PRODUCTION_ROLE,
        "release_resource.py",
        "release_provenance.py",
        "refs/heads/main",
        "security_scan.py",
        "get-resources",
    ],
    "rollback-resource.yml": [
        "workflow_dispatch",
        "environment: production",
        PRODUCTION_ROLE,
        "rollback_resource.py",
        "refs/heads/main",
        "security_scan.py",
        "get-resources",
    ],
    "release-reservation-expiry.yml": [
        "workflow_dispatch",
        "environment: production",
        PRODUCTION_ROLE,
        "release_reservation_expiry.py",
        "release_provenance.py",
        "refs/heads/main",
        "security_scan.py",
        "erap-reservation-expiry",
    ],
    "rollback-reservation-expiry.yml": [
        "workflow_dispatch",
        "environment: production",
        PRODUCTION_ROLE,
        "rollback_reservation_expiry.py",
        "refs/heads/main",
        "security_scan.py",
        "erap-reservation-expiry",
    ],
}
FORBIDDEN = ["AWS_SECRET_ACCESS_KEY", "AWS_ACCESS_KEY_ID", "aws_secret_access_key"]


def _exchange_release_is_unsafe(text):
    forbidden = (
        "deploy_exchange.py",
        "deploy_backend.py",
        "update-function-configuration",
        "put-role-policy",
        "erap-exchange-expiry",
        "erap-notifications",
    )
    return any(item in text for item in forbidden)


def _exchange_rollback_is_unsafe(text):
    forbidden = (
        "deploy_exchange.py",
        "deploy_backend.py",
        "release_exchange.py",
        "update-function-code",
        "publish-version",
        "update-function-configuration",
        "put-role-policy",
        "erap-exchange-expiry",
        "erap-notifications",
        "inputs.commit",
    )
    return any(item in text for item in forbidden)


_OTHER_FUNCTIONS = (
    "create-request",
    "emergency-resource-allocation",
    "emergency-resource-auto-release",
    "erap-catalog",
    "erap-public-resources",
    "erap-locations",
    "erap-create-organization",
    "erap-get-organization",
    "erap-billing",
    "erap-exchange",
    "erap-notifications",
)


def _resource_release_is_unsafe(text):
    forbidden = (
        "deploy_backend.py",
        "deploy_exchange.py",
        "update-function-configuration",
        "put-role-policy",
        "push:",
        *_OTHER_FUNCTIONS,
    )
    return any(item in text for item in forbidden)


def _reservation_expiry_release_is_unsafe(text):
    forbidden = (
        "deploy_backend.py",
        "deploy_exchange.py",
        "release_resource.py",
        "release_exchange.py",
        "ensure_reservation_expiry.py",
        "update-function-configuration",
        "put-role-policy",
        "push:",
        "get-resources",
        *_OTHER_FUNCTIONS,
    )
    return any(item in text for item in forbidden)


def _reservation_expiry_rollback_is_unsafe(text):
    forbidden = (
        "deploy_backend.py",
        "deploy_exchange.py",
        "release_reservation_expiry.py",
        "release_resource.py",
        "update-function-code",
        "publish-version",
        "update-function-configuration",
        "put-role-policy",
        "ensure_reservation_expiry.py",
        "inputs.commit",
        "push:",
        "get-resources",
        *_OTHER_FUNCTIONS,
    )
    return any(item in text for item in forbidden)


def _resource_rollback_is_unsafe(text):
    forbidden = (
        "deploy_backend.py",
        "deploy_exchange.py",
        "release_resource.py",
        "update-function-code",
        "publish-version",
        "update-function-configuration",
        "put-role-policy",
        "inputs.commit",
        "push:",
        *_OTHER_FUNCTIONS,
    )
    return any(item in text for item in forbidden)


def _production_trust_refs():
    trust = json.loads((ROOT / "infra" / "github-production-trust.json").read_text(encoding="utf-8"))
    statement = trust["Statement"][0]
    condition = statement["Condition"]["StringEquals"]
    return condition["token.actions.githubusercontent.com:job_workflow_ref"]


def main():
    try:
        import yaml
    except ImportError:
        yaml = None
    folder = ROOT / ".github" / "workflows"
    for name, needles in REQUIRED.items():
        path = folder / name
        if not path.is_file():
            raise SystemExit(f"Missing workflow {name}")
        text = path.read_text(encoding="utf-8")
        for needle in needles:
            if needle not in text:
                raise SystemExit(f"{name} is missing {needle}")
        for secret in FORBIDDEN:
            if secret in text:
                raise SystemExit(f"{name} contains {secret}")
        if name == "ci.yml" and "id-token: write" in text:
            raise SystemExit("CI must not request AWS credentials")
        if name == "deploy-backend.yml" and ("deploy_backend.py" in text or DEPLOY_ROLE in text or "id-token: write" in text):
            raise SystemExit("development workflow must not publish alias live")
        if name in {"release.yml", "rollback.yml"} and "release_exchange.py" in text:
            raise SystemExit(f"{name} must not publish Exchange")
        if name == "release-exchange.yml" and _exchange_release_is_unsafe(text):
            raise SystemExit("exchange release must publish only erap-exchange")
        if name == "rollback-exchange.yml" and _exchange_rollback_is_unsafe(text):
            raise SystemExit("exchange rollback must move only an existing erap-exchange version")
        if name == "release-resource.yml" and _resource_release_is_unsafe(text):
            raise SystemExit("resource release must publish only get-resources")
        if name == "rollback-resource.yml" and _resource_rollback_is_unsafe(text):
            raise SystemExit("resource rollback must move only an existing get-resources version")
        if name == "release-reservation-expiry.yml" and _reservation_expiry_release_is_unsafe(text):
            raise SystemExit("reservation expiry release must publish only erap-reservation-expiry")
        if name == "rollback-reservation-expiry.yml" and _reservation_expiry_rollback_is_unsafe(text):
            raise SystemExit("reservation expiry rollback must move only an existing erap-reservation-expiry version")
        if name in {"release.yml", "rollback.yml", "release-exchange.yml", "rollback-exchange.yml"} and "release_resource.py" in text:
            raise SystemExit(f"{name} must not publish get-resources")
        if yaml is not None:
            loaded = yaml.safe_load(text)
            if not isinstance(loaded, dict) or "jobs" not in loaded:
                raise SystemExit(f"{name} has no jobs")
        print(f"ok {name}")
    for name in ("github-deploy-trust.json", "github-production-trust.json"):
        trust = (ROOT / "infra" / name).read_text(encoding="utf-8")
        if REPO not in trust or "repo:*" in trust or '"*"' in trust:
            raise SystemExit(f"{name} is not limited to this repository")
    deploy_trust = (ROOT / "infra" / "github-deploy-trust.json").read_text(encoding="utf-8")
    if "emergency-resource-allocation@1374188159:environment:development" not in deploy_trust:
        raise SystemExit("deploy trust is not limited to the development environment")
    expected_refs = [
        f"{REPO}/.github/workflows/{name}@refs/heads/main"
        for name in (
            "release.yml",
            "rollback.yml",
            "release-exchange.yml",
            "rollback-exchange.yml",
            "release-resource.yml",
            "rollback-resource.yml",
            "release-reservation-expiry.yml",
            "rollback-reservation-expiry.yml",
        )
    ]
    if _production_trust_refs() != expected_refs:
        raise SystemExit("production trust workflow refs are not the approved main workflows")
    policy = json.loads((ROOT / "infra" / "github-production-resource-policy.json").read_text(encoding="utf-8"))
    allowed_actions = {
        "lambda:GetAlias",
        "lambda:GetFunctionConfiguration",
        "lambda:UpdateFunctionCode",
        "lambda:PublishVersion",
        "lambda:UpdateAlias",
    }
    allowed_resources = {
        "arn:aws:lambda:eu-north-1:481838970142:function:get-resources",
        "arn:aws:lambda:eu-north-1:481838970142:function:get-resources:*",
    }
    for statement in policy["Statement"]:
        actions = statement["Action"]
        resources = statement["Resource"]
        action_set = set(actions if isinstance(actions, list) else [actions])
        resource_set = set(resources if isinstance(resources, list) else [resources])
        if action_set != allowed_actions or resource_set != allowed_resources:
            raise SystemExit("resource production policy is not limited to get-resources")
    expiry_policy = json.loads(
        (ROOT / "infra" / "github-production-reservation-expiry-policy.json").read_text(encoding="utf-8")
    )
    expiry_resources = {
        "arn:aws:lambda:eu-north-1:481838970142:function:erap-reservation-expiry",
        "arn:aws:lambda:eu-north-1:481838970142:function:erap-reservation-expiry:*",
    }
    expiry_actions = allowed_actions | {"lambda:CreateAlias"}
    for statement in expiry_policy["Statement"]:
        actions = statement["Action"]
        resources = statement["Resource"]
        action_set = set(actions if isinstance(actions, list) else [actions])
        resource_set = set(resources if isinstance(resources, list) else [resources])
        if action_set != expiry_actions or resource_set != expiry_resources:
            raise SystemExit("reservation expiry production policy is not limited to erap-reservation-expiry")
        if "lambda:CreateAlias" not in action_set:
            raise SystemExit("reservation expiry production policy must allow CreateAlias")
    print("workflow check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
