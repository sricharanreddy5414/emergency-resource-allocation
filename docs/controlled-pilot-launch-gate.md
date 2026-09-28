# Controlled pilot launch gate

## Current decision — 28 September 2026

Application commit `2b9c5cffac58cb993cc69403f021c45217ea6ba3`. Decision: PENDING HUMAN ACTION.

CORE APPLICATION PILOT: PASSED. Request type, request creation, matching, allocation, duplicate rejection, release, and public/private isolation were completed by the authenticated OWNER. This governance pass did not change application code.

GitHub `production` now exists. It requires approval from repository owner `sricharanreddy5414`, and its deployment branch policy allows only `main`. The `development` environment was left unchanged. Self-review is still allowed because that owner is the only reviewer; the approval step is still required.

Deploy backend run 36367668112 for `2b9c5cf` succeeded, including Verify hardening. Local pytest is 91 passed. Security scan, frontend check, workflow check, package check, and smoke test passed. The local AWS CLI session is expired, so Cognito users, SNS subscriptions, CloudWatch retention, and a DynamoDB restore were not touched.

The final closure pass on 28 September 2026 repeated the public checks. The Amplify site returned 200. Public discovery returned one item, Public Emergency Medical Supplies, with only safe fields, and `PILOT-MED-001` was absent. Protected routes returned 401. The `production` environment was read again and was not changed. The local AWS CLI session is still expired, so the user pool, the alarm topic subscriptions, CloudWatch retention, and a restore rehearsal were not executed.

Still pending a person: an approved operator email for `ERAP-Production-Alarms`, a second verified Cognito user, the live tenant-isolation test, ADMIN, OPERATOR, and MEMBER live tests, and a restore rehearsal. No application defect was found.

The table below is the earlier pre-pilot check. It is not the current decision.

Checked on 27 September 2026 against commit `aa9c9b62ce3931deb8e5fed4edea0be0bf1388a4`.

Statuses are PASS, PENDING, BLOCKED, or DEFERRED. There is no score.

The signed-in workflow was not executed. Categories that need that workflow stay PENDING.

| Category | Status | Evidence |
| --- | --- | --- |
| Authentication | PENDING | Cognito sign-in page loads. Self-service sign-up with email verification is available. MFA is off and was left off. No dedicated pilot user was signed in. |
| Organization onboarding | PENDING | Create-organization API and onboarding screen exist from earlier phases. Organization and member tables are empty. |
| Tenant isolation | PENDING | Live cross-tenant validation has no second organization. Unit tests still reject cross-tenant ids. |
| Roles | PENDING | Owner, admin, operator, and member rules are unchanged. They were not exercised with live users. |
| Locations | PENDING | Location API exists. The locations table is empty. |
| Resources | PENDING | Create and edit paths exist. No pilot resource was registered. Legacy resource count remains 4. |
| Requests | PENDING | Request validation exists. No pilot request was created. Legacy request count remains 31. |
| Matching | PENDING | Same-organization matching rules exist in code. No live match was run. |
| Allocation | PENDING | Conditional allocation and release exist in code. Legacy allocation count remains 22 and was not used. |
| Public discovery | PENDING | The public endpoint, filters, bad page controls, and field checks passed. No pilot public or private resource was published. |
| Audit | PENDING | Audit writing exists in code. The audit table is empty. |
| Security | PASS | Authorizer `y0hzhr` remains. Protected calls without a token return 401. Public responses checked here did not expose tenant or internal ids. Deployments use OIDC. No access keys were found in workflows. CORS stays restricted to the Amplify origin. |
| Monitoring | PENDING | Five ERAP alarms are OK and target `ERAP-Production-Alarms`. The topic has zero confirmed subscriptions, so alerting is not operational yet. |
| CI/CD | PASS | Pull requests run CI only. Pushes to `main` run CI and Deploy backend. Release and rollback are manual workflows. The last CI and deploy runs for this commit succeeded. |
| Rollback | PENDING | Alias `live` is the rollback point, and an earlier alias move was proven. The GitHub rollback workflow waits on a `production` environment that does not exist yet. |
| Backup | PASS | Point-in-time recovery and deletion protection are enabled on all ten tables. |
| Frontend | PENDING | The Amplify site responds and redirects to Cognito. Signed-in screens, including edit and logout, were not clicked. |
| Operational documentation | PASS | Pilot, data-safety, incident, and readiness documents exist. This gate and `docs/live-pilot-validation.md` record the current live check. |

## Earlier launch decision

This decision is the 27 September pre-pilot result. The current decision is at the top of this file.

PENDING — HUMAN PILOT ACTION REQUIRED

No software defect found in this check blocks the operator from starting. The monitored first pilot has not started, because the dedicated user, first organization, alarm subscription, and GitHub production reviewer are still manual actions. Details are in `docs/live-pilot-validation.md`.

## Deferred

- Turning MFA on after the operator has an enrollment plan.
- Splitting the shared operational Lambda role.
- Building a separate staging stack.
