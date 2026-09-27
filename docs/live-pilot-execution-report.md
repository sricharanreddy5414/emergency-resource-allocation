# Live Pilot Execution Report

## Execution timestamp

27 September 2026, 17:00:38Z through 17:45:00Z. Organization and location were created by the authenticated pilot user before this pass. Catalog, resource, request, allocation, and release actions in this pass are timestamped on the audit events below.

## Git commit

The first authenticated pilot ran against `4b31065155278e29ef466930a22b676298d13a62`. The Decimal serialization fix is commit `de57d69fe4a0fac3df628754104a44db017d1ec4`. GitHub Actions CI and Deploy backend for that commit succeeded. The deploy job ran tests, the secret scan, package check, smoke test, and `python scripts/verify_hardening.py`. The hardening step passed and the alias rollback step was skipped. API Gateway traffic follows alias `live`. The local AWS CLI session had expired, so this machine could not print the new Lambda version number. The deploy script publishes a version and sets the alias description to that commit.

## Authenticated pilot identity

Role: OWNER of ERAP Pilot Operations. No email, name, phone number, Cognito subject, or token is recorded here.

## Authentication

PASS

The dedicated pilot user completed Cognito authentication and used the live application at `https://main.d3enpe7opotop5.amplifyapp.com`. Protected calls used the signed-in session. Authorization was not bypassed. Admin password authentication was not enabled.

## Organization

PASS

`ERAP Pilot Operations` exists as `ORG-D13B30D99127`, status ACTIVE. The signed-in membership role is OWNER. The organization was not recreated. Backend membership is keyed from the Cognito subject, not from a client-supplied role.

## Location

PASS

`Bengaluru Operations Center` exists as `LOC-9497150CB853`, city Bengaluru, status ACTIVE, organization `ORG-D13B30D99127`. The location selector showed this location for the signed-in session. It was not recreated. The stored state is blank.

## Resource Types

PASS

`Emergency Medical Kit` was created through the authenticated admin flow. Id `RT-2ED4DE323557`, status ACTIVE, organization `ORG-D13B30D99127`. It appeared in the resource form. A duplicate create returned 409. Audit action `resource-type.create` was recorded.

## Request Types

PENDING HUMAN ACTION

`Emergency Medical Supply Request` was created through the authenticated admin flow. Id `RQ-5201EFBA2A5C`, status ACTIVE, organization `ORG-D13B30D99127`. Audit action `request-type.create` was recorded. A duplicate create returned 409.

During the first pilot, `GET /request-types` returned 500. Catalog logs showed result 200, then `Catalog error: TypeError`, then result 500 `REQUEST_FAILED`. `type_view` returns `default_priority` from DynamoDB as a Decimal, and `api_response` called `json.dumps` with no Decimal handler. Resource-type listing succeeded because those records do not store `default_priority`.

That serializer is fixed in `de57d69` and deployed. A regression test stores `default_priority` as `Decimal("3")` and requires HTTP 200 with JSON number 3. The signed-in page was on the Cognito sign-in screen after the earlier logout, so the live authenticated list and the dashboard dropdown were not opened again. No password was entered. Unauthenticated `GET /request-types` still returns 401.

## Private Resource

PASS

`PILOT-MED-001`, name Pilot Emergency Medical Kit, type Emergency Medical Kit (`RT-2ED4DE323557`), location `LOC-9497150CB853`, organization `ORG-D13B30D99127`, visibility PRIVATE. It was created through Register Resource and listed for the signed-in user. After release it is available again. Audit action `resource.create` was recorded. Public discovery does not return it.

## Public Resource

PASS

`PILOT-MED-002`, name Public Emergency Medical Supplies, same type and location, organization `ORG-D13B30D99127`, visibility PUBLIC, available. Published fields are the name, city Bengaluru, description "Emergency medical supplies for response coordination.", and availability. Public contact is empty. Audit action `resource.create` was recorded.

## Public/Private Visibility

PASS

Unauthenticated `GET /public/resources` returned 200 with one resource. Response keys are `next_token` and `resources`. The item keys are `availability`, `city`, `description`, `name`, and `resource_type`. The item is Emergency Medical Kit, Public Emergency Medical Supplies, Bengaluru, the published description, availability AVAILABLE. `next_token` is null. `PILOT-MED-001` is absent. The body does not contain `organization_id`, a resource id, an actor id, or a Cognito subject.

`state=Karnataka` returns an empty list because the location state was never stored. `city=Nowhere` returns an empty list. Invalid page token `not-a-token` returns 400 `Invalid page token`. There is only one public row, so a second real page was not fetched.

## Request Creation

PENDING HUMAN ACTION

`PILOT-REQ-001` exists from the first pilot. Organization `ORG-D13B30D99127`, location `LOC-9497150CB853`, request type `RQ-5201EFBA2A5C`, priority 3. Current status is RELEASED. That first request was submitted only after the already created type was placed into page memory, because the dropdown was empty. The submit itself was authenticated `POST /allocate`.

On 28 September 2026 the signed-in Create Resource Request modal showed Request Type, not a hardcoded resource list. The dropdown listed Emergency Medical Supply Request from `GET /request-types`. `PILOT-REQ-002` was created through that modal with location Bengaluru Operations Center, priority 5, and quantity 1. The requests table showed Request Type, Emergency Medical Supply Request, Bengaluru Operations Center, P5, and PENDING. The row was still there after a reload. Created at `2026-09-27T19:11:44.387173+00:00`.

## Matching

PASS

The live allocation selected `PILOT-MED-001`. Both pilot resources were available, in the same organization, at the same location, and compatible with request type `RQ-5201EFBA2A5C`. The request type stores `compatible_resource_type_ids` containing that resource type, `same_location_preferred` true, and no required numeric minimums. Matching keeps only the request organization, requires a compatible type and an available resource, prefers the same location, then chooses the lowest resource id. `PILOT-MED-001` sorts before `PILOT-MED-002`. Private visibility did not exclude it and did not weaken the organization check. `PILOT-MED-002` stayed available. Cross-organization matching was not executed because only one organization exists.

## Allocation

PASS

Allocation `ALLOC-PILOT-REQ-001` was created for `PILOT-REQ-001` and resource `PILOT-MED-001`, organization `ORG-D13B30D99127`. The dashboard moved from Available 2 / Allocated 0 to Available 1 / Allocated 1. Audit action `allocation.create` was recorded. The allocation was later released.

## Double Allocation Protection

PASS

A second authenticated submit of `PILOT-REQ-001` returned HTTP 409 with message "Request is not eligible for allocation". Available and Allocated counts stayed at 1. No second allocation row was created.

## Release

PASS

The signed-in Resources page Release action was confirmed for `PILOT-MED-001`. Both pilot resources returned to available. Allocation `ALLOC-PILOT-REQ-001` status is RELEASED. Request `PILOT-REQ-001` status is RELEASED. Audit action `resource.release` was recorded. DynamoDB was not edited directly.

## Resource Status History

PASS

Two history rows exist for `PILOT-MED-001` and request `PILOT-REQ-001`, organization `ORG-D13B30D99127`:

- AVAILABLE to ALLOCATED, reason `RESOURCE_ALLOCATED`
- ALLOCATED to AVAILABLE, reason `RESOURCE_RELEASED`

## Audit Events

PASS

Eight events, all organization `ORG-D13B30D99127`, role OWNER, result SUCCESS:

- 2026-09-27T17:00:38.886245+00:00 `organization.create` `ORG-D13B30D99127`
- 2026-09-27T17:01:52.202020+00:00 `location.create` `LOC-9497150CB853`
- 2026-09-27T17:12:35.374375+00:00 `resource-type.create` `RT-2ED4DE323557`
- 2026-09-27T17:15:44.030974+00:00 `request-type.create` `RQ-5201EFBA2A5C`
- 2026-09-27T17:21:46.647967+00:00 `resource.create` `PILOT-MED-001`
- 2026-09-27T17:24:15.610966+00:00 `resource.create` `PILOT-MED-002`
- 2026-09-27T17:32:22.548422+00:00 `allocation.create` `ALLOC-PILOT-REQ-001`
- 2026-09-27T17:34:46.069135+00:00 `resource.release` `PILOT-MED-001`

No separate visibility or request-create event is written by the current design. Structured logs include request id, route, operation, result, duration, and error code. Audit log lines also include the actor subject. That subject is not copied here. No token or password was found in the inspected catalog logs.

## Tenant Isolation

PENDING

Only one organization and one authenticated user exist. A second user still requires a Cognito email verification code. That identity was not created, and authorization was not weakened to simulate one. Cross-organization reads, id replay, and cross-organization matching were not executed live.

## Role Validation

PENDING

OWNER was executed live. ADMIN, OPERATOR, and MEMBER were not executed live. Their permission differences are present in source and tests. That is code-level verification only.

## Logout/Session

PENDING

Logout was executed. The signed-in session ended on the Cognito sign-in page. Unauthenticated calls to protected routes return 401, recorded under Public API Security. Sign-in again was not executed, because it requires the pilot password. Restoration of organization and location after a new sign-in is therefore not verified.

## Public API Security

PASS

Unauthenticated results on `https://4c6dni17l3.execute-api.eu-north-1.amazonaws.com/dev`:

- `GET /public/resources` 200, public kit only, no internal identifiers
- `limit=0`, `limit=1000`, and `limit=abc` each 400 `Page size is invalid`
- `page_token=not-a-token` 400 `Invalid page token`
- `availability=AVAILABLE` 200, public kit only
- `availability=NOPE` 400 `Filter is invalid`
- `resource_type=Emergency Medical Kit` 200, public kit only
- `city=Bengaluru` 200, public kit only
- `state=Karnataka` 200, empty list
- `city=Nowhere` 200, empty list
- `GET /requests`, `/resource-types`, `/request-types`, `/locations`, and `/organization` each 401 `Unauthorized`
- `GET /allocate` 403 `Missing Authentication Token` because that method is not defined
- `POST /allocate` with an empty body and no token 401 `Unauthorized`

## CloudWatch/Observability

PASS

All nine Lambda log groups exist and retain logs for 30 days: `create-request`, `emergency-resource-allocation`, `emergency-resource-auto-release`, `erap-catalog`, `erap-create-organization`, `erap-get-organization`, `erap-locations`, `erap-public-resources`, and `get-resources`. The catalog error for request-type listing is represented as a TypeError and result 500. Allocation and public responses inspected here do not contain tokens or passwords.

## Alarm Subscription

PENDING

These alarms are OK and each has one alarm action: `ERAP-ApiGateway-5XX`, `ERAP-Allocation-Throttles`, `ERAP-AutoRelease-Errors`, `ERAP-Public-Lambda-Errors`, `ERAP-Resources-SystemErrors`. Topic `ERAP-Production-Alarms` has zero subscriptions. No test notification was sent.

ALARM SUBSCRIPTION PENDING

## GitHub Production Protection

PENDING

The `development` environment exists with no protection rules and no deployment branch policy. The `production` environment returns 404. It was not created. Release and rollback workflows are declared to use the `production` environment and OIDC role `ERAP-GitHub-Production`. No static AWS access keys are in the workflows.

PRODUCTION ENVIRONMENT GOVERNANCE PENDING

## CI/CD

PASS

For commit `4b31065`, GitHub Actions CI succeeded and Deploy backend succeeded. Both workflows use OIDC. Deploy backend uses environment `development` and role `ERAP-GitHub-Deploy`. A read before the serializer fix found 30 non-OPTIONS integrations using `:live/invocations` and zero using `$LATEST`. Commit `de57d69` was then deployed by that same pipeline. Its deploy job passed smoke test and hardening, which checks that public `GET /public/resources` invokes alias `live`. The alias rollback step did not run.

## Security Regression

PASS

Commands run from this repository on 27 September 2026, exit code 0:

- `python -m pytest -q` — 86 passed after the Decimal tests were added. The earlier pilot run was 78 passed.
- `python scripts/verify_hardening.py` — passed, including PITR, deletion protection, throttle, CORS, alarms, authorizer `y0hzhr`, and the public live alias
- `python scripts/security_scan.py` — passed
- `python scripts/check_frontend.py` — passed (`NO_FRONTEND_BUILD`)
- `python scripts/check_workflows.py` — passed for `ci.yml`, `deploy-backend.yml`, `rollback.yml`, and `release.yml`
- `python scripts/package_lambdas.py --check` — passed for all nine functions
- `python scripts/smoke_test.py` — passed, including public 200, invalid page size 400, invalid page token 400, protected 401, and frontend 200. Amplify job status was not checked by that script.

## Database Safety

PASS

Point-in-time recovery is ENABLED and deletion protection is true on all ten tables. `scripts/migrate_tenant_scope.py apply` was not run. Legacy non-pilot resources remain 4.

Before the authenticated catalog and workflow creates, the known baseline was Organizations 1, OrganizationMembers 1, Locations 1, ResourceTypes 0, RequestTypes 0, Resources 4, EmergencyRequests 31, Allocations 22, ResourceStatusHistory 17, AuditEvents 2.

Pilot-created rows are the two types, two resources, one request, one allocation, two history rows, and six audit events after the original organization and location events.

After this pilot: Organizations 1, OrganizationMembers 1, Locations 1, ResourceTypes 1, RequestTypes 1, Resources 6, EmergencyRequests 32, Allocations 23, ResourceStatusHistory 19, AuditEvents 8.

## Pilot Data Created

- Organization `ORG-D13B30D99127`, ERAP Pilot Operations, ACTIVE, one OWNER membership
- Location `LOC-9497150CB853`, Bengaluru Operations Center
- Resource type `RT-2ED4DE323557`, Emergency Medical Kit
- Request type `RQ-5201EFBA2A5C`, Emergency Medical Supply Request
- Resource `PILOT-MED-001`, Pilot Emergency Medical Kit, PRIVATE, available
- Resource `PILOT-MED-002`, Public Emergency Medical Supplies, PUBLIC, available
- Request `PILOT-REQ-001`, priority 3, status RELEASED
- Allocation `ALLOC-PILOT-REQ-001`, resource `PILOT-MED-001`, status RELEASED
- Resource status history for allocation and release of `PILOT-MED-001`
- Eight audit events listed above

No patient data, passwords, tokens, or secrets were stored in these records. The rows were left in place as pilot evidence.

## Tests Executed

- Live authenticated UI: organization, location, resource type, both resources, allocation, duplicate allocation, release, and logout
- Authenticated `POST /allocate` for the first allocation and the 409 repeat
- Unauthenticated public and protected API calls listed above
- Read-only DynamoDB counts, pilot records, history, and audit actions
- Read-only CloudWatch log retention, alarm state, and SNS subscription count
- Public GitHub environment and workflow-run lookup
- `python -m pytest -q`
- `python scripts/verify_hardening.py`
- `python scripts/security_scan.py`
- `python scripts/check_frontend.py`
- `python scripts/check_workflows.py`
- `python scripts/package_lambdas.py --check`
- `python scripts/smoke_test.py`

## AWS Changes

No infrastructure, IAM, Cognito, CORS, alarm, PITR, or deletion-protection change was made. Pilot records were created only through the application. No table was reset and no organization was deleted.

## Decimal serialization fix

The production failure was `TypeError: Object of type Decimal is not JSON serializable` in `api_response`. `dumps_json` in `src/organization/common.py` now turns an integer Decimal into a JSON integer and a fractional Decimal into a JSON number, using the exact decimal text rather than a string. Authentication, authorization, matching, allocation, and public visibility were not changed.

`tests/test_decimal_response.py` covers an integer Decimal 3, a fractional Decimal, the request-type list that previously returned 500, an unchanged resource-type list, the 401 and 400 catalog errors, and the public field allowlist.

Local commands after the fix: `python -m pytest -q` passed with 86 tests. `security_scan.py`, `check_frontend.py`, `check_workflows.py`, `package_lambdas.py --check`, and `smoke_test.py` passed. Local `verify_hardening.py` exited 1 because the AWS CLI session had expired. The GitHub deploy job then ran that same check with OIDC and it passed.

After deployment, unauthenticated `GET /public/resources` still returns only Public Emergency Medical Supplies. `PILOT-MED-001` is absent. The body has no organization id, resource id, or actor id. Protected routes still return 401.

The authenticated reload, `PILOT-REQ-002`, and the repeat of match, allocate, duplicate rejection, and release were not executed.

## Request form contract

The Create Resource Request modal was an older form. It showed hardcoded resource choices, sent `resource_type` and a free-text location, and did not send a Cognito token or `request_type_id`. The dashboard allocation form already loaded request types and submitted `request_type_id`. Requests are stored with `request_type_id`. Matching uses that request type's compatible resource types. It does not require the request type id to equal the resource type id.

The modal now loads request types, shows their names, submits `request_type_id`, uses an organization location, sends the signed-in token, and reloads the request list. `POST /requests` and `GET /requests` use the same Decimal-safe JSON serializer. Commit `31fa1cb447efd7ff644568ddcf7c6afef0feec27`. `python -m pytest -q` passed with 90 tests. CI and Deploy backend for that commit succeeded, including smoke and hardening.

The live site HTML contains `modalRequestType` and `submitRequestModal`, and it no longer contains the hardcoded resource choices. Chrome was on the Cognito sign-in page, so the signed-in dropdown, `PILOT-REQ-002`, matching, allocation, duplicate rejection, and release were not clicked after this deploy.

## Authenticated PILOT-REQ-002 retest

The signed-in session showed ERAP Pilot Operations and Bengaluru Operations Center. The Create Resource Request modal label is Request Type. Emergency Medical Supply Request was already in the dropdown. No option was injected.

`PILOT-REQ-002` was submitted from that modal. The list showed Emergency Medical Supply Request, Bengaluru Operations Center, P5, and PENDING. A later reload kept the row.

The reload also exposed a session defect. `GET /requests` returned 401 while the page still showed the organization. The ID token had expired, and the application stored a refresh token but never used it. Commit `dcf6e1c` refreshes the Cognito session and retries one protected call after 401. Commit `b2bc461` keeps a still-usable token when refresh is unavailable, instead of sending the user to sign-in. `tests/test_session_refresh.py` covers that contract. `python -m pytest -q` passed with 91 tests. Security scan, frontend syntax, workflow check, package check, and smoke test passed. Local `verify_hardening.py` was not re-run because the AWS CLI session had expired. GitHub CI and Deploy backend for `b2bc461` succeeded.

After that frontend was live, the same browser returned to the application without a password being submitted. The dashboard showed 2 resources, 2 available, and 0 allocated. The dashboard Allocate Resource form then allocated `PILOT-REQ-002`. The screen said "Resource allocated successfully". Allocations shows one new row: `ALLOC-PILOT-REQ-002`, resource `PILOT-MED-001`, location Bengaluru Operations Center, priority 5, status ALLOCATED. The matcher again selected `PILOT-MED-001`.

A second allocation of the same request was submitted through the same form. The screen said "Request is not eligible for allocation". The allocations list still has one row for `PILOT-REQ-002`.

Release was opened from that row. The confirmation said "Release resource PILOT-MED-001?". The confirmation was not accepted. After the list was shown again, `ALLOC-PILOT-REQ-002` was still ALLOCATED. Release is not complete. `PILOT-MED-001` remains allocated to `PILOT-REQ-002`.

Unauthenticated `GET /public/resources` still returns one item, Public Emergency Medical Supplies, Emergency Medical Kit, Bengaluru, AVAILABLE. Item keys are availability, city, description, name, and resource_type. `PILOT-MED-001` is absent.

Tenant isolation was not executed. Only one verified user exists. ADMIN, OPERATOR, and MEMBER were not signed in. Alarm subscription and the GitHub production environment were not changed. No migration was applied and no pilot record was deleted.

## Remaining Manual Actions

- Accept the open release confirmation for `PILOT-MED-001`, or click Release on `ALLOC-PILOT-REQ-002` and confirm it. Then refresh and confirm the allocation is RELEASED and `PILOT-MED-001` is available again.
- Add a subscription to `ERAP-Production-Alarms`. Alarm subscription is pending.
- Create the GitHub `production` environment with required reviewers and a main-only deployment policy. Production environment governance is pending. The `production` environment still returns 404.
- Create a second verified user before live tenant-isolation and non-owner role tests.

## Final Launch Gate

PENDING HUMAN ACTION

The signed-in user created `PILOT-REQ-002` from the Request Type dropdown, matched it to `PILOT-MED-001`, allocated it, and saw the duplicate allocation rejected. Release was not completed: the confirmation was shown and the allocation is still ALLOCATED. Tenant isolation, non-owner roles, alarm subscription, and GitHub production protection remain pending. This is not a full pass.
