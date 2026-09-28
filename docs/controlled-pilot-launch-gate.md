# Controlled pilot launch gate

## Current decision — 28 September 2026

Commit `b5d72cf1ad7f300800810971bb8c165302f56998`. Decision: PENDING HUMAN ACTION.

The authenticated pilot for ERAP Pilot Operations is complete: request type, request creation, matching, allocation, duplicate rejection, and release. `PILOT-REQ-001`, `PILOT-REQ-002`, `ALLOC-PILOT-REQ-001`, and `ALLOC-PILOT-REQ-002` are RELEASED. `PILOT-MED-001` and `PILOT-MED-002` are AVAILABLE. This pass did not re-read DynamoDB because the local AWS CLI session is expired.

Re-checked here: 91 pytest tests, security scan, frontend check, workflow check, package check, and the public smoke test. Deploy backend run 36347114518 succeeded, including Verify hardening. Public discovery returns only Public Emergency Medical Supplies with safe fields. Protected routes return 401 without a token.

Still pending a person: SNS email confirmation, a second verified Cognito user, the live tenant-isolation test, ADMIN, OPERATOR, and MEMBER live tests, the GitHub `production` environment and reviewer, and a restore rehearsal. No application defect was found in this pass.

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
