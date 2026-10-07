"""Controlled Exchange release and rollback. These tests do not call AWS."""

import json
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import release_exchange
import rollback_exchange
from lambda_manifest import package_map


def _zip(path):
    names = package_map()["erap-exchange"]
    with zipfile.ZipFile(path, "w") as archive:
        for name in names:
            archive.writestr(name, b"")
    return path


def _config(code_sha="old", memory=256):
    return {
        "Runtime": "python3.14",
        "Handler": "handler.lambda_handler",
        "Role": "arn:aws:iam::1:role/ERAP-Exchange-Lambda-Role",
        "MemorySize": memory,
        "Timeout": 15,
        "Environment": None,
        "VpcConfig": {},
        "Layers": [],
        "Architectures": ["x86_64"],
        "CodeSha256": code_sha,
        "LastUpdateStatus": "Successful",
        "Description": "commit=b03a37a5b28db442d31fee590def8ee168a3d4f9",
    }


def _aws(state):
    def aws(args, region=None):
        del region
        state["calls"].append(list(args))
        if args[:2] == ["lambda", "get-alias"]:
            return {"FunctionVersion": state["alias"]}
        if args[:2] == ["lambda", "get-function-configuration"]:
            qualifier = args[args.index("--qualifier") + 1] if "--qualifier" in args else None
            if state.get("missing_version") and qualifier == state.get("requested"):
                raise SystemExit("version not found")
            memory = 128 if "drift" in state and state["drift"] == qualifier else 256
            code_sha = "old"
            if qualifier is None or qualifier == state.get("published"):
                code_sha = "new" if not state.get("code_mismatch") else "other"
            return _config(code_sha, memory)
        if args[:2] == ["lambda", "update-function-code"]:
            return {}
        if args[:2] == ["lambda", "publish-version"]:
            state["published"] = "18"
            return {"Version": "18"}
        if args[:2] == ["lambda", "update-alias"]:
            state["alias"] = args[args.index("--function-version") + 1]
            return {}
        raise AssertionError(args)

    return aws


def test_exchange_release_publishes_one_version_and_preserves_configuration(tmp_path):
    state = {"calls": [], "alias": "17"}
    report = release_exchange.publish_exchange(_aws(state), _zip(tmp_path / "erap-exchange.zip"), "d" * 40)
    commands = [call[1] for call in state["calls"]]
    assert commands.index("update-function-code") < commands.index("publish-version")
    assert commands.index("publish-version") < commands.index("update-alias")
    assert "update-function-configuration" not in commands
    assert report["previous_version"] == "17"
    assert report["published_version"] == "18"
    assert report["alias_version"] == "18"
    assert report["configuration_preserved"] is True
    assert all(call[3] == "erap-exchange" for call in state["calls"] if call[1].startswith("get-") or call[1].startswith("update-") or call[1] == "publish-version")


def test_exchange_release_stops_before_publish_when_configuration_drifts(tmp_path):
    state = {"calls": [], "alias": "17", "drift": None}
    with pytest.raises(SystemExit, match="drifted"):
        release_exchange.publish_exchange(_aws(state), _zip(tmp_path / "erap-exchange.zip"), "d" * 40)
    assert ["lambda", "publish-version"] not in [call[:2] for call in state["calls"]]
    assert ["lambda", "update-alias"] not in [call[:2] for call in state["calls"]]


def test_exchange_release_does_not_move_alias_when_snapshot_differs(tmp_path):
    state = {"calls": [], "alias": "17", "drift": "18"}
    with pytest.raises(SystemExit, match="does not match"):
        release_exchange.publish_exchange(_aws(state), _zip(tmp_path / "erap-exchange.zip"), "d" * 40)
    assert state["alias"] == "17"
    assert ["lambda", "update-alias"] not in [call[:2] for call in state["calls"]]


def test_exchange_release_refuses_outside_production(monkeypatch):
    monkeypatch.delenv("ERAP_ENVIRONMENT", raising=False)
    with pytest.raises(SystemExit, match="alias live"):
        release_exchange.main()


def test_exchange_rollback_moves_only_an_existing_numeric_version():
    state = {"calls": [], "alias": "18"}
    report = rollback_exchange.rollback_exchange(_aws(state), "17")
    commands = [call[1] for call in state["calls"]]
    assert "update-function-code" not in commands
    assert "publish-version" not in commands
    assert "update-function-configuration" not in commands
    assert report["alias_version"] == "17"
    assert report["published"] is False
    assert state["alias"] == "17"


@pytest.mark.parametrize("version", ["", "0", "d364d9a2742adb9b51a2509f33cb0f1581ec0a4a", "latest"])
def test_exchange_rollback_rejects_non_versions(version):
    state = {"calls": [], "alias": "18"}
    with pytest.raises(SystemExit, match="version"):
        rollback_exchange.rollback_exchange(_aws(state), version)
    assert state["calls"] == []


def test_exchange_rollback_does_not_move_alias_when_version_is_missing():
    state = {"calls": [], "alias": "18", "missing_version": True, "requested": "17"}
    with pytest.raises(SystemExit, match="not found"):
        rollback_exchange.rollback_exchange(_aws(state), "17")
    assert ["lambda", "update-alias"] not in [call[:2] for call in state["calls"]]


def test_exchange_workflows_are_manual_production_and_scoped():
    release = (ROOT / ".github" / "workflows" / "release-exchange.yml").read_text(encoding="utf-8")
    rollback = (ROOT / ".github" / "workflows" / "rollback-exchange.yml").read_text(encoding="utf-8")
    general = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "workflow_dispatch" in release
    assert "environment: production" in release
    assert "refs/heads/main" in release
    assert "release_provenance.py" in release
    assert "release_exchange.py" in release
    assert "deploy_exchange.py" not in release
    assert "erap-exchange-expiry" not in release
    assert "erap-notifications" not in release
    assert "deploy_backend.py" not in release
    assert "workflow_dispatch" in rollback
    assert "inputs.commit" not in rollback
    assert "publish-version" not in rollback
    assert "update-function-code" not in rollback
    assert "release_exchange.py" not in general
    publisher = (ROOT / "scripts" / "release_exchange.py").read_text(encoding="utf-8")
    assert "update-function-configuration" not in publisher
    assert "put-role-policy" not in publisher
    assert 'FUNCTION = "erap-exchange"' in publisher
    policy = json.loads((ROOT / "infra" / "github-production-exchange-policy.json").read_text(encoding="utf-8"))
    actions = []
    resources = []
    for statement in policy["Statement"]:
        action = statement["Action"]
        resource = statement["Resource"]
        actions.extend(action if isinstance(action, list) else [action])
        resources.extend(resource if isinstance(resource, list) else [resource])
    assert "lambda:UpdateFunctionConfiguration" not in actions
    assert "lambda:UpdateFunctionCode" in actions
    assert "lambda:PublishVersion" in actions
    assert "lambda:UpdateAlias" in actions
    assert all("function:erap-exchange" in resource for resource in resources)
    assert not any("erap-exchange-expiry" in resource or "erap-notifications" in resource for resource in resources)
