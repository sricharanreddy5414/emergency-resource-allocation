"""First and later erap-reservation-expiry releases. These tests do not call AWS."""

import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import release_reservation_expiry as release
import rollback_reservation_expiry as rollback
from lambda_manifest import package_map


MISSING_ALIAS = (
    "An error occurred (ResourceNotFoundException) when calling the GetAlias operation: "
    "Cannot find alias arn:aws:lambda:eu-north-1:481838970142:function:erap-reservation-expiry:live"
)


def _zip(path):
    names = package_map()["erap-reservation-expiry"]
    with zipfile.ZipFile(path, "w") as archive:
        for name in names:
            archive.writestr(name, b"")
    return path


def _config(code_sha="before", memory=256, runtime="python3.14"):
    return {
        "FunctionName": "erap-reservation-expiry",
        "Runtime": runtime,
        "Handler": "handler.lambda_handler",
        "Role": release.APPROVED_ROLE,
        "MemorySize": memory,
        "Timeout": 60,
        "Environment": None,
        "VpcConfig": {},
        "Layers": [],
        "Architectures": ["x86_64"],
        "CodeSha256": code_sha,
        "LastUpdateStatus": "Successful",
    }


def _aws(state):
    def aws(args, region=None):
        del region
        state["calls"].append(list(args))
        if args[:2] == ["lambda", "get-alias"]:
            if state.get("alias_error") and state.get("alias") is None:
                raise SystemExit(state["alias_error"])
            if state.get("alias") is None:
                raise SystemExit(MISSING_ALIAS)
            return {"FunctionVersion": state["alias"], "Name": "live"}
        if args[:2] == ["lambda", "get-function-configuration"]:
            qualifier = args[args.index("--qualifier") + 1] if "--qualifier" in args else None
            if state.get("missing_version") and qualifier == state.get("requested"):
                raise SystemExit("version not found")
            memory = 256
            if state.get("baseline_drift") and qualifier is None and not state.get("updated"):
                memory = 128
            elif "drift" in state and state["drift"] == qualifier:
                memory = 128
            code_sha = "before"
            if qualifier is None:
                code_sha = "latest"
            elif qualifier == state.get("published"):
                code_sha = "published-other" if state.get("code_mismatch") else "latest"
            config = _config(code_sha, memory)
            if state.get("foreign_function") and qualifier == state.get("requested"):
                config["FunctionName"] = "erap-exchange"
            return config
        if args[:2] == ["lambda", "update-function-code"]:
            state["updated"] = True
            return {}
        if args[:2] == ["lambda", "publish-version"]:
            state["published"] = state.get("next_version", "1")
            return {"Version": state["published"]}
        if args[:2] == ["lambda", "create-alias"]:
            state["alias"] = args[args.index("--function-version") + 1]
            return {}
        if args[:2] == ["lambda", "update-alias"]:
            state["alias"] = args[args.index("--function-version") + 1]
            return {}
        raise AssertionError(args)

    return aws


def _commands(state):
    return [call[1] for call in state["calls"]]


def test_first_release_creates_live_from_the_published_version(tmp_path):
    state = {"calls": [], "alias": None, "next_version": "1"}
    report = release.publish_reservation_expiry(_aws(state), _zip(tmp_path / "fn.zip"), "a" * 40)
    commands = _commands(state)
    assert commands.index("update-function-code") < commands.index("publish-version")
    assert commands.index("publish-version") < commands.index("create-alias")
    assert "update-alias" not in commands
    assert "update-function-configuration" not in commands
    assert report["previous_version"] is None
    assert report["published_version"] == "1"
    assert report["alias_version"] == "1"
    assert report["first_release"] is True
    assert state["alias"] == "1"
    assert all(call[3] == "erap-reservation-expiry" for call in state["calls"])


def test_later_release_moves_the_existing_alias(tmp_path):
    state = {"calls": [], "alias": "1", "next_version": "2"}
    report = release.publish_reservation_expiry(_aws(state), _zip(tmp_path / "fn.zip"), "b" * 40)
    commands = _commands(state)
    assert "create-alias" not in commands
    assert commands.index("publish-version") < commands.index("update-alias")
    assert report["previous_version"] == "1"
    assert report["published_version"] == "2"
    assert report["alias_version"] == "2"
    assert report["first_release"] is False
    assert state["alias"] == "2"


def test_unexpected_alias_error_fails_closed(tmp_path):
    state = {"calls": [], "alias": None, "alias_error": "An error occurred (AccessDeniedException) when calling the GetAlias operation"}
    with pytest.raises(SystemExit, match="AccessDeniedException"):
        release.publish_reservation_expiry(_aws(state), _zip(tmp_path / "fn.zip"), "c" * 40)
    commands = _commands(state)
    assert commands == ["get-alias"]
    assert state["alias"] is None


def test_unrelated_not_found_does_not_start_a_first_release(tmp_path):
    state = {
        "calls": [],
        "alias": None,
        "alias_error": "An error occurred (ResourceNotFoundException) when calling the GetFunction operation",
    }
    with pytest.raises(SystemExit, match="GetFunction"):
        release.publish_reservation_expiry(_aws(state), _zip(tmp_path / "fn.zip"), "d" * 40)
    assert _commands(state) == ["get-alias"]


def test_first_release_stops_when_the_baseline_drifted(tmp_path):
    state = {"calls": [], "alias": None, "baseline_drift": True}
    with pytest.raises(SystemExit, match="drifted"):
        release.publish_reservation_expiry(_aws(state), _zip(tmp_path / "fn.zip"), "e" * 40)
    commands = _commands(state)
    assert "update-function-code" not in commands
    assert "publish-version" not in commands
    assert "create-alias" not in commands
    assert state["alias"] is None


def test_later_release_stops_when_configuration_drifts(tmp_path):
    state = {"calls": [], "alias": "1", "next_version": "2", "drift": None}
    with pytest.raises(SystemExit, match="drifted"):
        release.publish_reservation_expiry(_aws(state), _zip(tmp_path / "fn.zip"), "f" * 40)
    assert "publish-version" not in _commands(state)
    assert "update-alias" not in _commands(state)
    assert "create-alias" not in _commands(state)
    assert state["alias"] == "1"


def test_first_release_does_not_create_alias_when_published_snapshot_drifts(tmp_path):
    state = {"calls": [], "alias": None, "next_version": "1", "drift": "1"}
    with pytest.raises(SystemExit, match="does not match"):
        release.publish_reservation_expiry(_aws(state), _zip(tmp_path / "fn.zip"), "a" * 40)
    assert "create-alias" not in _commands(state)
    assert state["alias"] is None


def test_later_release_does_not_move_alias_when_published_snapshot_drifts(tmp_path):
    state = {"calls": [], "alias": "1", "next_version": "2", "drift": "2"}
    with pytest.raises(SystemExit, match="does not match"):
        release.publish_reservation_expiry(_aws(state), _zip(tmp_path / "fn.zip"), "b" * 40)
    assert "update-alias" not in _commands(state)
    assert state["alias"] == "1"


def test_release_does_not_promote_when_code_sha_differs(tmp_path):
    state = {"calls": [], "alias": "1", "next_version": "2", "code_mismatch": True}
    with pytest.raises(SystemExit, match="packaged code"):
        release.publish_reservation_expiry(_aws(state), _zip(tmp_path / "fn.zip"), "b" * 40)
    assert state["alias"] == "1"
    assert "update-alias" not in _commands(state)


def test_rollback_moves_only_an_existing_numeric_version():
    state = {"calls": [], "alias": "2"}
    report = rollback.rollback_reservation_expiry(_aws(state), "1")
    commands = _commands(state)
    assert "update-function-code" not in commands
    assert "publish-version" not in commands
    assert "create-alias" not in commands
    assert "update-function-configuration" not in commands
    assert report["alias_version"] == "1"
    assert report["published"] is False
    assert state["alias"] == "1"
    assert all(call[3] == "erap-reservation-expiry" for call in state["calls"])


@pytest.mark.parametrize("version", ["", "0", "59052589bd0eb6ed3fbf9e1340cb3f71e9d57314", "$LATEST"])
def test_rollback_rejects_non_versions(version):
    state = {"calls": [], "alias": "1"}
    with pytest.raises(SystemExit, match="version"):
        rollback.rollback_reservation_expiry(_aws(state), version)
    assert state["calls"] == []


def test_release_script_targets_only_the_expiry_function():
    source = (ROOT / "scripts" / "release_reservation_expiry.py").read_text(encoding="utf-8")
    assert 'FUNCTION = "erap-reservation-expiry"' in source
    assert 'ALIAS' in source
    assert "create-alias" in source
    assert "update-alias" in source
    assert "update-function-configuration" not in source
    assert "put-role-policy" not in source
    assert "scheduler" not in source
    assert "dynamodb" not in source
    workflow = (ROOT / ".github" / "workflows" / "release-reservation-expiry.yml").read_text(encoding="utf-8")
    assert "workflow_dispatch" in workflow
    assert "environment: production" in workflow
    assert "refs/heads/main" in workflow
    assert "release_provenance.py" in workflow
    assert "release_reservation_expiry.py" in workflow
    assert "push:" not in workflow
    assert "get-alias" not in workflow
