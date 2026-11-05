"""Dashboard operations summary layout and duplicate Create request control."""

import re
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "frontend" / "index.html"
APP = ROOT / "frontend" / "app.js"
STYLE = ROOT / "frontend" / "style.css"
DESIGN = ROOT / "frontend" / "design-system.css"


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
    assert "withoutCreateRequestButtons" in empty_markup
    assert 'id="overviewCreateRequest"' in INDEX.read_text(encoding="utf-8")


def _css_blocks(css, selector):
    return re.findall(rf"{re.escape(selector)}\s*\{{([^}}]*)\}}", css)


def test_operational_context_is_not_truncated():
    html = INDEX.read_text(encoding="utf-8")
    assert 'id="operationalContext"' in html
    style = STYLE.read_text(encoding="utf-8")
    design = DESIGN.read_text(encoding="utf-8")
    blocks = _css_blocks(style, ".context-line") + _css_blocks(design, ".context-line")
    assert blocks, "expected .context-line rules"
    combined = "\n".join(blocks)
    assert "ellipsis" not in combined
    assert "nowrap" not in combined
    assert "line-clamp" not in combined
    assert "overflow: hidden" not in re.sub(r"\s+", " ", combined)
    assert "white-space: normal" in combined
    assert "#operationalContext" in html or 'id="operationalContext"' in html


def node_executable():
    found = shutil.which("node")
    if found:
        return found
    portable = Path(r"C:\Users\sri charan reddy\AppData\Local\Temp\erap-node\node-v22.20.0-win-x64\node.exe")
    if portable.is_file():
        return str(portable)
    return None


def test_empty_operations_runtime_has_one_create_request_control():
    node = node_executable()
    assert node, "Node is required to execute the dashboard create-request UI test"
    result = subprocess.run(
        [node, str(ROOT / "tests" / "dashboard_create_request_ui.mjs")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "dashboard create request ui passed" in result.stdout
