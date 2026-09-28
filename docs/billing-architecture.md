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

## What Phase A does not implement

Razorpay, checkout, webhooks, billing API routes, EventBridge expiry, write enforcement, the billing screen, price activation, and the grandfather backfill.
