"""Phase 18 beta operations docs. These tests do not call AWS."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_beta_feedback_process_is_documented_without_secrets():
    text = (ROOT / "docs" / "controlled-beta-feedback.md").read_text(encoding="utf-8")
    for label in ("P0", "P1", "P2", "P3", "P4"):
        assert label in text
    assert "ORG-D878EAF5135D" in text
    assert "password" in text.lower()
    assert "Correlation ID" in text
    assert "ORG-D13B30D99127" not in text
    assert "sub_TiEekQFpwByhkU" not in text
    assert "sub_Thke6MCZ8oT1A2" not in text
    assert "rzp_live_" not in text
    assert "webhook_secret" not in text
    assert "src/shared/access.py" in text


def test_billing_architecture_matches_production_mode():
    text = (ROOT / "docs" / "billing-architecture.md").read_text(encoding="utf-8")
    assert "Razorpay live mode is not enabled" not in text
    assert "Live Razorpay is not enabled" not in text
    assert "erap/billing/razorpay/production" in text
    assert "do not fall back to the test secret" in text


def test_support_and_runbook_point_at_the_feedback_process():
    support = (ROOT / "docs" / "controlled-beta-support.md").read_text(encoding="utf-8")
    runbook = (ROOT / "docs" / "controlled-beta-runbook.md").read_text(encoding="utf-8")
    assert "docs/controlled-beta-feedback.md" in support
    assert "docs/controlled-beta-feedback.md" in runbook
