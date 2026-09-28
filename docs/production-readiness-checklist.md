# Production readiness checklist

## Current launch gate — 28 September 2026

Commit `b5d72cf1ad7f300800810971bb8c165302f56998`. Gate: PENDING HUMAN ACTION.

The authenticated pilot is complete and no application defect was found in this recheck. Local pytest is 91 passed. Security scan, frontend check, workflow check, package check, and smoke test passed locally. GitHub Deploy backend run 36347114518 succeeded, including Verify hardening, so table protection, throttles, CORS, alarms, the Cognito authorizer, and the public `live` alias were checked through OIDC. The local AWS CLI session is expired, so DynamoDB rows, Cognito settings, Lambda log retention, and the SNS subscription count were not re-read in this pass.

Pending human action: confirm an alarm email, create the GitHub `production` environment with a required reviewer, verify a second user for tenant isolation and non-owner roles, and rehearse restore. A restore rehearsal has not been executed.

The sections below are the earlier Phase 8 snapshot. Where they say no organization exists or the public list is empty, the current pilot section above replaces them.

Status values are PASS, PARTIAL, NOT READY, or DEFERRED. This records the system after the Phase 8 alias, alarm, and log-retention changes.

## A. Architecture

PASS. The existing API, Cognito pool, tables, and Amplify app remain. API Gateway now invokes alias `live`. Auto-release uses the same alias.

## B. Authentication

PARTIAL. Protected routes use Cognito authorizer `y0hzhr`. Missing tokens return 401. The web client allows SRP, not admin password auth, so this phase did not complete a scripted login. MFA is off. Impact: a bad client change could weaken login, and live sign-in was not repeated here. Next action: sign in once with a real operator through the Amplify site before inviting another organization. Do not turn on a password flow just for a script.

## C. Authorization

PARTIAL. The backend checks membership and role. Unit tests cover member and operator limits. A live role test needs a signed-in user. Impact: a regression in the deployed package would not be caught by the public smoke test. Next action: the first real organization should try one operator action and one member denial.

## D. Multi-tenancy

PARTIAL. Queries are organization-scoped and cross-tenant reads return 404 in tests. No organization rows exist, so two live tenants were not created. Impact: the first tenants are the first live proof. Next action: create two organizations only as a deliberate test, or treat the first customer as a monitored pilot.

## E. Multi-location

PARTIAL. Location writes require an admin role and the location must belong to the organization. Live location create was not run. Next action: same as the first organization pilot.

## F. Resources

PARTIAL. Create, update, visibility, and release are implemented and covered by tests. Live create was not run. Public discovery of the four legacy resources stays empty.

## G. Requests

PARTIAL. Create and update require a member role and a pending state. Live create was not run.

## H. Matching

PASS for the code path. Same-organization matching and the conditional allocation write are tested. Live matching was not run because there is no tenant.

## I. Public discovery

PASS. `GET /public/resources` returns 200 and an empty list. Oversized pages and bad tokens return 400. The body does not include `organization_id`.

## J. Data protection

PASS. Point-in-time recovery and deletion protection are enabled on all ten tables. Legacy rows were not modified.

## K. AWS security

PARTIAL. GitHub uses OIDC and no access keys. The deploy roles cannot write DynamoDB or delete functions. The four operational Lambdas still share one role that can write all four operational tables. Impact: one function can call an action it does not need. Next action: split that role only with a test that allocation, release, and auto-release still work.

## L. CI/CD

PARTIAL. Pull requests run tests only. `main` deploys the live backend because there is one API. Release and rollback are manual and can use the `production` environment, but that environment has no required reviewer yet. Impact: a push to `main` changes the site the public URL calls. Next action: add a required reviewer on `production` before using Release or Rollback. That does not stop the automatic `main` deploy.

## M. Monitoring

PARTIAL. The five `ERAP-*` alarms publish to `ERAP-Production-Alarms`. Nothing is subscribed, so nobody is paged. The older allocation alarm still uses `EmergencyResourceNotifications`. Lambda logs are kept for 30 days. Next action: subscribe an operator endpoint to `ERAP-Production-Alarms`.

## N. Backup and recovery

PARTIAL. PITR is on and `docs/disaster-recovery.md` describes restore to a new table. A restore was not executed. Impact: the procedure is untested. Next action: rehearse a restore only into a new table name when a maintenance window exists.

## O. Rollback

PASS. Alias `live` was moved from version 2 to version 1 and back. Public, invalid, and unauthenticated checks passed on both versions. Version 1 and version 2 contain the same application code.

## P. Frontend

PASS. Amplify `main` serves `https://main.d3enpe7opotop5.amplifyapp.com`. The page, `app.js`, and `style.css` return 200. The API base is the existing `dev` stage. Source has no access keys, no localhost API, and no payload logs.

## Q. API

PASS. Throttling remains 20 per second with burst 40, and public GET remains 5 per second with burst 10. CORS is the Amplify origin. Authorizer `y0hzhr` is still attached.

## R. Documentation

PASS. Operations are described in the runbook, the OIDC note, the rollback note, and this checklist.

## Deferred

Isolated staging is DEFERRED. The platform has no production tenant data. A second full stack would add cost and a second set of tables without a current isolation benefit.
