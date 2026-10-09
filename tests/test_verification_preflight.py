"""Local fail-closed checks for the verification manifest. No AWS calls."""

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_preflight():
    path = ROOT / "scripts" / "verification_preflight.py"
    spec = importlib.util.spec_from_file_location("verification_preflight", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


preflight_module = load_preflight()


def manifest():
    account = "210987654321"
    region = "eu-north-1"
    api = "abcd1234ef"
    pool = "eu-north-1_VerifyPool1"
    app = "dverifyapp0001"
    amplify_region = "us-east-1"
    authorizer = "a1b2c3"
    branch = "verification"
    origin = f"https://{branch}.{app}.amplifyapp.com"
    lambda_role = f"arn:aws:iam::{account}:role/erap-verification-lambda"
    payload = {
        "purpose": "erap-verification",
        "account_id": account,
        "region": region,
        "environment": "verification",
        "teardown_enabled": False,
        "frontend_origin": origin,
        "lambda_alias": "verify",
        "api": {
            "id": api,
            "stage": "verify",
            "arn": f"arn:aws:execute-api:{region}:{account}:{api}",
            "routes": [],
        },
        "cognito": {
            "pool_id": pool,
            "arn": f"arn:aws:cognito-idp:{region}:{account}:userpool/{pool}",
            "authorizer": {
                "id": authorizer,
                "type": "COGNITO_USER_POOLS",
                "pool_id": pool,
                "api_id": api,
            },
        },
        "amplify": {
            "app_id": app,
            "branch": branch,
            "region": amplify_region,
            "arn": f"arn:aws:amplify:{amplify_region}:{account}:apps/{app}",
            "origin": origin,
        },
        "cors": {
            "allow_origin": origin,
            "allow_methods": ["GET", "POST", "OPTIONS"],
            "allow_headers": ["Content-Type", "Authorization"],
        },
        "functions": [],
        "tables": [],
        "roles": [
            {"name": "erap-verification-deploy", "arn": f"arn:aws:iam::{account}:role/erap-verification-deploy"},
            {"name": "erap-verification-operator", "arn": f"arn:aws:iam::{account}:role/erap-verification-operator"},
            {"name": "erap-verification-lambda", "arn": lambda_role},
        ],
        "protected": {
            "account_id": "481838970142",
            "api_id": "4c6dni17l3",
            "api_stage": "dev",
            "lambda_alias": "emergency-resource-allocation:live",
            "cognito_pool_id": "eu-north-1_vv7adAAC9",
            "amplify_app_id": "d3enpe7opotop5",
            "authorizer_id": "y0hzhr",
        },
        "approvals": [
            {"id": approval_id, "status": "approved"}
            for approval_id in preflight_module.REQUIRED_APPROVALS
        ],
    }
    for name in preflight_module.REQUIRED_FUNCTIONS:
        payload["functions"].append({
            "name": name,
            "arn": f"arn:aws:lambda:{region}:{account}:function:{name}",
            "environment": "verification",
            "role_arn": lambda_role,
        })
    for name in preflight_module.REQUIRED_TABLES:
        payload["tables"].append({
            "name": name,
            "arn": f"arn:aws:dynamodb:{region}:{account}:table/{name}",
        })
    for method, path, function in preflight_module.REQUIRED_ROUTES:
        payload["api"]["routes"].append({
            "method": method,
            "path": path,
            "function": function,
            "authorizer_id": authorizer,
            "integration_arn": f"arn:aws:lambda:{region}:{account}:function:{function}:verify",
        })
    return payload


def assert_blocked(payload, message):
    errors = preflight_module.preflight(payload)
    assert message in errors
    with pytest.raises(preflight_module.VerificationBlocked) as caught:
        preflight_module.ready(payload)
    assert message in str(caught.value)


def test_checked_in_manifest_fails_closed_through_ready():
    payload = preflight_module.load_manifest(ROOT / "config" / "erap-verification-manifest.json")
    assert payload["account_id"] == "917320177579"
    assert payload["account_id"] != payload["protected"]["account_id"]
    assert payload["teardown_enabled"] is False
    assert_blocked(payload, "api id contains an unresolved marker")
    assert_blocked(payload, "approval dedicated-account is not approved")


def test_load_manifest_does_not_validate():
    payload = preflight_module.load_manifest(ROOT / "config" / "erap-verification-manifest.json")
    assert payload["api"]["id"] == "UNRESOLVED"
    with pytest.raises(preflight_module.VerificationBlocked):
        preflight_module.ready(payload)


def test_ready_accepts_only_a_complete_synthetic_manifest_and_teardown_stays_blocked():
    assert preflight_module.ready(manifest()) is True
    with pytest.raises(preflight_module.VerificationBlocked, match="Teardown is disabled"):
        preflight_module.refuse_teardown()


def test_ready_rejects_when_role_checks_pass_and_an_approval_is_pending():
    payload = manifest()
    payload["approvals"][0]["status"] = "pending"
    role_errors = []
    preflight_module._check_roles(payload, payload["account_id"], role_errors)
    assert role_errors == []
    assert_blocked(payload, "approval dedicated-account is not approved")


def test_main_returns_failure_for_the_checked_in_manifest(capsys):
    code = preflight_module.main([])
    captured = capsys.readouterr()
    assert code == 1
    assert "verification preflight failed:" in captured.out
    assert "api id contains an unresolved marker" in captured.out
    assert "authorizer id contains an unresolved marker" in captured.out
    assert "cors allow_origin contains an unresolved marker" in captured.out
    assert "approval dedicated-account is not approved" in captured.out


def test_main_success_text_does_not_authorize_deployment(tmp_path, capsys):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest()), encoding="utf-8")
    code = preflight_module.main(["--manifest", str(path)])
    captured = capsys.readouterr()
    assert code == 0
    assert "does not authorize deployment, provisioning, or teardown" in captured.out
    with pytest.raises(preflight_module.VerificationBlocked):
        preflight_module.refuse_teardown()


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda payload: payload.__setitem__("note", "extra"), "manifest has unsupported fields: note"),
        (lambda payload: payload["api"].__setitem__("deploy_stage", "dev"), "api has unsupported fields: deploy_stage"),
        (lambda payload: payload["api"]["routes"][0].__setitem__("qualifier", "live"), "routes[0] has unsupported fields: qualifier"),
        (lambda payload: payload["functions"][0].__setitem__("qualifier", "live"), "functions[0] has unsupported fields: qualifier"),
        (lambda payload: payload["cognito"]["authorizer"].__setitem__("provider", "y0hzhr"), "authorizer has unsupported fields: provider"),
        (lambda payload: payload["cors"].__setitem__("allow_credentials", True), "cors has unsupported fields: allow_credentials"),
        (lambda payload: payload["tables"][0].__setitem__("arn_alias", "extra"), "tables[0] has unsupported fields: arn_alias"),
    ],
)
def test_unknown_keys_are_rejected_at_each_level(mutate, message):
    payload = manifest()
    mutate(payload)
    assert_blocked(payload, message)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda payload: payload["tables"][0].__setitem__(
                "arn", "arn:aws:dynamodb:eu-north-1:481838970142:table/Organizations"
            ),
            "Organizations ARN uses the protected account",
        ),
        (lambda payload: payload["api"].__setitem__("id", "4c6dni17l3"), "api id must not be the protected API"),
        (
            lambda payload: payload["cognito"].__setitem__("pool_id", "eu-north-1_vv7adAAC9"),
            "cognito pool_id must not be the protected pool",
        ),
        (
            lambda payload: payload["amplify"].__setitem__("app_id", "d3enpe7opotop5"),
            "amplify app_id must not be the protected app",
        ),
        (lambda payload: payload["api"].__setitem__("stage", "dev"), "api stage must not be dev"),
        (
            lambda payload: payload.__setitem__("lambda_alias", "emergency-resource-allocation:live"),
            "lambda_alias must not be the protected live alias",
        ),
        (
            lambda payload: payload["cognito"]["authorizer"].__setitem__("id", "y0hzhr"),
            "authorizer id must not be the protected authorizer",
        ),
    ],
)
def test_nested_protected_identifiers_and_unsafe_stage_or_alias_fail(mutate, message):
    payload = manifest()
    mutate(payload)
    assert_blocked(payload, message)


@pytest.mark.parametrize(
    ("value", "message"),
    [
        (210987654321, "account_id must be a 12-digit string"),
        (True, "account_id must be a 12-digit string"),
        (None, "account_id must be a 12-digit string"),
        ("1234", "account_id must be a 12-digit string"),
        ("UNRESOLVED", "account_id contains an unresolved marker"),
        (" 210987654321", "account_id must not contain whitespace"),
        ("481838970142", "account_id must not be the protected account"),
        ("000000000000", "account_id contains an unresolved marker"),
    ],
)
def test_account_id_type_and_placeholder_values_fail(value, message):
    payload = manifest()
    payload["account_id"] = value
    assert_blocked(payload, message)


def test_duplicate_function_table_role_and_approval_identifiers_fail():
    payload = manifest()
    payload["functions"].append(dict(payload["functions"][0]))
    assert_blocked(payload, "duplicate function name: erap-create-organization")

    payload = manifest()
    payload["tables"].append(dict(payload["tables"][0]))
    assert_blocked(payload, "duplicate table name: Organizations")

    payload = manifest()
    payload["roles"].append(dict(payload["roles"][2]))
    assert_blocked(payload, "duplicate role name: erap-verification-lambda")

    payload = manifest()
    payload["approvals"].append(dict(payload["approvals"][0]))
    assert_blocked(payload, "duplicate approval id: dedicated-account")


def test_missing_and_malformed_route_authorizer_cors_stage_and_origin_fail():
    payload = manifest()
    payload["api"]["routes"] = []
    assert_blocked(payload, "routes must be the required verification methods in order")

    payload = manifest()
    payload["api"]["routes"] = "GET"
    assert_blocked(payload, "routes must be a list")

    payload = manifest()
    payload["cognito"]["authorizer"] = None
    assert_blocked(payload, "authorizer must be an object")

    payload = manifest()
    payload["cors"] = None
    assert_blocked(payload, "cors must be an object")

    payload = manifest()
    payload["api"]["stage"] = "prod"
    assert_blocked(payload, "api stage must be verify")

    payload = manifest()
    payload["frontend_origin"] = "https://other.dverifyapp0001.amplifyapp.com"
    payload["amplify"]["origin"] = payload["frontend_origin"]
    payload["cors"]["allow_origin"] = payload["frontend_origin"]
    assert_blocked(payload, "frontend origin must match the verification Amplify branch origin")


def test_authorizer_must_reference_the_verification_pool_and_api():
    payload = manifest()
    payload["cognito"]["authorizer"]["pool_id"] = "eu-north-1_OtherPool1"
    assert_blocked(payload, "authorizer pool_id must match the verification Cognito pool")

    payload = manifest()
    payload["cognito"]["authorizer"]["api_id"] = "zzzz9999yy"
    assert_blocked(payload, "authorizer api_id must match the verification API")


def test_protected_account_and_cross_account_arns_fail():
    payload = manifest()
    payload["tables"][0]["arn"] = "arn:aws:dynamodb:eu-north-1:481838970142:table/Organizations"
    assert_blocked(payload, "Organizations ARN uses the protected account")

    payload = manifest()
    payload["tables"][0]["arn"] = "arn:aws:dynamodb:eu-north-1:210987654322:table/Organizations"
    assert_blocked(payload, "Organizations ARN account does not match the verification account")

    payload = manifest()
    payload["functions"][0]["role_arn"] = "arn:aws:iam::210987654322:role/erap-verification-lambda"
    assert_blocked(payload, "erap-create-organization role_arn must be the verification lambda role in the verification account")


def test_missing_environment_and_wrong_environment_fail():
    payload = manifest()
    del payload["functions"][0]["environment"]
    assert_blocked(payload, "functions[0] is missing fields: environment")

    payload = manifest()
    payload["functions"][0]["environment"] = "production"
    assert_blocked(payload, "erap-create-organization environment must be verification")


def test_wildcards_and_embedded_unresolved_markers_fail():
    payload = manifest()
    payload["roles"][0]["arn"] = "arn:aws:iam::210987654321:role/erap-verification-deploy?"
    assert_blocked(payload, "erap-verification-deploy ARN contains a wildcard")

    payload = manifest()
    payload["api"]["arn"] = "arn:aws:execute-api:eu-north-1:210987654321:abcd1234ef/*"
    assert_blocked(payload, "api ARN contains a wildcard")

    payload = manifest()
    payload["frontend_origin"] = "https://verification.dverifyapp0001.amplifyapp.com.unresolved"
    assert_blocked(payload, "frontend origin contains an unresolved marker")


@pytest.mark.parametrize("value", [None, True, "false", 0, "no"])
def test_teardown_must_be_boolean_false(value):
    payload = manifest()
    payload["teardown_enabled"] = value
    assert_blocked(payload, "teardown_enabled must be false")


def test_missing_teardown_field_fails():
    payload = manifest()
    del payload["teardown_enabled"]
    assert_blocked(payload, "teardown_enabled must be false")
    assert_blocked(payload, "manifest is missing fields: teardown_enabled")


def test_production_origin_with_main_branch_fails():
    payload = manifest()
    payload["amplify"]["branch"] = "main"
    payload["amplify"]["app_id"] = "d3enpe7opotop5"
    origin = "https://main.d3enpe7opotop5.amplifyapp.com"
    payload["frontend_origin"] = origin
    payload["amplify"]["origin"] = origin
    payload["cors"]["allow_origin"] = origin
    payload["amplify"]["arn"] = "arn:aws:amplify:us-east-1:210987654321:apps/d3enpe7opotop5"
    assert_blocked(payload, "amplify branch must not be the protected main branch")
    assert_blocked(payload, "frontend origin must not be the protected Amplify app")


def test_bare_live_alias_fails():
    payload = manifest()
    payload["lambda_alias"] = "live"
    assert_blocked(payload, "lambda_alias must be verify")


def test_route_integration_live_alias_fails():
    payload = manifest()
    payload["api"]["routes"][2]["integration_arn"] = payload["api"]["routes"][2]["integration_arn"].replace(
        ":verify", ":live"
    )
    assert_blocked(payload, "routes[2] integration ARN must not use the protected live alias")


def test_duplicate_route_fails():
    payload = manifest()
    payload["api"]["routes"].append(dict(payload["api"]["routes"][0]))
    assert_blocked(payload, "duplicate route: POST /organization")


def test_cors_origin_must_match_frontend_origin():
    payload = manifest()
    payload["cors"]["allow_origin"] = "https://other.dverifyapp0001.amplifyapp.com"
    assert_blocked(payload, "cors allow_origin must match the frontend origin")


def test_route_authorizer_must_match_cognito_authorizer():
    payload = manifest()
    payload["api"]["routes"][0]["authorizer_id"] = "b9c8d7"
    assert_blocked(payload, "routes[0] authorizer_id must match the verification authorizer")


def test_unknown_amplify_and_role_keys_fail():
    payload = manifest()
    payload["amplify"]["note"] = "extra"
    assert_blocked(payload, "amplify has unsupported fields: note")

    payload = manifest()
    payload["roles"][0]["policy"] = "inline"
    assert_blocked(payload, "roles[0] has unsupported fields: policy")


def test_numeric_and_malformed_pool_ids_fail():
    payload = manifest()
    payload["cognito"]["pool_id"] = 12345
    assert_blocked(payload, "cognito pool_id must be a string")

    payload = manifest()
    payload["cognito"]["pool_id"] = "not-a-pool"
    assert_blocked(payload, "cognito pool_id must be a verification pool id")


def test_cognito_pool_region_must_match_arn_region():
    payload = manifest()
    pool = "us-east-1_VerifyPool1"
    payload["cognito"]["pool_id"] = pool
    payload["cognito"]["authorizer"]["pool_id"] = pool
    payload["cognito"]["arn"] = f"arn:aws:cognito-idp:eu-north-1:210987654321:userpool/{pool}"
    errors = preflight_module.preflight(payload)
    assert errors == ["cognito pool_id region does not match the Cognito ARN region"]
    assert_blocked(payload, "cognito pool_id region does not match the Cognito ARN region")


def test_missing_pending_and_malformed_approvals_fail():
    payload = manifest()
    del payload["approvals"]
    assert_blocked(payload, "approvals must be a list")

    payload = manifest()
    payload["approvals"][0]["status"] = "pending"
    assert_blocked(payload, "approval dedicated-account is not approved")

    payload = manifest()
    payload["approvals"].append("pending")
    assert_blocked(payload, "approvals[6] must be an object")

    payload = manifest()
    payload["approvals"] = {"id": "dedicated-account", "status": "approved"}
    assert_blocked(payload, "approvals must be a list")
