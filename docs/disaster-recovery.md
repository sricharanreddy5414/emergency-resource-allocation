# Disaster recovery

Phase 11C records how to recover ERAP. It does not change product behavior.

NEVER restore directly over a production table during routine testing.

## 1. Scope

This runbook covers the DynamoDB tables, Lambdas, API Gateway API `4c6dni17l3` stage `dev`, Cognito pool `eu-north-1_vv7adAAC9`, EventBridge schedules, IAM roles, CloudWatch log groups, the Amplify app `d3enpe7opotop5`, and the one S3 bucket found in the account. Region for the API and data is `eu-north-1`. Amplify is in `us-east-1`.

## 2. Systems covered

Verified in the account on 30 September 2026:

- 14 DynamoDB tables, listed in `scripts/lambda_manifest.py` `TABLES`. No extra tables were present.
- 15 Lambda functions, each with alias `live`, built from this repository by `scripts/package_lambdas.py`.
- API Gateway routes on `4c6dni17l3` / `dev`, deployment `tr1rz2`, authorizer `y0hzhr`.
- Schedules `erap-exchange-expiry-hourly`, `erap-billing-expiry-daily`, and rule `EmergencyResourceAutoReleaseRule`.
- Cognito pool `eu-north-1_vv7adAAC9`.
- Secret name `erap/billing/razorpay/test` in Secrets Manager. The value is not in Git.
- Amplify app `emergency-resource-allocation`, branch `main`, repository `sricharanreddy5414/emergency-resource-allocation`.
- S3 bucket `emergency-resource-allocation-sricharan-2026`. The application code does not name this bucket.

## 3. Data classification

CRITICAL means loss changes active business or authorization state. IMPORTANT means loss removes history, unread state, catalog setup, or billing evidence. Nothing in DynamoDB is treated as disposable.

| Table | Class | Reason |
|---|---|---|
| Organizations | CRITICAL | Tenant root. Membership and resources point here. |
| OrganizationMembers | CRITICAL | Authorization. A user reaches an organization only through a membership row. |
| Locations | CRITICAL | Resources and requests are placed at a location. |
| Resources | CRITICAL | Quantity, status, and ownership live here. |
| EmergencyRequests | CRITICAL | Open emergency work. |
| Allocations | CRITICAL | Holds and exchange quantity movement. |
| ResourceExchanges | CRITICAL | Requests, offers, transfer, handover, and QR session state. |
| OrganizationSubscriptions | CRITICAL | Write access depends on this row. Razorpay remains the provider-side source. |
| ResourceTypes | IMPORTANT | Catalog configuration. Not derived from another table. |
| RequestTypes | IMPORTANT | Catalog configuration. Not derived from another table. |
| ResourceStatusHistory | IMPORTANT | History. Current status is on `Resources`. |
| AuditEvents | IMPORTANT | Operator audit. The raw Cognito subject is stored here. |
| Notifications | IMPORTANT | Inbox and unread state. Exchange state is not stored here. |
| BillingEvents | IMPORTANT | Webhook idempotency. Provider payment state is outside AWS. |

CloudWatch logs, Lambda zip bytes, and the S3 bucket are not the business system of record.

## 4. PITR status

`describe-continuous-backups` showed point-in-time recovery `ENABLED` on all 14 tables. Deletion protection was `true` on all 14. Billing mode was `PAY_PER_REQUEST`. Encryption was the DynamoDB-owned key. There were no user on-demand backups and no system backup summaries.

Earliest restorable time is when PITR was enabled, not a full 35-day window yet. Latest restorable time during the audit was within about one minute of the describe call.

| Table | Earliest restorable (local +05:30) | TTL |
|---|---|---|
| Organizations | 2026-09-27 20:09 | disabled |
| OrganizationMembers | 2026-09-27 20:09 | disabled |
| Locations | 2026-09-27 20:09 | disabled |
| Resources | 2026-09-27 20:10 | disabled |
| EmergencyRequests | 2026-09-27 20:10 | disabled |
| Allocations | 2026-09-27 20:10 | disabled |
| ResourceStatusHistory | 2026-09-27 20:10 | disabled |
| ResourceTypes | 2026-09-27 20:10 | disabled |
| RequestTypes | 2026-09-27 20:10 | disabled |
| AuditEvents | 2026-09-27 20:10 | disabled |
| OrganizationSubscriptions | 2026-09-28 23:26 | disabled |
| BillingEvents | 2026-09-28 23:26 | disabled |
| ResourceExchanges | 2026-09-29 22:43 | enabled, attribute `qr_ttl_epoch` |
| Notifications | 2026-09-30 01:53 | enabled, attribute `expires_at` |

Do not disable PITR. Do not disable deletion protection.

## 5. Backup strategy

Recovery uses DynamoDB point-in-time recovery plus this Git repository.

`list-backup-plans` returned no plans and `list-backup-vaults` returned no vaults. AWS Backup was not added. On-demand backups were not created. PITR plus the repository is enough for this single-account system: data is restored to a new table, and code, routes, and schedules are reapplied from Git and the existing deploy scripts.

What Git does not contain: DynamoDB items, Cognito passwords, the Razorpay secret value, and live alias version numbers.

## 6. Restore procedure

1. Confirm the failed table and the UTC time to recover. Read `EarliestRestorableDateTime` and `LatestRestorableDateTime` first.
2. Restore to a new table name. Never use the production table name as the target.
3. Wait until the new table is `ACTIVE`. Check keys, GSIs, billing mode, and TTL.
4. If TTL is disabled on a table that should expire rows, set the same attribute the source used: `expires_at` on notifications, `qr_ttl_epoch` on exchanges. The 11C copy of `Notifications` came back with TTL disabled.
5. Read with count or a known key. Do not print tenant attributes into chat or tickets.
6. Restore related critical tables to the same time before any cutover. See the exchange and tenant sections.
7. Cut over only by changing the table name the function reads, in a planned change. This phase did not cut over.
8. Keep the old table until the new one is accepted. Deletion protection will block an accidental delete.

## 7. Temporary restore procedure

The tested command shape is `restore-table-to-point-in-time` with `--source-table-name Notifications`, `--target-table-name erap-dr11c-notifications-temp`, and `--use-latest-restorable-time`.

That copy reached `ACTIVE` in 240 seconds. Keys were `pk` and `sk`. GSI `UnreadByUserIndex` was present. A count-only read returned one scanned row and no attributes. Production `Notifications` stayed `ACTIVE` with deletion protection on and TTL still `expires_at`.

The copy had deletion protection off, so it was deleted with `delete-table`. It is gone. If a future copy has deletion protection on, leave it and record the name. Do not turn protection off to force a cleanup.

Do not set a Lambda environment variable or alias to the temporary name. Do not add an API route to it.

## 8. Production restore warning

NEVER restore directly over a production table during routine testing.

`restore-table-to-point-in-time` cannot use the source name as the target while that table exists. Do not delete the production table to make the name available. Deletion protection is there to stop that.

## 9. Tenant isolation precautions

A restore copies every organization in the table. It cannot skip `ORG-D13B30D99127`. Isolation means the new table is not wired to the API, and operators do not print item attributes.

`Organizations` and `OrganizationMembers` must be restored to the same time. Membership is the authorization boundary. A newer membership table against an older organization table, or the reverse, can admit a user into an organization state that did not exist at that time, or lock out a current member.

Cognito `sub` values are the member keys. Restoring membership without the same user pool leaves those keys pointing at people who may no longer exist in Cognito.

Do not test isolation by mixing organizations in production.

## 10. Exchange recovery considerations

Restore these together, to one time, before serving traffic:

1. `Organizations` and `OrganizationMembers`
2. `Locations`
3. `Resources` and `Allocations`
4. `ResourceExchanges`
5. `EmergencyRequests` if emergency holds from that time must match

Quantity and handover state are split across `Resources`, `Allocations`, and `ResourceExchanges`. Restoring only the exchange table can show a completed handover while the resource quantity still has the pre-handover value. QR session hashes live on the exchange item. A raw QR token is not stored and cannot be rebuilt.

Do not edit production exchange items by hand.

## 11. Billing recovery considerations

Restore `OrganizationSubscriptions` and `BillingEvents` to the same time. The subscription row is what ERAP uses for write access. `BillingEvents` stops a webhook from being applied twice.

Razorpay remains the source of truth for provider payment and subscription status. Restoring AWS tables does not change `sub_TiEekQFpwByhkU` or any other provider subscription. After a restore, compare the organization row with the provider before accepting new webhooks. Do not create a subscription or take a payment as part of recovery.

The webhook secret is `erap/billing/razorpay/test`. Recovery needs that secret to stay in Secrets Manager. It is not in Git.

## 12. Notification recovery considerations

`Notifications` holds `EVENT#` rows and inbox rows. Exchange business state does not depend on them. Losing the table loses history and unread markers. Future exchange actions can emit new events. Old inbox rows and unread state cannot be rebuilt from the exchange table.

TTL attribute `expires_at` is about 90 days. A point-in-time restore brings back rows that had not expired at that time. The tested copy did not keep TTL enabled. Re-enable `expires_at` before the restored table is used.

A notification failure must still not roll back an exchange.

## 13. Lambda recovery

`python scripts/package_lambdas.py --check` rebuilds every function zip from `scripts/lambda_manifest.py`. Runtimes and handlers are in the deploy scripts and `infra/*.json`. API Gateway and the schedules invoke alias `live`.

`scripts/deploy_backend.py` publishes the nine `PACKAGES` functions. Exchange, notifications, expiry, and billing functions are published by their own deploy scripts. Moving `live` with `scripts/set_live_version.py --version <n>` restores a published version without uploading code. A failed deploy workflow runs that script with `--from-summary`, which points `live` at the previous versions in that job's summary. Confirm the alias description is the intended `commit=` value after any rollback.

No Lambda was published in Phase 11C.

## 14. API Gateway recovery

API `4c6dni17l3`, stage `dev`, deployment `tr1rz2`. Authorizer `y0hzhr`. Default throttle is 20 requests per second, burst 40. `GET /public/resources` is 5 per second, burst 10. Gateway error CORS allows only the Amplify origin. `scripts/verify_hardening.py` checks those values.

Routes are live on the API. The repository has route notes in `infra/` for billing and exchange, not a full export of every method. Recreate a bad deployment by updating the stage `deploymentId` to the previous id, then run the hardening check. Do not create a second API.

## 15. Cognito recovery

Pool `eu-north-1_vv7adAAC9` in `eu-north-1`. Deletion protection is `ACTIVE`. MFA configuration is `OPTIONAL`. Software-token MFA is enabled. SMS MFA is off. There is no Lambda trigger on the pool. App clients are `ERAP - Emergency Resource Allocation Platform` (`1lb1eppq5t1obsi59to4bd18eo`) and `ERAP-Web-Frontend` (`3je7latr22bqhggoavlva00hp5`). Group `Admin` exists.

Passwords and software-token secrets cannot be exported. A new pool would not keep the same `sub` values, so membership rows would not match. Do not reset users, disable MFA, or change the pool in a recovery drill.

## 16. EventBridge recovery

Verified schedules:

| Name | Expression | State | Target |
|---|---|---|---|
| erap-exchange-expiry-hourly | `cron(15 * * * ? *)` | ENABLED | `erap-exchange-expiry:live` via `ERAP-Exchange-Expiry-Scheduler-Role` |
| erap-billing-expiry-daily | `cron(0 2 * * ? *)` | ENABLED | `erap-billing-expiry:live` via `ERAP-Billing-Expiry-Scheduler-Role` |
| EmergencyResourceAutoReleaseRule | `rate(5 minutes)` | ENABLED | `emergency-resource-auto-release:live` |

The two expiry schedules are also in `infra/exchange-expiry.json` and `infra/billing-expiry.json`. The auto-release rule is retargeted by `scripts/route_api_to_live_alias.py`. Do not change these expressions unless the live rule is actually wrong.

## 17. IAM recovery

Runtime roles found, all without `AdministratorAccess` and without inline `Scan`, `DeleteItem`, `BatchWriteItem`, `RestoreTableToPointInTime`, or `backup:*`:

`ERAP-Billing-Expiry-Lambda-Role`, `ERAP-Billing-Expiry-Scheduler-Role`, `ERAP-Billing-Lambda-Role`, `ERAP-Billing-Webhook-Lambda-Role`, `ERAP-Catalog-Lambda-Role`, `ERAP-Exchange-Expiry-Lambda-Role`, `ERAP-Exchange-Expiry-Scheduler-Role`, `ERAP-Exchange-Lambda-Role`, `ERAP-Notifications-Lambda-Role`, `ERAP-Organization-Lambda-Role`, `ERAP-Public-Discovery-Role`.

`ERAP-GitHub-Deploy` and `ERAP-GitHub-Production` are deploy roles, not application roles. Restore permission stays with the operator principal that can call `RestoreTableToPointInTime`. Do not add that action to a Lambda role.

Role documents that the deploy scripts own can be reapplied by those scripts. The shared operational role used by the original four functions is still the live execution role for those functions; splitting it is outside this phase.

## 18. CloudWatch recovery

Lambda log groups for the owned functions are retained for 30 days. Logs are not a data backup. An expired log line cannot be restored by this runbook. Alarms `ERAP-ApiGateway-5XX`, `ERAP-Allocation-Throttles`, `ERAP-AutoRelease-Errors`, `ERAP-Public-Lambda-Errors`, `ERAP-Resources-SystemErrors`, and `EmergencyResourceAllocation-Lambda-Errors` exist and have an alarm action. The SNS topic has no email subscription, so an alarm does not page anyone.

## 19. RPO

Operational target, not a guaranteed SLA.

During the audit, `LatestRestorableDateTime` on each table was within about one minute of the describe call. Recent writes newer than that timestamp can be lost. Data older than `EarliestRestorableDateTime` cannot be restored. That earliest time is still the day PITR was enabled (27–30 September 2026, depending on the table), so the service's 35-day window is not fully available yet.

## 20. RTO

Operational target, not a guaranteed SLA.

The `Notifications` copy became `ACTIVE` 240 seconds after the restore call was accepted. That measures one table of 46 approximate items, restored to a new name, with no cutover. A critical restore adds same-time restores of the related tables, TTL checks, and a planned cutover. Those extra steps were not timed.

Code recovery is a `live` alias move when the version already exists, or a deploy from Git when it does not.

## 21. Validation checklist

- `python scripts/verify_hardening.py` reports every table protected, both expiry schedules, the auto-release rule, and a `live` alias on every packaged function.
- Restored table status is `ACTIVE`.
- Key schema and GSIs match the source.
- TTL matches the source, or it has been set again on purpose.
- Production table name, deletion protection, and PITR are unchanged.
- No Lambda environment variable or API route points at the temporary table.
- Count or key reads do not print tenant attributes.
- Billing provider status was compared before webhook traffic resumes.

## 22. Rollback/abort conditions

Abort if the restore target name is an existing production table. Abort if the restored keys or GSIs differ from the source. Abort if the only way to clean up is to disable deletion protection. Abort a cutover if `Organizations` and `OrganizationMembers` are not from the same time. Leave the new table unused and keep serving the original tables.

A code rollback is `set_live_version.py` to the previous published version, then the smoke test and the hardening check. That does not restore data.

## 23. Pilot safety rules

Do not read or write `ORG-D13B30D99127` as a recovery drill. A full-table restore still copies every tenant, including the pilot, into the new table. That is why the new table must stay disconnected. Do not query pilot keys on the copy. Do not change the pilot subscription. The Phase 11C test did not print pilot attributes and did not modify the production pilot rows.

## 24. Incident evidence to capture

Record the UTC time, the source table, the new table name, restore start, time to `ACTIVE`, PITR earliest and latest times, key schema, GSI names, TTL status, deletion protection on both tables, and the alias `commit=` values. Omit item bodies, tokens, webhook signatures, and secret values.

## 25. Known limitations

- The 35-day PITR window is not fully populated yet.
- The notifications restore did not keep TTL enabled.
- Table restore cannot select one organization.
- Cognito passwords and the Razorpay secret cannot be rebuilt from Git.
- API route definitions are only partly represented in `infra/`.
- S3 bucket `emergency-resource-allocation-sricharan-2026` has versioning and AES256 encryption, and no lifecycle rule. Object contents were not listed. Application code does not reference it.
- No production cutover was performed.
- AWS Backup was not added.
