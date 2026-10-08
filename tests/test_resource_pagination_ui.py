"""Resource inventory pagination stays on the client and treats tokens as opaque."""

import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def node_executable():
    found = shutil.which("node")
    if found:
        return found
    portable = Path(r"C:\Users\sri charan reddy\AppData\Local\Temp\erap-node\node-v22.20.0-win-x64\node.exe")
    if portable.is_file():
        return str(portable)
    return None


def pagination_block():
    source = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
    start = source.index("/* RESOURCE_PAGE_START */")
    end = source.index("/* RESOURCE_PAGE_END */")
    return source, source[start:end]


def test_resource_pagination_ui():
    node = node_executable()
    assert node, "Node is required to execute the resource pagination test"
    result = subprocess.run(
        [node, str(ROOT / "tests" / "resource_pagination_ui.mjs")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_page_token_stays_opaque_and_in_memory():
    source, block = pagination_block()
    for banned in ("localStorage", "sessionStorage", "atob(", "btoa(", "console.log"):
        assert banned not in block
    assert 'params.set("page_token", pageToken)' in block
    assert "selectedOrganizationId()" in block
    assert "JSON.parse(resourceNextPageToken" not in block
    html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    assert 'id="resourceLoadMore"' in html
    assert 'id="overviewResourceLoadMore"' in html
    cleared = source[source.index("function clearTenantData("):source.index("function setOperationalActionsEnabled(")]
    assert "resetResourcePagination();" in cleared
