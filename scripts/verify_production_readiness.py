"""Read-only production readiness gate.

Documentation is checked here. AWS checks are the existing hardening,
recovery, and security posture scripts. This file does not create or
change AWS resources.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REQUIRED = {
    "docs/service-dependency-map.md": ("AMPLIFY", "API GATEWAY", "DYNAMODB"),
    "docs/production-release-runbook.md": ("python -m pytest -q", "python scripts/verify_hardening.py"),
    "docs/incident-response.md": ("SEV-1", "SEV-2", "SEV-3"),
    "docs/failure-runbook.md": ("API Gateway", "Secrets Manager"),
    "docs/production-support.md": ("correlation id", "READ"),
    "docs/production-readiness-checklist.md": ("READY", "DEFERRED"),
    "docs/production-launch-gate.md": (
        "Production launch is NOT authorized by Phase 13.",
        "NO-GO",
    ),
    "docs/production-launch-checklist.md": ("NO-GO", "BLOCKED"),
    "docs/production-launch-blocker-closure.md": (
        "NO-GO",
        "PRODUCTION PRICING DECISION REQUIRED",
        "ONBOARDING VALIDATION BLOCKED",
    ),
    "docs/saas-commercial-readiness.md": ("Production launch is NOT authorized by Phase 14.",),
    "docs/security-posture.md": ("Authentication",),
    "docs/disaster-recovery.md": ("NEVER restore directly over a production table during routine testing.",),
    "docs/observability.md": ("correlation_id",),
}


def check_docs():
    for relative, needles in REQUIRED.items():
        path = ROOT / relative
        if not path.is_file():
            raise SystemExit(f"missing {relative}")
        text = path.read_text(encoding="utf-8")
        for needle in needles:
            if needle not in text:
                raise SystemExit(f"{relative} missing {needle}")
    print("ok documentation")


def main():
    check_docs()
    sys.path.insert(0, str(ROOT / "scripts"))
    from verify_hardening import main as hardening
    from verify_recovery import main as recovery
    from verify_security_posture import main as posture

    hardening()
    recovery()
    posture()
    print("ok production readiness")
    return 0


if __name__ == "__main__":
    sys.exit(main())
