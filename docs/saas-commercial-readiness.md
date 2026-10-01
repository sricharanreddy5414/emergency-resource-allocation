# SaaS commercial readiness

Phase 14 inspected the billing implementation that is already deployed. It did not launch ERAP, did not create a payment, and did not change a provider subscription.

Production launch is NOT authorized by Phase 14.

Production Razorpay configuration is not provisioned in Phase 14.

## Pricing

VERIFIED. The server catalog in `src/billing/plans.py` is the price source.

| Application plan | Interval | Amount | Purchasable |
|---|---|---|---|
| `FREE_TRIAL` | none | 0 | no |
| `GRANDFATHERED` | none | 0 | no |
| `MONTHLY` | month | 99900 paise (₹999) | yes |
| `YEARLY` | year | 999900 paise (₹9,999) | yes |

`GET /billing/plans` returns those two purchasable plans with `amount_minor`. It does not return provider plan ids. The billing page formats `amount_minor` from that response. The frontend does not hard-code ₹999 or ₹9,999.

## Provider plan mapping

VERIFIED. `src/billing/provider/razorpay.py` maps application names to the Razorpay test plans:

| Application plan | Test provider plan | Cycles |
|---|---|---|
| `MONTHLY` | `plan_ThiWT35Gf1jyio` | 468 |
| `YEARLY` | `plan_ThiWTXOzBHl2Qb` | 39 |

Checkout accepts only `plan_id` and `organization_id`. A client-supplied amount, currency, or provider plan id is rejected. The server map chooses the provider plan. These ids are test plans. They are not production plans.

## Trial

VERIFIED. `POST /organization` writes one `TRIALING` row with plan `FREE_TRIAL`. `trial_start` is the organization `created_at`. `trial_end` is 15 UTC days later (`TRIAL_DAYS`). A repeated create with the same client request id keeps that window. Checkout does not start a second trial. The daily worker `erap-billing-expiry` queries `LifecycleDueIndex` and moves a due `TRIALING` row to `EXPIRED`. It does not scan. After expiry, operational writes return `BILLING_REQUIRED`. Reads and owner billing routes stay available. Organization rows are not deleted.

## Subscription states

VERIFIED. Stored statuses are `TRIALING`, `ACTIVE`, `PAST_DUE`, `CANCELLED`, `EXPIRED`, and `GRANDFATHERED`.

Implemented transitions in `src/billing/transitions.py`:

| From | To |
|---|---|
| no row | `TRIALING` or `GRANDFATHERED` |
| `TRIALING` | `ACTIVE`, `EXPIRED` |
| `ACTIVE` | `PAST_DUE`, `CANCELLED` |
| `PAST_DUE` | `ACTIVE`, `EXPIRED` |
| `CANCELLED` | `ACTIVE`, `EXPIRED` |
| `EXPIRED` | `ACTIVE` |
| `GRANDFATHERED` | `ACTIVE` |

`ACTIVE` does not move directly to `EXPIRED`. `PAST_DUE` does not move to `CANCELLED`. A provider cancellation received while the row is `PAST_DUE` is stored as ignored and the row stays `PAST_DUE`. That is the current rule, not an undocumented accident. Writes remain allowed in `PAST_DUE`.

A missing subscription row is presented as `GRANDFATHERED` and is not created by a billing read.

## Payment states

VERIFIED as a separate field. `PENDING`, `PAID`, `FAILED`, and `REFUNDED` are payment states. They are not subscription statuses.

`subscription.charged` and `subscription.activated` are the events that may move a legal row to `ACTIVE`, and they record payment state `PAID`. Creating a checkout stores the provider subscription id and `pending_plan_id` only. It leaves `subscription_status` unchanged. A paid flag on the client does not activate the organization.

`payment.failed`, `subscription.pending`, and `subscription.halted` target `PAST_DUE` when that transition is legal.

REFUNDED is an allowed payment state on a billing event. No webhook event currently maps to it. Refund handling is NOT IMPLEMENTED.

`subscription.completed` is intentionally unmapped. Completing every cycle does not expire the row early.

## Billing authorization

VERIFIED.

| Role | Read billing, plans, and events | Checkout and cancel |
|---|---|---|
| OWNER | yes | yes |
| ADMIN | yes | no |
| OPERATOR | no | no |
| MEMBER | no | no |

Inactive memberships are omitted by `list_memberships` and `authorize` denies a non-active status. Another organization's id in the body is not access. Billing routes use `access="billing"`, so an expired subscription can still open billing. Operational writes use the write gate.

## Organization onboarding

VERIFIED in code. PARTIAL as a live walkthrough in this phase.

Sign-in uses Cognito. A user with no membership is shown the create-organization form. `POST /organization` creates the organization, the owner membership, and the 15-day trial together. A user with memberships gets the organization selector. The header "awaiting organization" is the shell before a selection is applied. It is not a second onboarding product. This phase did not create an organization.

## Organization lifecycle

VERIFIED. `Organizations.status` and `subscription_status` are different fields. `ARCHIVED` and `SUSPENDED` are not billing statuses. Membership listing copies the organization status into the access check, so an archived or suspended organization is denied even if a subscription row says `ACTIVE`. A billing change does not restore that organization. Cancelling or expiring a subscription does not delete organization, member, resource, request, or exchange rows.

## Cancellation

VERIFIED. Only an OWNER can call `POST /billing/cancel`, and only while the stored status can legally become `CANCELLED`, which today means `ACTIVE`, with a Razorpay subscription id already stored. The call asks Razorpay to cancel at cycle end and sets `cancel_at_period_end`. The status stays `ACTIVE`, and writes stay allowed, until the period end. The expiry worker then moves that row to `CANCELLED`. The billing page shows "Schedule cancellation" only for that owner case. A second request does not call the provider again.

## Expiry

VERIFIED. `erap-billing-expiry-daily` runs at 02:00 UTC and targets `erap-billing-expiry:live`. It expires a due trial and completes a due scheduled cancellation. It does not delete customer data. `CANCELLED` and `EXPIRED` block operational writes and leave reads and billing access in place.

## Grandfathered

VERIFIED in code. A `GRANDFATHERED` row, or a missing row presented as grandfathered, has no provider subscription and no trial clock. It is not purchasable by itself. Checkout is allowed from that state, and activation still waits for a provider event. The expiry worker does not expire it. This phase did not read or write the pilot organization and did not create another grandfathered customer.

## Webhook

VERIFIED by the existing webhook tests. `POST /billing/webhook` is public and requires the Razorpay signature. The secret name is `erap/billing/razorpay/test`. Any other secret id is rejected. The organization comes from `ProviderSubscriptionIndex`, not from the body. Unknown subscriptions and unknown event types are ignored. A duplicate provider event id is not applied twice. An invalid signature returns 401 and writes no billing event.

## Billing UI

VERIFIED, with one help-text correction.

The billing page labels all six subscription statuses, shows trial dates, period dates, a pending plan as awaiting confirmation, and owner-only checkout and cancellation. Prices come from `GET /billing/plans`. Admins see the page and cannot start checkout or cancel.

The help page used to say purchase stays unavailable until a plan is offered. That contradicted the purchasable monthly and yearly plans. The help text now says checkout does not activate the subscription. Production checkout uses the production provider and does not fall back to the test provider.

## Test and production separation

VERIFIED. The webhook loader accepts only `erap/billing/razorpay/test`. Live key material is rejected by the provider client. No production secret was created. No production plan id was added.

## Customer support

VERIFIED against `docs/production-support.md`. Support uses organization id, correlation id, and entity ids. It does not ask for passwords, MFA codes, tokens, card numbers, or Razorpay secrets. The in-product authenticator panel is the signed-in user's own setup, not a support request.

## Legal surfaces

NOT IMPLEMENTED. The repository has no terms, privacy policy, billing terms, or refund policy. Phase 14 does not invent them. They are a launch prerequisite, not a billing defect.

## Commercial limits

NOT IMPLEMENTED. Plan records carry an empty entitlements map. Numeric limit keys can be validated if a later phase supplies them. No member, location, resource, request, or exchange quota is enforced. This phase did not add one.

## What remains before a launch decision

- A written decision to provision production Razorpay plans, keys, and webhook secret outside this phase.
- Legal terms, privacy, and refund or cancellation text approved by the business.
- A choice about whether a provider cancellation during `PAST_DUE` should end writes. Today it does not.
- Refund events, if the business wants `REFUNDED` to change access.
- A signed-in onboarding walkthrough on a non-pilot organization. This phase did not create one.

Phase 15 is the launch decision. This document does not start it.
