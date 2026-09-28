# Controlled pilot launch gate

## Current decision — 28 September 2026

CORE APPLICATION PILOT: PASSED.

SECURITY AND TENANT ISOLATION: LIVE EXECUTED AND PASSED. A second Cognito user signed in through the normal login, created a separate organization, and saw zero resources, zero requests, and zero allocations. That organization did not expose ERAP Pilot Operations data.

OPERATIONAL / GOVERNANCE: the confirmed alarm subscription, the point-in-time restore into a new table, GitHub `production` protection, OIDC, and the hardening checks are complete. Production tables were not overwritten.

OVERALL LAUNCH GATE: NOT PASSED.

ADMIN, OPERATOR, and MEMBER remain CODE-LEVEL VERIFIED / LIVE PENDING. Member management is now implemented and documented in `docs/organization-member-management.md`. An owner or admin can invite `ADMIN`, `OPERATOR`, or `MEMBER`, and the invited person must accept with a verified Cognito email. Live validation of those three roles has not been executed. No existing membership row was edited to simulate a result.

GitHub `production` requires approval from `sricharanreddy5414` and allows only `main`. The `development` environment was not changed.

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
