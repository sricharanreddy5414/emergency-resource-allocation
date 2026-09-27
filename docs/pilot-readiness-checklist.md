# Pilot readiness checklist

Statuses are PASS, PARTIAL, PENDING, or DEFERRED. There is no combined score.

Live authenticated checks stay PENDING until a person signs in with a pilot account created in Cognito without weakening the app client. This phase did not create that account.

## A. Authentication

- Cognito authorizer remains on protected methods: PASS
- Unauthenticated protected calls return 401: PASS
- Scripted or admin-password sign-in: DEFERRED
- MFA: DEFERRED
- Live sign-in, logout, and return to a protected page: PENDING

## B. Organization onboarding

- Onboarding screen and create-organization API exist: PASS
- First real organization created through the website: PENDING

## C. Tenant isolation

- Unit tests reject cross-tenant access: PASS
- Live organization A cannot read organization B: PENDING

## D. Roles

- Owner and admin manage the catalog. Operator can operate resources. Member cannot allocate: PASS in code and unit tests
- Live role checks with two users: PENDING
- Client-supplied role or organization id is ignored: PASS

## E. Locations

- Location API and same-organization matching rules exist: PASS
- Two live locations in the pilot organization: PENDING

## F. Resource types

- Catalog create and deactivate for owner or admin: PASS in code
- First live resource type: PENDING

## G. Request types

- Catalog create and attribute validation: PASS in code
- First live request type: PENDING

## H. Resources

- Create, list, and history: PASS in code
- Edit name, type, attributes, visibility, contact, and location through the existing update API: PASS in code
- Live create and edit: PENDING

## I. Public and private visibility

- Public response allowlist and private exclusion: PASS in code and unit tests
- Live public and private pilot resources: PENDING

## J. Requests

- Create, attribute checks, and invalid-body rejection: PASS in code
- Live request: PENDING

## K. Matching

- Same location preferred, other locations in the organization allowed, other organizations excluded: PASS in code and unit tests
- Live match: PENDING

## L. Allocation

- Conditional allocate and release, including double-claim protection: PASS in code and unit tests
- Live allocate and release: PENDING

## M. Audit

- Audit events for organization, catalog, resource, visibility, request, allocation, and release: PASS in code
- Live audit rows for the pilot: PENDING

## N. Frontend

- Registration modal closes after save: PASS
- Edit action on a resource row for operator, admin, and owner: PASS
- Logout clears token and organization session keys: PASS
- Live click-through of onboarding and edit: PENDING

## O. API

- Stage `dev` invokes alias `live`: PASS
- Throttling and restricted CORS remain: PASS
- Structured success logs without tokens: PASS

## P. Monitoring

- Alarms exist and publish to `ERAP-Production-Alarms`: PASS
- Lambda logs retained 30 days: PASS
- An operator is subscribed to the alarm topic: PENDING

## Q. CI/CD

- Tests, packaging, and OIDC deploy on `main`: PASS
- No static AWS keys: PASS
- Migration apply is not in the workflows: PASS

## R. Rollback

- Alias move and previous API deployment `p29gcw` are documented: PASS
- GitHub `production` environment required reviewer: PENDING

## S. Data safety

- Operator guidance in `docs/pilot-data-safety.md`: PASS
- Legacy tenantless rows unchanged: PASS
- Live confirmation that public text was reviewed by the organization: PENDING

## T. Incident response

- `docs/incident-runbook.md` and `docs/pilot-runbook.md`: PASS
- A named on-call subscription for alarms: PENDING
