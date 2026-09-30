# Production support

Support identifies a case with an organization id, a correlation id, and the entity id the caller already has. Support does not ask for passwords, MFA codes, access tokens, refresh tokens, Cognito tokens, Razorpay secrets, webhook signatures, or raw QR tokens.

Useful ids:

- organization id
- the signed-in user as a hashed subject in CloudWatch (`actor_sub_hash`), not the raw `sub`, unless the operator is already looking at DynamoDB membership for a known case
- API `correlation_id` (the API Gateway request id)
- exchange request id
- offer id
- billing event id
- notification id

Search one CloudWatch log group for that correlation id. The line includes `service`, `operation`, `outcome`, and `error_code` when the handler logged a result. Do not attach the full request body to a ticket.

## What support may do

READ is the default. Examples: `get-alias`, `verify_hardening.py`, `verify_recovery.py`, `verify_security_posture.py`, `smoke_test_production.py`, and a signed-in read of an organization the caller is a member of.

CONTROLLED WRITE is an alias move, a schedule target correction, or a PITR restore to a new table, and only when the matching runbook says so.

DESTRUCTIVE WRITE is emergency-only and needs an explicit decision recorded in the incident note. That includes deleting a table, disabling deletion protection, rotating the Razorpay secret, resetting a Cognito user, or restoring over a live table. Routine support does not do these.

There is no hidden maintenance API. Do not add one to finish a ticket.

## Organizations support may read

`ORG-A66B0A1E4F96` and `ORG-17D0E2939B2D` are the non-pilot organizations used for live checks. Do not open the pilot organization named in `docs/disaster-recovery.md`. Do not change `sub_TiEekQFpwByhkU`.

## Data modification policy

| Class | When | Examples |
|---|---|---|
| READ | Always preferred | Alias describe, smoke GET, log line, table protection describe |
| CONTROLLED WRITE | The runbook names the change and a verifier follows it | Move `live` to a known version, fix an EventBridge target, PITR to a new table |
| DESTRUCTIVE WRITE | Emergency, explicit authorization, SEV-1 or confirmed data loss | Delete a non-production restore copy, and only that copy |

A code rollback does not roll back DynamoDB. A frontend redeploy does not move Lambda aliases.
