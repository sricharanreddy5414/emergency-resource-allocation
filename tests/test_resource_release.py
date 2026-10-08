"""Controlled get-resources release and rollback. These tests do not call AWS."""

import json
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import release_resource
import rollback_resource
from lambda_manifest import package_map


def _zip(path):
    names = package_map()["get-resources"]
    with zipfile.ZipFile(path, "w") as archive:
        for name in names:
            archive.writestr(name, b"")
    return path


def _config(code_sha="before", memory=256):
    return {
        "FunctionName": "get-resources",
        "Runtime": "python3.14",
        "Handler": "lambda_function.lambda_handler",
        "Role": "arn:aws:iam::481838970142:role/service-role/emergency-resource-allocation-role-12qymvku",
        "MemorySize": memory,
        "Timeout": 15,
        "Environment": None,
        "VpcConfig": {},
        "Layers": [],
        "Architectures": ["x86_64"],
        "CodeSha256": code_sha,
        "LastUpdateStatus": "Successful",
        "Description": "commit=d364d9a2742adb9b51a2509f33cb0f1581ec0a4a",
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
            return {}
        if args[:2] == ["lambda", "publish-version"]:
            state["published"] = "85"
            return {"Version": "85"}
        if args[:2] == ["lambda", "update-alias"]:
            state["alias"] = args[args.index("--function-version") + 1]
            return {}
        raise AssertionError(args)

    return aws


def test_resource_package_contains_the_page_token_module_only_for_get_resources():
    packages = package_map()
    assert "resource_page_token.py" in packages["get-resources"]
    assert packages["get-resources"]["lambda_function.py"] == "src/resource/handler.py"
    for name, mapping in packages.items():
        if name != "get-resources":
            assert "resource_page_token.py" not in mapping


def test_resource_release_publishes_one_version_and_preserves_configuration(tmp_path):
    state = {"calls": [], "alias": "84"}
    report = release_resource.publish_resource(_aws(state), _zip(tmp_path / "get-resources.zip"), "d" * 40)
    commands = [call[1] for call in state["calls"]]
    assert commands.index("update-function-code") < commands.index("publish-version")
    assert commands.index("publish-version") < commands.index("update-alias")
    assert "update-function-configuration" not in commands
    assert report["previous_version"] == "84"
    assert report["published_version"] == "85"
    assert report["alias_version"] == "85"
    assert report["configuration_preserved"] is True
    assert report["recorded"]["role"].endswith("emergency-resource-allocation-role-12qymvku")
    assert report["recorded"]["runtime"] == "python3.14"
    assert report["recorded"]["handler"] == "lambda_function.lambda_handler"
    assert report["recorded"]["memory"] == 256
    assert report["recorded"]["timeout"] == 15
    assert report["recorded"]["environment_keys"] == []
    assert all(
        call[3] == "get-resources"
        for call in state["calls"]
        if call[1].startswith("get-") or call[1].startswith("update-") or call[1] == "publish-version"
    )


def test_resource_release_stops_before_publish_when_configuration_drifts(tmp_path):
    state = {"calls": [], "alias": "84", "drift": None}
    with pytest.raises(SystemExit, match="drifted"):
        release_resource.publish_resource(_aws(state), _zip(tmp_path / "get-resources.zip"), "d" * 40)
    assert ["lambda", "publish-version"] not in [call[:2] for call in state["calls"]]
    assert ["lambda", "update-alias"] not in [call[:2] for call in state["calls"]]


def test_resource_release_does_not_move_alias_when_snapshot_differs(tmp_path):
    state = {"calls": [], "alias": "84", "drift": "85"}
    with pytest.raises(SystemExit, match="does not match"):
        release_resource.publish_resource(_aws(state), _zip(tmp_path / "get-resources.zip"), "d" * 40)
    assert state["alias"] == "84"
    assert ["lambda", "update-alias"] not in [call[:2] for call in state["calls"]]


def test_resource_release_does_not_move_alias_when_code_sha_differs(tmp_path):
    state = {"calls": [], "alias": "84", "code_mismatch": True}
    with pytest.raises(SystemExit, match="packaged code"):
        release_resource.publish_resource(_aws(state), _zip(tmp_path / "get-resources.zip"), "d" * 40)
    assert state["alias"] == "84"
    assert ["lambda", "update-alias"] not in [call[:2] for call in state["calls"]]


def test_resource_release_rejects_a_package_missing_required_modules(tmp_path):
    path = tmp_path / "get-resources.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("lambda_function.py", b"")
    state = {"calls": [], "alias": "84"}
    with pytest.raises(SystemExit, match="zip entries"):
        release_resource.publish_resource(_aws(state), path, "d" * 40)
    assert state["calls"] == []


def test_resource_release_refuses_outside_production(monkeypatch):
    monkeypatch.delenv("ERAP_ENVIRONMENT", raising=False)
    with pytest.raises(SystemExit, match="alias live"):
        release_resource.main()


def test_resource_rollback_moves_only_an_existing_numeric_version():
    state = {"calls": [], "alias": "85"}
    report = rollback_resource.rollback_resource(_aws(state), "84")
    commands = [call[1] for call in state["calls"]]
    assert "update-function-code" not in commands
    assert "publish-version" not in commands
    assert "update-function-configuration" not in commands
    assert report["alias_version"] == "84"
    assert report["published"] is False
    assert state["alias"] == "84"
    assert all(call[3] == "get-resources" for call in state["calls"])


@pytest.mark.parametrize("version", ["", "0", "d364d9a2742adb9b51a2509f33cb0f1581ec0a4a", "latest"])
def test_resource_rollback_rejects_non_versions(version):
    state = {"calls": [], "alias": "84"}
    with pytest.raises(SystemExit, match="version"):
        rollback_resource.rollback_resource(_aws(state), version)
    assert state["calls"] == []


def test_resource_rollback_does_not_move_alias_when_version_is_missing():
    state = {"calls": [], "alias": "84", "missing_version": True, "requested": "83"}
    with pytest.raises(SystemExit, match="not found"):
        rollback_resource.rollback_resource(_aws(state), "83")
    assert ["lambda", "update-alias"] not in [call[:2] for call in state["calls"]]


def test_resource_rollback_rejects_a_version_from_another_function():
    state = {"calls": [], "alias": "84", "foreign_function": True, "requested": "18"}
    with pytest.raises(SystemExit, match="does not belong"):
        rollback_resource.rollback_resource(_aws(state), "18")
    assert ["lambda", "update-alias"] not in [call[:2] for call in state["calls"]]


def test_resource_workflows_are_manual_production_and_scoped():
    release = (ROOT / ".github" / "workflows" / "release-resource.yml").read_text(encoding="utf-8")
    rollback = (ROOT / ".github" / "workflows" / "rollback-resource.yml").read_text(encoding="utf-8")
    general = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    exchange = (ROOT / ".github" / "workflows" / "release-exchange.yml").read_text(encoding="utf-8")
    assert "workflow_dispatch" in release
    assert "environment: production" in release
    assert "refs/heads/main" in release
    assert "release_provenance.py" in release
    assert "release_resource.py" in release
    assert "origin/main" in release
    assert "push:" not in release
    assert "deploy_backend.py" not in release
    assert "pull_request:" not in release
    assert "workflow_dispatch" in rollback
    assert "environment: production" in rollback
    assert "inputs.commit" not in rollback
    assert "publish-version" not in rollback
    assert "update-function-code" not in rollback
    assert "push:" not in rollback
    assert "release_resource.py" not in general
    assert "release_resource.py" not in exchange
    publisher = (ROOT / "scripts" / "release_resource.py").read_text(encoding="utf-8")
    assert "update-function-configuration" not in publisher
    assert "put-role-policy" not in publisher
    assert 'FUNCTION = "get-resources"' in publisher
    policy = json.loads((ROOT / "infra" / "github-production-resource-policy.json").read_text(encoding="utf-8"))
    actions = []
    resources = []
    for statement in policy["Statement"]:
        action = statement["Action"]
        resource = statement["Resource"]
        actions.extend(action if isinstance(action, list) else [action])
        resources.extend(resource if isinstance(resource, list) else [resource])
    assert "lambda:*" not in actions
    assert "lambda:UpdateFunctionConfiguration" not in actions
    assert "secretsmanager:GetSecretValue" not in actions
    assert "lambda:UpdateFunctionCode" in actions
    assert "lambda:PublishVersion" in actions
    assert "lambda:UpdateAlias" in actions
    assert resources == [
        "arn:aws:lambda:eu-north-1:481838970142:function:get-resources",
        "arn:aws:lambda:eu-north-1:481838970142:function:get-resources:*",
    ]
    trust = json.loads((ROOT / "infra" / "github-production-trust.json").read_text(encoding="utf-8"))
    refs = trust["Statement"][0]["Condition"]["StringEquals"]["token.actions.githubusercontent.com:job_workflow_ref"]
    prefix = "sricharanreddy5414/emergency-resource-allocation/.github/workflows/"
    assert refs == [
        f"{prefix}release.yml@refs/heads/main",
        f"{prefix}rollback.yml@refs/heads/main",
        f"{prefix}release-exchange.yml@refs/heads/main",
        f"{prefix}rollback-exchange.yml@refs/heads/main",
        f"{prefix}release-resource.yml@refs/heads/main",
        f"{prefix}rollback-resource.yml@refs/heads/main",
        f"{prefix}release-reservation-expiry.yml@refs/heads/main",
        f"{prefix}rollback-reservation-expiry.yml@refs/heads/main",
        f"{prefix}release-allocation.yml@refs/heads/main",
        f"{prefix}rollback-allocation.yml@refs/heads/main",
    ]
