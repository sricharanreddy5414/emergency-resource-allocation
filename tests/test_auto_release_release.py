"""Controlled auto-release release and rollback. These tests do not call AWS."""

import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import release_auto_release
import rollback_auto_release
from lambda_manifest import package_map


FUNCTION = "emergency-resource-auto-release"


def _zip(path):
    names = package_map()[FUNCTION]
    with zipfile.ZipFile(path, "w") as archive:
        for name in names:
            archive.writestr(name, b"")
    return path


def _config(code_sha="before", memory=256):
    return {
        "FunctionName": FUNCTION,
        "Runtime": "python3.14",
        "Handler": "lambda_function.lambda_handler",
        "Role": "arn:aws:iam::481838970142:role/service-role/emergency-resource-allocation-role-12qymvku",
        "MemorySize": memory,
        "Timeout": 30,
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
            state["alias_reads"] = state.get("alias_reads", 0) + 1
            if state.get("no_revision") and state["alias_reads"] == 1:
                return {"FunctionVersion": state["alias"]}
            if state.get("move_before_update") and state["alias_reads"] == 2:
                return {"FunctionVersion": "99", "RevisionId": "rev-new"}
            if state.get("alias_race") and state.get("updated"):
                return {"FunctionVersion": state["stuck"], "RevisionId": state["revision"]}
            return {"FunctionVersion": state["alias"], "RevisionId": state["revision"]}
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
                config["FunctionName"] = "create-request"
            return config
        if args[:2] == ["lambda", "update-function-code"]:
            return {}
        if args[:2] == ["lambda", "publish-version"]:
            state["published"] = "78"
            return {"Version": "78"}
        if args[:2] == ["lambda", "update-alias"]:
            revision = args[args.index("--revision-id") + 1]
            if revision != state["revision"]:
                raise AssertionError(revision)
            if state.get("revision_conflict"):
                raise SystemExit("auto-release alias changed before update")
            state["updated"] = True
            if not state.get("alias_race"):
                state["alias"] = args[args.index("--function-version") + 1]
            return {}
        raise AssertionError(args)

    return aws


def test_auto_release_package_contains_only_the_auto_release_handler():
    mapping = package_map()[FUNCTION]
    assert mapping["lambda_function.py"] == "src/auto_release/handler.py"
    assert "src/allocation/service.py" not in mapping.values()
    assert "src/request/handler.py" not in mapping.values()
    assert "src/resource/handler.py" not in mapping.values()
    assert "src/reservation_expiry_handler.py" not in mapping.values()


def test_auto_release_release_publishes_one_version_and_preserves_configuration(tmp_path):
    state = {"calls": [], "alias": "77", "revision": "rev-77"}
    report = release_auto_release.publish_auto_release(_aws(state), _zip(tmp_path / f"{FUNCTION}.zip"), "d" * 40)
    commands = [call[1] for call in state["calls"]]
    assert commands.count("update-function-code") == 1
    assert commands.count("publish-version") == 1
    assert commands.count("update-alias") == 1
    assert commands.index("update-function-code") < commands.index("publish-version")
    assert commands.index("publish-version") < commands.index("update-alias")
    assert "update-function-configuration" not in commands
    assert "create-alias" not in commands
    assert report["function"] == FUNCTION
    assert report["alias"] == "live"
    assert report["previous_version"] == "77"
    assert report["published_version"] == "78"
    assert report["alias_version"] == "78"
    assert report["configuration_preserved"] is True
    assert all(call[call.index("--function-name") + 1] == FUNCTION for call in state["calls"] if "--function-name" in call)
    alias_update = next(call for call in state["calls"] if call[1] == "update-alias")
    assert alias_update[alias_update.index("--name") + 1] == "live"
    assert alias_update[alias_update.index("--function-version") + 1] == "78"
    assert alias_update[alias_update.index("--revision-id") + 1] == "rev-77"
    assert state["alias"] == "78"


def test_auto_release_release_stops_before_publish_when_configuration_drifts(tmp_path):
    state = {"calls": [], "alias": "77", "revision": "rev-77", "drift": None}
    with pytest.raises(SystemExit, match="drifted"):
        release_auto_release.publish_auto_release(_aws(state), _zip(tmp_path / f"{FUNCTION}.zip"), "d" * 40)
    assert ["lambda", "publish-version"] not in [call[:2] for call in state["calls"]]
    assert ["lambda", "update-alias"] not in [call[:2] for call in state["calls"]]


def test_auto_release_release_does_not_move_alias_when_snapshot_differs(tmp_path):
    state = {"calls": [], "alias": "77", "revision": "rev-77", "drift": "78"}
    with pytest.raises(SystemExit, match="does not match"):
        release_auto_release.publish_auto_release(_aws(state), _zip(tmp_path / f"{FUNCTION}.zip"), "d" * 40)
    assert state["alias"] == "77"
    assert ["lambda", "update-alias"] not in [call[:2] for call in state["calls"]]


def test_auto_release_release_does_not_move_alias_when_code_sha_differs(tmp_path):
    state = {"calls": [], "alias": "77", "revision": "rev-77", "code_mismatch": True}
    with pytest.raises(SystemExit, match="packaged code"):
        release_auto_release.publish_auto_release(_aws(state), _zip(tmp_path / f"{FUNCTION}.zip"), "d" * 40)
    assert state["alias"] == "77"


def test_auto_release_release_rejects_an_alias_race(tmp_path):
    state = {"calls": [], "alias": "77", "revision": "rev-77", "stuck": "77", "alias_race": True}
    with pytest.raises(SystemExit, match="did not move"):
        release_auto_release.publish_auto_release(_aws(state), _zip(tmp_path / f"{FUNCTION}.zip"), "d" * 40)
    assert state["alias"] == "77"


def test_auto_release_release_refuses_when_alias_changes_before_update(tmp_path):
    state = {"calls": [], "alias": "77", "revision": "rev-77", "move_before_update": True}
    with pytest.raises(SystemExit, match="changed before update"):
        release_auto_release.publish_auto_release(_aws(state), _zip(tmp_path / f"{FUNCTION}.zip"), "d" * 40)
    assert state["alias"] == "77"
    assert ["lambda", "update-alias"] not in [call[:2] for call in state["calls"]]


def test_auto_release_release_refuses_a_revision_conflict(tmp_path):
    state = {"calls": [], "alias": "77", "revision": "rev-77", "revision_conflict": True}
    with pytest.raises(SystemExit, match="changed before update"):
        release_auto_release.publish_auto_release(_aws(state), _zip(tmp_path / f"{FUNCTION}.zip"), "d" * 40)
    assert state["alias"] == "77"
    assert ["lambda", "update-alias"] in [call[:2] for call in state["calls"]]
    assert state.get("updated") is not True


def test_auto_release_release_rejects_a_package_missing_required_modules(tmp_path):
    path = tmp_path / f"{FUNCTION}.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("lambda_function.py", b"")
    state = {"calls": [], "alias": "77", "revision": "rev-77"}
    with pytest.raises(SystemExit, match="zip entries"):
        release_auto_release.publish_auto_release(_aws(state), path, "d" * 40)
    assert state["calls"] == []


def test_auto_release_release_refuses_outside_production(monkeypatch):
    monkeypatch.delenv("ERAP_ENVIRONMENT", raising=False)
    with pytest.raises(SystemExit, match="alias live"):
        release_auto_release.main()


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
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, check=True, capture_output=True, text=True
    ).stdout.strip()


def test_auto_release_release_rejects_a_sha_that_is_not_on_main(monkeypatch, tmp_path):
    side_sha = _repository(tmp_path)
    monkeypatch.setenv("ERAP_ENVIRONMENT", "production")
    monkeypatch.setenv("RELEASE_SHA", side_sha)
    monkeypatch.setenv("RELEASE_MAIN_REF", "main")
    monkeypatch.setattr(release_auto_release, "ROOT", tmp_path)
    with pytest.raises(SystemExit, match="not on main"):
        release_auto_release.main()


def test_auto_release_rollback_moves_only_an_existing_numeric_version():
    state = {"calls": [], "alias": "78", "revision": "rev-78"}
    report = rollback_auto_release.rollback_auto_release(_aws(state), "77")
    commands = [call[1] for call in state["calls"]]
    assert "update-function-code" not in commands
    assert "publish-version" not in commands
    assert "update-function-configuration" not in commands
    assert "create-alias" not in commands
    assert commands.count("update-alias") == 1
    assert report["function"] == FUNCTION
    assert report["alias"] == "live"
    assert report["previous_version"] == "78"
    assert report["alias_version"] == "77"
    assert report["published"] is False
    assert state["alias"] == "77"
    assert all(call[call.index("--function-name") + 1] == FUNCTION for call in state["calls"] if "--function-name" in call)
    alias_update = next(call for call in state["calls"] if call[1] == "update-alias")
    assert alias_update[alias_update.index("--revision-id") + 1] == "rev-78"


@pytest.mark.parametrize("version", ["", "0", "d364d9a2742adb9b51a2509f33cb0f1581ec0a4a", "latest"])
def test_auto_release_rollback_rejects_non_versions(version):
    state = {"calls": [], "alias": "77", "revision": "rev-77"}
    with pytest.raises(SystemExit, match="version"):
        rollback_auto_release.rollback_auto_release(_aws(state), version)
    assert state["calls"] == []


def test_auto_release_rollback_does_not_move_alias_when_version_is_missing():
    state = {"calls": [], "alias": "77", "revision": "rev-77", "missing_version": True, "requested": "76"}
    with pytest.raises(SystemExit, match="not found"):
        rollback_auto_release.rollback_auto_release(_aws(state), "76")
    assert ["lambda", "update-alias"] not in [call[:2] for call in state["calls"]]


def test_auto_release_rollback_rejects_a_version_from_another_function():
    state = {"calls": [], "alias": "77", "revision": "rev-77", "foreign_function": True, "requested": "80"}
    with pytest.raises(SystemExit, match="does not belong"):
        rollback_auto_release.rollback_auto_release(_aws(state), "80")
    assert ["lambda", "update-alias"] not in [call[:2] for call in state["calls"]]


def test_auto_release_rollback_rejects_an_alias_race():
    state = {"calls": [], "alias": "78", "revision": "rev-78", "stuck": "78", "alias_race": True}
    with pytest.raises(SystemExit, match="did not move"):
        rollback_auto_release.rollback_auto_release(_aws(state), "77")
    assert state["alias"] == "78"


def test_auto_release_rollback_refuses_when_alias_changes_before_update():
    state = {"calls": [], "alias": "78", "revision": "rev-78", "move_before_update": True}
    with pytest.raises(SystemExit, match="changed before update"):
        rollback_auto_release.rollback_auto_release(_aws(state), "77")
    assert state["alias"] == "78"
    assert ["lambda", "update-alias"] not in [call[:2] for call in state["calls"]]


def test_auto_release_workflows_are_manual_production_and_scoped():
    release = (ROOT / ".github" / "workflows" / "release-auto-release.yml").read_text(encoding="utf-8")
    rollback = (ROOT / ".github" / "workflows" / "rollback-auto-release.yml").read_text(encoding="utf-8")
    general = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    allocation = (ROOT / ".github" / "workflows" / "release-allocation.yml").read_text(encoding="utf-8")
    create_request = (ROOT / ".github" / "workflows" / "release-create-request.yml").read_text(encoding="utf-8")
    assert "workflow_dispatch" in release
    assert "environment: production" in release
    assert "refs/heads/main" in release
    assert "release_provenance.py" in release
    assert "release_auto_release.py" in release
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
    assert "release_auto_release.py" not in general
    assert "rollback_auto_release.py" not in general
    assert "release_auto_release.py" not in allocation
    assert "release_auto_release.py" not in create_request
    assert "deploy_backend.py" in general
    publisher = (ROOT / "scripts" / "release_auto_release.py").read_text(encoding="utf-8")
    rollback_source = (ROOT / "scripts" / "rollback_auto_release.py").read_text(encoding="utf-8")
    assert "update-function-configuration" not in publisher
    assert "put-role-policy" not in publisher
    assert "deploy_backend.py" not in publisher
    assert 'FUNCTION = "emergency-resource-auto-release"' in publisher
    assert 'FUNCTION = "emergency-resource-auto-release"' in rollback_source
    for other in (
        "create-request",
        "get-resources",
        "emergency-resource-allocation",
        "erap-exchange",
        "erap-billing",
        "deploy_backend.py",
    ):
        assert other not in publisher
        assert other not in rollback_source
    handler = (ROOT / "src" / "auto_release" / "handler.py").read_text(encoding="utf-8")
    assert "HOLDER_PAGE_SIZE = 100" in handler
    assert "HOLDER_PAGE_LIMIT = 25" in handler
    assert "ResourceReleased" in handler
    assert ".scan(" not in handler
    policy = json.loads((ROOT / "infra" / "github-production-auto-release-policy.json").read_text(encoding="utf-8"))
    actions = []
    resources = []
    for statement in policy["Statement"]:
        action = statement["Action"]
        resource = statement["Resource"]
        actions.extend(action if isinstance(action, list) else [action])
        resources.extend(resource if isinstance(resource, list) else [resource])
    assert actions == [
        "lambda:GetAlias",
        "lambda:GetFunctionConfiguration",
        "lambda:UpdateFunctionCode",
        "lambda:PublishVersion",
        "lambda:UpdateAlias",
    ]
    assert "lambda:*" not in actions
    assert "lambda:CreateAlias" not in actions
    assert "lambda:UpdateFunctionConfiguration" not in actions
    assert "lambda:DeleteFunction" not in actions
    assert "dynamodb:PutItem" not in actions
    assert "dynamodb:UpdateItem" not in actions
    assert "iam:PassRole" not in actions
    assert "iam:CreatePolicy" not in actions
    assert "secretsmanager:GetSecretValue" not in actions
    assert "apigateway:GET" not in actions
    assert "s3:GetObject" not in actions
    assert "cognito-idp:AdminInitiateAuth" not in actions
    assert resources == [
        "arn:aws:lambda:eu-north-1:481838970142:function:emergency-resource-auto-release",
        "arn:aws:lambda:eu-north-1:481838970142:function:emergency-resource-auto-release:*",
    ]
    trust = json.loads((ROOT / "infra" / "github-production-trust.json").read_text(encoding="utf-8"))
    equals = trust["Statement"][0]["Condition"]["StringEquals"]
    refs = equals["token.actions.githubusercontent.com:job_workflow_ref"]
    prefix = "sricharanreddy5414/emergency-resource-allocation/.github/workflows/"
    assert f"{prefix}release-auto-release.yml@refs/heads/main" in refs
    assert f"{prefix}rollback-auto-release.yml@refs/heads/main" in refs
    assert equals["token.actions.githubusercontent.com:repository"] == "sricharanreddy5414/emergency-resource-allocation"
    assert equals["token.actions.githubusercontent.com:ref"] == "refs/heads/main"
    assert "environment:production" in equals["token.actions.githubusercontent.com:sub"]
    assert "repo:*" not in json.dumps(trust)
    assert '"*"' not in json.dumps(equals["token.actions.githubusercontent.com:job_workflow_ref"])
