"""Controlled emergency-resource-allocation release and rollback. These tests do not call AWS."""

import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import release_allocation
import rollback_allocation
from lambda_manifest import package_map


def _zip(path):
    names = package_map()["emergency-resource-allocation"]
    with zipfile.ZipFile(path, "w") as archive:
        for name in names:
            archive.writestr(name, b"")
    return path


def _config(code_sha="before", memory=256):
    return {
        "FunctionName": "emergency-resource-allocation",
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
            if state.get("alias_race") and state.get("updated"):
                return {"FunctionVersion": state["stuck"]}
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
                config["FunctionName"] = "get-resources"
            return config
        if args[:2] == ["lambda", "update-function-code"]:
            return {}
        if args[:2] == ["lambda", "publish-version"]:
            state["published"] = "80"
            return {"Version": "80"}
        if args[:2] == ["lambda", "update-alias"]:
            state["updated"] = True
            if not state.get("alias_race"):
                state["alias"] = args[args.index("--function-version") + 1]
            return {}
        raise AssertionError(args)

    return aws


def test_allocation_package_contains_only_the_allocation_handler():
    packages = package_map()
    mapping = packages["emergency-resource-allocation"]
    assert mapping["lambda_function.py"] == "src/allocation/service.py"
    assert "src/resource/handler.py" not in mapping.values()
    assert "src/reservation_expiry_handler.py" not in mapping.values()
    assert "src/exchange/service.py" not in mapping.values()
    assert "matching.py" in mapping
    assert "resource_state.py" in mapping


def test_allocation_release_publishes_one_version_and_preserves_configuration(tmp_path):
    state = {"calls": [], "alias": "79"}
    report = release_allocation.publish_allocation(_aws(state), _zip(tmp_path / "allocation.zip"), "d" * 40)
    commands = [call[1] for call in state["calls"]]
    assert commands.index("update-function-code") < commands.index("publish-version")
    assert commands.index("publish-version") < commands.index("update-alias")
    assert "update-function-configuration" not in commands
    assert "create-alias" not in commands
    assert report["function"] == "emergency-resource-allocation"
    assert report["previous_version"] == "79"
    assert report["published_version"] == "80"
    assert report["alias_version"] == "80"
    assert report["configuration_preserved"] is True
    assert report["recorded"]["runtime"] == "python3.14"
    assert report["recorded"]["memory"] == 256
    assert report["recorded"]["timeout"] == 15
    assert all(call[3] == "emergency-resource-allocation" for call in state["calls"])


def test_allocation_release_stops_before_publish_when_configuration_drifts(tmp_path):
    state = {"calls": [], "alias": "79", "drift": None}
    with pytest.raises(SystemExit, match="drifted"):
        release_allocation.publish_allocation(_aws(state), _zip(tmp_path / "allocation.zip"), "d" * 40)
    assert ["lambda", "publish-version"] not in [call[:2] for call in state["calls"]]
    assert ["lambda", "update-alias"] not in [call[:2] for call in state["calls"]]


def test_allocation_release_does_not_move_alias_when_snapshot_differs(tmp_path):
    state = {"calls": [], "alias": "79", "drift": "80"}
    with pytest.raises(SystemExit, match="does not match"):
        release_allocation.publish_allocation(_aws(state), _zip(tmp_path / "allocation.zip"), "d" * 40)
    assert state["alias"] == "79"
    assert ["lambda", "update-alias"] not in [call[:2] for call in state["calls"]]


def test_allocation_release_does_not_move_alias_when_code_sha_differs(tmp_path):
    state = {"calls": [], "alias": "79", "code_mismatch": True}
    with pytest.raises(SystemExit, match="packaged code"):
        release_allocation.publish_allocation(_aws(state), _zip(tmp_path / "allocation.zip"), "d" * 40)
    assert state["alias"] == "79"


def test_allocation_release_rejects_an_alias_race(tmp_path):
    state = {"calls": [], "alias": "79", "stuck": "79", "alias_race": True}
    with pytest.raises(SystemExit, match="did not move"):
        release_allocation.publish_allocation(_aws(state), _zip(tmp_path / "allocation.zip"), "d" * 40)
    assert state["alias"] == "79"


def test_allocation_release_rejects_a_package_missing_required_modules(tmp_path):
    path = tmp_path / "allocation.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("lambda_function.py", b"")
    state = {"calls": [], "alias": "79"}
    with pytest.raises(SystemExit, match="zip entries"):
        release_allocation.publish_allocation(_aws(state), path, "d" * 40)
    assert state["calls"] == []


def test_allocation_release_refuses_outside_production(monkeypatch):
    monkeypatch.delenv("ERAP_ENVIRONMENT", raising=False)
    with pytest.raises(SystemExit, match="alias live"):
        release_allocation.main()


def _repository(tmp_path):
    environment = os.environ.copy()
    environment.update(
        {
            "GIT_AUTHOR_NAME": "ERAP Test",
            "GIT_AUTHOR_EMAIL": "erap-test@example.com",
            "GIT_COMMITTER_NAME": "ERAP Test",
            "GIT_COMMITTER_EMAIL": "erap-test@example.com",
        }
    )

    def git(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, env=environment, capture_output=True, text=True)

    git("init", "-b", "main")
    (tmp_path / "README").write_text("one\n", encoding="utf-8")
    git("add", "README")
    git("commit", "-m", "one")
    main_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, check=True, capture_output=True, text=True
    ).stdout.strip()
    git("checkout", "-b", "side")
    (tmp_path / "README").write_text("side\n", encoding="utf-8")
    git("add", "README")
    git("commit", "-m", "side")
    side_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, check=True, capture_output=True, text=True
    ).stdout.strip()
    return main_sha, side_sha


def test_allocation_release_rejects_a_malformed_sha(monkeypatch):
    monkeypatch.setenv("ERAP_ENVIRONMENT", "production")
    monkeypatch.setenv("RELEASE_SHA", "not-a-sha")
    with pytest.raises(SystemExit, match="malformed"):
        release_allocation.main()


def test_allocation_release_rejects_a_sha_that_is_not_on_main(monkeypatch, tmp_path):
    _main, side_sha = _repository(tmp_path)
    monkeypatch.setenv("ERAP_ENVIRONMENT", "production")
    monkeypatch.setenv("RELEASE_SHA", side_sha)
    monkeypatch.setenv("RELEASE_MAIN_REF", "main")
    monkeypatch.setattr(release_allocation, "ROOT", tmp_path)
    with pytest.raises(SystemExit, match="not on main"):
        release_allocation.main()


def test_allocation_rollback_moves_only_an_existing_numeric_version():
    state = {"calls": [], "alias": "80"}
    report = rollback_allocation.rollback_allocation(_aws(state), "79")
    commands = [call[1] for call in state["calls"]]
    assert "update-function-code" not in commands
    assert "publish-version" not in commands
    assert "update-function-configuration" not in commands
    assert "create-alias" not in commands
    assert report["alias_version"] == "79"
    assert report["published"] is False
    assert state["alias"] == "79"
    assert all(call[3] == "emergency-resource-allocation" for call in state["calls"])


@pytest.mark.parametrize("version", ["", "0", "d364d9a2742adb9b51a2509f33cb0f1581ec0a4a", "latest"])
def test_allocation_rollback_rejects_non_versions(version):
    state = {"calls": [], "alias": "79"}
    with pytest.raises(SystemExit, match="version"):
        rollback_allocation.rollback_allocation(_aws(state), version)
    assert state["calls"] == []


def test_allocation_rollback_does_not_move_alias_when_version_is_missing():
    state = {"calls": [], "alias": "79", "missing_version": True, "requested": "78"}
    with pytest.raises(SystemExit, match="not found"):
        rollback_allocation.rollback_allocation(_aws(state), "78")
    assert ["lambda", "update-alias"] not in [call[:2] for call in state["calls"]]


def test_allocation_rollback_rejects_a_version_from_another_function():
    state = {"calls": [], "alias": "79", "foreign_function": True, "requested": "88"}
    with pytest.raises(SystemExit, match="does not belong"):
        rollback_allocation.rollback_allocation(_aws(state), "88")
    assert ["lambda", "update-alias"] not in [call[:2] for call in state["calls"]]


def test_allocation_rollback_rejects_an_alias_race():
    state = {"calls": [], "alias": "80", "stuck": "80", "alias_race": True}
    with pytest.raises(SystemExit, match="did not move"):
        rollback_allocation.rollback_allocation(_aws(state), "79")
    assert state["alias"] == "80"


def test_allocation_workflows_are_manual_production_and_scoped():
    release = (ROOT / ".github" / "workflows" / "release-allocation.yml").read_text(encoding="utf-8")
    rollback = (ROOT / ".github" / "workflows" / "rollback-allocation.yml").read_text(encoding="utf-8")
    general = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "workflow_dispatch" in release
    assert "environment: production" in release
    assert "refs/heads/main" in release
    assert "release_provenance.py" in release
    assert "release_allocation.py" in release
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
    assert "release_allocation.py" not in general
    assert "deploy_backend.py" in general
    publisher = (ROOT / "scripts" / "release_allocation.py").read_text(encoding="utf-8")
    assert "update-function-configuration" not in publisher
    assert "put-role-policy" not in publisher
    assert "deploy_backend.py" not in publisher
    assert 'FUNCTION = "emergency-resource-allocation"' in publisher
    policy = json.loads((ROOT / "infra" / "github-production-allocation-policy.json").read_text(encoding="utf-8"))
    actions = []
    resources = []
    for statement in policy["Statement"]:
        action = statement["Action"]
        resource = statement["Resource"]
        actions.extend(action if isinstance(action, list) else [action])
        resources.extend(resource if isinstance(resource, list) else [resource])
    assert "lambda:*" not in actions
    assert "lambda:CreateAlias" not in actions
    assert "lambda:UpdateFunctionConfiguration" not in actions
    assert "lambda:DeleteFunction" not in actions
    assert "dynamodb:PutItem" not in actions
    assert "iam:PassRole" not in actions
    assert "secretsmanager:GetSecretValue" not in actions
    assert resources == [
        "arn:aws:lambda:eu-north-1:481838970142:function:emergency-resource-allocation",
        "arn:aws:lambda:eu-north-1:481838970142:function:emergency-resource-allocation:*",
    ]
    trust = json.loads((ROOT / "infra" / "github-production-trust.json").read_text(encoding="utf-8"))
    refs = trust["Statement"][0]["Condition"]["StringEquals"]["token.actions.githubusercontent.com:job_workflow_ref"]
    assert "sricharanreddy5414/emergency-resource-allocation/.github/workflows/release.yml@refs/heads/main" in refs
    assert "sricharanreddy5414/emergency-resource-allocation/.github/workflows/release-allocation.yml@refs/heads/main" in refs
    assert "sricharanreddy5414/emergency-resource-allocation/.github/workflows/rollback-allocation.yml@refs/heads/main" in refs
