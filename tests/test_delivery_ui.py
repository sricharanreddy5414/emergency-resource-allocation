"""Static checks for the delivery tracking page. No HTTP or AWS."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
APP = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
JS = (ROOT / "frontend" / "delivery.js").read_text(encoding="utf-8")


def test_delivery_page_and_nav_exist():
    assert 'id="deliveries"' in HTML
    assert 'data-section="deliveries"' in HTML
    assert "hidden" in HTML[HTML.index('data-section="deliveries"') - 80:HTML.index('data-section="deliveries"') + 80]
    assert HTML.count('data-section="deliveries"') >= 2
    assert "setDeliveryChrome" in JS
    assert "nav.hidden = !apiAvailable" in JS
    assert '<script src="delivery.js"></script>' in HTML
    assert "Create Delivery" in APP
    assert "data-create-delivery" in APP
    assert "DELIVERIES_API_URL" in APP


def test_page_does_not_claim_gps_or_signatures():
    assert "Not GPS" in JS or "not GPS" in JS
    assert "sessionStorage" not in JS
    assert "BROWSER SESSION" not in HTML


def test_frontend_uses_authenticated_api():
    assert "waitForIdToken" in JS
    assert "Authorization" in JS
    assert "Bearer " in JS
    assert 'deliverySuffix(item.delivery_id, "ready")' in JS
    assert 'deliverySuffix(ident, "handover/verify")' in JS
    assert 'deliverySuffix(selectedId, "history")' in JS
    assert "DELIVERY_API_UNDEPLOYED" in JS
    assert "missingauthenticationtoken" in JS
    assert "canWriteDeliveries" in JS
    assert "canOperateResources" in JS
    assert "deliveryBusy" in JS
    assert "encodeURIComponent" in JS
    assert "ALLOCATED" in JS


def test_counts_are_not_hardcoded_totals():
    assert 'id="deliveryCountTotal"' in HTML
    assert "payload.counts" in JS


def test_empty_and_loading_copy_present():
    assert "Loading deliveries..." in HTML
    assert "Select a delivery" in HTML
    assert "No delivery records for this organization" in JS


def test_org_switch_reloads_deliveries():
    assert "typeof loadDeliveries === \"function\"" in APP
    index = APP.index("function clearTenantData()")
    chunk = APP[index:index + 900]
    assert "loadDeliveries" in chunk


def test_invalid_api_records_are_rejected():
    assert "function isDeliveryRecord" in JS
    assert "sanitizeRows" in JS
    assert "Load the allocation before creating a delivery." in JS
