# Phase 14H — Verification deployment gate design review

**Decision: NO-GO for cloud provisioning, deployment, alias movement, and teardown.**

The Phase 14G validator is a local, fail-closed readiness gate. It does not prove that verification infrastructure exists and does not authorize deployment. A local module, `scripts/verification_provisioner.py`, now implements that predicate behind mocked hooks. The checked-in manifest records verification account `917320177579`. The protected production account is `481838970142`. Remaining API IDs, Cognito IDs, Amplify identifiers, Lambda, table, role, and integration ARNs, authorizer IDs, and the frontend origin stay `UNRESOLVED`. Teardown has no reviewed design, and the existing deploy path still targets account `481838970142`, API `4c6dni17l3`, stage `dev`, and alias `live`. Extending those scripts would bypass the gate. Cloud provisioning and deployment remain NO-GO.

Phase 14H.2, Phase 14H.4, and Phase 14H.6 correct the design text after independent audits. A later local correction closed process-bypass gaps in the mocked gate. The cloud decision remains NO-GO. This document does not authorize provisioning, deployment, teardown, or writing synthetic data. The three manual live checks remain NOT RUN.

## Local code versus executed tests

`scripts/verification_provisioner.py` and `tests/test_verification_provisioner.py` are local code. `run()` and `evaluate()` call `ready()`, snapshot the manifest and account id that `ready()` accepted, and reject a later change to that document. Credential, identity, resource-client, and mutation hooks run only after that sealed validation. `policy_blocks()` is applied before those hooks. It rejects a policy that is not an object or whose `Resource` value is not text. It blocks `NotResource` at any depth, a `Resource` of `*` or containing `*`, a `Resource` containing the live `PROTECTED_ACCOUNT`, and an account-root ARN. The design does not spell that ARN. The gate blocks `arn:<partition>:iam::<account>:root` for any partition and any account, with `root` matched in any letter case. It does not classify `user/root` or `role/root` as that ARN; the design does not call those resource types account root. A false veto is not a permission grant. The module does not import or construct an AWS client. `teardown()` only calls `refuse_teardown()`.

The checked-in manifest records `account_id` `917320177579`. It does not treat that ID as proof that a verification stack exists. API IDs, Cognito IDs, Amplify identifiers, Lambda, table, role, and integration ARNs, authorizer IDs, and the frontend origin remain `UNRESOLVED`. All six approvals remain `pending`. `teardown_enabled` remains `false`. This section does not change an approval.

Commands in "Evidence from the Phase 14H review" were run during that review, except where that section says a command was not re-run. Commands in "Evidence from the local gate corrections" are the only commands this correction executed. A passing local test is not deployment readiness.

## Scope and files inspected

Worktree: `C:\Azure\erap-phase-14g`

Branch: `feature/phase-14g-guarded-verification-deployer`

Base commit: `41b576f00a1c85d9381f2a65453e84cd18601f6a`

Inspected, and not modified by this review except for this document:

- `scripts/verification_preflight.py`
- `config/erap-verification-manifest.json`
- `tests/test_verification_preflight.py`
- `docs/verification-preflight.md`
- `scripts/deploy_backend.py`
- `scripts/lambda_manifest.py`
- `scripts/security_scan.py`
- `scripts/set_live_version.py`, `scripts/route_api_to_live_alias.py`, `scripts/deploy_exchange.py`, and the `scripts/release_*.py` publishers
- `.github/workflows/ci.yml`, `deploy-backend.yml`, the `release-*.yml` workflows, and the `rollback-*.yml` workflows

`scripts/lambda_manifest.py` pins the current stack in code: `ACCOUNT = "481838970142"`, `API_ID = "4c6dni17l3"`, `API_STAGE = "dev"`, `ALIAS = "live"`, `AMPLIFY_APP_ID = "d3enpe7opotop5"`, and `ALLOWED_ORIGIN` equal to `https://main.d3enpe7opotop5.amplifyapp.com`. `scripts/deploy_backend.py` imports `ALIAS` from that module and moves that alias in `point_alias`. `deploy-backend.yml` runs tests and `check_workflows.py`; it does not publish `live`.

Both the release workflows and the rollback workflows can move the existing Lambda alias `live`:

- Release: `release.yml`, `release-allocation.yml`, `release-auto-release.yml`, `release-create-request.yml`, `release-exchange.yml`, `release-reservation-expiry.yml`, `release-resource.yml`
- Rollback: `rollback.yml`, `rollback-allocation.yml`, `rollback-auto-release.yml`, `rollback-create-request.yml`, `rollback-exchange.yml`, `rollback-reservation-expiry.yml`, `rollback-resource.yml`

Both paths stay outside the verification provisioner. The provisioner must not call them, import their scripts, or add a verification job to them. None of these workflows import `verification_preflight`.

The checked-in manifest records verification account `917320177579` and protected production account `481838970142`. API, Cognito pool, authorizer, Amplify app, route integrations, roles, functions, tables, and frontend origin remain `UNRESOLVED`. All six approvals are `pending`. `teardown_enabled` is `false`. Cloud provisioning and deployment remain NO-GO.

## Where each protection belongs

| Protection | Owner |
| --- | --- |
| Manifest shape, allowlists, protected identifiers, region match, wildcard rejection, pending approvals, `teardown_enabled is False` | Validator: `ready()` in `scripts/verification_preflight.py`, and only when that function is called and a failure is not ignored |
| Fixed manifest path, no override flags, call `ready()` before any cloud client, stop on `VerificationBlocked` or any other exception, import the validator's protected constants, no teardown function | Future verification-only provisioner, in a new module that does not import `deploy_backend` or `lambda_manifest` |
| Read-only caller-identity check with explicitly selected verification-account credentials, after `ready()`. The returned account must equal the manifest `account_id` and differ from `PROTECTED_ACCOUNT` before any resource client or mutation. A third account is rejected | Future provisioner process check. This check does not by itself prevent credential misuse |
| Role cannot describe or change account `481838970142`; no `Resource: "*"`; no production billing secret or alarm topic; no trust from `ERAP-GitHub-Deploy` or `ERAP-GitHub-Production` | IAM in a separate verification account. This is the independent cloud-side control |

`ready()` enforces the validator checks only when a caller actually invokes it and does not ignore `VerificationBlocked` or a non-empty `preflight()` result. The validator cannot verify which AWS credentials a future provisioner will use. `main(--manifest)` accepts a local manifest override, so that CLI is not, by itself, a trusted provisioner entry point. IAM policies and separate-account isolation must enforce the cloud-side boundary. A passing `ready()` is not permission to call the existing deploy scripts.

## Threat model and mitigation matrix

| Risk | Attack path | Preventive control | Test that must fail the operation |
| --- | --- | --- | --- |
| Parse without validation | Call `load_manifest()` and pass the dict to a create or update function | Provisioner entry calls `ready(load_manifest(FIXED_PATH))` and has no other loader. `load_manifest` stays parse-only | A unit test feeds the checked-in file to the provisioner entry and asserts it raises `VerificationBlocked` before any cloud-client factory is called |
| Ignore `preflight()` | Call `preflight()`, discard the list, and continue | Provisioner must not call `preflight()` directly. It calls `ready()`, which raises | A test stubs a manifest with one known error and asserts the entry raises rather than returning a client |
| Catch and continue | `except VerificationBlocked: proceed` | Provisioner has one top-level handler that exits non-zero. It does not catch `VerificationBlocked` around individual AWS calls | A test raises `VerificationBlocked` from `ready` and asserts the process function returns non-zero and the client factory call count is zero |
| Alternate manifest | `main(["--manifest", other])` or an env var pointing at a second file | Provisioner has no `--manifest`, env var, or config argument. `verification_preflight.main` stays a local reviewer tool and is not the provisioner | A test shows the provisioner signature accepts no path, and a second JSON file beside the fixed path is never read |
| Skip allowlist or protected checks | Reimplement a shorter checker, or call a private helper such as `_check_roles` only | The only success path is `ready()`. Private helpers are not an API. Protected identifiers come from the validator module | A manifest that passes `_check_roles` and has a pending approval is rejected because `ready()` raises. The existing test `test_ready_rejects_when_role_checks_pass_and_an_approval_is_pending` is the validator half of this |
| AWS before validation | Construct `boto3` or call `aws_cli.aws` at import time, or build a resource client before identity matches the manifest account | No module-level cloud SDK import. The only cloud operation before a resource client is the read-only identity check, and it runs only after `ready()` succeeds, using explicitly selected verification-account credentials | Importing the provisioner does not load `boto3` or `aws_cli`. The unresolved manifest never calls a client factory. A resource-client spy stays at zero until `ready()` has returned true and the caller account equals the manifest `account_id` and differs from `PROTECTED_ACCOUNT` |
| Publish `live` or deploy `dev` | Reuse `deploy_backend.point_alias`, `set_live_version.point`, `route_api_to_live_alias`, or a release or rollback workflow | Provisioner must not import those modules or invoke those workflows. It reads every protected constant from `verification_preflight` at comparison time | The provisioner source does not import `deploy_backend` or `lambda_manifest`. Each protected constant has a discriminating drift test in the required-test section. The negative control, which compares against the original literal, allows the otherwise-legal target |
| Reuse current-account ARNs or wildcards | Copy `infra/github-deploy-policy.json` or `deploy_exchange.py` policy documents, which target account `481838970142` and include a log ARN ending in `*` | Do not copy those policies. Validator already rejects `*`, `?`, and the protected account inside target fields. The new IAM policy is written only after the account id is real, with explicit ARNs. IAM in the other account remains the boundary if the process check is skipped | Existing wildcard and protected-account tests stay. A future policy fixture containing `Resource: "*"` or account `481838970142` must fail the provisioner's policy check |
| Teardown of the wrong resources | A cleanup flag lists names, then deletes by name in whatever account the credentials use | No teardown function and no delete API call in the first provisioner. `refuse_teardown()` keeps raising. A later design must re-run `ready()`, pass the identity check, and delete only ARNs that match the manifest exactly | A spy on delete APIs stays at zero for every public provisioner function. `refuse_teardown()` still raises after a passing `ready()`, which the current tests already cover |
| Synthetic data in the existing environment | A setup script calls `https://4c6dni17l3.execute-api.eu-north-1.amazonaws.com/dev` or writes an organization, request, notification, or exchange with current-account or third-account credentials | No write targets `PROTECTED_API` or `PROTECTED_STAGE`. No synthetic organization, request, notification, or exchange write is sent at the existing environment. Tests use fake clients | A request-factory spy stays at zero for the unresolved manifest. A target URL or stage equal to the imported `PROTECTED_API` or `PROTECTED_STAGE` aborts. Put and update spies for organizations, requests, notifications, and exchanges stay at zero when the caller identity is `481838970142` or any account other than the manifest `account_id` |

## Required provisioner control flow

A separate module, not `scripts/deploy_backend.py`, follows this order. The local module is `scripts/verification_provisioner.py`. It does not call AWS. Cloud use remains NO-GO.

1. Load only `config/erap-verification-manifest.json` through `load_manifest` of that fixed path. Do not call `main()`, and do not accept `--manifest`, an environment variable, or another path.
2. Call `ready()`. On `VerificationBlocked` or any other exception, exit non-zero. Do not catch and continue. No cloud client exists yet.
3. Select verification-account credentials explicitly. Do not use the ambient default credential chain. An exception during credential selection exits non-zero. No resource-specific client is constructed and no mutation runs.
4. The first cloud operation is a read-only caller-identity check with those selected credentials.
5. Abort before any resource-specific client unless every identity condition below is true. Missing, malformed, or unexpected identity data is a failure. An exception from the identity call is a failure. Do not catch any of these and continue.
   - The identity payload contains an account id.
   - That account id is a 12-digit string with no whitespace.
   - That account id equals the manifest `account_id` that `ready()` already accepted.
   - That account id differs from `verification_preflight.PROTECTED_ACCOUNT`.
6. A third account is rejected at this step. It is not enough that the account differs from `481838970142`. If it is not the manifest `account_id`, the provisioner exits non-zero with zero resource-specific clients and zero mutations.
7. The identity check is a process control. IAM account boundaries remain the independent security control. The identity check alone does not prevent credential misuse.
8. Only after step 5 succeeds, construct resource clients. An exception during resource-client construction exits non-zero, leaves the mutation count at zero, and must not be reported as successful readiness.
9. Compare every target with the protected constants on the `verification_preflight` module, read at comparison time as `verification_preflight.PROTECTED_*`. The provisioner consumes all ten: `PROTECTED_ACCOUNT`, `PROTECTED_API`, `PROTECTED_STAGE`, `PROTECTED_ALIAS`, `PROTECTED_POOL`, `PROTECTED_AMPLIFY`, `PROTECTED_ORIGIN`, `PROTECTED_AUTHORIZER`, `PROTECTED_BILLING_SECRET`, and `PROTECTED_ALARM`. None is excluded. A `from verification_preflight import PROTECTED_API` binding, or any other copied literal, is a second copy and fails the drift tests below.
10. Re-check the action against the manifest allowlists: account, `eu-north-1`, the eight functions, nine tables, three roles, required routes, Cognito pool and authorizer, CORS origin, and frontend origin `https://verification.{app-id}.amplifyapp.com`.
11. Abort if the target equals any imported protected constant, including the protected API, stage `dev`, the protected pool, the protected authorizer, the protected Amplify app, the protected origin, the protected billing secret, the protected alarm, alias `live`, or `emergency-resource-allocation:live`.
12. Immediately before any authorized mutation, repeat the account-boundary check: the caller account still equals the manifest `account_id` and still differs from `PROTECTED_ACCOUNT`. A mismatch or an exception here exits non-zero. The mutation call count stays zero.
13. An explicitly authorized create may run only after that final check, only in the isolated verification account, and only for an exact allowlisted resource. Discovery that adds a resource to the allowlist is a failure.
14. Do not publish an alias named `live`. The verification alias remains `verify`. Do not call a release workflow or a rollback workflow.
15. Do not write synthetic organizations, requests, notifications, or exchanges to the existing environment, including API `4c6dni17l3` and stage `dev`.
16. Do not implement teardown. `refuse_teardown()` remains the only cleanup entry, and it raises.

## Validation-bypass analysis

Current validator behavior, from `scripts/verification_preflight.py`:

- `load_manifest` parses JSON and returns it. The checked-in file loads successfully and is still invalid. `test_load_manifest_does_not_validate` covers that split. Using the returned object as authorization is a bypass.
- `preflight` returns a list. Ignoring the list is a bypass. `ready()` is the predicate that turns that list into `VerificationBlocked`.
- `ready()` enforces the checks only when it is called and the caller does not ignore the exception. It raises `VerificationBlocked` when the list is non-empty. It does not create a client and cannot see AWS credentials.
- `refuse_teardown` always raises, including after `ready` returns true.
- `main` returns 1 for the unresolved file. Its `--manifest` argument supports a local override. That override is acceptable for a human reviewer. It is not, by itself, a trusted provisioner entry point.

The existing validator tests cover the validator's own fail-closed cases: protected identifiers, wildcards, duplicates, pool-region mismatch, production origin with branch `main`, bare `live`, a `:live` integration, CORS mismatch, route authorizer mismatch, unknown keys, pending approvals, and non-false teardown. Provisioner behavior is covered only by `tests/test_verification_provisioner.py`. Those tests use injected spies. They do not authorize a cloud client.

IAM policies and a separate account are what stop a validated manifest from being applied to account `481838970142` when a process check is skipped or pointed at the wrong credentials.

## IAM and account isolation

A later implementation needs all of the following before any apply:

- A human confirms a dedicated account with a read-only identity call that uses explicitly selected credentials for that account. The checked-in manifest already records `917320177579`. That value must not be `481838970142`, `000000000000`, or `123456789012`. Recording the ID does not confirm that the AWS account or a verification stack exists. This design does not treat the infrastructure as existing.
- The verification deploy role exists only in that account. The current GitHub OIDC roles, `ERAP-GitHub-Deploy` and `ERAP-GitHub-Production`, are not granted permission to assume it.
- The role's resource list is the manifest's function, table, route, and role ARNs. No `Resource: "*"`, no `NotResource`, and no account-root ARN. The local gate blocks `NotResource` and `arn:<partition>:iam::<account>:root`. The design does not say whether an IAM user or role whose name is `root` is an account-root ARN, so that name is not given the account-root classification.
- The role cannot call `iam:Create*`, `organizations:*`, or `sts:AssumeRole` into `481838970142`.
- Lambda environment for the eight functions sets `environment=verification` and table names only through variables where the code already honors them. `EmergencyRequests`, `Resources`, `Allocations`, and `ResourceStatusHistory` are still hardcoded in the current handlers. Isolating those names requires the separate account. Do not point those functions at the current account to override the names.
- Do not copy `infra/github-deploy-policy.json` or the inline policy in `scripts/deploy_exchange.py`.
- Do not grant `secretsmanager:GetSecretValue` on `erap/billing/razorpay/production` or alarm rights on `ERAP-Production-Alarms`.
- CORS and the frontend origin are the verification Amplify origin, not `https://main.d3enpe7opotop5.amplifyapp.com`.

The process identity check and this IAM boundary are both required. Passing the identity check does not replace the account boundary.

## Required automated tests before implementation

Already present from the Phase 14H review: `tests/test_verification_preflight.py` (50 passed). They must stay green. They do not exercise a provisioner.

Still required, and not written, before a provisioner is coded. Each item is a separate assertion:

- The provisioner entry on the checked-in manifest raises `VerificationBlocked` and the cloud-client factory call count is zero.
- A pending approval stops the entry with `approval dedicated-account is not approved`.
- Account `481838970142` stops the entry with `account_id must not be the protected account`.
- Stage `dev` stops the entry with `api stage must not be dev`.
- Alias `live` stops the entry with `lambda_alias must be verify`.
- A pool region that differs from the Cognito ARN region stops the entry with `cognito pool_id region does not match the Cognito ARN region`.
- A stub that raises `VerificationBlocked` from `ready()` produces a non-zero result and a client-factory count of zero.
- The provisioner module has no module-level import of `boto3`, `botocore`, or `aws_cli`. A lazy import is reachable only after `ready()` returns true and the identity check passes.
- The provisioner source does not import `deploy_backend` or `lambda_manifest`.
- The provisioner signature accepts no manifest path. An environment variable and a second JSON file beside `config/erap-verification-manifest.json` are never read.
- No cloud client is constructed when `ready()` fails.
- An IAM document with `Resource` equal to `*` or an ARN containing `481838970142` is rejected.

Protected-constant drift. The provisioner consumes all ten constants. None is excluded. Each test below is specified so that an attribute read at use time aborts and a negative control that uses the original literal allows the same target. A value that the manifest, account-equality, schema, or approval rules already reject is not used as the patched value. These tests have not been executed.

Shared procedure for every constant:

1. Build the in-memory fixture from the existing validator test double: account `210987654321`, API `abcd1234ef`, stage `verify`, alias `verify`, pool `eu-north-1_VerifyPool1`, Amplify app `dverifyapp0001`, origin `https://verification.dverifyapp0001.amplifyapp.com`, authorizer `a1b2c3`, and every approval `approved`. Call `ready()` while every protected constant still has its source value. `ready()` returns true. These strings stay in the test. They are not written into `config/erap-verification-manifest.json`.
2. With the constants still unpatched, run the comparison against the legal target in the table. The comparison allows that target. This is the evidence that the target is legal before the patch.
3. Monkeypatch exactly one module attribute to that same legal target. Leave the other nine constants at their source values. Do not call `ready()` again. Calling `ready()` after the patch would reject the fixture through the validator and would hide whether the provisioner read the constant.
4. The compliant comparison reads `verification_preflight.<CONSTANT>` at that moment. The legal target now equals the patched attribute, so the comparison aborts. The mutation count is zero. For `PROTECTED_ACCOUNT`, the resource-specific client count is also zero.
5. The negative control is a local function in the test, not a change to the validator or the provisioner. It compares the same target to the original literal in the table. That literal differs from the legal target, so the negative control allows the target: the mutation spy increments. For `PROTECTED_ACCOUNT`, the negative control constructs a resource-specific client.
6. The test asserts both outcomes. The compliant path aborts. The negative control allows the target. If the negative control also aborts, the test fails, because that abort came from an unrelated rule. The pair of results is the evidence that the compliant abort was caused by the patched attribute.

| Constant | Comparison that reads the attribute | Legal target, and the patched value | Original literal in the negative control |
| --- | --- | --- | --- |
| `PROTECTED_ACCOUNT` | After the caller equals the manifest `account_id`, reject a caller equal to `verification_preflight.PROTECTED_ACCOUNT` | `210987654321` | `481838970142` |
| `PROTECTED_API` | Reject an API id equal to `verification_preflight.PROTECTED_API` | `abcd1234ef` | `4c6dni17l3` |
| `PROTECTED_STAGE` | Reject a stage equal to `verification_preflight.PROTECTED_STAGE` | `verify` | `dev` |
| `PROTECTED_ALIAS` | Reject an alias equal to `verification_preflight.PROTECTED_ALIAS` | `verify` | `emergency-resource-allocation:live` |
| `PROTECTED_POOL` | Reject a pool id equal to `verification_preflight.PROTECTED_POOL` | `eu-north-1_VerifyPool1` | `eu-north-1_vv7adAAC9` |
| `PROTECTED_AMPLIFY` | Reject an Amplify app id equal to `verification_preflight.PROTECTED_AMPLIFY` | `dverifyapp0001` | `d3enpe7opotop5` |
| `PROTECTED_ORIGIN` | Reject an origin equal to `verification_preflight.PROTECTED_ORIGIN` | `https://verification.dverifyapp0001.amplifyapp.com` | `https://main.d3enpe7opotop5.amplifyapp.com` |
| `PROTECTED_AUTHORIZER` | Reject an authorizer id equal to `verification_preflight.PROTECTED_AUTHORIZER` | `a1b2c3` | `y0hzhr` |
| `PROTECTED_BILLING_SECRET` | Reject a secret id equal to `verification_preflight.PROTECTED_BILLING_SECRET` | `erap/billing/verification-only` | `erap/billing/razorpay/production` |
| `PROTECTED_ALARM` | Reject an alarm name equal to `verification_preflight.PROTECTED_ALARM` | `ERAP-Verification-Alarms` | `ERAP-Production-Alarms` |

The billing-secret and alarm rows use test-only action names. They are not manifest fields, not checked-in configuration, and not cloud resources. Before the patch, neither name equals the source constant, and the manifest fixture has already passed `ready()`. After the patch, only the attribute-reading comparison rejects that name.

The drift paragraph's sentence "These tests have not been executed" records the design review. The local correction later executed the discriminating drift tests through the sealed gate. That run is listed only in "Evidence from the local gate corrections." It does not close the NO-GO.

Caller identity. The in-memory manifest account in these tests is the existing validator test double `210987654321`. It is not a cloud account and it is not written into the checked-in manifest. Each rejection case keeps the resource-specific client count at zero and the mutation count at zero.

- Correct verification account: identity `210987654321` equals the manifest `account_id` and differs from `PROTECTED_ACCOUNT`. A resource client may be constructed. A mutation runs only after the final account-boundary check returns that same account.
- Protected account: identity `481838970142` exits non-zero even when the selected credentials were labeled as verification credentials.
- Third account: identity `999999999999` exits non-zero. Differing from `481838970142` is not acceptance.
- Missing or malformed identity: a payload with no account field, and a payload whose account is `12ab`, each exit non-zero.
- Identity-check exception: the identity call raises `RuntimeError`. The result is non-zero. The success text from `verification_preflight.main` is not used.

Unexpected exceptions. Each fault is a separate test. Pass means a non-zero result, the workflow does not continue past the failed step, and the mutation count is zero. A caught exception that continues fails the test.

- Credential selection raises `RuntimeError` before the identity call. The resource-specific client count is zero.
- Caller-identity retrieval raises `RuntimeError`. The resource-specific client count is zero.
- Resource-client construction raises `RuntimeError` after a matching identity. The result is a failure, not successful readiness, and the mutation count is zero.
- The pre-mutation account-boundary check raises `RuntimeError`. The mutation count is zero and no later create call runs.

Remaining operation limits. Each item is a separate assertion:

- Delete API spies stay at zero.
- No write is addressed to API `4c6dni17l3` or stage `dev`.
- Organization, request, notification, and exchange write spies stay at zero for the existing environment.
- An allowlisted create spy is invoked only when identity equals the manifest account, that account is not `481838970142`, and the target ARN is one of the manifest's exact resource ARNs.

Those tests need a provisioner module. Adding them by weakening `ready()` or by making the unresolved manifest pass is not acceptable. Unresolved manifest fields stay unresolved.

## Implementation acceptance criteria

A later implementation phase may start only when every item below is true:

- This NO-GO is replaced by a separate written approval that names the provisioner module and forbids edits to `scripts/deploy_backend.py`, `scripts/lambda_manifest.py`, and the existing release, rollback, and `deploy-backend.yml` workflows.
- A human has recorded a real verification account id that `ready()` accepts, and the six approvals are `approved` only after that review. Until then the checked-in manifest must keep failing.
- The provisioner control flow above is implemented. `ready()` finishes before any cloud client. The caller account equals the manifest `account_id` and differs from `PROTECTED_ACCOUNT` before any resource client. The same account check is repeated immediately before any mutation. The new tests pass without network access.
- The provisioner reads all ten protected constants from `verification_preflight` at comparison time and does not duplicate them. Each constant has a discriminating drift test whose negative control allows the otherwise-legal target.
- `python scripts/verification_preflight.py` still exits non-zero on any manifest that is missing an account, uses the protected identifiers, or sets `teardown_enabled` to anything other than boolean false.
- `python scripts/security_scan.py` passes for the roots it scans. That result does not validate `config/` or `docs/`. CI still does not call AWS.
- Release workflows and rollback workflows remain outside the provisioner.
- Teardown remains absent.

## Blockers and approvals

Do not provision, deploy, move alias `live`, or tear down while any of these remain:

- Dedicated verification stack: unconfirmed. The manifest `account_id` is `917320177579`; that is not proof the account or stack exists. The protected production account remains `481838970142`.
- Manifest API IDs, Cognito IDs, Amplify identifiers, Lambda, table, role, and integration ARNs, authorizer IDs, and frontend origin: `UNRESOLVED`.
- Approvals `dedicated-account`, `guarded-deployer`, `verification-stack`, `verification-users`, `synthetic-data`, and `teardown-arns`: `pending`.
- Teardown design: not reviewed. `refuse_teardown()` must keep raising.
- Cloud provisioner: not approved. Local mocked code and tests exist; they do not authorize AWS. The six approvals above stay `pending`.
- Existing release workflows, existing rollback workflows, and `lambda_manifest.ALIAS`: still able to move alias `live`. They stay the production path and must not gain a verification job.
- Synthetic data and the three live checks: not approved. Those checks remain NOT RUN.

## Evidence from the Phase 14H review

Commands run locally in `C:\Azure\erap-phase-14g` during the Phase 14H review. No AWS CLI and no cloud client. Phase 14H.2, Phase 14H.4, and Phase 14H.6 did not re-run them.

- `python -m pytest tests/test_verification_preflight.py -q`: 50 passed, exit 0.
- `python -m compileall -q scripts/verification_preflight.py tests/test_verification_preflight.py`: exit 0.
- `python scripts/security_scan.py`: `security scan passed`, exit 0.

`scripts/security_scan.py` scans the configured roots `src`, `frontend`, `scripts`, `tests`, `.github`, and `infra`. It does not scan `config/` or `docs/`. That passing result is not validation of `config/erap-verification-manifest.json` or of this design document.

`python scripts/verification_preflight.py` was not re-run in the Phase 14H review. Source inspection shows the checked-in `account_id` is `917320177579`, remaining resource identifiers are still `UNRESOLVED`, and the approvals are still `pending`, which `ready()` rejects.

## Evidence from the local gate corrections

Commands below were run locally in `C:\Azure\erap-phase-14g` after the `NotResource` and account-root policy correction. No AWS CLI and no cloud client. `tests/conftest.py` imports `access`, which imports `boto3.dynamodb.conditions` at import time. The autouse fixture replaces `subscriptions_table` with an in-memory stub. The provisioner tests inject spies and do not call a table factory. This correction did not re-run `python scripts/security_scan.py` or `python scripts/verification_preflight.py`.

- `python -m pytest tests/test_verification_provisioner.py tests/test_verification_preflight.py -q -p no:cacheprovider`: 108 passed, exit 0. That is 58 provisioner tests and 50 preflight tests.
- `python -m compileall -q scripts/verification_provisioner.py tests/test_verification_provisioner.py`: exit 0.

Those results do not authorize provisioning, deployment, alias movement, or teardown.
