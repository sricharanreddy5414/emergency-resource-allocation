"""Mocked checks for the verification provisioner. No AWS calls."""

import ast
import importlib.util
import inspect
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts" / "verification_provisioner.py"

ORIGINAL = {
    "PROTECTED_ACCOUNT": "481838970142",
    "PROTECTED_API": "4c6dni17l3",
    "PROTECTED_STAGE": "dev",
    "PROTECTED_ALIAS": "emergency-resource-allocation:live",
    "PROTECTED_POOL": "eu-north-1_vv7adAAC9",
    "PROTECTED_AMPLIFY": "d3enpe7opotop5",
    "PROTECTED_ORIGIN": "https://main.d3enpe7opotop5.amplifyapp.com",
    "PROTECTED_AUTHORIZER": "y0hzhr",
    "PROTECTED_BILLING_SECRET": "erap/billing/razorpay/production",
    "PROTECTED_ALARM": "ERAP-Production-Alarms",
}

DRIFT = (
    ("api_id", "PROTECTED_API", "abcd1234ef"),
    ("stage", "PROTECTED_STAGE", "verify"),
    ("alias", "PROTECTED_ALIAS", "verify"),
    ("pool_id", "PROTECTED_POOL", "eu-north-1_VerifyPool1"),
    ("amplify_app_id", "PROTECTED_AMPLIFY", "dverifyapp0001"),
    ("origin", "PROTECTED_ORIGIN", "https://verification.dverifyapp0001.amplifyapp.com"),
    ("authorizer_id", "PROTECTED_AUTHORIZER", "a1b2c3"),
    ("secret_id", "PROTECTED_BILLING_SECRET", "erap/billing/verification-only"),
    ("alarm_name", "PROTECTED_ALARM", "ERAP-Verification-Alarms"),
)


def load_provisioner():
    spec = importlib.util.spec_from_file_location("verification_provisioner", SOURCE)
    module = importlib.util.module_from_spec(spec)
    sys.modules["verification_provisioner"] = module
    spec.loader.exec_module(module)
    return module


def load_manifest_helper():
    path = ROOT / "tests" / "test_verification_preflight.py"
    spec = importlib.util.spec_from_file_location("verification_preflight_tests", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.manifest


provisioner = load_provisioner()
manifest = load_manifest_helper()


class Spy:
    def __init__(self, result=None, raises=None):
        self.calls = []
        self.result = result
        self.raises = raises

    def __call__(self, *args, **kwargs):
        if self.raises is not None and len(self.calls) >= self.raises[0]:
            raise self.raises[1]
        self.calls.append((args, kwargs))
        return self.result


def credentials():
    return {"selected": "verification"}


def identity(account="210987654321"):
    def read(_credentials):
        return {"Account": account}

    return read


def manifest_policy(payload):
    return {"Resource": payload["roles"][2]["arn"]}


def account_drift_policy():
    """Veto fixture that does not embed the fixture account or a wildcard."""
    return {"Resource": "local-verification-policy"}


def allowed_action(payload, **fields):
    action = {"arn": payload["functions"][0]["arn"]}
    action.update(fields)
    return action


def test_import_does_not_load_a_cloud_sdk():
    added = set(sys.modules)
    load_provisioner()
    introduced = set(sys.modules) - added
    assert "boto3" not in introduced
    assert "botocore" not in introduced
    assert "aws_cli" not in introduced


def test_source_reads_constants_and_does_not_copy_literals():
    text = SOURCE.read_text(encoding="utf-8")
    tree = ast.parse(text)
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        if isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
            imported.extend(alias.name for alias in node.names)
    assert "boto3" not in imported
    assert "botocore" not in imported
    assert "aws_cli" not in imported
    assert "deploy_backend" not in imported
    assert "lambda_manifest" not in imported
    for literal in ORIGINAL.values():
        assert literal not in text
    assert "def teardown" in text
    assert "def continue_after_ready" not in text
    assert "def evaluate" in text
    names = [node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)]
    assert "delete" not in names
    assert "continue_after_ready" not in names


def test_public_execution_validates_before_hooks():
    assert not hasattr(provisioner, "continue_after_ready")
    source = inspect.getsource(provisioner.evaluate)
    assert source.index("_validate") < source.index("_execute")
    run_parameters = inspect.signature(provisioner.run).parameters
    assert "manifest" not in run_parameters
    assert "path" not in run_parameters


def test_execution_without_validation_calls_no_hooks():
    payload = manifest()
    creds = Spy(result={"selected": "verification"})
    ident = Spy(result={"Account": "210987654321"})
    clients = Spy()
    mutate = Spy()
    with pytest.raises(provisioner.ProvisionBlocked, match="validation was not completed"):
        provisioner._execute(
            None,
            payload,
            select_credentials=creds,
            read_identity=ident,
            policy=manifest_policy(payload),
            resource_clients=clients,
            mutate=mutate,
            action=allowed_action(payload),
        )
    with pytest.raises(provisioner.ProvisionBlocked, match="validation was not completed"):
        provisioner._GatePass(object(), {}, "210987654321")
    assert creds.calls == []
    assert ident.calls == []
    assert clients.calls == []
    assert mutate.calls == []


def test_checked_in_manifest_blocks_before_any_client():
    clients = Spy()
    mutate = Spy()
    creds = Spy(result={"selected": "verification"})
    with pytest.raises(provisioner.validator().VerificationBlocked) as caught:
        provisioner.run(
            select_credentials=creds,
            read_identity=identity(),
            policy={"Resource": "*"},
            resource_clients=clients,
            mutate=mutate,
            action={"arn": "arn:aws:lambda:eu-north-1:210987654321:function:example"},
        )
    blocked = str(caught.value)
    assert "api id contains an unresolved marker" in blocked
    assert "approval dedicated-account is not approved" in blocked
    assert creds.calls == []
    assert clients.calls == []
    assert mutate.calls == []


def test_run_accepts_no_manifest_path_and_ignores_an_environment_override(monkeypatch, tmp_path):
    signature = inspect.signature(provisioner.run)
    assert "manifest" not in signature.parameters
    assert "path" not in signature.parameters
    other = tmp_path / "other.json"
    other.write_text('{"purpose": "not-the-fixed-file"}', encoding="utf-8")
    monkeypatch.setenv("ERAP_VERIFICATION_MANIFEST", str(other))
    monkeypatch.setenv("VERIFICATION_MANIFEST", str(other))
    mutate = Spy()
    with pytest.raises(provisioner.validator().VerificationBlocked) as caught:
        provisioner.run(
            select_credentials=credentials,
            read_identity=identity(),
            policy=manifest_policy(manifest()),
            resource_clients=Spy(),
            mutate=mutate,
        )
    blocked = str(caught.value)
    assert "api id contains an unresolved marker" in blocked
    assert "approval dedicated-account is not approved" in blocked
    assert mutate.calls == []
    assert os.environ["ERAP_VERIFICATION_MANIFEST"] == str(other)


@pytest.mark.parametrize(
    ("mutate_manifest", "message"),
    [
        (lambda payload: payload["approvals"].__setitem__(0, {**payload["approvals"][0], "status": "pending"}), "approval dedicated-account is not approved"),
        (lambda payload: payload.__setitem__("account_id", "481838970142"), "account_id must not be the protected account"),
        (lambda payload: payload["api"].__setitem__("stage", "dev"), "api stage must not be dev"),
        (lambda payload: payload.__setitem__("lambda_alias", "live"), "lambda_alias must be verify"),
    ],
)
def test_ready_failures_stop_before_clients(mutate_manifest, message):
    payload = manifest()
    mutate_manifest(payload)
    clients = Spy()
    mutate = Spy()
    creds = Spy(result={"selected": "verification"})
    with pytest.raises(provisioner.validator().VerificationBlocked, match=message):
        provisioner.evaluate(
            payload,
            select_credentials=creds,
            read_identity=identity(),
            policy=manifest_policy(manifest()),
            resource_clients=clients,
            mutate=mutate,
        )
    assert creds.calls == []
    assert clients.calls == []
    assert mutate.calls == []


def test_pool_region_mismatch_stops_before_clients():
    payload = manifest()
    pool = "us-east-1_VerifyPool1"
    payload["cognito"]["pool_id"] = pool
    payload["cognito"]["authorizer"]["pool_id"] = pool
    payload["cognito"]["arn"] = f"arn:aws:cognito-idp:eu-north-1:210987654321:userpool/{pool}"
    clients = Spy()
    mutate = Spy()
    with pytest.raises(
        provisioner.validator().VerificationBlocked,
        match="cognito pool_id region does not match the Cognito ARN region",
    ):
        provisioner.evaluate(
            payload,
            select_credentials=credentials,
            read_identity=identity(),
            policy=manifest_policy(manifest()),
            resource_clients=clients,
            mutate=mutate,
        )
    assert clients.calls == []
    assert mutate.calls == []


def test_ready_exception_is_not_treated_as_success():
    def broken_ready(_manifest):
        raise provisioner.validator().VerificationBlocked("gate stopped")

    original = provisioner.validator().ready
    provisioner.validator().ready = broken_ready
    clients = Spy()
    mutate = Spy()
    creds = Spy(result={"selected": "verification"})
    try:
        with pytest.raises(provisioner.validator().VerificationBlocked, match="gate stopped"):
            provisioner.evaluate(
                manifest(),
                select_credentials=creds,
                read_identity=identity(),
                policy={"Resource": "*"},
                resource_clients=clients,
                mutate=mutate,
            )
    finally:
        provisioner.validator().ready = original
    assert creds.calls == []
    assert clients.calls == []
    assert mutate.calls == []


def test_credential_exception_builds_no_client():
    payload = manifest()
    clients = Spy()
    identity_spy = Spy()
    mutate = Spy()

    def explode():
        raise RuntimeError("credentials unavailable")

    with pytest.raises(RuntimeError, match="credentials unavailable"):
        provisioner.evaluate(
            payload,
            select_credentials=explode,
            read_identity=identity_spy,
            policy=manifest_policy(payload),
            resource_clients=clients,
            mutate=mutate,
            action=allowed_action(payload),
        )
    assert identity_spy.calls == []
    assert clients.calls == []
    assert mutate.calls == []


def test_missing_credentials_do_not_continue():
    payload = manifest()
    identity_spy = Spy()
    clients = Spy()
    mutate = Spy()
    with pytest.raises(provisioner.ProvisionBlocked, match="verification credentials were not selected"):
        provisioner.evaluate(
            payload,
            select_credentials=lambda: None,
            read_identity=identity_spy,
            policy=manifest_policy(payload),
            resource_clients=clients,
            mutate=mutate,
            action=allowed_action(payload),
        )
    assert identity_spy.calls == []
    assert clients.calls == []
    assert mutate.calls == []


def test_identity_exception_builds_no_client():
    payload = manifest()
    clients = Spy()
    mutate = Spy()

    def explode(_credentials):
        raise RuntimeError("identity unavailable")

    with pytest.raises(RuntimeError, match="identity unavailable"):
        provisioner.evaluate(
            payload,
            select_credentials=credentials,
            read_identity=explode,
            policy=manifest_policy(payload),
            resource_clients=clients,
            mutate=mutate,
            action=allowed_action(payload),
        )
    assert clients.calls == []
    assert mutate.calls == []


@pytest.mark.parametrize(
    "payload_identity",
    [{}, {"Account": "12ab"}, {"Account": " 210987654321"}, {"Account": None}],
)
def test_missing_or_malformed_identity_builds_no_client(payload_identity):
    payload = manifest()
    clients = Spy()
    mutate = Spy()
    with pytest.raises(provisioner.ProvisionBlocked):
        provisioner.evaluate(
            payload,
            select_credentials=credentials,
            read_identity=lambda _credentials: payload_identity,
            policy=manifest_policy(payload),
            resource_clients=clients,
            mutate=mutate,
            action=allowed_action(payload),
        )
    assert clients.calls == []
    assert mutate.calls == []


@pytest.mark.parametrize("account", ["481838970142", "999999999999"])
def test_protected_and_third_accounts_build_no_client(account):
    payload = manifest()
    clients = Spy()
    mutate = Spy()
    with pytest.raises(provisioner.ProvisionBlocked):
        provisioner.evaluate(
            payload,
            select_credentials=lambda: {"selected": "labeled-verification"},
            read_identity=identity(account),
            policy=manifest_policy(payload),
            resource_clients=clients,
            mutate=mutate,
            action=allowed_action(payload),
        )
    assert clients.calls == []
    assert mutate.calls == []


def test_credential_hook_cannot_replace_the_validated_account():
    payload = manifest()
    identity_spy = Spy(result={"Account": "999999999999"})
    clients = Spy()
    mutate = Spy()

    def select():
        payload["account_id"] = "999999999999"
        return {"selected": "verification"}

    with pytest.raises(provisioner.ProvisionBlocked, match="manifest changed after validation"):
        provisioner.evaluate(
            payload,
            select_credentials=select,
            read_identity=identity_spy,
            policy=manifest_policy(payload),
            resource_clients=clients,
            mutate=mutate,
            action=allowed_action(payload),
        )
    assert identity_spy.calls == []
    assert clients.calls == []
    assert mutate.calls == []
    assert payload["account_id"] == "999999999999"


def test_nested_configuration_change_stops_before_clients():
    payload = manifest()
    clients = Spy()
    mutate = Spy()

    def read(_credentials):
        payload["api"]["id"] = "zzzzzzzzzz"
        return {"Account": "210987654321"}

    with pytest.raises(provisioner.ProvisionBlocked, match="manifest changed after validation"):
        provisioner.evaluate(
            payload,
            select_credentials=credentials,
            read_identity=read,
            policy=manifest_policy(payload),
            resource_clients=clients,
            mutate=mutate,
            action=allowed_action(payload),
        )
    assert clients.calls == []
    assert mutate.calls == []


def test_resource_client_cannot_change_configuration_and_then_mutate():
    payload = manifest()
    mutate = Spy()

    def clients(_credentials):
        payload["functions"][0]["arn"] = payload["functions"][0]["arn"] + "-mutated"

    with pytest.raises(provisioner.ProvisionBlocked, match="manifest changed after validation"):
        provisioner.evaluate(
            payload,
            select_credentials=credentials,
            read_identity=identity(),
            policy=manifest_policy(payload),
            resource_clients=clients,
            mutate=mutate,
            action=allowed_action(payload),
        )
    assert mutate.calls == []


def test_matching_account_constructs_a_client_and_mutates_only_after_recheck():
    payload = manifest()
    arn = payload["functions"][0]["arn"]
    seen = []

    def read(_credentials):
        seen.append("identity")
        return {"Account": "210987654321"}

    def clients(_credentials):
        seen.append("client")

    def mutate(action):
        seen.append(("mutate", action["arn"]))

    provisioner.evaluate(
        payload,
        select_credentials=credentials,
        read_identity=read,
        policy=manifest_policy(payload),
        resource_clients=clients,
        mutate=mutate,
        action={"arn": arn},
    )
    assert seen == ["identity", "client", "identity", ("mutate", arn)]


def test_resource_client_exception_is_not_readiness_and_does_not_mutate():
    payload = manifest()
    mutate = Spy()

    def explode(_credentials):
        raise RuntimeError("client factory failed")

    with pytest.raises(RuntimeError, match="client factory failed"):
        provisioner.evaluate(
            payload,
            select_credentials=credentials,
            read_identity=identity(),
            policy=manifest_policy(payload),
            resource_clients=explode,
            mutate=mutate,
            action=allowed_action(payload),
        )
    assert mutate.calls == []


def test_pre_mutation_identity_exception_does_not_mutate():
    payload = manifest()
    calls = {"count": 0}
    clients = Spy()
    mutate = Spy()

    def read(_credentials):
        calls["count"] += 1
        if calls["count"] == 1:
            return {"Account": "210987654321"}
        raise RuntimeError("boundary failed")

    with pytest.raises(RuntimeError, match="boundary failed"):
        provisioner.evaluate(
            payload,
            select_credentials=credentials,
            read_identity=read,
            policy=manifest_policy(payload),
            resource_clients=clients,
            mutate=mutate,
            action=allowed_action(payload),
        )
    assert len(clients.calls) == 1
    assert mutate.calls == []


def test_allowlisted_create_rejects_other_arns_and_never_deletes():
    payload = manifest()
    allowed = payload["functions"][0]["arn"]
    mutate = Spy()
    provisioner.evaluate(
        payload,
        select_credentials=credentials,
        read_identity=identity(),
        policy=manifest_policy(payload),
        resource_clients=Spy(),
        mutate=mutate,
        action={"arn": allowed},
    )
    assert len(mutate.calls) == 1
    blocked = Spy()
    clients = Spy()
    with pytest.raises(provisioner.ProvisionBlocked, match="target is not an exact manifest resource"):
        provisioner.evaluate(
            payload,
            select_credentials=credentials,
            read_identity=identity(),
            policy=manifest_policy(payload),
            resource_clients=clients,
            mutate=blocked,
            action={"arn": allowed + "-extra"},
        )
    assert blocked.calls == []
    assert not hasattr(provisioner, "delete")


def test_synthetic_and_protected_api_writes_do_not_mutate():
    payload = manifest()
    mutate = Spy()
    with pytest.raises(provisioner.ProvisionBlocked, match="synthetic records are not written"):
        provisioner.evaluate(
            payload,
            select_credentials=credentials,
            read_identity=identity(),
            policy=manifest_policy(payload),
            resource_clients=Spy(),
            mutate=mutate,
            action={
                "arn": payload["functions"][0]["arn"],
                "record": "request",
                "api_id": "4c6dni17l3",
                "stage": "dev",
            },
        )
    assert mutate.calls == []


@pytest.mark.parametrize(
    "record",
    ["organization", "request", "notification", "exchange"],
)
def test_synthetic_records_do_not_mutate(record):
    payload = manifest()
    mutate = Spy()
    with pytest.raises(provisioner.ProvisionBlocked, match="synthetic records are not written"):
        provisioner.evaluate(
            payload,
            select_credentials=credentials,
            read_identity=identity(),
            policy=manifest_policy(payload),
            resource_clients=Spy(),
            mutate=mutate,
            action={"arn": payload["functions"][0]["arn"], "record": record},
        )
    assert mutate.calls == []


@pytest.mark.parametrize(
    "policy",
    [None, [], "Resource", 1, {"Resource": None}, {"Resource": [1]}, {"Statement": [{"Resource": {"wildcard": "*"}}]}],
)
def test_malformed_policy_calls_no_hooks(policy):
    payload = manifest()
    creds = Spy(result={"selected": "verification"})
    ident = Spy(result={"Account": "210987654321"})
    clients = Spy()
    mutate = Spy()
    with pytest.raises(provisioner.ProvisionBlocked, match="verification policy is malformed"):
        provisioner.evaluate(
            payload,
            select_credentials=creds,
            read_identity=ident,
            policy=policy,
            resource_clients=clients,
            mutate=mutate,
            action=allowed_action(payload),
        )
    assert creds.calls == []
    assert ident.calls == []
    assert clients.calls == []
    assert mutate.calls == []


@pytest.mark.parametrize(
    "policy",
    [
        {"Resource": "*"},
        {"Statement": [{"Resource": "arn:aws:iam::481838970142:role/example"}]},
        {"NotResource": "*"},
        {"Statement": [{"Effect": "Allow", "NotResource": "*"}]},
        {"Resource": "arn:aws:iam::111122223333:root"},
        {"Resource": ["arn:aws:iam::210987654321:role/erap-verification-lambda", "arn:aws:iam::210987654321:root"]},
    ],
)
def test_blocked_policy_is_enforced_before_hooks(policy):
    payload = manifest()
    creds = Spy(result={"selected": "verification"})
    ident = Spy(result={"Account": "210987654321"})
    clients = Spy()
    mutate = Spy()
    with pytest.raises(provisioner.ProvisionBlocked, match="verification policy is blocked"):
        provisioner.evaluate(
            payload,
            select_credentials=creds,
            read_identity=ident,
            policy=policy,
            resource_clients=clients,
            mutate=mutate,
            action=allowed_action(payload),
        )
    assert creds.calls == []
    assert ident.calls == []
    assert clients.calls == []
    assert mutate.calls == []


def test_policy_for_the_manifest_account_is_not_rejected_by_the_protected_account_rule():
    payload = manifest()
    arn = payload["roles"][2]["arn"]
    assert "481838970142" not in arn
    assert provisioner.policy_blocks({"Resource": arn}) is False
    mutate = Spy()
    provisioner.evaluate(
        payload,
        select_credentials=credentials,
        read_identity=identity(),
        policy={"Resource": arn},
        resource_clients=Spy(),
        mutate=mutate,
        action={"arn": payload["functions"][0]["arn"]},
    )
    assert len(mutate.calls) == 1


def test_teardown_stays_blocked_after_ready():
    payload = manifest()
    assert provisioner.validator().ready(payload) is True
    clients = Spy()
    mutate = Spy()
    with pytest.raises(provisioner.validator().VerificationBlocked, match="Teardown is disabled"):
        provisioner.teardown(payload, resource_clients=clients, mutate=mutate)
    assert clients.calls == []
    assert mutate.calls == []
    assert not hasattr(provisioner, "delete")


@pytest.mark.parametrize(("field", "constant", "legal"), DRIFT)
def test_protected_constant_drift_is_discriminating(monkeypatch, field, constant, legal):
    module = provisioner.validator()
    payload = manifest()
    assert module.ready(payload) is True
    assert provisioner.protected_value_blocks(field, legal) is False
    action = allowed_action(payload, **{field: legal})
    policy = manifest_policy(payload)
    allowed = Spy()
    provisioner.evaluate(
        payload,
        select_credentials=credentials,
        read_identity=identity(),
        policy=policy,
        resource_clients=Spy(),
        mutate=allowed,
        action=action,
    )
    assert len(allowed.calls) == 1

    gate = provisioner._validate(payload)
    monkeypatch.setattr(module, constant, legal)
    ready_calls = {"count": 0}
    original_ready = module.ready

    def counting(value):
        ready_calls["count"] += 1
        return original_ready(value)

    monkeypatch.setattr(module, "ready", counting)
    blocked_mutations = Spy()
    with pytest.raises(provisioner.ProvisionBlocked, match="target equals a protected identifier"):
        provisioner._execute(
            gate,
            payload,
            select_credentials=credentials,
            read_identity=identity(),
            policy=policy,
            resource_clients=Spy(),
            mutate=blocked_mutations,
            action=action,
        )
    assert ready_calls["count"] == 0
    assert blocked_mutations.calls == []

    negative = Spy()

    def negative_control(value):
        if value == ORIGINAL[constant]:
            raise provisioner.ProvisionBlocked("hardcoded literal matched")
        negative(value)

    negative_control(legal)
    assert len(negative.calls) == 1
    with pytest.raises(provisioner.ProvisionBlocked, match="hardcoded literal matched"):
        negative_control(ORIGINAL[constant])


def test_account_drift_blocks_the_client_and_the_negative_control_allows_it(monkeypatch):
    module = provisioner.validator()
    payload = manifest()
    assert module.ready(payload) is True
    policy = account_drift_policy()
    allowed_clients = Spy()
    allowed_mutations = Spy()
    provisioner.evaluate(
        payload,
        select_credentials=credentials,
        read_identity=identity(),
        policy=policy,
        resource_clients=allowed_clients,
        mutate=allowed_mutations,
        action=allowed_action(payload),
    )
    assert len(allowed_clients.calls) == 1
    assert len(allowed_mutations.calls) == 1

    gate = provisioner._validate(payload)
    monkeypatch.setattr(module, "PROTECTED_ACCOUNT", "210987654321")
    ready_calls = {"count": 0}
    original_ready = module.ready

    def counting(value):
        ready_calls["count"] += 1
        return original_ready(value)

    monkeypatch.setattr(module, "ready", counting)
    blocked_clients = Spy()
    blocked_mutations = Spy()
    with pytest.raises(provisioner.ProvisionBlocked, match="caller account is the protected account"):
        provisioner._execute(
            gate,
            payload,
            select_credentials=credentials,
            read_identity=identity(),
            policy=policy,
            resource_clients=blocked_clients,
            mutate=blocked_mutations,
            action=allowed_action(payload),
        )
    assert ready_calls["count"] == 0
    assert blocked_clients.calls == []
    assert blocked_mutations.calls == []

    def negative_control(account):
        if account == ORIGINAL["PROTECTED_ACCOUNT"]:
            raise provisioner.ProvisionBlocked("hardcoded literal matched")
        return "client-constructed"

    assert negative_control("210987654321") == "client-constructed"
    with pytest.raises(provisioner.ProvisionBlocked, match="hardcoded literal matched"):
        negative_control(ORIGINAL["PROTECTED_ACCOUNT"])
