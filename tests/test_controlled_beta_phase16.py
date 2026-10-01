"""Phase 16 branding and runbooks. These tests do not call AWS."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOGO = "brand/ERAP-logo-4K.png"


def test_official_logo_is_the_shared_mark():
    css = (ROOT / "frontend" / "brand" / "logo.css").read_text(encoding="utf-8")
    html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    assert (ROOT / "frontend" / LOGO).is_file()
    assert "object-fit: contain" in css
    assert html.count(LOGO) >= 3
    assert "logo-icon" not in html
    for name in ("terms.html", "privacy.html", "cancellation.html", "refund.html"):
        page = (ROOT / "frontend" / "legal" / name).read_text(encoding="utf-8")
        assert "../brand/ERAP-logo-4K.png" in page
        assert "logo.css" in page


def test_onboarding_explains_the_organization_and_hides_the_id():
    html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    app = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
    assert "You are creating an organization." in html
    assert "You are creating an organization." in app
    assert "15-day trial" in html
    assert 'organization.name || "Organization"' in app
    assert 'id="locationModalClose"' in html
    assert 'id="locationNotNowBtn"' in html
    assert "function dismissLocationPrompt()" in app


def test_beta_runbooks_stay_operational_and_do_not_name_protected_records():
    support = (ROOT / "docs" / "controlled-beta-support.md").read_text(encoding="utf-8")
    runbook = (ROOT / "docs" / "controlled-beta-runbook.md").read_text(encoding="utf-8")
    for text in (support, runbook):
        assert "ORG-D13B30D99127" not in text
        assert "sub_TiEekQFpwByhkU" not in text
        assert "sub_Thke6MCZ8oT1A2" not in text
        assert "rzp_live_" not in text
        assert "webhook_secret" not in text
    assert "ORG-D878EAF5135D" in support
    assert "correlation id" in support
    assert "15 UTC days" in runbook
    assert "cancel" in runbook.lower()
    assert "manual" in runbook.lower()
