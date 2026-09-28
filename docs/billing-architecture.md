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

## Future webhook architecture

A later `POST /billing/webhook` will verify the provider signature, insert `provider_event_id` once, and call `change_subscription_status`. A duplicate event must not apply twice. The browser return URL will not be proof of payment. That route does not exist yet.

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

## What this phase does not implement

Razorpay, checkout, webhooks, billing API routes, EventBridge expiry, write enforcement, the billing screen, price activation, the grandfather backfill, and creating the billing tables.
