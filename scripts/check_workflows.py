"""Check the workflow files without deploying."""

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
    print("workflow check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
