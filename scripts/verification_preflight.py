"""Fail-closed local validator for a future ERAP verification manifest.

This module parses and checks a JSON document. It does not call AWS, create
resources, publish aliases, or delete anything. A passing result is not
authorization to deploy, provision, assume a role, or tear anything down.

ready() is the validation predicate for a future deployment gate. preflight()
returns the errors that predicate uses; those results must not be ignored.
load_manifest() only parses JSON and must not be used as validation. Catching
VerificationBlocked must stop the operation. It must not be treated as permission
to continue.

scripts/deploy_backend.py and the existing workflows do not call this module.
They must stay unwired and must not provision a verification stack until a
separately reviewed integration phase.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


class VerificationBlocked(RuntimeError):
    """Raised when validation fails or teardown is requested."""


PROTECTED_ACCOUNT = "481838970142"
PROTECTED_API = "4c6dni17l3"
PROTECTED_STAGE = "dev"
PROTECTED_ALIAS = "emergency-resource-allocation:live"
PROTECTED_POOL = "eu-north-1_vv7adAAC9"
PROTECTED_AMPLIFY = "d3enpe7opotop5"
PROTECTED_ORIGIN = "https://main.d3enpe7opotop5.amplifyapp.com"
PROTECTED_AUTHORIZER = "y0hzhr"
PROTECTED_BILLING_SECRET = "erap/billing/razorpay/production"
PROTECTED_ALARM = "ERAP-Production-Alarms"

PURPOSE = "erap-verification"
REQUIRED_ENVIRONMENT = "verification"
REQUIRED_REGION = "eu-north-1"
REQUIRED_STAGE = "verify"
REQUIRED_ALIAS = "verify"
REQUIRED_AUTHORIZER_TYPE = "COGNITO_USER_POOLS"
REQUIRED_BRANCH = "verification"
REQUIRED_CORS_METHODS = ("GET", "POST", "OPTIONS")
REQUIRED_CORS_HEADERS = ("Content-Type", "Authorization")

REQUIRED_FUNCTIONS = (
    "erap-create-organization",
    "erap-get-organization",
    "erap-locations",
    "erap-catalog",
    "create-request",
    "emergency-resource-allocation",
    "erap-notifications",
    "erap-exchange",
)
REQUIRED_TABLES = (
    "Organizations",
    "OrganizationMembers",
    "OrganizationSubscriptions",
    "Locations",
    "RequestTypes",
    "ResourceTypes",
    "EmergencyRequests",
    "Notifications",
    "ResourceExchanges",
)
REQUIRED_APPROVALS = (
    "dedicated-account",
    "guarded-deployer",
    "verification-stack",
    "verification-users",
    "synthetic-data",
    "teardown-arns",
)
REQUIRED_ROLES = (
    "erap-verification-deploy",
    "erap-verification-operator",
    "erap-verification-lambda",
)
# Application routes the planned checks need. These are not created by deploying
# the Lambda functions. Integration ARNs and the authorizer id stay unresolved
# until a real verification API is recorded.
REQUIRED_ROUTES = (
    ("POST", "/organization", "erap-create-organization"),
    ("GET", "/organization", "erap-get-organization"),
    ("POST", "/locations", "erap-locations"),
    ("POST", "/resource-types", "erap-catalog"),
    ("POST", "/request-types", "erap-catalog"),
    ("POST", "/requests", "create-request"),
    ("GET", "/requests/{request_id}", "emergency-resource-allocation"),
    ("GET", "/notifications", "erap-notifications"),
    ("POST", "/notifications/{notification_id}/read", "erap-notifications"),
    ("POST", "/exchange/requests", "erap-exchange"),
    ("GET", "/exchange/requests/{exchange_request_id}", "erap-exchange"),
)

TOP_LEVEL_KEYS = (
    "purpose",
    "account_id",
    "region",
    "environment",
    "teardown_enabled",
    "frontend_origin",
    "lambda_alias",
    "api",
    "cognito",
    "amplify",
    "cors",
    "functions",
    "tables",
    "roles",
    "protected",
    "approvals",
)
API_KEYS = ("id", "stage", "arn", "routes")
ROUTE_KEYS = ("method", "path", "function", "authorizer_id", "integration_arn")
COGNITO_KEYS = ("pool_id", "arn", "authorizer")
AUTHORIZER_KEYS = ("id", "type", "pool_id", "api_id")
AMPLIFY_KEYS = ("app_id", "branch", "region", "arn", "origin")
CORS_KEYS = ("allow_origin", "allow_methods", "allow_headers")
FUNCTION_KEYS = ("name", "arn", "environment", "role_arn")
TABLE_KEYS = ("name", "arn")
ROLE_KEYS = ("name", "arn")
APPROVAL_KEYS = ("id", "status")
PROTECTED_KEYS = (
    "account_id",
    "api_id",
    "api_stage",
    "lambda_alias",
    "cognito_pool_id",
    "amplify_app_id",
    "authorizer_id",
)

ACCOUNT_PATTERN = re.compile(r"^[0-9]{12}$")
API_PATTERN = re.compile(r"^[a-z0-9]{10}$")
AUTHORIZER_PATTERN = re.compile(r"^[a-z0-9]{6,12}$")
POOL_PATTERN = re.compile(r"^[a-z]{2}-(?:[a-z]+-)+[0-9]_[A-Za-z0-9]+$")
AMPLIFY_PATTERN = re.compile(r"^d[a-z0-9]{12,}$")
REGION_PATTERN = re.compile(r"^[a-z]{2}-[a-z]+-[0-9]$")
MARKER_PATTERN = re.compile(
    r"unresolved|placeholder|changeme|\btbd\b|\btodo\b|pending|example",
    re.IGNORECASE,
)
DEV_PATTERN = re.compile(r"(?<![A-Za-z0-9])dev(?![A-Za-z0-9])")
WILDCARD_CHARACTERS = ("*", "?")
EXACT_ACCOUNT_PLACEHOLDERS = {"000000000000", "123456789012"}


def refuse_teardown(*_args, **_kwargs):
    """Teardown cannot run. Recording ARNs later does not enable this function."""
    raise VerificationBlocked(
        "Teardown is disabled until verification-account resource ARNs are recorded and validated"
    )


def default_manifest_path(repo=None):
    root = Path(repo) if repo is not None else Path(__file__).resolve().parents[1]
    return root / "config" / "erap-verification-manifest.json"


def load_manifest(path):
    """Parse a manifest file.

    This does not validate the document. Call ready() before any future
    deployment decision. Do not treat the returned object as authorized input.
    """
    text = Path(path).read_text(encoding="utf-8")
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("manifest must be a JSON object")
    return payload


def preflight(manifest):
    """Return actionable errors. An empty list means the manifest is internally consistent.

    Callers must not ignore a non-empty result. ready() is the predicate that
    enforces that contract for a future gate.
    """
    if not isinstance(manifest, dict):
        return ["manifest must be a JSON object"]

    errors = []
    _object_schema(manifest, TOP_LEVEL_KEYS, "manifest", errors)
    _check_identity(manifest, errors)
    account = manifest.get("account_id") if isinstance(manifest.get("account_id"), str) else ""
    _check_api(manifest, account, errors)
    _check_cognito(manifest, account, errors)
    _check_amplify(manifest, errors)
    _check_cors(manifest, errors)
    _check_origin_relationship(manifest, errors)
    role_arns = _check_roles(manifest, account, errors)
    _check_functions(manifest, account, role_arns, errors)
    _check_tables(manifest, account, errors)
    _check_approvals(manifest, errors)
    _check_protected_block(manifest, errors)
    _check_forbidden_text(manifest, errors)
    return errors


def ready(manifest):
    """Return True only after every required check succeeds.

    This is the validation predicate for a future deployment gate. Success does
    not authorize account access, deployment, provisioning, or teardown. A caller
    that catches VerificationBlocked must stop.
    """
    errors = preflight(manifest)
    if errors:
        raise VerificationBlocked("; ".join(errors))
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Validate an ERAP verification manifest. Does not call AWS or authorize deployment."
    )
    parser.add_argument("--manifest", default=str(default_manifest_path()))
    args = parser.parse_args(argv)
    try:
        manifest = load_manifest(args.manifest)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"verification preflight failed: {error}")
        return 1
    errors = preflight(manifest)
    if errors:
        print("verification preflight failed:")
        for item in errors:
            print(f"- {item}")
        return 1
    print("verification preflight passed; this does not authorize deployment, provisioning, or teardown")
    return 0


def _object_schema(value, allowed, label, errors):
    if not isinstance(value, dict):
        errors.append(f"{label} must be an object")
        return False
    extra = sorted(set(value) - set(allowed))
    missing = [key for key in allowed if key not in value]
    if extra:
        errors.append(f"{label} has unsupported fields: {', '.join(extra)}")
    if missing:
        errors.append(f"{label} is missing fields: {', '.join(missing)}")
    return True


def _has_wildcard(value):
    return any(character in value for character in WILDCARD_CHARACTERS)


def _has_marker(value):
    return bool(MARKER_PATTERN.search(value))


def _clean_identifier(value, label, errors):
    """Return a usable string, or None after recording why the value is rejected."""
    if not isinstance(value, str):
        errors.append(f"{label} must be a string")
        return None
    if value != value.strip() or any(character.isspace() for character in value):
        errors.append(f"{label} must not contain whitespace")
        return None
    if value == "" or _has_marker(value):
        errors.append(f"{label} contains an unresolved marker")
        return None
    if _has_wildcard(value):
        errors.append(f"{label} contains a wildcard")
        return None
    return value


def _check_identity(manifest, errors):
    if manifest.get("purpose") != PURPOSE:
        errors.append("purpose must be erap-verification")
    if manifest.get("environment") != REQUIRED_ENVIRONMENT:
        errors.append("environment must be verification")
    if manifest.get("region") != REQUIRED_REGION:
        errors.append("region must be eu-north-1")
    if manifest.get("teardown_enabled") is not False:
        errors.append("teardown_enabled must be false")

    alias = manifest.get("lambda_alias")
    if alias == PROTECTED_ALIAS:
        errors.append("lambda_alias must not be the protected live alias")
    elif alias != REQUIRED_ALIAS:
        errors.append("lambda_alias must be verify")

    account = manifest.get("account_id")
    if "account_id" not in manifest:
        return
    if not isinstance(account, str):
        errors.append("account_id must be a 12-digit string")
        return
    if account != account.strip() or any(character.isspace() for character in account):
        errors.append("account_id must not contain whitespace")
        return
    if account == "" or _has_marker(account) or account in EXACT_ACCOUNT_PLACEHOLDERS:
        errors.append("account_id contains an unresolved marker")
        return
    if not ACCOUNT_PATTERN.fullmatch(account):
        errors.append("account_id must be a 12-digit string")
        return
    if account == PROTECTED_ACCOUNT:
        errors.append("account_id must not be the protected account")


def _expected_origin(branch, app_id):
    return f"https://{branch}.{app_id}.amplifyapp.com"


def _check_api(manifest, account, errors):
    api = manifest.get("api")
    if not isinstance(api, dict):
        errors.append("api must be an object")
        return
    _object_schema(api, API_KEYS, "api", errors)
    api_id = _clean_identifier(api.get("id"), "api id", errors) if "id" in api else None
    if isinstance(api_id, str) and api_id == PROTECTED_API:
        errors.append("api id must not be the protected API")
    elif isinstance(api_id, str) and not API_PATTERN.fullmatch(api_id):
        errors.append("api id must be a 10-character verification API id")

    stage = api.get("stage") if "stage" in api else None
    if "stage" in api:
        if stage == PROTECTED_STAGE:
            errors.append("api stage must not be dev")
        elif stage != REQUIRED_STAGE:
            errors.append("api stage must be verify")

    if isinstance(api_id, str) and isinstance(account, str) and account:
        _check_arn(
            api.get("arn"),
            service="execute-api",
            region=REQUIRED_REGION,
            account=account,
            resource=api_id,
            label="api",
            errors=errors,
        )
    elif "arn" in api:
        _clean_identifier(api.get("arn"), "api ARN", errors)

    _check_routes(api.get("routes") if "routes" in api else None, manifest, account, errors)


def _check_routes(routes, manifest, account, errors):
    if not isinstance(routes, list):
        errors.append("routes must be a list")
        return
    seen = []
    actual = []
    authorizer_id = _authorizer_id(manifest)
    for index, route in enumerate(routes):
        label = f"routes[{index}]"
        if not isinstance(route, dict):
            errors.append(f"{label} must be an object")
            continue
        _object_schema(route, ROUTE_KEYS, label, errors)
        method = route.get("method")
        path = route.get("path")
        function = route.get("function")
        if isinstance(method, str) and isinstance(path, str):
            identity = (method, path)
            if identity in seen:
                errors.append(f"duplicate route: {method} {path}")
            seen.append(identity)
            actual.append((method, path, function if isinstance(function, str) else ""))
        if function not in REQUIRED_FUNCTIONS:
            errors.append(f"{label} function is not a verification function")
        route_authorizer = route.get("authorizer_id")
        if "authorizer_id" in route:
            cleaned = _clean_identifier(route_authorizer, f"{label} authorizer_id", errors)
            if cleaned == PROTECTED_AUTHORIZER:
                errors.append(f"{label} authorizer_id must not be the protected authorizer")
            elif cleaned and authorizer_id and cleaned != authorizer_id:
                errors.append(f"{label} authorizer_id must match the verification authorizer")
        if function in REQUIRED_FUNCTIONS and isinstance(account, str) and account:
            _check_arn(
                route.get("integration_arn"),
                service="lambda",
                region=REQUIRED_REGION,
                account=account,
                resource=f"function:{function}:{REQUIRED_ALIAS}",
                label=f"{label} integration",
                errors=errors,
            )
        elif "integration_arn" in route:
            _clean_identifier(route.get("integration_arn"), f"{label} integration ARN", errors)
    if tuple(actual) != REQUIRED_ROUTES:
        errors.append("routes must be the required verification methods in order")


def _authorizer_id(manifest):
    cognito = manifest.get("cognito")
    if not isinstance(cognito, dict):
        return ""
    authorizer = cognito.get("authorizer")
    if not isinstance(authorizer, dict):
        return ""
    value = authorizer.get("id")
    return value if isinstance(value, str) else ""


def _check_cognito(manifest, account, errors):
    cognito = manifest.get("cognito")
    if not isinstance(cognito, dict):
        errors.append("cognito must be an object")
        return
    _object_schema(cognito, COGNITO_KEYS, "cognito", errors)
    pool = _clean_identifier(cognito.get("pool_id"), "cognito pool_id", errors) if "pool_id" in cognito else None
    if pool == PROTECTED_POOL:
        errors.append("cognito pool_id must not be the protected pool")
    elif isinstance(pool, str) and not POOL_PATTERN.fullmatch(pool):
        errors.append("cognito pool_id must be a verification pool id")
    if isinstance(pool, str) and isinstance(account, str) and account:
        _check_arn(
            cognito.get("arn"),
            service="cognito-idp",
            region=REQUIRED_REGION,
            account=account,
            resource=f"userpool/{pool}",
            label="cognito",
            errors=errors,
        )
    elif "arn" in cognito:
        _clean_identifier(cognito.get("arn"), "cognito ARN", errors)
    _check_pool_region(pool, cognito.get("arn") if "arn" in cognito else None, errors)
    _check_authorizer(cognito.get("authorizer") if "authorizer" in cognito else None, manifest, pool, errors)


def _check_pool_region(pool, arn, errors):
    """Require the pool id prefix to name the same region as the Cognito ARN."""
    if not isinstance(pool, str) or not POOL_PATTERN.fullmatch(pool):
        return
    prefix = pool.split("_", 1)[0]
    if not REGION_PATTERN.fullmatch(prefix):
        errors.append("cognito pool_id region is missing")
        return
    if not isinstance(arn, str) or _has_marker(arn) or _has_wildcard(arn):
        errors.append("cognito ARN region is missing")
        return
    parsed = _parse_arn(arn)
    if parsed is None or not parsed["region"]:
        errors.append("cognito ARN region is missing")
        return
    if prefix != parsed["region"]:
        errors.append("cognito pool_id region does not match the Cognito ARN region")


def _check_authorizer(authorizer, manifest, pool, errors):
    if not isinstance(authorizer, dict):
        errors.append("authorizer must be an object")
        return
    _object_schema(authorizer, AUTHORIZER_KEYS, "authorizer", errors)
    if authorizer.get("type") != REQUIRED_AUTHORIZER_TYPE:
        errors.append("authorizer type must be COGNITO_USER_POOLS")
    authorizer_id = _clean_identifier(authorizer.get("id"), "authorizer id", errors) if "id" in authorizer else None
    if authorizer_id == PROTECTED_AUTHORIZER:
        errors.append("authorizer id must not be the protected authorizer")
    elif isinstance(authorizer_id, str) and not AUTHORIZER_PATTERN.fullmatch(authorizer_id):
        errors.append("authorizer id must be a verification authorizer id")
    authorizer_pool = authorizer.get("pool_id") if "pool_id" in authorizer else None
    if "pool_id" in authorizer:
        cleaned_pool = _clean_identifier(authorizer_pool, "authorizer pool_id", errors)
        if cleaned_pool == PROTECTED_POOL:
            errors.append("authorizer pool_id must not be the protected pool")
        elif cleaned_pool and pool and cleaned_pool != pool:
            errors.append("authorizer pool_id must match the verification Cognito pool")
    api = manifest.get("api") if isinstance(manifest.get("api"), dict) else {}
    api_id = api.get("id") if isinstance(api.get("id"), str) else ""
    if "api_id" in authorizer:
        cleaned_api = _clean_identifier(authorizer.get("api_id"), "authorizer api_id", errors)
        if cleaned_api == PROTECTED_API:
            errors.append("authorizer api_id must not be the protected API")
        elif cleaned_api and api_id and not _has_marker(api_id) and cleaned_api != api_id:
            errors.append("authorizer api_id must match the verification API")


def _check_amplify(manifest, errors):
    amplify = manifest.get("amplify")
    if not isinstance(amplify, dict):
        errors.append("amplify must be an object")
        return
    _object_schema(amplify, AMPLIFY_KEYS, "amplify", errors)
    app_id = _clean_identifier(amplify.get("app_id"), "amplify app_id", errors) if "app_id" in amplify else None
    if app_id == PROTECTED_AMPLIFY:
        errors.append("amplify app_id must not be the protected app")
    elif isinstance(app_id, str) and not AMPLIFY_PATTERN.fullmatch(app_id):
        errors.append("amplify app_id must be a verification app id")
    branch = amplify.get("branch") if "branch" in amplify else None
    if branch == "main" or branch == PROTECTED_AMPLIFY:
        errors.append("amplify branch must not be the protected main branch")
    elif branch != REQUIRED_BRANCH:
        errors.append("amplify branch must be verification")
    region = _clean_identifier(amplify.get("region"), "amplify region", errors) if "region" in amplify else None
    if isinstance(region, str) and not REGION_PATTERN.fullmatch(region):
        errors.append("amplify region must be an AWS region")
    account = manifest.get("account_id") if isinstance(manifest.get("account_id"), str) else ""
    if isinstance(app_id, str) and isinstance(region, str) and REGION_PATTERN.fullmatch(region) and account:
        _check_arn(
            amplify.get("arn"),
            service="amplify",
            region=region,
            account=account,
            resource=f"apps/{app_id}",
            label="amplify",
            errors=errors,
        )
    elif "arn" in amplify:
        _clean_identifier(amplify.get("arn"), "amplify ARN", errors)
    if "origin" in amplify:
        _clean_identifier(amplify.get("origin"), "amplify origin", errors)


def _check_cors(manifest, errors):
    cors = manifest.get("cors")
    if not isinstance(cors, dict):
        errors.append("cors must be an object")
        return
    _object_schema(cors, CORS_KEYS, "cors", errors)
    if "allow_origin" in cors:
        origin = _clean_identifier(cors.get("allow_origin"), "cors allow_origin", errors)
        if origin == PROTECTED_ORIGIN or (isinstance(origin, str) and PROTECTED_AMPLIFY in origin):
            errors.append("cors allow_origin must not be the protected Amplify app")
    if "allow_methods" in cors:
        methods = cors.get("allow_methods")
        if not isinstance(methods, list):
            errors.append("cors allow_methods must be a list")
        elif tuple(methods) != REQUIRED_CORS_METHODS:
            errors.append("cors allow_methods must be GET, POST, OPTIONS")
        if isinstance(methods, list) and any(isinstance(item, str) and _has_wildcard(item) for item in methods):
            errors.append("cors allow_methods contains a wildcard")
    if "allow_headers" in cors:
        headers = cors.get("allow_headers")
        if not isinstance(headers, list):
            errors.append("cors allow_headers must be a list")
        elif tuple(headers) != REQUIRED_CORS_HEADERS:
            errors.append("cors allow_headers must be Content-Type, Authorization")
        if isinstance(headers, list) and any(isinstance(item, str) and _has_wildcard(item) for item in headers):
            errors.append("cors allow_headers contains a wildcard")


def _check_origin_relationship(manifest, errors):
    amplify = manifest.get("amplify") if isinstance(manifest.get("amplify"), dict) else {}
    cors = manifest.get("cors") if isinstance(manifest.get("cors"), dict) else {}
    frontend = manifest.get("frontend_origin")
    amplify_origin = amplify.get("origin")
    cors_origin = cors.get("allow_origin")
    if "frontend_origin" in manifest:
        cleaned = _clean_identifier(frontend, "frontend origin", errors)
        if cleaned == PROTECTED_ORIGIN or (isinstance(cleaned, str) and PROTECTED_AMPLIFY in cleaned):
            errors.append("frontend origin must not be the protected Amplify app")
    branch = amplify.get("branch")
    app_id = amplify.get("app_id")
    if (
        isinstance(branch, str)
        and isinstance(app_id, str)
        and not _has_marker(branch)
        and not _has_marker(app_id)
        and isinstance(frontend, str)
        and isinstance(amplify_origin, str)
        and not _has_marker(frontend)
        and not _has_marker(amplify_origin)
    ):
        expected = _expected_origin(branch, app_id)
        if frontend != expected or amplify_origin != expected:
            errors.append("frontend origin must match the verification Amplify branch origin")
    if (
        isinstance(frontend, str)
        and isinstance(cors_origin, str)
        and not _has_marker(frontend)
        and not _has_marker(cors_origin)
        and frontend != cors_origin
    ):
        errors.append("cors allow_origin must match the frontend origin")


def _check_roles(manifest, account, errors):
    roles = manifest.get("roles")
    found = {}
    if not isinstance(roles, list):
        errors.append("roles must be a list")
        return found
    for index, role in enumerate(roles):
        label = f"roles[{index}]"
        if not isinstance(role, dict):
            errors.append(f"{label} must be an object")
            continue
        _object_schema(role, ROLE_KEYS, label, errors)
        name = role.get("name")
        if not isinstance(name, str) or name not in REQUIRED_ROLES:
            errors.append(f"{label} name is not a verification role")
            continue
        if name in found:
            errors.append(f"duplicate role name: {name}")
        else:
            found[name] = role.get("arn")
        if isinstance(account, str) and account:
            _check_arn(
                role.get("arn"),
                service="iam",
                region="",
                account=account,
                resource=f"role/{name}",
                label=name,
                errors=errors,
            )
        elif "arn" in role:
            _clean_identifier(role.get("arn"), f"{name} ARN", errors)
    if tuple(found) != REQUIRED_ROLES and set(found) != set(REQUIRED_ROLES):
        errors.append("roles must name the deploy, operator, and lambda roles")
    elif tuple(name for name in found) != REQUIRED_ROLES and set(found) == set(REQUIRED_ROLES):
        errors.append("roles must name the deploy, operator, and lambda roles in order")
    return found


def _check_functions(manifest, account, role_arns, errors):
    functions = manifest.get("functions")
    if not isinstance(functions, list):
        errors.append("functions must be a list")
        return
    names = []
    lambda_role = role_arns.get("erap-verification-lambda")
    for index, item in enumerate(functions):
        label = f"functions[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{label} must be an object")
            continue
        _object_schema(item, FUNCTION_KEYS, label, errors)
        name = item.get("name")
        if not isinstance(name, str):
            errors.append(f"{label} name must be a string")
            name = ""
        if name in names:
            errors.append(f"duplicate function name: {name}")
        names.append(name)
        if "environment" not in item:
            pass
        elif item.get("environment") != REQUIRED_ENVIRONMENT:
            errors.append(f"{name or label} environment must be verification")
        role_arn = item.get("role_arn")
        if "role_arn" in item and (role_arn != lambda_role or not isinstance(role_arn, str)):
            errors.append(f"{name or label} role_arn must be the verification lambda role in the verification account")
        elif isinstance(role_arn, str) and isinstance(account, str) and account and _arn_account(role_arn) != account:
            errors.append(f"{name or label} role_arn must be the verification lambda role in the verification account")
        if name and isinstance(account, str) and account:
            _check_arn(
                item.get("arn"),
                service="lambda",
                region=REQUIRED_REGION,
                account=account,
                resource=f"function:{name}",
                label=name or label,
                errors=errors,
            )
        elif "arn" in item:
            _clean_identifier(item.get("arn"), f"{label} ARN", errors)
    if tuple(names) != REQUIRED_FUNCTIONS:
        errors.append("functions must be exactly the eight verification functions in order")


def _check_tables(manifest, account, errors):
    tables = manifest.get("tables")
    if not isinstance(tables, list):
        errors.append("tables must be a list")
        return
    names = []
    for index, item in enumerate(tables):
        label = f"tables[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{label} must be an object")
            continue
        _object_schema(item, TABLE_KEYS, label, errors)
        name = item.get("name")
        if not isinstance(name, str):
            errors.append(f"{label} name must be a string")
            name = ""
        if name in names:
            errors.append(f"duplicate table name: {name}")
        names.append(name)
        if name and isinstance(account, str) and account:
            _check_arn(
                item.get("arn"),
                service="dynamodb",
                region=REQUIRED_REGION,
                account=account,
                resource=f"table/{name}",
                label=name or label,
                errors=errors,
            )
        elif "arn" in item:
            _clean_identifier(item.get("arn"), f"{label} ARN", errors)
    if tuple(names) != REQUIRED_TABLES:
        errors.append("tables must be exactly the nine verification tables in order")


def _check_approvals(manifest, errors):
    approvals = manifest.get("approvals")
    if not isinstance(approvals, list):
        errors.append("approvals must be a list")
        return
    found = []
    for index, item in enumerate(approvals):
        label = f"approvals[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{label} must be an object")
            continue
        _object_schema(item, APPROVAL_KEYS, label, errors)
        approval_id = item.get("id")
        if not isinstance(approval_id, str) or approval_id not in REQUIRED_APPROVALS:
            errors.append(f"{label} id is not a required approval")
            continue
        if approval_id in found:
            errors.append(f"duplicate approval id: {approval_id}")
        found.append(approval_id)
        if item.get("status") != "approved":
            errors.append(f"approval {approval_id} is not approved")
    if tuple(found) != REQUIRED_APPROVALS:
        errors.append("approvals must list the required checkpoints in order")


def _check_protected_block(manifest, errors):
    protected = manifest.get("protected")
    expected = {
        "account_id": PROTECTED_ACCOUNT,
        "api_id": PROTECTED_API,
        "api_stage": PROTECTED_STAGE,
        "lambda_alias": PROTECTED_ALIAS,
        "cognito_pool_id": PROTECTED_POOL,
        "amplify_app_id": PROTECTED_AMPLIFY,
        "authorizer_id": PROTECTED_AUTHORIZER,
    }
    if not isinstance(protected, dict):
        errors.append("protected must be an object")
        return
    _object_schema(protected, PROTECTED_KEYS, "protected", errors)
    if protected != expected:
        errors.append("protected identifiers must match the current ERAP stack and must not be edited")


def _check_arn(value, *, service, region, account, resource, label, errors):
    if not isinstance(value, str):
        errors.append(f"{label} ARN must be a string")
        return
    if _has_marker(value):
        errors.append(f"{label} ARN contains an unresolved marker")
        return
    if _has_wildcard(value):
        errors.append(f"{label} ARN contains a wildcard")
        return
    parsed = _parse_arn(value)
    if parsed is None:
        errors.append(f"{label} ARN is malformed")
        return
    if parsed["service"] != service:
        errors.append(f"{label} ARN service is not {service}")
    if parsed["region"] != region:
        errors.append(f"{label} ARN region does not match the verification region")
    if parsed["account"] == PROTECTED_ACCOUNT:
        errors.append(f"{label} ARN uses the protected account")
    elif parsed["account"] != account:
        errors.append(f"{label} ARN account does not match the verification account")
    if parsed["resource"] != resource:
        errors.append(f"{label} ARN resource does not match the verification resource")
    if value.endswith(":live") or PROTECTED_ALIAS in value:
        errors.append(f"{label} ARN must not use the protected live alias")


def _parse_arn(value):
    parts = value.split(":")
    if len(parts) < 6 or parts[0] != "arn" or parts[1] != "aws":
        return None
    if any(part == "" for part in (parts[0], parts[1], parts[2], parts[4])):
        return None
    return {
        "service": parts[2],
        "region": parts[3],
        "account": parts[4],
        "resource": ":".join(parts[5:]),
    }


def _arn_account(value):
    parsed = _parse_arn(value) if isinstance(value, str) else None
    return parsed["account"] if parsed else ""


def _check_forbidden_text(manifest, errors):
    searchable = dict(manifest)
    searchable.pop("protected", None)
    fields = list(_fields(searchable))
    protected_tokens = (
        PROTECTED_ACCOUNT,
        PROTECTED_API,
        PROTECTED_ALIAS,
        PROTECTED_POOL,
        PROTECTED_AMPLIFY,
        PROTECTED_ORIGIN,
        PROTECTED_AUTHORIZER,
        PROTECTED_BILLING_SECRET,
        PROTECTED_ALARM,
        "github-deploy-policy",
    )
    if any(token in text for _key, text in fields for token in protected_tokens):
        errors.append("manifest target contains a protected identifier")
    if any(_has_wildcard(text) for _key, text in fields):
        errors.append("manifest target contains a wildcard")
    if any(DEV_PATTERN.search(text) for _key, text in fields):
        errors.append("manifest target contains the protected stage dev")
    if any(key != "status" and _has_marker(text) for key, text in fields):
        errors.append("manifest target contains an unresolved marker")


def _fields(value, key=""):
    if isinstance(value, str):
        yield key, value
    elif isinstance(value, dict):
        for child_key, item in value.items():
            yield from _fields(item, str(child_key))
    elif isinstance(value, list):
        for item in value:
            yield from _fields(item, key)


if __name__ == "__main__":
    raise SystemExit(main())
