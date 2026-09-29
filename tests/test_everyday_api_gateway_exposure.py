"""Unit checks for everyday API Gateway exposure script constants."""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "scripts")]


def load_module():
    path = ROOT / "scripts" / "expose_everyday_resource_routes.py"
    spec = importlib.util.spec_from_file_location("expose_everyday_resource_routes", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_route_segments_are_exactly_phase3_paths():
    module = load_module()
    paths = [module.PARENT_PATH + "/" + "/".join(segments) for segments in module.ROUTE_SEGMENTS]
    assert "/allocate/resources/reserve" in paths
    assert "/allocate/resources/everyday/return" in paths
    assert "/allocate/resources/maintenance" in paths
    assert "/allocate/resources/maintenance/complete" in paths
    assert "/allocate/resources/damage" in paths
    assert "/allocate/resources/damage/recover" in paths
    assert "/allocate/resources/retire" in paths
    assert "/allocate/resources/assign" in paths
    assert "/allocate/resources/unassign" in paths
    assert "/allocate/resources/in-use" in paths
    assert len(paths) == len(set(paths))


def test_live_uri_targets_get_resources_alias():
    module = load_module()
    assert module.FUNCTION == "get-resources"
    assert ":get-resources:live/invocations" in module.LIVE_URI
    assert module.AUTHORIZER_ID == "y0hzhr"
