"""Production readiness docs and the read-only smoke guard. No AWS calls."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from smoke_test_production import FORBIDDEN_ORG, assert_safe_url
from verify_production_readiness import check_docs


def test_readiness_docs_exist():
    check_docs()


def test_smoke_refuses_the_pilot_organization():
    try:
        assert_safe_url("https://example.test/" + FORBIDDEN_ORG)
    except SystemExit as error:
        assert "pilot" in str(error)
    else:
        raise AssertionError("pilot url was accepted")


def test_new_runbooks_do_not_name_the_pilot_organization():
    for relative in (
        "docs/service-dependency-map.md",
        "docs/production-release-runbook.md",
        "docs/incident-response.md",
        "docs/failure-runbook.md",
        "docs/production-support.md",
        "docs/production-readiness-checklist.md",
        "docs/production-launch-gate.md",
        "docs/production-launch-checklist.md",
        "docs/production-launch-blocker-closure.md",
        "docs/legal-launch-requirements.md",
    ):
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert FORBIDDEN_ORG not in text
    closure = (ROOT / "docs/production-launch-blocker-closure.md").read_text(encoding="utf-8")
    assert "sub_TiEekQFpwByhkU" not in closure
    assert "sub_Thke6MCZ8oT1A2" not in closure
