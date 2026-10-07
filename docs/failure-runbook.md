# Failure runbook

Safe actions are reads and alias moves. Destructive writes need an explicit decision and are not the default. DynamoDB restore always targets a new table. See `docs/disaster-recovery.md`.

## 1. API Gateway failure

- Symptoms: every route times out or returns 5xx before a Lambda log line exists.
- First diagnostic: `python scripts/verify_hardening.py`.
- Safe action: read the stage deployment id. Do not create a second API.
- Recovery: point stage `dev` on `4c6dni17l3` at the previous deployment id, as in `docs/rollback.md`.
- Verification: hardening and `python scripts/smoke_test_production.py`.
- Escalation: SEV-1 if all callers fail.

## 2. Lambda failure

- Symptoms: one route fails and its log shows `outcome` `failed`.
- First diagnostic: `aws lambda get-alias --function-name <name> --name live --region eu-north-1`.
- Safe action: compare the alias description commit with `git log`.
- Recovery: move that alias to the last known-good version. For the nine `PACKAGES` functions, use the rules in `docs/rollback.md`.
- Verification: smoke the route without writing, then hardening.
- Escalation: SEV-2 when the route is allocation, exchange, or billing.

## 3. DynamoDB issue

- Symptoms: `ResourceNotFoundException`, throttling, or users see empty data after a time that matches a bad write.
- First diagnostic: `python scripts/verify_hardening.py` for PITR and deletion protection.
- Safe action: do not scan a table to explore. Do not disable deletion protection.
- Recovery: PITR to a new table name. Restore Organizations with OrganizationMembers. Restore exchange tables to the same time. Restore the two billing tables together. Re-enable TTL on a Notifications copy before any cutover.
- Verification: describe the new table, then decide cutover outside this drill.
- Escalation: SEV-1 if the live table is missing or unreadable.

## 4. Cognito issue

- Symptoms: protected routes return 401 for valid users, or the hosted page does not load.
- First diagnostic: `python scripts/verify_security_posture.py` (MFA must stay OPTIONAL, software token on, SMS off).
- Safe action: do not reset users and do not change MFA.
- Recovery: confirm authorizer `y0hzhr` still points at pool `eu-north-1_vv7adAAC9`. Fix a bad client setting only with a written change.
- Verification: unauthenticated call stays 401. A signed-in browser session can open one organization.
- Escalation: SEV-1 when nobody can sign in.

## 5. EventBridge issue

- Symptoms: exchanges or trials never expire, or allocations never auto-release.
- First diagnostic: `python scripts/verify_recovery.py`.
- Safe action: do not change the cron or rate unless the target is wrong.
- Recovery: put the target back on the `:live` alias named in `scripts/recovery_expectations.py`.
- Verification: the same script prints `ok schedule` and `ok auto-release`.
- Escalation: SEV-3 until the lag is hours, then SEV-2.

## 6. Exchange expiry issue

- Symptoms: open exchanges remain after `qr` or offer deadlines.
- First diagnostic: schedule `erap-exchange-expiry-hourly` and alias `erap-exchange-expiry`.
- Safe action: read one log line. Do not edit production exchange rows by hand.
- Recovery: fix the target or the alias. The next hour runs the worker.
- Verification: `python scripts/verify_recovery.py`.
- Escalation: SEV-2 if handover is stuck for many organizations.

## 7. Billing webhook issue

- Symptoms: checkout stays pending after a test payment, or Razorpay retries.
- First diagnostic: webhook Lambda logs. An invalid signature is 401 and writes no BillingEvents row.
- Safe action: do not print the secret `erap/billing/razorpay/test`. Do not create a payment to test.
- Recovery: confirm the function alias and that the role can `GetSecretValue` on that secret. Do not edit `sub_TiEekQFpwByhkU`.
- Verification: a rejected signature still returns 401. A later real provider retry is the provider's call.
- Escalation: SEV-2 when paid state cannot update.

## 8. Billing expiry issue

- Symptoms: trials do not end.
- First diagnostic: schedule `erap-billing-expiry-daily` at `cron(0 2 * * ? *)` and alias `erap-billing-expiry`.
- Safe action: do not force a status write.
- Recovery: restore the schedule target to `function:erap-billing-expiry:live`.
- Verification: `python scripts/verify_recovery.py`.
- Escalation: SEV-3.

## 9. Notification failure

- Symptoms: inbox empty or mark-read fails, while the exchange itself succeeded.
- First diagnostic: alias `erap-notifications` and table Notifications TTL `expires_at`.
- Safe action: do not add email, SMS, or push. Do not scan the table.
- Recovery: alias rollback with `scripts/deploy_notifications.py` only when the code is the defect. Notification loss does not roll back the exchange.
- Verification: a signed-in read of the inbox for `ORG-A66B0A1E4F96` or `ORG-17D0E2939B2D`.
- Escalation: SEV-3.

## 10. QR failure

- Symptoms: handover confirm fails. The raw token must not be copied into a ticket.
- First diagnostic: exchange log `error_code` and `correlation_id`.
- Safe action: do not log or request the raw QR token.
- Recovery: code rollback of `erap-exchange` if a new alias caused it. Revoke is an application action, not a database delete.
- Verification: existing handover tests, then one read of the exchange. Do not create a QR session to test the incident.
- Escalation: SEV-2 when handover cannot complete.

## 11. Resource allocation failure

- Symptoms: reserve or release returns 5xx.
- First diagnostic: aliases for `emergency-resource-allocation` and `get-resources`, then the log line.
- Safe action: do not create an allocation to reproduce it.
- Recovery: roll those `PACKAGES` aliases back together when they were published as one commit.
- Verification: unauthenticated routes still return 401. A signed-in user reads resources.
- Escalation: SEV-2.

## 12. Amplify frontend issue

- Symptoms: the site is blank and the API smoke still returns 200.
- First diagnostic: open `https://main.d3enpe7opotop5.amplifyapp.com` and the Amplify job list for branch `main`.
- Safe action: do not delete the app.
- Recovery: redeploy the last successful Amplify job. Backend alias rollback does not move the site.
- Verification: the page returns 200 and references `app.js`, which `python scripts/smoke_test.py` checks.
- Escalation: SEV-2 when operators cannot work and the API is healthy.

## 13. GitHub deployment failure

- Symptoms: Deploy backend red after the deploy step.
- First diagnostic: open the job. If Verify hardening failed, the next step runs `set_live_version.py --from-summary`.
- Safe action: do not start a local deploy while it is running.
- Recovery: if aliases were restored, `live` is the previous version for functions that job still owned. If the job failed before publish, aliases are unchanged. Fix the cause and push again only when the diff is understood.
- Verification: `get-alias` descriptions and `python scripts/verify_hardening.py`.
- Escalation: SEV-2 if the failed job left a bad alias that the restore did not own.

## 14. Secrets Manager issue

- Symptoms: checkout or webhook cannot load `erap/billing/razorpay/test` or `erap/billing/razorpay/production`.
- First diagnostic: the billing log says the read failed, without the value.
- Safe action: do not paste the secret into chat, code, or a ticket. Do not rotate it as a first step.
- Recovery: confirm the webhook and billing roles can read that secret name. API-key rotation, when an operator later chooses it, follows `docs/security-posture.md`. Automatic rotation is not safe. Do not change the webhook secret while the verifier accepts only one secret.
- Verification: the function starts and a bad signature still returns 401.
- Escalation: SEV-2 for payments, SEV-3 if only a status read fails.

## 15. S3 issue

- Symptoms: a public ACL or bucket policy appears, or the console warns that Block Public Access is off.
- First diagnostic: `python scripts/verify_security_posture.py`.
- Safe action: do not list or download objects.
- Recovery: the application does not read this bucket. Turn all four Block Public Access settings on and remove an anonymous `GetObject` statement if one returns.
- Verification: the posture script prints `ok s3`.
- Escalation: SEV-1 if anonymous reads are possible.
