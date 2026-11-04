"""Controlled organization/location release and rollback. These tests do not call AWS."""

import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import release_organization
import rollback_organization
from lambda_manifest import ACCOUNT, package_map


FUNCTIONS = (
    "erap-get-organization",
    "erap-create-organization",
    "erap-locations",
)
ROLE = f"arn:aws:iam::{ACCOUNT}:role/service-role/erap-organization-role"


def _zip(path, name):
    names = package_map()[name]
    with zipfile.ZipFile(path, "w") as archive:
        for entry in names:
            archive.writestr(entry, b"")
    return path


def _archives(folder):
    return {name: _zip(folder / f"{name}.zip", name) for name in FUNCTIONS}


def _config(name, code_sha="before", memory=256, role=ROLE):
    return {
        "FunctionName": name,
        "Runtime": "python3.14",
        "Handler": "handler.lambda_handler",
        "Role": role,
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
        if region and region != "eu-north-1":
            raise AssertionError(region)
        state["calls"].append(list(args))
        name = args[args.index("--function-name") + 1] if "--function-name" in args else ""
        alias_key = f"alias:{name}"
        if args[:2] == ["lambda", "get-alias"]:
            if state.get("alias_race") and state.get("updated"):
                return {"FunctionVersion": state["stuck"].get(name, state["alias"][name])}
            return {"FunctionVersion": state["alias"][name]}
        if args[:2] == ["lambda", "get-function-configuration"]:
            qualifier = args[args.index("--qualifier") + 1] if "--qualifier" in args else None
            if state.get("missing_version") and qualifier == state.get("requested"):
                raise SystemExit("version not found")
            memory = 128 if state.get("drift") == (name, qualifier) else 256
            code_sha = "before"
            if qualifier is None:
                code_sha = "latest"
            elif qualifier == state.get("published", {}).get(name):
                code_sha = "published-other" if state.get("code_mismatch") else "latest"
            config = _config(name, code_sha, memory)
            if state.get("wrong_account"):
                config["Role"] = "arn:aws:iam::000000000000:role/other"
            if state.get("foreign_function") and qualifier == state.get("requested"):
                config["FunctionName"] = "get-resources"
            return config
        if args[:2] == ["lambda", "update-function-code"]:
            return {}
        if args[:2] == ["lambda", "publish-version"]:
            state.setdefault("published", {})[name] = "79"
            return {"Version": "79"}
        if args[:2] == ["lambda", "update-alias"]:
            state["updated"] = True
            if not state.get("alias_race"):
                state["alias"][name] = args[args.index("--function-version") + 1]
            return {}
        raise AssertionError(args)

    return aws


def test_organization_packages_include_cors_modules_and_exclude_billing():
    packages = package_map()
    for name in FUNCTIONS:
        mapping = packages[name]
        assert mapping["common.py"] == "src/organization/common.py"
        assert mapping["observability.py"] == "src/shared/observability.py"
        assert "src/billing/checkout.py" not in mapping.values()
        assert "src/allocation/service.py" not in mapping.values()
        assert "src/resource/handler.py" not in mapping.values()
    assert packages["erap-get-organization"]["get_handler.py"] == "src/organization/get_handler.py"
    assert packages["erap-create-organization"]["handler.py"] == "src/organization/handler.py"
    assert packages["erap-locations"]["handler.py"] == "src/locations/handler.py"
    assert release_organization.FUNCTIONS == FUNCTIONS
    assert "erap-billing" not in release_organization.FUNCTIONS
    assert "get-resources" not in release_organization.FUNCTIONS


def test_organization_release_publishes_only_the_three_functions(tmp_path):
    state = {
        "calls": [],
        "alias": {name: "78" for name in FUNCTIONS},
    }
    report = release_organization.publish_organization_functions(
        _aws(state), _archives(tmp_path), "d" * 40
    )
    names = [call[3] for call in state["calls"] if call[:2] == ["lambda", "update-function-code"]]
    assert names == list(FUNCTIONS)
    commands = [call[1] for call in state["calls"]]
    assert commands.index("get-alias") < commands.index("update-function-code")
    assert "update-function-configuration" not in commands
    assert "create-alias" not in commands
    assert report["previous_versions"] == {name: "78" for name in FUNCTIONS}
    assert [item["published_version"] for item in report["functions"]] == ["79", "79", "79"]
    assert report["account"] == ACCOUNT
    assert report["region"] == "eu-north-1"
    assert all(call[3] in FUNCTIONS for call in state["calls"] if "--function-name" in call)


def test_organization_release_refuses_extra_or_missing_archives(tmp_path):
    state = {"calls": [], "alias": {name: "78" for name in FUNCTIONS}}
    archives = _archives(tmp_path)
    archives["erap-billing"] = archives["erap-locations"]
    with pytest.raises(SystemExit, match="exactly the three"):
        release_organization.publish_organization_functions(_aws(state), archives, "d" * 40)
    del archives["erap-billing"]
    del archives["erap-locations"]
    with pytest.raises(SystemExit, match="exactly the three"):
        release_organization.publish_organization_functions(_aws(state), archives, "d" * 40)
    assert state["calls"] == []


def test_organization_release_rejects_a_package_missing_required_modules(tmp_path):
    archives = _archives(tmp_path)
    path = tmp_path / "erap-get-organization.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("get_handler.py", b"")
    archives["erap-get-organization"] = path
    state = {"calls": [], "alias": {name: "78" for name in FUNCTIONS}}
    with pytest.raises(SystemExit, match="zip entries"):
        release_organization.publish_organization_functions(_aws(state), archives, "d" * 40)
    assert state["calls"] == []


def test_organization_release_refuses_wrong_account(tmp_path):
    state = {
        "calls": [],
        "alias": {name: "78" for name in FUNCTIONS},
        "wrong_account": True,
    }
    with pytest.raises(SystemExit, match="account"):
        release_organization.publish_organization_functions(
            _aws(state), _archives(tmp_path), "d" * 40
        )
    assert ["lambda", "update-function-code"] not in [call[:2] for call in state["calls"]]


def test_organization_release_refuses_wrong_region(monkeypatch, tmp_path):
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    state = {"calls": [], "alias": {name: "78" for name in FUNCTIONS}}
    with pytest.raises(SystemExit, match="eu-north-1"):
        release_organization.publish_organization_functions(
            _aws(state), _archives(tmp_path), "d" * 40
        )
    assert state["calls"] == []


def test_organization_release_stops_before_publish_when_configuration_drifts(tmp_path):
    state = {
        "calls": [],
        "alias": {name: "78" for name in FUNCTIONS},
        "drift": ("erap-get-organization", None),
    }
    with pytest.raises(SystemExit, match="drifted"):
        release_organization.publish_organization_functions(
            _aws(state), _archives(tmp_path), "d" * 40
        )
    assert ["lambda", "update-alias"] not in [call[:2] for call in state["calls"]]


def test_organization_release_refuses_outside_production(monkeypatch):
    monkeypatch.delenv("ERAP_ENVIRONMENT", raising=False)
    with pytest.raises(SystemExit, match="alias live"):
        release_organization.main()


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


def test_organization_release_rejects_a_sha_that_is_not_on_main(monkeypatch, tmp_path):
    side_sha = _repository(tmp_path)
    monkeypatch.setenv("ERAP_ENVIRONMENT", "production")
    monkeypatch.setenv("RELEASE_SHA", side_sha)
    monkeypatch.setenv("RELEASE_MAIN_REF", "main")
    monkeypatch.setattr(release_organization, "ROOT", tmp_path)
    with pytest.raises(SystemExit, match="not on main"):
        release_organization.main()


def test_organization_rollback_moves_only_the_three_aliases():
    state = {"calls": [], "alias": {name: "79" for name in FUNCTIONS}}
    versions = {name: "78" for name in FUNCTIONS}
    report = rollback_organization.rollback_organization_functions(_aws(state), versions)
    commands = [call[1] for call in state["calls"]]
    assert "update-function-code" not in commands
    assert "publish-version" not in commands
    assert "update-function-configuration" not in commands
    assert {item["function"] for item in report["functions"]} == set(FUNCTIONS)
    assert all(item["alias_version"] == "78" for item in report["functions"])
    assert all(call[3] in FUNCTIONS for call in state["calls"] if "--function-name" in call)


def test_organization_rollback_rejects_extra_function_names():
    state = {"calls": [], "alias": {name: "79" for name in FUNCTIONS}}
    versions = {name: "78" for name in FUNCTIONS}
    versions["erap-billing"] = "1"
    with pytest.raises(SystemExit, match="exactly the three"):
        rollback_organization.rollback_organization_functions(_aws(state), versions)
    assert state["calls"] == []


@pytest.mark.parametrize("version", ["", "0", "latest", "d364d9a2742adb9b51a2509f33cb0f1581ec0a4a"])
def test_organization_rollback_rejects_non_versions(version):
    state = {"calls": [], "alias": {name: "79" for name in FUNCTIONS}}
    versions = {name: "78" for name in FUNCTIONS}
    versions["erap-locations"] = version
    with pytest.raises(SystemExit, match="version"):
        rollback_organization.rollback_organization_functions(_aws(state), versions)


def test_organization_rollback_rejects_a_version_from_another_function():
    state = {
        "calls": [],
        "alias": {name: "79" for name in FUNCTIONS},
        "foreign_function": True,
        "requested": "88",
    }
    versions = {name: "78" for name in FUNCTIONS}
    versions["erap-get-organization"] = "88"
    with pytest.raises(SystemExit, match="does not belong"):
        rollback_organization.rollback_organization_functions(_aws(state), versions)
    assert ["lambda", "update-alias"] not in [call[:2] for call in state["calls"]]


def test_organization_rollback_refuses_outside_production(monkeypatch):
    monkeypatch.delenv("ERAP_ENVIRONMENT", raising=False)
    with pytest.raises(SystemExit, match="alias live"):
        rollback_organization.main()


def test_organization_workflows_are_manual_production_and_scoped():
    release = (ROOT / ".github" / "workflows" / "release-organization.yml").read_text(encoding="utf-8")
    rollback = (ROOT / ".github" / "workflows" / "rollback-organization.yml").read_text(encoding="utf-8")
    general = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "workflow_dispatch" in release
    assert "environment: production" in release
    assert "refs/heads/main" in release
    assert "release_provenance.py" in release
    assert "release_organization.py" in release
    assert "push:" not in release
    assert "deploy_backend.py" not in release
    assert "erap-billing" not in release
    assert "get-resources" not in release
    assert "workflow_dispatch" in rollback
    assert "environment: production" in rollback
    assert "rollback_organization.py" in rollback
    assert "update-function-code" not in rollback
    assert "publish-version" not in rollback
    assert "inputs.commit" not in rollback
    assert "release_organization.py" not in general
    assert "ERAP-GitHub-Production-Organization" in release
    assert "ERAP-GitHub-Production-Organization" in rollback
    assert "role/ERAP-GitHub-Production\n" not in release.replace("\r", "")
    assert "role/ERAP-GitHub-Production\n" not in rollback.replace("\r", "")
    publisher = (ROOT / "scripts" / "release_organization.py").read_text(encoding="utf-8")
    assert "update-function-configuration" not in publisher
    assert "put-role-policy" not in publisher
    assert "deploy_backend.py" not in publisher
    policy = json.loads(
        (ROOT / "infra" / "github-production-organization-policy.json").read_text(encoding="utf-8")
    )
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
    assert "iam:PassRole" not in actions
    assert "dynamodb:PutItem" not in actions
    assert set(resources) == {
        "arn:aws:lambda:eu-north-1:481838970142:function:erap-get-organization",
        "arn:aws:lambda:eu-north-1:481838970142:function:erap-get-organization:*",
        "arn:aws:lambda:eu-north-1:481838970142:function:erap-create-organization",
        "arn:aws:lambda:eu-north-1:481838970142:function:erap-create-organization:*",
        "arn:aws:lambda:eu-north-1:481838970142:function:erap-locations",
        "arn:aws:lambda:eu-north-1:481838970142:function:erap-locations:*",
    }
    trust = json.loads((ROOT / "infra" / "github-production-trust.json").read_text(encoding="utf-8"))
    refs = trust["Statement"][0]["Condition"]["StringEquals"]["token.actions.githubusercontent.com:job_workflow_ref"]
    assert all("release-organization.yml" not in item for item in refs)
    assert all("rollback-organization.yml" not in item for item in refs)
    dedicated = json.loads(
        (ROOT / "infra" / "github-production-organization-trust.json").read_text(encoding="utf-8")
    )
    dedicated_refs = dedicated["Statement"][0]["Condition"]["StringEquals"][
        "token.actions.githubusercontent.com:job_workflow_ref"
    ]
    assert dedicated_refs == [
        "sricharanreddy5414/emergency-resource-allocation/.github/workflows/release-organization.yml@refs/heads/main",
        "sricharanreddy5414/emergency-resource-allocation/.github/workflows/rollback-organization.yml@refs/heads/main",
    ]
    setup = (ROOT / "scripts" / "setup_github_oidc_organization.py").read_text(encoding="utf-8")
    assert "ERAP-GitHub-Production-Organization" in setup
    assert "github-production-organization-policy.json" in setup
    assert "github-deploy-policy.json" not in setup
    assert "DEPLOY_ROLE" not in setup
    assert "PRODUCTION_ROLE" not in setup
    assert "setup_github_oidc.py" not in setup
