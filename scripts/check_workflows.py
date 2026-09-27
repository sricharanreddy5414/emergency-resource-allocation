"""Check the workflow files without deploying."""

import sys
from pathlib import Path

from lambda_manifest import DEPLOY_ROLE, PRODUCTION_ROLE, REPO, ROOT


REQUIRED = {
    "ci.yml": ["pull_request", "pytest", "security_scan.py", "package_lambdas.py", "check_frontend.py"],
    "deploy-backend.yml": ["environment: development", DEPLOY_ROLE, "id-token: write", "deploy_backend.py", "smoke_test.py"],
    "rollback.yml": ["workflow_dispatch", "environment: production", PRODUCTION_ROLE, "deploy_backend.py"],
    "release.yml": ["workflow_dispatch", "environment: production", PRODUCTION_ROLE, "deploy_backend.py"],
}
FORBIDDEN = ["AWS_SECRET_ACCESS_KEY", "AWS_ACCESS_KEY_ID", "aws_secret_access_key"]


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
    if "environment:development" not in deploy_trust:
        raise SystemExit("deploy trust is not limited to the development environment")
    print("workflow check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
