"""Controlled create-request release and rollback. These tests do not call AWS."""

import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import release_create_request
import rollback_create_request
from lambda_manifest import package_map


def _zip(path):
    names = package_map()["create-request"]
    with zipfile.ZipFile(path, "w") as archive:
        for name in names:
            archive.writestr(name, b"")
    return path


def _config(code_sha="before", memory=256):
    return {
        "FunctionName": "create-request",
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
            state["published"] = "79"
            return {"Version": "79"}
        if args[:2] == ["lambda", "update-alias"]:
            state["updated"] = True
            if not state.get("alias_race"):
                state["alias"] = args[args.index("--function-version") + 1]
            return {}
        raise AssertionError(args)

    return aws


def test_create_request_package_contains_only_the_request_handler():
    packages = package_map()
    mapping = packages["create-request"]
    assert mapping["lambda_function.py"] == "src/request/handler.py"
    assert "src/allocation/service.py" not in mapping.values()
    assert "src/resource/handler.py" not in mapping.values()
    assert "src/auto_release/handler.py" not in mapping.values()
    assert "src/reservation_expiry_handler.py" not in mapping.values()


def test_create_request_release_publishes_one_version_and_preserves_configuration(tmp_path):
    state = {"calls": [], "alias": "78"}
    report = release_create_request.publish_create_request(
        _aws(state), _zip(tmp_path / "create-request.zip"), "d" * 40
    )
    commands = [call[1] for call in state["calls"]]
    assert commands.index("update-function-code") < commands.index("publish-version")
    assert commands.index("publish-version") < commands.index("update-alias")
    assert "update-function-configuration" not in commands
    assert "create-alias" not in commands
    assert report["function"] == "create-request"
    assert report["alias"] == "live"
    assert report["previous_version"] == "78"
    assert report["published_version"] == "79"
    assert report["alias_version"] == "79"
    assert report["configuration_preserved"] is True
    assert all(call[3] == "create-request" for call in state["calls"])
    alias_update = next(call for call in state["calls"] if call[1] == "update-alias")
    assert alias_update[alias_update.index("--name") + 1] == "live"
    assert alias_update[alias_update.index("--function-version") + 1] == "79"


def test_create_request_release_stops_before_publish_when_configuration_drifts(tmp_path):
    state = {"calls": [], "alias": "78", "drift": None}
    with pytest.raises(SystemExit, match="drifted"):
        release_create_request.publish_create_request(_aws(state), _zip(tmp_path / "create-request.zip"), "d" * 40)
    assert ["lambda", "publish-version"] not in [call[:2] for call in state["calls"]]
    assert ["lambda", "update-alias"] not in [call[:2] for call in state["calls"]]


def test_create_request_release_does_not_move_alias_when_snapshot_differs(tmp_path):
    state = {"calls": [], "alias": "78", "drift": "79"}
    with pytest.raises(SystemExit, match="does not match"):
        release_create_request.publish_create_request(_aws(state), _zip(tmp_path / "create-request.zip"), "d" * 40)
    assert state["alias"] == "78"
    assert ["lambda", "update-alias"] not in [call[:2] for call in state["calls"]]


def test_create_request_release_does_not_move_alias_when_code_sha_differs(tmp_path):
    state = {"calls": [], "alias": "78", "code_mismatch": True}
    with pytest.raises(SystemExit, match="packaged code"):
        release_create_request.publish_create_request(_aws(state), _zip(tmp_path / "create-request.zip"), "d" * 40)
    assert state["alias"] == "78"


def test_create_request_release_rejects_an_alias_race(tmp_path):
    state = {"calls": [], "alias": "78", "stuck": "78", "alias_race": True}
    with pytest.raises(SystemExit, match="did not move"):
        release_create_request.publish_create_request(_aws(state), _zip(tmp_path / "create-request.zip"), "d" * 40)
    assert state["alias"] == "78"


def test_create_request_release_rejects_a_package_missing_required_modules(tmp_path):
    path = tmp_path / "create-request.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("lambda_function.py", b"")
    state = {"calls": [], "alias": "78"}
    with pytest.raises(SystemExit, match="zip entries"):
        release_create_request.publish_create_request(_aws(state), path, "d" * 40)
    assert state["calls"] == []


def test_create_request_release_refuses_outside_production(monkeypatch):
    monkeypatch.delenv("ERAP_ENVIRONMENT", raising=False)
    with pytest.raises(SystemExit, match="alias live"):
        release_create_request.main()


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
    git("checkout", "-b", "side")
    (tmp_path / "README").write_text("side\n", encoding="utf-8")
    git("add", "README")
    git("commit", "-m", "side")
    side_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, check=True, capture_output=True, text=True
    ).stdout.strip()
    return side_sha


def test_create_request_release_rejects_a_malformed_sha(monkeypatch):
    monkeypatch.setenv("ERAP_ENVIRONMENT", "production")
    monkeypatch.setenv("RELEASE_SHA", "not-a-sha")
    with pytest.raises(SystemExit, match="malformed"):
        release_create_request.main()


def test_create_request_release_rejects_a_sha_that_is_not_on_main(monkeypatch, tmp_path):
    side_sha = _repository(tmp_path)
    monkeypatch.setenv("ERAP_ENVIRONMENT", "production")
    monkeypatch.setenv("RELEASE_SHA", side_sha)
    monkeypatch.setenv("RELEASE_MAIN_REF", "main")
    monkeypatch.setattr(release_create_request, "ROOT", tmp_path)
    with pytest.raises(SystemExit, match="not on main"):
        release_create_request.main()


def test_create_request_rollback_moves_only_an_existing_numeric_version():
    state = {"calls": [], "alias": "79"}
    report = rollback_create_request.rollback_create_request(_aws(state), "78")
    commands = [call[1] for call in state["calls"]]
    assert "update-function-code" not in commands
    assert "publish-version" not in commands
    assert "update-function-configuration" not in commands
    assert "create-alias" not in commands
    assert report["function"] == "create-request"
    assert report["alias"] == "live"
    assert report["alias_version"] == "78"
    assert report["published"] is False
    assert state["alias"] == "78"
    assert all(call[3] == "create-request" for call in state["calls"])


@pytest.mark.parametrize("version", ["", "0", "d364d9a2742adb9b51a2509f33cb0f1581ec0a4a", "latest"])
def test_create_request_rollback_rejects_non_versions(version):
    state = {"calls": [], "alias": "78"}
    with pytest.raises(SystemExit, match="version"):
        rollback_create_request.rollback_create_request(_aws(state), version)
    assert state["calls"] == []


def test_create_request_rollback_does_not_move_alias_when_version_is_missing():
    state = {"calls": [], "alias": "78", "missing_version": True, "requested": "77"}
    with pytest.raises(SystemExit, match="not found"):
        rollback_create_request.rollback_create_request(_aws(state), "77")
    assert ["lambda", "update-alias"] not in [call[:2] for call in state["calls"]]


def test_create_request_rollback_rejects_a_version_from_another_function():
    state = {"calls": [], "alias": "78", "foreign_function": True, "requested": "88"}
    with pytest.raises(SystemExit, match="does not belong"):
        rollback_create_request.rollback_create_request(_aws(state), "88")
    assert ["lambda", "update-alias"] not in [call[:2] for call in state["calls"]]


def test_create_request_rollback_rejects_an_alias_race():
    state = {"calls": [], "alias": "79", "stuck": "79", "alias_race": True}
    with pytest.raises(SystemExit, match="did not move"):
        rollback_create_request.rollback_create_request(_aws(state), "78")
    assert state["alias"] == "79"


def test_create_request_workflows_are_manual_production_and_scoped():
    release = (ROOT / ".github" / "workflows" / "release-create-request.yml").read_text(encoding="utf-8")
    rollback = (ROOT / ".github" / "workflows" / "rollback-create-request.yml").read_text(encoding="utf-8")
    general = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "workflow_dispatch" in release
    assert "environment: production" in release
    assert "refs/heads/main" in release
    assert "release_provenance.py" in release
    assert "release_create_request.py" in release
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
    assert "release_create_request.py" not in general
    assert "deploy_backend.py" in general
    publisher = (ROOT / "scripts" / "release_create_request.py").read_text(encoding="utf-8")
    assert "update-function-configuration" not in publisher
    assert "put-role-policy" not in publisher
    assert "deploy_backend.py" not in publisher
    assert 'FUNCTION = "create-request"' in publisher
    assert 'ALIAS' in publisher
    policy = json.loads((ROOT / "infra" / "github-production-create-request-policy.json").read_text(encoding="utf-8"))
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
    assert "apigateway:GET" not in actions
    assert resources == [
        "arn:aws:lambda:eu-north-1:481838970142:function:create-request",
        "arn:aws:lambda:eu-north-1:481838970142:function:create-request:*",
    ]
    trust = json.loads((ROOT / "infra" / "github-production-trust.json").read_text(encoding="utf-8"))
    refs = trust["Statement"][0]["Condition"]["StringEquals"]["token.actions.githubusercontent.com:job_workflow_ref"]
    prefix = "sricharanreddy5414/emergency-resource-allocation/.github/workflows/"
    assert f"{prefix}release-create-request.yml@refs/heads/main" in refs
    assert f"{prefix}rollback-create-request.yml@refs/heads/main" in refs
    assert all(item.endswith("@refs/heads/main") for item in refs)
    assert "environment:production" in trust["Statement"][0]["Condition"]["StringEquals"]["token.actions.githubusercontent.com:sub"]
