# Controlled beta support

ERAP is in a controlled limited beta. Support identifies a case from the organization name, the organization id the operator already has, a correlation id, and an entity id. Support does not ask the user for a password, MFA code, one-time code, access token, cookie, card number, Razorpay key, webhook secret, or raw QR payload.

The first controlled-beta organization is `ORG-D878EAF5135D`. It is real beta data. Do not delete it, reset it, or use it as a disposable test organization.

## Identify an organization

Ask for the organization name shown in the selector. The id is `ORG-` plus 12 characters and is stored on the Organizations row. Read that row with `organization_id`. Do not scan the table.

## Identify a user

CloudWatch records `actor_sub_hash`, not the raw Cognito subject. Use the hash to match log lines. Open the OrganizationMembers row only when the case already names that organization. Do not ask the user to paste a JWT.

## Inspect billing

Read OrganizationSubscriptions by `organization_id`. Useful fields are `subscription_status`, `plan_id`, `trial_start`, `trial_end`, `cancel_at_period_end`, and `provider_subscription_id`. A new organization is `TRIALING` on `FREE_TRIAL` for 15 UTC days. Checkout does not set `ACTIVE`. Do not create a checkout or a payment to diagnose a case.

Production mode reads `erap/billing/razorpay/production`. Test mode reads `erap/billing/razorpay/test`. Do not print either secret. Production plans are `plan_TiMn4MluXeOMK1` and `plan_TiMpOnO7K5GT0Q`. Test plans are `plan_ThiWT35Gf1jyio` and `plan_ThiWTXOzBHl2Qb`.

## Inspect a failed operation

Search the Lambda log group for the API Gateway request id. The line includes `service`, `operation`, `outcome`, and `error_code`. The user should see a short message, not a table name or a stack trace. If the screen shows a technical dump, treat that as a defect and keep the raw line in the operator log only.

## Inspect Exchange

Use the exchange request id or offer id from the screen. Read ResourceExchanges for that organization. Status names are the ones the product already uses, including offer, accept, transfer pending, and completion. Do not edit a row by hand. Notification failure does not roll back the exchange.

## Inspect notifications

Read the Notifications inbox for that organization. Unread, read, and read-all are user actions. An empty inbox is a valid state. Confirm `erap-notifications` alias `live` only when the inbox itself errors.

## Inspect CloudWatch

Region `eu-north-1`. Log groups are `/aws/lambda/` plus the function name. Retention is 30 days. Do not log or paste authorization headers, cookies, secrets, webhook secrets, QR tokens, or payment credentials into a ticket.

## Correlate a request

The correlation id is the API Gateway request id. Use the same id in the browser network panel and in CloudWatch. A frontend failure with no request id is a browser or network failure. A request id with `outcome` `failed` is a backend failure.

## Escalation

| Severity | When | Action |
|---|---|---|
| SEV-1 | Sign-in is down or every organization fails | Use `docs/incident-response.md` and `docs/failure-runbook.md` |
| SEV-2 | One route, Exchange, billing, or the webhook is failing | Roll that function's `live` alias only when the failure starts at a deploy |
| SEV-3 | One organization, a display issue, or a delayed notification | Read the case. Do not change data |

## Never request from a user

Passwords, MFA codes, one-time codes, card numbers, UPI details, Razorpay secrets, webhook secrets, cookies, or raw QR payloads.
