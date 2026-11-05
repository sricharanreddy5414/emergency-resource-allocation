"""Dashboard operations summary layout and duplicate Create request control."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "frontend" / "index.html"
APP = ROOT / "frontend" / "app.js"


def _dashboard_html():
    html = INDEX.read_text(encoding="utf-8")
    start = html.index('id="dashboard"')
    end = html.index('id="resources"')
    return html[start:end]


def test_dashboard_keeps_live_metric_ids():
    dashboard = _dashboard_html()
    for metric_id in (
        "totalResources",
        "allocatedResources",
        "analyticsPendingRequests",
        "analyticsReleasedRequests",
        "availableResources",
        "analyticsAllocatedRequests",
        "analyticsTotalRequests",
    ):
        assert f'id="{metric_id}"' in dashboard


def test_dashboard_has_exactly_one_create_request_button():
    dashboard = _dashboard_html()
    assert dashboard.count('id="overviewCreateRequest"') == 1
    assert dashboard.count("Create request") == 1
    assert dashboard.count("Create Request") == 0
    heading_at = dashboard.index("Operations now")
    button_at = dashboard.index('id="overviewCreateRequest"')
    empty_at = dashboard.index('id="activeOperations"')
    assert heading_at < button_at < empty_at


def test_empty_operations_does_not_inject_a_second_create_request():
    js = APP.read_text(encoding="utf-8")
    start = js.index("function renderActiveOperations()")
    end = js.index("function renderRecentOperations()")
    empty_markup = js[start:end]
    assert "No active operations" in empty_markup
    assert "Create Request" not in empty_markup
    assert "Create request" not in empty_markup
    assert "js-open-request" not in empty_markup
    assert 'id="overviewCreateRequest"' in INDEX.read_text(encoding="utf-8")
