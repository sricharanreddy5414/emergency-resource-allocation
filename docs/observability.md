# Operational logging

Phase 11B adds structured CloudWatch logs. It does not change API responses, authorization decisions, Exchange transitions, notification recipients, billing state, or QR behavior.

## Correlation

`begin_request` stores one correlation id per invocation.

- If API Gateway supplies `requestContext.requestId`, that value is the correlation id.
- Otherwise the function generates `secrets.token_hex(8)`.
- The id is never taken from `Authorization`, cookies, or a token.
- Error bodies already include `error.request_id`. That field is this same id.
- No new response header was added.

## Log line

Operational events are one JSON object:

`timestamp`, `level`, `service`, `operation`, `outcome`, `correlation_id`, and only the identifiers that apply (`organization_id`, `actor_sub_hash`, `exchange_request_id`, `exchange_offer_id`, `resource_id`, `notification_event_id`, `billing_event_id`, `provider_subscription_id`, `error_code`).

Levels:

- `INFO` for a committed operation or an accepted webhook transition
- `WARNING` for a denial, an ignored or rejected webhook, an idempotent notification, or a skipped expiry item
- `ERROR` for an unexpected failure, including a notification emit failure and a billing lookup failure

## Actor hash

Logs do not contain the raw Cognito `sub`. `actor_sub_hash` is the first 16 hex characters of SHA-256 over the UTF-8 sub. The same person always hashes the same way. The hash cannot be reversed. There is no pepper and no encryption.

## Redaction

These fields are dropped if a caller passes them: authorization headers, cookies, passwords, MFA or OTP values, secrets, webhook secrets, signatures, raw QR tokens, token hashes, access or refresh tokens, card data, email, phone, raw bodies, and headers. A value that looks like a Bearer token or a JWT is dropped.

Notification failure still does not roll back Exchange. The failure line is `service=notifications`, `outcome=failed`, with the exception class and DynamoDB error code only.

QR issue, rotation, and preview still do not create notifications. A successful confirm still emits `exchange.handover.completed`. QR logs may include `session_id` and `exchange_request_id`. They do not include the raw token or its hash.

Billing webhook logs include the provider event id, event type, organization id, provider subscription id, and processing status. They do not include the signature, the secret, or the raw body.

## Metrics and alarms

No custom CloudWatch metrics were added. Organization, request, and user identifiers stay in logs, not metric dimensions.

## Retention

The project already keeps Lambda logs for 30 days (`scripts/set_log_retention.py` and `docs/production-hardening.md`). A read of every `/aws/lambda/` group in this account showed that policy on 12 groups. Three groups created with later functions had no retention limit:

- `/aws/lambda/erap-exchange`
- `/aws/lambda/erap-exchange-expiry`
- `/aws/lambda/erap-notifications`

Those three were set to 30 days, the same period as the other owned functions. Log groups were not deleted. Groups that were already 30 days were left on that period.

## Alarms

No new alarms were added because the existing project does not have an established alerting destination. The current alarms publish to SNS, and no email or other endpoint is subscribed.

## Limits

Structured logs cover authorization, Exchange HTTP results, notification emit, exchange expiry summaries, QR operations, billing webhooks, billing expiry, resource create, allocation, and the everyday and lifecycle resource commands. Older unstructured error lines may still exist beside these JSON lines. DynamoDB audit rows still store the raw `actor_sub`; only the CloudWatch copy is hashed.
