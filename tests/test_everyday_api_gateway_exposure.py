"""Unit checks for everyday API Gateway exposure script constants."""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_module():
    path = ROOT / "scripts" / "expose_everyday_resource_routes.py"
    spec = importlib.util.spec_from_file_location("expose_everyday_resource_routes", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_route_segments_are_exactly_phase3_paths():
    module = load_module()
    paths = [module.PARENT_PATH + "/" + "/".join(segments) for segments in module.ROUTE_SEGMENTS]
    assert paths == [
        "/allocate/resources/reserve",
        "/allocate/resources/reservation-release",
        "/allocate/resources/everyday",
        "/allocate/resources/everyday/return",
    ]


def test_live_uri_targets_get_resources_alias():
    module = load_module()
    assert module.FUNCTION == "get-resources"
    assert ":get-resources:live/invocations" in module.LIVE_URI
    assert module.AUTHORIZER_ID == "y0hzhr"
