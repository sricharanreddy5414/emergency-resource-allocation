# Billing architecture

Phase A defines the billing domain. It does not charge anyone, create tables, or change who can use ERAP today.

## Organization-level billing

The customer is the organization. Members do not have their own subscriptions. `OWNER` will manage billing. `ADMIN` will be able to view it later. `OPERATOR` and `MEMBER` will not view or manage it. Those role checks are not wired to an API in this phase.

Subscription state is not stored in `Organizations.status`. That field remains the organization lifecycle (`ACTIVE`, `SUSPENDED`, `ARCHIVED`). Authorization still requires `ACTIVE`.

## Trial lifecycle

A new organization, when a later phase writes the row, receives:

- `trial_start`: server UTC time at creation
- `trial_end`: `trial_start` plus 15 days
- `plan_id`: `FREE_TRIAL`
- `subscription_status`: `TRIALING`

The clock is `datetime` in UTC. A zoned input is converted to UTC. A naive clock is rejected. Retrying the same creation keeps the original `trial_start` and `trial_end`. The one-trial-per-owner rule is not implemented yet.

## Phase B — Organization trial creation

`POST /organization` writes the trial when it creates a new organization. `trial_start` is the same server UTC value stored as `Organizations.created_at`. `trial_end` is that instant plus 15 days, from `new_trial_subscription`. The plan is `FREE_TRIAL`, the interval is `none`, and the status is `TRIALING`. Provider ids and period fields are empty. `cancel_at_period_end` is false.

The response body is unchanged. It does not include provider ids or trial fields.

The same `user_sub` and `client_request_id` still resolve to the same organization id. The subscription write uses `attribute_not_exists(organization_id)`. A replay reads the existing row and does not change `trial_start` or `trial_end`.

Organization creation does not use `TransactWriteItems`. The existing flow writes the organization, then the owner membership, each with its own conditional put. The trial write is a third conditional put after those succeed. A membership failure returns `Unable to create organization` and does not write a trial. A subscription failure returns the same error and is not reported as success. A retry uses the original `created_at` and inserts the trial only if it is still missing.

A missing subscription row still means legacy access through `access_when_subscription_missing`. Creating one organization does not scan or update any other organization. Pilot and other existing rows are not backfilled. One trial per owner is not enforced: each new organization created through this flow receives its own 15-day trial.

`OrganizationSubscriptions` is still not deployed. `infra/billing-tables.json` remains `"applied": false`. The create-organization package includes the billing modules so the function can write the row once the table exists.

## Subscription states

`TRIALING`, `ACTIVE`, `PAST_DUE`, `CANCELLED`, `EXPIRED`, `GRANDFATHERED`.

`REFUNDED` is a payment-event state, not a subscription state.

## State transitions

| From | To |
|---|---|
| no row | `TRIALING` or `GRANDFATHERED` |
| `TRIALING` | `ACTIVE` or `EXPIRED` |
| `ACTIVE` | `PAST_DUE` or `CANCELLED` |
| `PAST_DUE` | `ACTIVE` or `EXPIRED` |
| `CANCELLED` | `ACTIVE` or `EXPIRED` |
| `EXPIRED` | `ACTIVE` |
| `GRANDFATHERED` | `ACTIVE` |

Any other change is rejected. The frontend cannot set `subscription_status`.

## Plans

| Plan | Interval | Amount | Currency | Purchasable |
|---|---|---|---|---|
| `FREE_TRIAL` | `none` | 0 minor units | INR | no |
| `GRANDFATHERED` | `none` | 0 minor units | INR | no |
| `MONTHLY` | `month` | unset | INR | no |
| `YEARLY` | `year` | unset | INR | no |

Intervals are only `none`, `month`, and `year`. Money is an integer number of minor units (paise for INR). Floats are rejected. Monthly and yearly have no price because commercial pricing is not decided. `require_purchasable` rejects every current plan.

Entitlement keys exist for later limits (`MAX_MEMBERS`, `MAX_LOCATIONS`, `MAX_RESOURCES`, `MAX_REQUESTS`, `ADVANCED_FEATURES`, `AUDIT_HISTORY`, `API_ACCESS`). Current plans set no limits, and nothing enforces them.

## Money representation

INR 999.00 would be `99900` paise. No such price is configured. `amount_minor` is `0` for the trial and grandfathered plans, and `null` for monthly and yearly.

## Event model

`BillingEvents` will use partition key `provider_event_id`. A row records `provider`, `event_type`, `organization_id`, `provider_payment_id`, `payment_state`, `received_at`, `processed_at`, and `processing_status`.

Processing statuses: `RECEIVED`, `PROCESSED`, `IGNORED`, `REJECTED`.

Payment states: `PENDING`, `PAID`, `FAILED`, `REFUNDED`.

The model rejects card numbers, CVV, bank credentials, webhook secrets, and tokens. No event is stored in this phase.

## Grandfathered behavior

`GRANDFATHERED` means existing access, no provider subscription, and no trial clock. The domain helper can build that shape. It does not write it.

A missing `OrganizationSubscriptions` row means the same legacy access. `access_when_subscription_missing` returns `grandfathered`. `src/shared/access.py` is unchanged, so current organizations keep working. The pilot organization is not modified. The backfill is a later phase.

## Future Razorpay integration

Razorpay is the intended first provider. This phase does not call Razorpay, store Razorpay secrets, or open checkout. The `provider` value `razorpay` is reserved on events. Checkout must keep using `require_purchasable`, which fails while prices are unset.

## Webhook architecture

Phase D implements `POST /billing/webhook` in code and tests. The route specification is in `infra/billing-checkout.json` with authorization `NONE`. Cognito is not used, because Razorpay calls the route. The route and the `erap-billing-webhook` function are not deployed. Live Razorpay is not enabled.

See Phase D below. A browser redirect or a checkout response still does not activate a subscription.

## Future expiry architecture

A later daily job will move `TRIALING` to `EXPIRED` after `trial_end`, and close cancelled or past-due periods when their paid window ends. It will not reuse the allocation auto-release function. No schedule is created in this phase.

## Security principles

- Subscription status changes go through the state machine.
- Organization identity for a later billing API must come from the Cognito subject and membership, not from a client-supplied role.
- Payment instruments and provider secrets are not fields on these records.
- Runtime IAM for the future billing role allows get, put, and update on the two new tables only. It does not allow delete.

## Migration strategy

Do not scan or update production organizations in Phase A. When a later phase backfills, it writes a `GRANDFATHERED` row only where a subscription row is absent. Until then, missing rows stay legacy access.

## Tables

`infra/billing-tables.json` specifies `OrganizationSubscriptions` (partition key `organization_id`) and `BillingEvents` (partition key `provider_event_id`) in `eu-north-1`, with point-in-time recovery and deletion protection. `"applied": false`. These tables were not created.

## Phase C — Razorpay test checkout foundation

`POST /billing/checkout` is specified for the existing API `4c6dni17l3`, with the existing Cognito authorizer. The route, the `erap-billing` function, and the Secrets Manager secret are not created. `infra/billing-checkout.json` is `"applied": false`. `BILLING_PACKAGES` describes the zip. It is not in `PACKAGES`, so the deploy, alias, and route scripts still touch only the nine existing functions.

Only an `OWNER` membership can call checkout. `ADMIN`, `OPERATOR`, and `MEMBER` receive 403. A missing token receives 401. `organization_id` in the body is only a selector. The write uses the organization from `authorize`. Organization A cannot open checkout for Organization B.

The body may contain `plan_id`. Amount, currency, price, and provider ids are rejected. The server plan map and `RAZORPAY_PLAN_LINKS` decide whether Razorpay may be called. `MONTHLY` and `YEARLY` stay `purchasable: false`, and both Razorpay plan ids are unset, so checkout returns 409 `Plan is not currently available for purchase` and does not call Razorpay.

When a later configuration makes a plan purchasable and sets a Razorpay plan id plus `total_count`, the provider sends `POST https://api.razorpay.com/v1/subscriptions` with HTTP Basic auth, a 10 second timeout, `plan_id`, `total_count`, `quantity` 1, and a note of the organization id. It does not send an amount or a customer id. Razorpay fills `customer_id` only after the payer authorises, so `provider_customer_id` stays empty. The test key id must start with `rzp_test_`. The secret id must be `erap/billing/razorpay/test`, holding `key_id`, `key_secret`, and `webhook_secret`. Checkout uses the key pair. Phase D uses `webhook_secret` only to verify signatures. The secret value is not in git and is not created by this repository. A live key or any other secret id fails closed.

Eligible stored states are `TRIALING`, `EXPIRED`, `CANCELLED`, and `GRANDFATHERED`. `ACTIVE` and `PAST_DUE` do not start another subscription. A `TRIALING` or `EXPIRED` row that already has `provider_subscription_id` is left unchanged. `CANCELLED` may start a new provider subscription. A missing row stays grandfathered and is not created here. The conditional update sets `provider`, `provider_subscription_id`, and `updated_at` only. It does not set `subscription_status` to `ACTIVE` and does not set the billing period. Razorpay does not document an idempotency key for this call, so ERAP does not invent one.

The response may contain `provider`, `provider_subscription_id`, and `public_key_id`. It does not contain the key secret. Provider failures become `Billing provider rejected the request` or `Billing provider is unavailable`.

Checkout success does not activate a subscription. Only a verified webhook can do that.

## Phase D — Verified Razorpay webhooks

This phase is code and tests only. `POST /billing/webhook` is not deployed. Live Razorpay is not enabled. The test secret is not created. No organization is charged, and pilot data is not modified.

Razorpay authenticates the call with `X-Razorpay-Signature`. The signature is HMAC-SHA256 of the exact raw body, hex-encoded, compared with `hmac.compare_digest` against `webhook_secret` from `erap/billing/razorpay/test`. The body is not parsed and reserialized before the check. A missing or invalid signature returns 401, writes no `BillingEvents` row, and does not read `organization_id` from the payload. The response and logs do not contain the secret or the calculated signature.

`X-Razorpay-Event-Id` is `provider_event_id`. A missing id returns 400 and is not invented. The first verified delivery writes the event with `attribute_not_exists(provider_event_id)` and status `RECEIVED`. A later delivery of a `PROCESSED` or `IGNORED` id returns 200 and does not change the subscription or `processed_at`.

The organization comes from `OrganizationSubscriptions.provider_subscription_id` through `ProviderSubscriptionIndex`. That index is specified and not created. The webhook does not scan. `organization_id` in the payload, including Razorpay notes, is ignored. An unknown subscription id or an unknown event type is stored as `IGNORED` and returns 200, so Razorpay does not retry it. No organization or subscription is created.

Supported events call `change_subscription_status` and do not carry their own transition table:

- `subscription.activated` and `subscription.charged` move `TRIALING` or `PAST_DUE` to `ACTIVE` when that change is legal. A `CANCELLED` row stays cancelled when the event time is missing or not newer than `cancelled_at`.
- `payment.failed`, `subscription.pending`, and `subscription.halted` move `ACTIVE` to `PAST_DUE`. They do not set `EXPIRED`.
- `subscription.cancelled` moves `ACTIVE` to `CANCELLED` and sets `cancelled_at` from the provider. `cancel_at_period_end` changes only when the payload contains `cancel_at_cycle_end`. `PAST_DUE` to `CANCELLED` is not in the Phase A state machine, so that event is `IGNORED`.

`current_period_start` and `current_period_end` change only when the payload contains those unix times. Payment state stays on the event: `PENDING`, `PAID`, `FAILED`, or `REFUNDED`. `REFUNDED` is not a subscription status.

If the signature and event are valid but DynamoDB fails, the handler returns 500 and leaves the event `RECEIVED` when the row was written. It does not mark `PROCESSED`. Razorpay can retry. Logs contain `provider`, `provider_event_id`, `event_type`, `organization_id`, `provider_subscription_id`, and `processing_status` only.

A later phase may repair a subscription by fetching it from Razorpay. This phase does not.

`erap-billing-webhook` is packaged in `BILLING_PACKAGES` and is not in `PACKAGES`. Deploy, alias, and route scripts still skip it. Its specified IAM is get, update, and query on `OrganizationSubscriptions` and `ProviderSubscriptionIndex`, plus get, put, and update on `BillingEvents`, plus `GetSecretValue` on the test secret. It has no delete, no scan, and no access to operational tables.

## Phase E — Billing API

The authenticated billing API is code and tests only. The routes are specified on API `4c6dni17l3`, stage `dev`, with Cognito authorizer `y0hzhr`. `"applied"` stays false. `erap-billing` serves the authenticated routes. `erap-billing-webhook` stays separate and is still signature-authenticated. Neither function is deployed. The billing screen is not implemented. Subscription enforcement is not implemented. `MONTHLY` and `YEARLY` remain `purchasable: false`.

`organization_id` is only a selector. `authorize` checks the Cognito subject, an `ACTIVE` membership, and that organization. One membership and no selector uses that organization. Several memberships and no selector still require a selection. Organization A cannot read or change Organization B. The role in the body is ignored. `GET /organization` is unchanged.

| Route | OWNER | ADMIN | OPERATOR | MEMBER |
| --- | --- | --- | --- | --- |
| `GET /billing` | yes | yes | 403 | 403 |
| `GET /billing/plans` | yes | yes | 403 | 403 |
| `POST /billing/checkout` | yes | 403 | 403 | 403 |
| `POST /billing/cancel` | yes | 403 | 403 | 403 |
| `GET /billing/events` | yes | yes | 403 | 403 |

A missing token is 401. Errors use the existing `message` and `error.code` shape.

`GET /billing` returns `organization_id`, the subscription presentation, and `next_action`. The presentation keeps `TRIALING`, `ACTIVE`, `PAST_DUE`, `CANCELLED`, `EXPIRED`, and `GRANDFATHERED`. Empty timestamps are null. It does not return provider ids, the key secret, or the webhook secret. A missing row is presented as `GRANDFATHERED` with `next_action` `none` and is not written. `TRIALING`, `EXPIRED`, and `CANCELLED` use `subscribe`. `ACTIVE` uses `manage_subscription` until cancellation is requested, then `none`. `PAST_DUE` uses `payment_required`.

`GET /billing/plans` returns `MONTHLY` and `YEARLY` with `plan_id`, `display_name`, `billing_interval`, `currency`, and `purchasable`. `amount_minor` is included only when a price is configured. It is omitted now. Razorpay plan ids, secrets, and entitlements are omitted.

`POST /billing/checkout` is the Phase C flow. The body may contain `plan_id` and a selector `organization_id`. Amount, currency, and provider ids are rejected. An unavailable plan returns 409 before any provider call. A successful provider response stores `provider_subscription_id` and does not set `ACTIVE`.

`POST /billing/cancel` is owner-only. The body cannot set status, timestamps, or the provider subscription id. The state machine must allow `CANCELLED` from the current status, which today means `ACTIVE`. The server subscription id is sent to `POST /v1/subscriptions/{id}/cancel` with Razorpay's `cancel_at_cycle_end: true`, so cancellation is at the end of the current cycle rather than immediate. After Razorpay accepts it, ERAP sets `cancel_at_period_end` and leaves `subscription_status` unchanged. The webhook later applies `ACTIVE` to `CANCELLED`. The call does not set `EXPIRED`. A second request does not call Razorpay again. A provider failure does not change the row.

`GET /billing/events` queries `OrganizationBillingEventsIndex` (`organization_id`, `received_at`) for the authorized organization, newest first, at most 50 items. It does not scan. The response contains the normalized event fields only. That index is specified in `infra/billing-tables.json` and is not created.

The authenticated function's specified IAM is membership get and query, get and update on `OrganizationSubscriptions`, query on `OrganizationBillingEventsIndex`, and `GetSecretValue` on the test secret. It has no delete and no scan. The webhook policy is unchanged.

A later frontend can call these routes. It should display `next_action` and `purchasable` from the API instead of deciding them locally.

## Phase F — Subscription enforcement

Enforcement is in `authorize`, after the Cognito subject, active membership, organization selector, `Organizations.status`, and role checks. The billing rules live in `billing.entitlements`. Handlers pass `access="read"`, `access="write"`, or `access="billing"`. They do not each decide the subscription state. `Organizations.status` is not changed and is not used as the billing state.

A missing `OrganizationSubscriptions` row is `GRANDFATHERED`. Authorization does not create that row. An existing row uses the stored status. A billing lookup failure on a write returns 500 `Unable to verify billing` and does not treat the organization as grandfathered.

| Stored state | Reads | Operational writes | Billing API |
| --- | --- | --- | --- |
| missing row | allowed | allowed | allowed by Phase E role |
| `GRANDFATHERED` | allowed | allowed | allowed by Phase E role |
| `TRIALING` | allowed | allowed | allowed by Phase E role |
| `ACTIVE` | allowed | allowed | allowed by Phase E role |
| `PAST_DUE` | allowed | allowed | allowed by Phase E role |
| `CANCELLED` | allowed | blocked | allowed by Phase E role |
| `EXPIRED` | allowed | blocked | allowed by Phase E role |

`ACTIVE` with `cancel_at_period_end` stays writable through `current_period_end`. Authorization does not move it to `CANCELLED` when the period ends. The webhook remains the status change. Once the stored status is `CANCELLED` or `EXPIRED`, operational writes are blocked even if older cancellation fields remain. `PAST_DUE` has no extra grace timestamp and stays writable.

Reads do not query the subscription table, because every recognized state allows them. Writes query `organization_id` once. Billing routes pass `access="billing"`, so an `EXPIRED` organization can still open billing, including owner checkout and cancel. `ADMIN` checkout and cancel stay 403. Public resource discovery and the auto-release job are unchanged.

Blocked writes return 403 with code `BILLING_REQUIRED` and the message `An active subscription is required for this operation`. The body includes `request_id`. It does not include provider ids or secrets.

The write routes that pass `access="write"` are location create, update, and deactivate; resource create, update, and release; request create and update; allocation; resource-type and request-type changes; and member invite, role change, deactivate, reactivate, and invitation acceptance. Creating an organization does not pass through this check.

## What this phase does not implement

EventBridge expiry, the billing screen, price activation, the grandfather backfill, one trial per owner, refunds, plan changes, provider repair fetches, creating the billing tables, creating or deploying the billing routes, creating the Razorpay test secret, and enabling live Razorpay.
