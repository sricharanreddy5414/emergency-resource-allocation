"""Register Resource type dropdown distinguishes catalog load states."""

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


def test_resource_type_dropdown_ui():
    node = node_executable()
    assert node, "Node is required to execute the resource type dropdown UI test"
    result = subprocess.run(
        [node, str(ROOT / "tests" / "resource_type_dropdown_ui.mjs")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "resource type dropdown ui passed" in result.stdout


def test_resource_type_loader_does_not_treat_missing_types_as_empty_success():
    app = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
    catalog = app[app.index("function parseCatalogTypes(") : app.index("async function loadCatalog(")]
    loader = app[app.index("async function loadCatalog(") : app.index("async function loadResourceTypes(")]
    resource_loader = app[app.index("async function loadResourceTypes(") : app.index("async function loadRequestTypes(")]
    assert "Array.isArray(data.types)" in catalog
    assert "data.types || []" not in catalog
    assert "data.types || []" not in loader
    assert "throw catalogLoadError(\"http\")" in loader
    assert "throw catalogLoadError(\"network\")" in loader
    assert "waitForIdToken()" in loader
    assert "page_token=" in loader
    assert "resourceTypesLoadFailed = true" in resource_loader
    assert "resourceTypesLoading = true" in resource_loader
    assert "await loadCatalog(RESOURCE_TYPES_API_URL)" in resource_loader
    prepare = app[app.index("function prepareNewResourceForm(") : app.index("function openRegisterModal(")]
    assert "loadResourceTypes()" in prepare
    assert "resourceTypesLoadFailed" in prepare
    assert "resourceTypes || []).length" in prepare or "!(resourceTypes || []).length" in prepare
    switch_location = app[app.index("async function switchLocation(") : app.index("async function createLocation(")]
    assert "await loadResourceTypes()" in switch_location
    assert "clearTenantData();" in switch_location
    fill = app[app.index("function fillCatalogSelects(") : app.index("function showSelectedTypeAttributes(")]
    assert 'item.status || "ACTIVE"' in fill
    assert "resource_type_id: type" in app[app.index("async function registerResource(") : app.index("async function registerResource(") + 900]


def test_resource_type_management_form_remains():
    page = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    app = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
    assert 'id="saveResourceTypeBtn"' in page
    assert 'id="resourceTypeName"' in page
    assert "async function saveResourceType(" in app
    assert "await loadResourceTypes();" in app[app.index("async function saveResourceType(") :]
    save = app[app.index("async function saveResourceType(") : app.index("async function saveRequestType(")]
    assert "waitForIdToken()" in save
    assert '$(id).value = ""' in save
    assert 'id="resourceTypeCatalogHint"' in page
