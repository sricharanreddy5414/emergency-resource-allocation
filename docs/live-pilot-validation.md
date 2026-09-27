# Live pilot validation

Validation date: 27 September 2026.

This record covers what was checked on the live system. The signed-in pilot was not performed. No organization, location, resource, request, allocation, or audit event was created.

## 1. Pilot date

27 September 2026. Unauthenticated and configuration checks only.

## 2. Commit tested

`aa9c9b62ce3931deb8e5fed4edea0be0bf1388a4`

Local `main` and `origin/main` both point at this commit. The working tree was clean before this document was added.

## 3. Frontend URL

`https://main.d3enpe7opotop5.amplifyapp.com`

The URL responds. It opens the existing Cognito sign-in page. That page offers account creation for a new email address. No account was created during this check.

## 4. API stage

REST API `4c6dni17l3`, stage `dev`, region `eu-north-1`.

Current deployment id: `xuwrkf`.

## 5. Lambda alias

All nine functions use alias `live` at published version 4.

The alias description is `commit=aa9c9b62ce3931deb8e5fed4edea0be0bf1388a4`.

## 6. Cognito user pool

Pool `eu-north-1_vv7adAAC9`. Authorizer `y0hzhr` is still attached.

MFA is OFF. It was not changed.

Self-service sign-up is allowed. Usernames are email addresses, and email verification is required. Admin password authentication was not enabled. Existing pool users were not used. No password was created or stored.

## 7. Organization created

No. `Organizations` count is 0. `OrganizationMembers` count is 0.

## 8. Locations created

No. `Locations` count is 0.

## 9. Resources created

No pilot resources. `Resources` count remains the legacy baseline of 4.

## 10. Requests created

No pilot requests. `EmergencyRequests` count remains 31.

## 11. Matching result

Not run. There is no pilot organization to match inside.

## 12. Allocation result

Not run. `Allocations` count remains 22. Those rows were not modified.

## 13. Release result

Not run. `ResourceStatusHistory` count remains 17.

## 14. Public discovery result

`GET /public/resources` returned 200 with an empty resource list.

These checks also passed:

- `limit=1` returned 200
- `limit=1000` returned 400
- an invalid page token returned 400
- resource type, city, state, and availability filters returned 200
- an invalid availability value returned 400

The response did not contain `organization_id`, `actor_sub`, `actor_id`, `resource_id`, `user_sub`, or `attributes`.

`GET /allocate/resources`, `GET /resource-types`, and `GET /requests` returned 401 without a token.

A pilot PUBLIC resource and a pilot PRIVATE resource were not published, so that comparison remains to be done after onboarding.

## 15. Audit result

`AuditEvents` count is 0. There is no pilot activity to audit.

## 16. Tenant isolation result

PENDING — live cross-tenant validation.

No second organization exists. Unit tests still cover cross-tenant rejection. That is not a live test.

## 17. Role result

Not run with live users. Owner, admin, operator, and member rules are unchanged in the backend. A client-supplied organization id, user id, or role is not the source of authorization.

## 18. Alarm status

These alarms exist, are in OK state, and publish to `ERAP-Production-Alarms`:

- `ERAP-ApiGateway-5XX`
- `ERAP-Allocation-Throttles`
- `ERAP-AutoRelease-Errors`
- `ERAP-Public-Lambda-Errors`
- `ERAP-Resources-SystemErrors`

The topic has zero subscriptions. Alerting is not operational until an operator confirms a subscription. No test message was sent.

## 19. GitHub production approval status

Not configured.

The repository has a `development` environment with no protection rules. A `production` environment does not exist. Release and rollback workflows are manual and are set to use `production`, so that environment must be created before those workflows can be used as an approval gate.

## 20. Test suite results

On 27 September 2026:

- `python -m pytest -q`: 78 passed
- `python scripts/security_scan.py`: passed
- `python scripts/check_frontend.py`: passed
- `python scripts/check_workflows.py`: passed
- `python scripts/package_lambdas.py --check`: passed for all nine functions
- `python scripts/verify_hardening.py`: passed
- `python scripts/smoke_test.py`: passed

GitHub CI run `36331699568` succeeded for this commit. Deploy backend run `36331699565` succeeded.

## 21. Issues discovered

No application defect was observed in the unauthenticated checks. The signed-in product path was not exercised.

## 22. Blockers

No software blocker was found.

The monitored pilot cannot be marked complete until a person creates a dedicated user and finishes the workflow in `docs/pilot-runbook.md`.

## 23. Non-blockers

- MFA remains off.
- The shared operational Lambda role was not split.
- There is no separate staging stack.
- The alarm topic has no subscriber yet.
- The GitHub `production` environment is not created yet.

## 24. Manual actions remaining

1. Create a dedicated pilot user. Open `https://main.d3enpe7opotop5.amplifyapp.com`, choose Create an account, and use a new mailbox reserved for this pilot. Confirm the verification email Cognito sends. Do not use an existing personal account. Do not turn on admin password sign-in.
2. Sign in and create the first organization in the onboarding form.
3. Create at least one location, one resource type, one request type, one PRIVATE resource, and one PUBLIC resource that contains only non-sensitive text.
4. Create one clearly marked pilot emergency request. Do not use information that could call a real responder.
5. Match, allocate, and release a pilot resource. Confirm the resource cannot be allocated twice and is available after release.
6. Confirm the PUBLIC resource appears on public discovery and the PRIVATE resource does not.
7. Read the audit events for those actions.
8. Sign out, open a protected page, and sign in again.
9. Subscribe the operator endpoint: AWS Console, SNS, topic `ERAP-Production-Alarms`, Create subscription, then confirm it. Do not put that address in source code.
10. GitHub, Settings, Environments, create `production`, add a required reviewer, and allow deployments from `main` only.

## Logging observed

Version 4 of `erap-public-resources` wrote structured lines containing `request_id`, `route`, `operation`, `result`, and `duration_ms`. Sampled lines were for `GET /public/resources` with results 200 and 400. Those lines did not contain a bearer token, an authorization header, or a token prefix. All nine Lambda log groups retain logs for 30 days.

## API routing observed

Thirty Lambda integrations on stage `dev` invoke alias `live`. None invoke `$LATEST`.

## Data left untouched

Legacy counts stayed at Resources 4, EmergencyRequests 31, Allocations 22, and ResourceStatusHistory 17. `scripts/migrate_tenant_scope.py apply` was not run.
