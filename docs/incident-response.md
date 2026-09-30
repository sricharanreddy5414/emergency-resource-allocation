# Incident response

For a small team. Times are a target, not a contract. Production launch is not authorized by this document.

Collect evidence before changing anything: the GitHub run URL, the `live` alias version and description, the API stage deployment id, and one CloudWatch line that shows `correlation_id`, `service`, `operation`, `outcome`, and `error_code`. Do not copy tokens, passwords, webhook signatures, or raw QR tokens into notes.

## SEV-1

Critical outage or a confirmed exposure of tenant data, credentials, or a public data store.

- Detection: API or Amplify down for everyone, authorizer failing closed for every caller, a tenant isolation failure where one organization can read or change another organization's data, or a public bucket, log, or response containing secrets or another tenant's rows. A tenant isolation failure is SEV-1.
- Immediate assessment: one person confirms the symptom with `python scripts/smoke_test_production.py` and `python scripts/verify_hardening.py`.
- Containment: if a bad alias caused it, follow `docs/rollback.md`. If S3 is public, turn Block Public Access on and remove the public statement. Do not delete tables.
- Evidence: save the command result and the alias description. Do not save secret values.
- Customer impact: which organizations can still sign in. Do not query the pilot organization.
- Rollback decision: roll back code when the previous alias was healthy. Do not roll back DynamoDB as part of that.
- Recovery: alias move, or a PITR restore to a new table using `docs/disaster-recovery.md`. Never restore over the live table in a drill.
- Verification: hardening, recovery, security posture, and the read-only smoke.
- Communication: tell affected operators the API is impaired and name the organization ids you already know. Do not ask them for passwords or tokens.
- Review: write what broke, what was changed, and what check would have caught it.

## SEV-2

A major path is down and a workaround is poor: allocation, exchange, billing webhook, or sign-in for protected routes.

- Detection: one worker or route fails, schedules miss, or webhook signatures all fail.
- Immediate assessment: identify the function from the route or schedule in `docs/service-dependency-map.md`. Read its `live` alias and the latest log line for that `correlation_id`.
- Containment: stop further deploys on `main` until the alias is understood. Do not cancel a running Deploy backend job that is already in Verify hardening; let the ownership restore finish.
- Evidence: schedule state from `python scripts/verify_recovery.py` when EventBridge is involved.
- Customer impact: name the feature, not every row.
- Rollback decision: roll back that function's alias when the previous version worked. Exchange, notifications, and billing are not moved by `set_live_version.py --version`.
- Recovery: alias, schedule target, or secret-policy access. Do not create a payment or edit `sub_TiEekQFpwByhkU`.
- Verification: the same verifiers, plus one read-only call on the affected route.
- Communication: say which feature is unavailable.
- Review: same as SEV-1, shorter.

## SEV-3

One organization, one page, or a delayed worker. The API is up.

- Detection: a single inbox, expiry lag, or a UI asset missing while `GET /public/resources` returns 200.
- Immediate assessment: confirm it is not SEV-1 by running the public smoke. Check TTL, the hourly or daily schedule, and Amplify job status.
- Containment: avoid a full alias rollback for a single-tenant data question.
- Evidence: organization id, request id, exchange request id, offer id, or billing event id.
- Customer impact: that organization or that feature.
- Rollback decision: do not roll back unless a recent alias matches the start of the errors.
- Recovery: fix forward when the data is valid and the code bug is small. Use PITR only when rows are lost.
- Verification: read-only smoke and the feature's log line.
- Communication: tell that operator. Do not ask for MFA codes.
- Review: optional if the cause is already a known deferred item, such as alarm email not being subscribed.

Escalation is the same person who can assume the operator AWS role and run the verifiers. There is no separate on-call vendor.
