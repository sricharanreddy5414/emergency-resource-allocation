# Controlled beta feedback

ERAP is a controlled limited beta. This is not a public launch. There is no separate feedback table, Lambda, or route. A beta report is a support case. Use `docs/controlled-beta-support.md` to investigate it and `docs/controlled-beta-runbook.md` to operate the product.

Site: `https://main.d3enpe7opotop5.amplifyapp.com`.

## Who can join

Invite a specific person or organization. Do not advertise open registration.

A person who can already sign in can create an organization with the existing onboarding form. That form is not a public signup campaign. The operator decides who is invited. Creating an organization makes that person the owner.

The first controlled-beta organization is `ORG-D878EAF5135D`, named Controlled Beta Organization. As last read for this phase it is ACTIVE, with one ACTIVE owner, a FREE_TRIAL subscription in TRIALING, a 15-day trial, no provider subscription, and no locations. Do not delete it, reset it, or use it as a disposable test organization.

Do not use the protected pilot organization or its subscriptions for beta testing.

## How a user is onboarded

1. Confirm who the person is, what they will coordinate, and that they accept a controlled beta.
2. They sign in with the existing Cognito account. MFA stays whatever the pool already requires. Do not collect a password or a one-time code.
3. They create an organization, or select one they already belong to. The owner is responsible for that organization.
4. The organization starts ACTIVE. The owner membership starts ACTIVE. Billing starts as TRIALING on FREE_TRIAL for 15 UTC days. Creation does not take a payment and does not open checkout.
5. Tell them the trial length, the monthly price of ₹999, the yearly price of ₹9,999, that checkout does not by itself activate the subscription, that cancellation runs to the end of the paid period, and that a refund is a manual support process.
6. They add only the locations, members, and resources they need. Do not require Exchange or QR unless that organization will use them.

Authorization stays on the server. The organization selector is not the security boundary. `src/shared/access.py` requires the Cognito identity, an active membership, a valid organization selection, an organization that is allowed to operate, and a role that is allowed for that action.

## What users can exercise

Organization, members, locations, resources, requests, allocation, Exchange, notifications, billing visibility, and this support path.

## What users should not expect

Unlimited scale, a public directory, a separate uptime guarantee beyond the existing incident severities, unbuilt enterprise features, or a custom integration that is not already in the product. Do not ask them to complete a real production payment during beta operations.

## How to report an issue

The reporter sends the template below to the operator who invited them. They may attach a screenshot of the screen they see.

They must not send a password, MFA code, one-time code, access token, refresh token, Cognito token, API key, Razorpay secret, webhook secret, cookie, card number, or raw QR payload.

## Classification

| Class | Meaning | Examples |
|---|---|---|
| P0 | Security or data isolation | Cross-tenant data, authentication bypass, authorization bypass, secret exposure, destructive data corruption |
| P1 | Critical product failure | A core workflow is down, the organization is unusable, billing is broadly failing, allocation is broken, an Exchange record is corrupt |
| P2 | Functional bug | Wrong behavior, wrong validation, a missing notification, a wrong screen state, a workflow edge case |
| P3 | UX or documentation | Confusing wording, layout, an unclear empty state, a minor responsive issue, a documentation mismatch |
| P4 | Enhancement | A feature request, a convenience, or a future integration |

A feature request is not a bug.

## P0 and P1 response

Use the existing severities in `docs/controlled-beta-support.md`, `docs/incident-response.md`, and `docs/failure-runbook.md`. Do not invent a second emergency procedure.

1. Name the organization and the workflow.
2. Keep the screen, the time, and the correlation id when the user has one. The correlation id is the API Gateway request id.
3. Do not ask for a secret.
4. Decide whether other organizations could be affected.
5. Contain only with the existing rollback or incident steps. Do not delete tables, the API, Cognito, Amplify, or the beta organization.
6. Record what happened, find the cause, add a regression test for a code defect, and confirm recovery.

P2 through P4 wait for reproduction. Do not start a production checkout, cancel a subscription, or edit a subscription row to investigate.

## Onboarding checklist

Before invitation: identity, intended use, beta scope, the limits above, and this reporting path.

Account: the person can sign in. Do not record their password or MFA code.

Organization: name, owner, and an active membership.

Initial setup, only if they need it: a location, members, and resources.

First workflow, only if they need it: register a resource, create a request, and complete the operational step their role allows.

Optional: Exchange, notifications, and QR handover.

Billing: trial status is visible, prices match ₹999 and ₹9,999, and they understand the trial does not take a payment.

Support: they have this template and the never-send list. A correlation id is optional and comes from a failed request, not from a token.

## Feedback template

Beta organization:

User role:

Date/time:

Workflow:

What were you trying to do?

What happened?

What did you expect?

Steps to reproduce:

User-visible error:

Correlation ID:

Severity:

Screenshot available:

Business impact:

## Review

For each report:

1. Classify it P0 through P4.
2. Reproduce it when that can be done without a payment, a protected-data change, or a secret.
3. Mark it as a bug, a documentation issue, a UX issue, or an enhancement.
4. Name which organizations could be affected.
5. Add a regression test when the fix is code.
6. Order the work by operational impact.
7. Change the product only after the report is reproduced.

Do not treat every suggestion as work to build now.

## First real beta workflow

This records what was observed. It does not add a quote, a score, or a rating.

REAL BETA ORGANIZATION: `ORG-4708B62B1B9C`, ERAP First User Beta

REAL BETA ROLE: OWNER

WORKFLOW: Location → Resource → Request → Allocation → Release

WORKFLOW RESULT: SUCCESS

The location is Beta Test Location, `LOC-6ABA6BDF2CB4`. The resource is `RES-BETA-1`, state AVAILABLE. The request is `REQ-BETA-1`, state RELEASED. The allocation is `ALLOC-REQ-BETA-1`, state RELEASED. History recorded `RESOURCE_RELEASED`.

AUTOMATED TESTS: 657 passed at the Phase 21 baseline.

USER FEEDBACK: NOT YET PROVIDED

The eight feedback questions have not been answered. Do not fill them in from an operator's impression.

Phase 21 fixed the operations timeline so a RELEASED allocation shows `released_at`. That fix stays.

The overview heading could still say "All locations" after the location selector had selected Beta Test Location. The list filter followed the selected location. The heading is refreshed from the active location context.

"Return" and "Emergency release" stay as separate actions. Return closes an open everyday allocation and records `EVERYDAY_RESOURCE_RETURNED`. Emergency release closes an allocated emergency allocation, sets the request and allocation to RELEASED, and records `RESOURCE_RELEASED`. No user statement says these labels were confusing.
