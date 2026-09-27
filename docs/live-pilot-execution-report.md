# Live pilot execution report

Execution date: 27 September 2026.

The authenticated workflow was not executed. Cognito requires an email verification code, that code is delivered only to the signup mailbox, and there is no inbox available to this session. The verification step was not bypassed.

## Pilot identity

Not created.

No dedicated user was submitted to Cognito. Creating one would send a verification message to an address this session cannot read, or it would require confirming the user without that message. Existing pool users were not used.

## Organization

Not created. `Organizations` is 0. `OrganizationMembers` is 0.

## Locations

Not created. Count: 0.

## Resource types

Not created. Count: 0.

## Request types

Not created. Count: 0.

## Resources

No pilot resources. The table count remains the legacy baseline of 4.

## Requests

No pilot requests. The table count remains 31.

## Allocations

No pilot allocations. The table count remains 22. `ResourceStatusHistory` remains 17.

## Audit events

Count: 0.

## What was verified without a login

Commit `876a9747a693908ea8d7d92e5463c8dc208fe23f` matches `origin/main`.

Alias `live` is version 5 on all nine functions, described with that commit. Thirty API integrations invoke `live`. None invoke `$LATEST`. Stage deployment remains `xuwrkf`.

Public discovery returned 200 with an empty list. Invalid page size and invalid page token return 400. Protected resource, type, and request calls return 401. The public body did not include organization, actor, or internal resource identifiers.

Point-in-time recovery, deletion protection, throttling, restricted CORS, authorizer `y0hzhr`, and the five ERAP alarms passed `python scripts/verify_hardening.py`. All nine log groups retain logs for 30 days. Structured public logs were already observed on the previous version of the same code. This run did not generate an authenticated request to log.

`ERAP-Production-Alarms` has zero subscriptions. No test alert was sent.

The GitHub `production` environment does not exist. The `development` environment has no protection rules. Workflows use OIDC. They do not contain static AWS keys or tenant migration.

The repository has no Playwright, Selenium, Cypress, or Cognito test-login utility. No token was fabricated.

## Cognito signup facts

These settings were read and not changed.

- Self-service signup is enabled.
- The username is an email address.
- Email is auto-verified. Cognito sends the code with its default email path. There is no pre-signup trigger that can confirm a user.
- Required attributes are name and phone number, in addition to the email username.
- MFA is off.
- The website client allows the authorization-code flow. Its callback is the Amplify URL. Admin password authentication is not enabled.
- Passwords must be at least 8 characters and include uppercase, lowercase, a number, and a symbol.

The site client is `ERAP-Web-Frontend`. A second app client has a callback host that does not match the live Amplify host. The website does not use that second client. It was left unchanged.

## Results

Authentication: PENDING

Organization: PENDING

Locations: PENDING

Resources: PENDING

Private visibility: PENDING

Public visibility: PENDING

Requests: PENDING

Matching: PENDING

Allocation: PENDING

Release: PENDING

Audit: PENDING

Tenant isolation: PENDING

Roles: PENDING

Logout/session: PENDING

Monitoring: PENDING

Security: PASS

CI/CD: PASS

Public visibility is PENDING because no pilot public resource exists. The empty public endpoint itself returned 200 and rejected bad page input.

## Tests

- `python -m pytest -q`: 78 passed
- `python scripts/security_scan.py`: passed
- `python scripts/check_frontend.py`: passed
- `python scripts/check_workflows.py`: passed
- `python scripts/package_lambdas.py --check`: passed for nine functions
- `python scripts/verify_hardening.py`: passed
- `python scripts/smoke_test.py`: passed

## Pilot data created

None.

## Legacy data

Untouched. `scripts/migrate_tenant_scope.py apply` was not run.

## Manual step required

PILOT AUTHENTICATION WAITING FOR EMAIL VERIFICATION

1. Open `https://main.d3enpe7opotop5.amplifyapp.com`.
2. Choose Create an account.
3. Use a new mailbox reserved for this pilot, not an existing personal account.
4. Enter a name and a phone number. The pool requires both.
5. Choose a password that meets the rules above. Do not send that password in chat or commit it.
6. Open the verification message from Cognito and enter the code.
7. Sign in once, so the account is confirmed.
8. Tell the operator session that the dedicated pilot user is verified.

After that confirmation, organization, location, catalog, resource, request, match, allocation, release, audit, and logout can be run through the normal application. Do not insert those rows directly in DynamoDB.

## Launch decision

PENDING HUMAN AUTHENTICATION STEP

The required authenticated workflow was not executed. The platform checks that do not need a login passed. No software defect found in those checks blocks the operator from completing signup.
