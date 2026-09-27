# Phase 9 pilot current state

Audit date: 27 September 2026. Starting commit: `b081a5c7f7fc7ed6d5c174f3869ae49deec64cfe`.

This document records what was already working before any Phase 9 change, what has never been exercised with a real organization, and the small operational fixes made after the audit.

## Already working

The live path is GitHub Actions to a published Lambda version, then alias `live`, then API Gateway REST API `4c6dni17l3` stage `dev` in `eu-north-1`. The frontend is Amplify app `d3enpe7opotop5` at `https://main.d3enpe7opotop5.amplifyapp.com`.

Phase 8 left these controls in place:

- Cognito authorizer `y0hzhr` on protected methods. Unauthenticated calls receive 401.
- Public discovery is a separate function that queries only the public index.
- Stage throttle is 20 requests per second with a burst of 40. Public resource reads are 5 per second with a burst of 10.
- CORS allows the Amplify origin. It is not `*`.
- Point-in-time recovery and deletion protection are on for the ten DynamoDB tables.
- CloudWatch alarms publish to SNS topic `ERAP-Production-Alarms`. That topic has no subscriber.
- Lambda log groups keep logs for 30 days.
- GitHub deploys with OIDC. There are no AWS access keys in GitHub.
- Tenant migration apply is not part of CI/CD.
- Legacy tenantless counts remain Resources 4, EmergencyRequests 31, Allocations 22, ResourceStatusHistory 17.
- Unit tests cover role checks, cross-tenant 404 behavior, public field allowlists, and conditional allocation.

Organization onboarding in code is: Cognito login, membership lookup, onboarding form when the user has no organization, create organization, assign the creator as owner, then location and catalog screens.

Resource update already existed as `PUT /resources`. The handler loads the row by id, requires membership in that row's organization, keeps the stored `organization_id`, checks the location and resource type, validates attributes, and writes an audit event. A client-supplied `organization_id`, `user_sub`, or `role` is not the source of authorization.

## Never tested with a real organization

No safe pilot identity exists. The web app client allows refresh, user, and SRP sign-in. It does not allow admin password authentication. Enabling that flow would weaken Cognito, so this phase did not script a login and did not use the seven existing pool users.

Because of that, these live checks remain pending:

- First login through the Amplify site.
- Creating the first organization, location, resource type, request type, resource, request, allocation, and release as a signed-in user.
- A second organization proving live cross-tenant isolation.
- Publishing a real public resource and confirming it on the public endpoint.
- Reading live audit rows for those actions.

The code paths and unit tests exist. They are not a substitute for one monitored human pilot.

## What this phase tests

- The resource screen can edit an existing resource through the existing update API.
- A successful registration closes the real modal.
- Logout clears the saved organization and location keys as well as the tokens.
- Successful API responses write one structured log line with request id, route, operation, status, and duration. Browser preflight requests are not logged. Tokens and authorization headers are not logged.
- Operator documents exist for onboarding, data safety, incidents, and the readiness checklist.

## Known limitations

- MFA is off on the user pool. It was not turned on. Forcing it would change sign-in for every existing user before the first organization exists.
- Four operational functions share `emergency-resource-allocation-role-12qymvku`. Splitting it without a live allocation test could break allocate, release, or auto-release. It was left in place.
- `ERAP-Production-Alarms` has no email or chat subscription. An operator must add one in the AWS console. Do not put a personal address in source code.
- The GitHub `production` environment and required reviewer are still a manual repository setting. See `docs/ci-cd-runbook.md`.
- There is no second AWS stack for staging.
- Legacy tenantless rows must stay untouched. Do not run `scripts/migrate_tenant_scope.py apply`.
