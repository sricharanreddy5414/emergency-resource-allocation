"""Gateway route source of truth for approve/reject/complete. Does not call AWS."""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_module():
    path = ROOT / "scripts" / "expose_request_lifecycle_routes.py"
    spec = importlib.util.spec_from_file_location("expose_request_lifecycle_routes", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_request_lifecycle_routes_match_cancel_pattern():
    module = load_module()
    paths = module.planned_routes()
    assert paths == [
        "/requests/cancel",
        "/requests/approve",
        "/requests/reject",
        "/requests/complete",
    ]
    assert module.FUNCTION == "create-request"
    assert module.AUTHORIZER_ID == "y0hzhr"
    assert ":create-request:live/invocations" in module.LIVE_URI
    assert module.PARENT_PATH == "/requests"


def test_request_lifecycle_script_does_not_weaken_auth():
    source = (ROOT / "scripts" / "expose_request_lifecycle_routes.py").read_text(encoding="utf-8")
    assert "COGNITO_USER_POOLS" in source
    assert 'OPTIONS", "NONE"' in source or '"OPTIONS", "NONE"' in source
    assert "AWS_PROXY" in source
    assert "create-request:live" in source
    assert source.count("ROUTE_SEGMENTS") >= 1
    assert "/requests/approve" in source
    assert "This script is not invoked by CI" in source


def test_handler_dispatches_the_same_paths():
    handler = (ROOT / "src" / "request" / "handler.py").read_text(encoding="utf-8")
    for suffix in ("/requests/cancel", "/requests/approve", "/requests/reject", "/requests/complete"):
        assert suffix in handler
