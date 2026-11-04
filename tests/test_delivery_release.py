"""Controlled delivery-module release. These tests do not call AWS."""

import json
import os
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(ROOT / "scripts"))

import release_delivery
import rollback_delivery
from lambda_manifest import ACCOUNT, package_map

FUNCTION = "emergency-resource-allocation"
ROLE = f"arn:aws:iam::{ACCOUNT}:role/service-role/erap-allocation-role"


def _zip(path):
    names = package_map()[FUNCTION]
    with zipfile.ZipFile(path, "w") as archive:
        for entry in names:
            body = b""
            if entry == "lambda_function.py":
                body = b"from delivery_handler import handle_delivery, is_delivery_path\n"
            archive.writestr(entry, body)
    return path


def _config(code_sha="before", memory=256, role=ROLE):
    return {
        "FunctionName": FUNCTION,
        "Runtime": "python3.14",
        "Handler": "lambda_function.lambda_handler",
        "Role": role,
        "MemorySize": memory,
        "Timeout": 15,
        "Environment": None,
        "VpcConfig": {},
        "Layers": [],
        "Architectures": ["x86_64"],
        "CodeSha256": code_sha,
        "LastUpdateStatus": "Successful",
        "Description": "commit=previous",
    }


def _aws(state):
    def aws(args, region=None):
        if region and region != "eu-north-1":
            raise AssertionError(region)
        state["calls"].append(list(args))
        if args[:2] == ["lambda", "get-alias"]:
            return {"FunctionVersion": state["alias"]}
        if args[:2] == ["lambda", "get-function-configuration"]:
            qualifier = args[args.index("--qualifier") + 1] if "--qualifier" in args else None
            code_sha = "latest" if qualifier is None else "published"
            if qualifier == state.get("previous"):
                code_sha = "before"
            if qualifier == state.get("published"):
                code_sha = "latest"
            config = _config(code_sha)
            if state.get("wrong_account"):
                config["Role"] = "arn:aws:iam::000000000000:role/other"
            return config
        if args[:2] == ["lambda", "update-function-code"]:
            state["updated"] = True
            return {}
        if args[:2] == ["lambda", "publish-version"]:
            state["published"] = "12"
            return {"Version": "12"}
        if args[:2] == ["lambda", "update-alias"]:
            state["alias"] = args[args.index("--function-version") + 1]
            return {}
        raise AssertionError(args)

    return aws


def test_delivery_release_validates_zip_before_alias_read(tmp_path):
    archive = tmp_path / "empty.zip"
    with zipfile.ZipFile(archive, "w") as packaged:
        packaged.writestr("lambda_function.py", b"print(1)")
    state = {"calls": [], "alias": "9", "previous": "9"}
    with pytest.raises(SystemExit, match="zip"):
        release_delivery.publish_delivery(_aws(state), archive, "a" * 40)
    assert state["calls"] == []


def test_delivery_release_publishes_one_function(tmp_path):
    archive = _zip(tmp_path / "fn.zip")
    state = {"calls": [], "alias": "9", "previous": "9"}
    report = release_delivery.publish_delivery(_aws(state), archive, "b" * 40)
    assert report["previous_version"] == "9"
    assert report["published_version"] == "12"
    assert report["function"] == FUNCTION
    assert ["lambda", "get-alias"] in [call[:2] for call in state["calls"]]
    assert ["lambda", "update-function-code"] in [call[:2] for call in state["calls"]]
    assert ["lambda", "publish-version"] in [call[:2] for call in state["calls"]]
    assert ["lambda", "update-alias"] in [call[:2] for call in state["calls"]]


def test_delivery_release_refuses_outside_production(monkeypatch):
    monkeypatch.delenv("ERAP_ENVIRONMENT", raising=False)
    with pytest.raises(SystemExit, match="alias live"):
        release_delivery.main()


def test_delivery_rollback_moves_numeric_version():
    state = {"calls": [], "alias": "12"}
    report = rollback_delivery.rollback_delivery(_aws(state), "9")
    assert report["alias_version"] == "9"
    assert report["published"] is False


def test_delivery_rollback_rejects_non_version():
    state = {"calls": [], "alias": "12"}
    with pytest.raises(SystemExit, match="version"):
        rollback_delivery.rollback_delivery(_aws(state), "live")


def test_delivery_workflows_are_manual_production_and_scoped():
    release = (ROOT / ".github" / "workflows" / "release-delivery.yml").read_text(encoding="utf-8")
    rollback = (ROOT / ".github" / "workflows" / "rollback-delivery.yml").read_text(encoding="utf-8")
    general = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "workflow_dispatch" in release
    assert "environment: production" in release
    assert "release_delivery.py" in release
    assert "release_allocation.py" not in release
    assert "push:" not in release
    assert "ERAP-GitHub-Production-Delivery" in release
    assert "role/ERAP-GitHub-Production\n" not in release.replace("\r", "")
    assert "release_delivery.py" not in general
    policy = json.loads((ROOT / "infra" / "github-production-delivery-policy.json").read_text(encoding="utf-8"))
    actions = []
    resources = []
    for statement in policy["Statement"]:
        action = statement["Action"]
        resource = statement["Resource"]
        actions.extend(action if isinstance(action, list) else [action])
        resources.extend(resource if isinstance(resource, list) else [resource])
    assert "iam:PassRole" not in actions
    assert "lambda:CreateAlias" not in actions
    assert set(resources) == {
        "arn:aws:lambda:eu-north-1:481838970142:function:emergency-resource-allocation",
        "arn:aws:lambda:eu-north-1:481838970142:function:emergency-resource-allocation:*",
    }
    shared = json.loads((ROOT / "infra" / "github-production-trust.json").read_text(encoding="utf-8"))
    refs = shared["Statement"][0]["Condition"]["StringEquals"]["token.actions.githubusercontent.com:job_workflow_ref"]
    assert all("release-delivery.yml" not in item for item in refs)
    dedicated = json.loads((ROOT / "infra" / "github-production-delivery-trust.json").read_text(encoding="utf-8"))
    dedicated_refs = dedicated["Statement"][0]["Condition"]["StringEquals"][
        "token.actions.githubusercontent.com:job_workflow_ref"
    ]
    assert dedicated_refs == [
        "sricharanreddy5414/emergency-resource-allocation/.github/workflows/release-delivery.yml@refs/heads/main",
        "sricharanreddy5414/emergency-resource-allocation/.github/workflows/rollback-delivery.yml@refs/heads/main",
    ]
    setup = (ROOT / "scripts" / "setup_github_oidc_delivery.py").read_text(encoding="utf-8")
    assert "ERAP-GitHub-Production-Delivery" in setup
    assert "github-production-delivery-policy.json" in setup
    assert "github-deploy-policy.json" not in setup
    assert "DEPLOY_ROLE" not in setup
    assert rollback.count("workflow_dispatch") == 1
    table = json.loads((ROOT / "infra" / "deliveries-table.json").read_text(encoding="utf-8"))
    spec = table["tables"][0]
    assert spec["TableName"] == "Deliveries"
    assert spec["BillingMode"] == "PAY_PER_REQUEST"
    assert spec["KeySchema"] == [{"AttributeName": "delivery_id", "KeyType": "HASH"}]
    gsi = spec["GlobalSecondaryIndexes"][0]
    assert gsi["IndexName"] == "OrganizationLocationIndex"
    assert gsi["KeySchema"] == [
        {"AttributeName": "organization_id", "KeyType": "HASH"},
        {"AttributeName": "location_id", "KeyType": "RANGE"},
    ]
    assert set(table["runtime_iam_required_on_emergency_resource_allocation_role"]) == {
        "dynamodb:GetItem",
        "dynamodb:PutItem",
        "dynamodb:Query",
    }
    assert "dynamodb:Scan" in table["runtime_iam_not_requested"]
    assert "dynamodb:DeleteItem" in table["runtime_iam_not_requested"]
    assert isinstance(table.get("applied"), bool)


def test_delivery_release_refuses_wrong_account(tmp_path):
    archive = _zip(tmp_path / "fn.zip")
    state = {"calls": [], "alias": "9", "previous": "9", "wrong_account": True}
    with pytest.raises(SystemExit, match="account"):
        release_delivery.publish_delivery(_aws(state), archive, "c" * 40)
    assert ["lambda", "update-function-code"] not in [call[:2] for call in state["calls"]]
