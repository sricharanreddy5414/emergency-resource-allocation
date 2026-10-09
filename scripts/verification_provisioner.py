"""Fail-closed local gate for a future verification provisioner.

The design does not name an AWS API for creating resources. This module
therefore does not construct a cloud client of its own and does not call
AWS. Callers inject credential selection, identity lookup, resource-client
construction, and one allowlisted create callback.

run() and evaluate() are the public execution paths. Both call ready(),
snapshot the validated manifest, and only then invoke hooks. A hook cannot
run on a path that skipped that validation. policy_blocks() is a veto, not
a grant: a missing, non-object, or blocked policy stops the gate before any
hook. Returning from the gate does not authorize deployment, provisioning,
or teardown.

teardown() always defers to verification_preflight.refuse_teardown, which
raises. Cleanup is not implemented.
"""

from __future__ import annotations

import copy
import importlib.util
import re
import sys
from pathlib import Path


class ProvisionBlocked(RuntimeError):
    """The gate stopped. Callers must not continue."""


_VALIDATOR_NAME = "verification_preflight"
_SEAL = object()
_ACCOUNT_PATTERN = re.compile(r"^[0-9]{12}$")
# The design prohibits an account-root ARN and does not give its spelling.
# This blocks arn:<partition>:iam::<account>:root for any partition and account.
# user/root and role/root are different resource types and are not this form.
_ACCOUNT_ROOT_ARN = re.compile(r"^arn:[^:]*:iam::[^:]*:root$", re.IGNORECASE)
_ACTION_FIELDS = {
    "api_id": "PROTECTED_API",
    "stage": "PROTECTED_STAGE",
    "alias": "PROTECTED_ALIAS",
    "pool_id": "PROTECTED_POOL",
    "amplify_app_id": "PROTECTED_AMPLIFY",
    "origin": "PROTECTED_ORIGIN",
    "authorizer_id": "PROTECTED_AUTHORIZER",
    "secret_id": "PROTECTED_BILLING_SECRET",
    "alarm_name": "PROTECTED_ALARM",
}
_ACTION_KEYS = frozenset(_ACTION_FIELDS) | {"arn", "record"}
_SYNTHETIC_RECORDS = frozenset({"organization", "request", "notification", "exchange"})


class _GatePass:
    """Proof that ready() accepted this snapshot. Hooks never receive it."""

    def __init__(self, seal, snapshot, account):
        if seal is not _SEAL:
            raise ProvisionBlocked("validation was not completed")
        self.snapshot = snapshot
        self.account = account


def validator():
    """Return the live validator module.

    Protected constants are read from this object at comparison time so a
    test can patch one attribute without a copied literal in this file.
    """
    cached = getattr(validator, "_module", None)
    if cached is not None:
        return cached
    loaded = sys.modules.get(_VALIDATOR_NAME)
    if loaded is not None and hasattr(loaded, "ready"):
        validator._module = loaded
        return loaded
    path = Path(__file__).resolve().parent / "verification_preflight.py"
    spec = importlib.util.spec_from_file_location(_VALIDATOR_NAME, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[_VALIDATOR_NAME] = module
    spec.loader.exec_module(module)
    validator._module = module
    return module


def run(*, select_credentials, read_identity, policy, resource_clients=None, mutate=None, action=None):
    """Load only the fixed checked-in manifest and run the gate.

    There is no manifest path, environment override, or second file.
    """
    module = validator()
    manifest = module.load_manifest(module.default_manifest_path())
    return evaluate(
        manifest,
        select_credentials=select_credentials,
        read_identity=read_identity,
        policy=policy,
        resource_clients=resource_clients,
        mutate=mutate,
        action=action,
    )


def evaluate(manifest, *, select_credentials, read_identity, policy, resource_clients=None, mutate=None, action=None):
    """Validate, snapshot, then invoke hooks. There is no second entry."""
    gate = _validate(manifest)
    return _execute(
        gate,
        manifest,
        select_credentials=select_credentials,
        read_identity=read_identity,
        policy=policy,
        resource_clients=resource_clients,
        mutate=mutate,
        action=action,
    )


def _validate(manifest):
    """Call ready() on a snapshot and keep the account id ready() accepted."""
    if not isinstance(manifest, dict):
        raise ProvisionBlocked("manifest must be an object")
    snapshot = copy.deepcopy(manifest)
    validator().ready(snapshot)
    if manifest != snapshot:
        raise ProvisionBlocked("manifest changed during validation")
    account = snapshot.get("account_id")
    if not isinstance(account, str):
        raise ProvisionBlocked("validated account id is missing")
    return _GatePass(_SEAL, snapshot, account)


def _execute(gate, manifest, *, select_credentials, read_identity, policy, resource_clients=None, mutate=None, action=None):
    """Run hooks only for a pass that _validate already sealed.

    Policy is checked before credential selection. The snapshotted account
    is the only account authorization will accept. A change to the caller's
    manifest after validation stops the gate before the next hook.
    """
    if not isinstance(gate, _GatePass):
        raise ProvisionBlocked("validation was not completed")
    _assert_stable(manifest, gate)
    _require_policy(policy)
    _assert_stable(manifest, gate)
    credentials = select_credentials()
    if credentials is None:
        raise ProvisionBlocked("verification credentials were not selected")
    _assert_stable(manifest, gate)
    identity = read_identity(credentials)
    _assert_stable(manifest, gate)
    authorize_caller(gate.account, identity)
    _assert_stable(manifest, gate)
    if resource_clients is not None:
        resource_clients(credentials)
    _assert_stable(manifest, gate)
    if action is None:
        return None
    _reject_action(gate.snapshot, action)
    _assert_stable(manifest, gate)
    _recheck_caller(gate.account, read_identity, credentials)
    _assert_stable(manifest, gate)
    if mutate is not None:
        mutate(action)
    return None


def _assert_stable(manifest, gate):
    if manifest != gate.snapshot or manifest.get("account_id") != gate.account:
        raise ProvisionBlocked("manifest changed after validation")


def _require_policy(policy):
    """Reject a policy this veto cannot certify. This does not grant access."""
    if not isinstance(policy, dict) or not _resource_values_are_text(policy):
        raise ProvisionBlocked("verification policy is malformed")
    if policy_blocks(policy):
        raise ProvisionBlocked("verification policy is blocked")


def authorize_caller(manifest_account, identity):
    """Require a 12-digit caller equal to the snapshotted account and different from the live protected account."""
    account = _account_id(identity)
    if account != manifest_account:
        raise ProvisionBlocked("caller account does not equal the manifest account")
    if account == validator().PROTECTED_ACCOUNT:
        raise ProvisionBlocked("caller account is the protected account")
    return account


def protected_value_blocks(field, value):
    """Return whether value equals the live protected constant for field."""
    if field not in _ACTION_FIELDS:
        raise ProvisionBlocked("target field is not part of the provisioner contract")
    current = getattr(validator(), _ACTION_FIELDS[field])
    if value == current:
        return True
    if field == "alias" and value == "live":
        return True
    return False


def policy_blocks(document):
    """Return whether a document violates the design's resource veto.

    The veto covers NotResource at any depth, Resource *, a resource that
    names the live protected account, and an account-root ARN. A false result
    is not permission to create resources.
    """
    if _contains_not_resource(document):
        return True
    protected = validator().PROTECTED_ACCOUNT
    for resource in _resource_values(document):
        if resource == "*" or (isinstance(resource, str) and (protected in resource or "*" in resource or _is_account_root_arn(resource))):
            return True
    return False


def teardown(*_args, **_kwargs):
    """Teardown is permanently refused. This does not delete anything."""
    validator().refuse_teardown()


def _recheck_caller(manifest_account, read_identity, credentials):
    authorize_caller(manifest_account, read_identity(credentials))


def _account_id(identity):
    if not isinstance(identity, dict) or "Account" not in identity:
        raise ProvisionBlocked("caller identity is missing an account id")
    account = identity["Account"]
    if not isinstance(account, str) or account != account.strip() or any(character.isspace() for character in account):
        raise ProvisionBlocked("caller identity account id is malformed")
    if _ACCOUNT_PATTERN.fullmatch(account) is None:
        raise ProvisionBlocked("caller identity account id is malformed")
    return account


def _reject_action(manifest, action):
    if not isinstance(action, dict):
        raise ProvisionBlocked("action must be an object")
    unknown = sorted(set(action) - _ACTION_KEYS)
    if unknown:
        raise ProvisionBlocked("action has unsupported fields: " + ", ".join(unknown))
    record = action.get("record")
    if record in _SYNTHETIC_RECORDS:
        raise ProvisionBlocked("synthetic records are not written by this gate")
    for field in _ACTION_FIELDS:
        if field not in action:
            continue
        if protected_value_blocks(field, action[field]):
            raise ProvisionBlocked("target equals a protected identifier")
        _match_manifest_field(manifest, field, action[field])
    arn = action.get("arn")
    if not isinstance(arn, str) or arn not in _manifest_arns(manifest):
        raise ProvisionBlocked("target is not an exact manifest resource")
    if validator().PROTECTED_ACCOUNT in arn:
        raise ProvisionBlocked("target uses the protected account")


def _match_manifest_field(manifest, field, value):
    expected = {
        "api_id": manifest.get("api", {}).get("id"),
        "stage": manifest.get("api", {}).get("stage"),
        "alias": manifest.get("lambda_alias"),
        "pool_id": manifest.get("cognito", {}).get("pool_id"),
        "amplify_app_id": manifest.get("amplify", {}).get("app_id"),
        "origin": manifest.get("frontend_origin"),
        "authorizer_id": manifest.get("cognito", {}).get("authorizer", {}).get("id"),
    }
    if field in expected and value != expected[field]:
        raise ProvisionBlocked("target is outside the manifest allowlist")


def _manifest_arns(manifest):
    found = []
    for key in ("functions", "tables", "roles"):
        for item in manifest.get(key) or []:
            if isinstance(item, dict) and isinstance(item.get("arn"), str):
                found.append(item["arn"])
    routes = (manifest.get("api") or {}).get("routes") or []
    for route in routes:
        if isinstance(route, dict) and isinstance(route.get("integration_arn"), str):
            found.append(route["integration_arn"])
    return found


def _contains_not_resource(document):
    if isinstance(document, dict):
        for key, value in document.items():
            if isinstance(key, str) and key.lower() == "notresource":
                return True
            if _contains_not_resource(value):
                return True
    elif isinstance(document, list):
        for item in document:
            if _contains_not_resource(item):
                return True
    return False


def _is_account_root_arn(value):
    return isinstance(value, str) and _ACCOUNT_ROOT_ARN.fullmatch(value) is not None


def _resource_values_are_text(document):
    for resource in _resource_values(document):
        if not isinstance(resource, str) or resource != resource.strip() or any(character.isspace() for character in resource):
            return False
    return True


def _resource_values(document):
    if isinstance(document, dict):
        for key, value in document.items():
            if isinstance(key, str) and key.lower() == "resource":
                yield from _flatten(value)
            else:
                yield from _resource_values(value)
    elif isinstance(document, list):
        for item in document:
            yield from _resource_values(item)


def _flatten(value):
    if isinstance(value, list):
        for item in value:
            yield from _flatten(item)
    else:
        yield value
