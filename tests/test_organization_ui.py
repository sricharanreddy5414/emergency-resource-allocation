"""Organization and location dropdown wiring. These tests do not call AWS."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
APP = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")


def test_dropdowns_and_onboarding_exist():
    assert 'id="organizationSwitcher"' in HTML
    assert 'id="locationSwitcher"' in HTML
    assert 'id="organizationOnboardingBtn"' in HTML
    assert 'id="organizationModal"' in HTML
    assert 'id="createOrganizationBtn"' in HTML
    assert 'id="createLocationBtn"' in HTML
    assert 'id="profileOrganization"' in HTML
    assert 'id="profileRole"' in HTML


def test_empty_membership_opens_onboarding_and_clears_switcher():
    assert "applyOrganizationContext([])" in APP
    assert "openOrganizationOnboarding()" in APP
    assert 'option.textContent = "No organization"' in APP
    assert "No organization selected" in APP
    assert 'currentUser.organization?.name ||\n            "—"' in APP or 'currentUser.organization?.role ||' in APP


def test_membership_load_failure_does_not_invent_organizations():
    assert "return Array.isArray(data.organizations) ? data.organizations : []" in APP
    assert "sessionStorage" in APP
    assert "fake organization" not in APP.lower()
    assert "hardcoded organization" not in APP.lower()
    assert "ORGANIZATION_API_URL" in APP
    assert "LOCATIONS_API_URL" in APP


def test_create_organization_uses_authenticated_post():
    assert 'method: "POST"' in APP
    assert "client_request_id: organizationRequestId" in APP
    assert 'created.role || "OWNER"' in APP
    assert "organizationOnboardingRequired" in APP


def test_location_create_sends_selected_organization():
    assert "organization_id: selectedOrganizationId()" in APP
    assert "Unable to load locations from this page origin." in APP
    assert "Unable to create a location from this page origin." in APP
