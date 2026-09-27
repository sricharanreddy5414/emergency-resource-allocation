import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(args):
    result = subprocess.run([sys.executable, *args], cwd=ROOT, text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def test_lambda_packages_match_the_manifest():
    output = run(["scripts/package_lambdas.py", "--check"])
    assert "ok get-resources" in output
    assert "ok erap-public-resources" in output


def test_security_scan_passes():
    output = run(["scripts/security_scan.py"])
    assert "security scan passed" in output


def test_workflows_do_not_request_static_keys():
    output = run(["scripts/check_workflows.py"])
    assert "workflow check passed" in output


def test_frontend_syntax():
    output = run(["scripts/check_frontend.py"])
    assert "frontend syntax passed" in output
    assert "NO_FRONTEND_BUILD" in output


def test_deploy_policy_cannot_mutate_data_or_api():
    policy = json.loads((ROOT / "infra" / "github-deploy-policy.json").read_text(encoding="utf-8"))
    actions = []
    for statement in policy["Statement"]:
        action = statement["Action"]
        actions.extend(action if isinstance(action, list) else [action])
    denied = [
        "dynamodb:PutItem",
        "dynamodb:DeleteTable",
        "dynamodb:Scan",
        "lambda:DeleteFunction",
        "lambda:UpdateFunctionConfiguration",
        "apigateway:DELETE",
        "apigateway:PUT",
        "iam:CreateUser",
    ]
    for action in denied:
        assert action not in actions
    deploy = (ROOT / "scripts" / "deploy_backend.py").read_text(encoding="utf-8")
    assert "migrate_tenant_scope" not in deploy
    assert "delete-table" not in deploy
    assert "update-stage" not in deploy


def test_alias_uri_adds_live_once():
    sys.path.insert(0, str(ROOT / "scripts"))
    from alias_uri import live_invocation_uri

    original = "arn:aws:apigateway:eu-north-1:lambda:path/2015-03-31/functions/arn:aws:lambda:eu-north-1:1:function:get-resources/invocations"
    updated = live_invocation_uri(original, ["get-resources"])
    assert updated.endswith("function:get-resources:live/invocations")
    assert live_invocation_uri(updated, ["get-resources"]) == ""
    assert live_invocation_uri("MOCK", ["get-resources"]) == ""


def test_workflows_do_not_apply_tenant_migration():
    folder = ROOT / ".github" / "workflows"
    for path in folder.glob("*.yml"):
        assert "migrate_tenant_scope" not in path.read_text(encoding="utf-8")
