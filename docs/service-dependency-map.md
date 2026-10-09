# Service dependency map

Phase 13 inventory. Region is `eu-north-1` unless a row says otherwise. Account `481838970142`. Alias name is `live`. Versions move when a deploy publishes a function. Read the current number with `aws lambda get-alias --function-name <name> --name live --region eu-north-1`. Do not treat a version in an older note as current.

## Request path

```text
USER
  -> AMPLIFY  (app d3enpe7opotop5, branch main, us-east-1)
  -> API GATEWAY  (4c6dni17l3, stage dev)
  -> COGNITO AUTHORIZER  (y0hzhr, pool eu-north-1_vv7adAAC9)
  -> LAMBDA alias live
  -> AUTHORIZATION  (src/shared/access.py)
  -> DYNAMODB
```

`GET /public/resources` skips the Cognito authorizer. `POST /billing/webhook` skips it and checks the Razorpay signature instead.

## Worker paths

```text
EXCHANGE
  -> NOTIFICATIONS  (in-app rows on Notifications; failure of a notification does not roll back the exchange write)

EXCHANGE
  -> QR HANDOVER  (session rows on ResourceExchanges; raw tokens are not logged)

BILLING
  -> RAZORPAY production mode
  -> WEBHOOK  (erap-billing-webhook; test mode stays on the separate test secret)
  -> BILLING STATE  (OrganizationSubscriptions, BillingEvents)

EXPIRY
  -> EVENTBRIDGE schedule
  -> LAMBDA alias live
  -> DATABASE STATE

RESOURCE AUTO-RELEASE
  -> EVENTBRIDGE rule EmergencyResourceAutoReleaseRule
  -> LAMBDA emergency-resource-auto-release:live
  -> RESOURCES and related allocation rows
```

## Production service inventory

| Service | Purpose | AWS resource | Runtime / deploy | Depends on | Failure impact | Recovery |
|---|---|---|---|---|---|---|
| Frontend | Operator UI | Amplify `d3enpe7opotop5`, `https://main.d3enpe7opotop5.amplifyapp.com` | Amplify build of `main` in us-east-1 | API Gateway, Cognito | UI unavailable; API can still run | Redeploy the last successful Amplify job. See `docs/rollback.md`. |
| API | HTTPS edge | API `4c6dni17l3` stage `dev` | Current stage deployment `59k29o`. The Phase 13 baseline was `tr1rz2`. Routes are not recreated by the Lambda deploy. | Lambda aliases, Cognito | All callers fail | Point the stage at the previous deployment id. Do not delete deployments. |
| Authorizer | JWT check | Cognito authorizer `y0hzhr` | Pool `eu-north-1_vv7adAAC9`. MFA OPTIONAL, software token on, SMS off. | Cognito | Protected routes reject or fail closed | Do not change MFA or users from a runbook. |
| Organizations | Create and read orgs, members | `erap-create-organization`, `erap-get-organization` | Python 3.14, 15s, 256 MB, role `ERAP-Organization-Lambda-Role`. In `PACKAGES`, so push to `main` publishes them. | Organizations, OrganizationMembers, Cognito `sub` | Sign-in cannot resolve a tenant | Alias rollback for those functions. Restore Organizations and OrganizationMembers together. |
| Locations | Location CRUD | `erap-locations` | Same role and package path as organizations. 15s. | Locations | Location pickers fail | Alias rollback. PITR to a new table. |
| Catalog | Resource and request types | `erap-catalog` | Python 3.14, 15s, `ERAP-Catalog-Lambda-Role`. In `PACKAGES`. | ResourceTypes, RequestTypes | Type pickers fail | Alias rollback. |
| Public discovery | Unauthenticated resource list | `erap-public-resources` | Python 3.14, 10s, `ERAP-Public-Discovery-Role`. In `PACKAGES`. | Resources | Public page fails | Alias rollback. Response must not include `organization_id`. |
| Resources and allocation | Inventory, requests, allocate, release | `get-resources`, `create-request`, `emergency-resource-allocation` | Python 3.14, 15s, shared role `emergency-resource-allocation-role-12qymvku`. In `PACKAGES`. | Resources, EmergencyRequests, Allocations, ResourceStatusHistory | Core operations stop | Alias rollback of those three. Do not restore one of those tables alone when the others moved. |
| Auto-release | Release stale allocations | `emergency-resource-auto-release` | Python 3.14, 30s, same shared role. In `PACKAGES`. Target of `EmergencyResourceAutoReleaseRule` `rate(5 minutes)`. | Allocations, Resources | Stale allocations remain until the rule runs again | Confirm the rule is ENABLED and the target ends with `function:emergency-resource-auto-release:live`. |
| Exchange | Requests, offers, handover | `erap-exchange` | Python 3.14, 15s, `ERAP-Exchange-Lambda-Role`. Not in `PACKAGES`. Production release is the manual workflow Release exchange, from `main`, after production approval. | ResourceExchanges, membership, notifications, QR helper | Exchange UI fails | Rollback exchange moves alias `live` to an existing published version. Restore ResourceExchanges with Organizations and members in mind. |
| Exchange expiry | Close expired exchanges | `erap-exchange-expiry` | Python 3.14, 60s, `ERAP-Exchange-Expiry-Lambda-Role`. Schedule `erap-exchange-expiry-hourly`, `cron(15 * * * ? *)`. Deploy with `scripts/deploy_exchange_expiry.py`. | ResourceExchanges | Expired rows stay open until the next hour | `python scripts/verify_recovery.py`. |
| Notifications | In-app inbox | `erap-notifications` | Python 3.14, 15s, `ERAP-Notifications-Lambda-Role`. Not in `PACKAGES`. Deploy with `scripts/deploy_notifications.py`. | Notifications table, `UnreadByUserIndex`, TTL `expires_at` | Inbox fails. Exchange writes stay committed. | Alias rollback. PITR copy does not copy TTL; re-enable `expires_at` on a cutover table. See `docs/disaster-recovery.md`. |
| Billing API | Checkout, summary, events | `erap-billing` | Python 3.14, 29s, `ERAP-Billing-Lambda-Role`. Not in `PACKAGES`. | OrganizationSubscriptions, BillingEvents, Cognito, secret `erap/billing/razorpay/production` | Billing pages fail. Other operations follow entitlement rules already deployed. | Do not create a subscription to test this. Do not edit `sub_TiEekQFpwByhkU`. |
| Billing webhook | Provider events | `erap-billing-webhook` | Python 3.14, 29s, `ERAP-Billing-Webhook-Lambda-Role`. Public route, signature required. | Secret `erap/billing/razorpay/production`, subscriptions, events | Payments stop updating state | Invalid signatures stay 401 and write no event. Do not print the secret. |
| Billing expiry | Trial and lapse transitions | `erap-billing-expiry` | Python 3.14, 60s, `ERAP-Billing-Expiry-Lambda-Role`. Schedule `erap-billing-expiry-daily`, `cron(0 2 * * ? *)`. | OrganizationSubscriptions | Trials and past-due rows wait until the next day | Confirm the schedule target ends with `function:erap-billing-expiry:live`. |
| Data | System of record | 14 tables in `scripts/lambda_manifest.py` `TABLES` | On-demand, AWS-owned encryption, PITR, deletion protection | IAM roles above | Data loss or lockout | Restore to a new table. Never over a live table in a drill. |
| Object storage | Not on the request path | Bucket `emergency-resource-allocation-sricharan-2026` | Block Public Access on. No application reference. | None for API calls | Not a product dependency | Leave the block on. Do not list objects from a runbook. |
| Secrets | Razorpay credentials | Secrets Manager `erap/billing/razorpay/production` for deployed billing and the webhook. `erap/billing/razorpay/test` remains the test-mode secret. | Read at runtime by checkout and webhook roles | Billing only | Checkout and webhook fail | Restore access on the existing secret. Do not rotate it from this runbook. Do not print either value. |
| Logs | Operator evidence | CloudWatch log groups. Live groups that already have retention keep 30 days. Reservation-expiry has a repository 30-day policy that is not applied in AWS yet. | Phase 11B JSON lines | Lambda | Diagnosis is harder. The API still runs. | Retention script `scripts/set_log_retention.py`. No dashboard in this phase. |
| Deploy | Publish nine functions | GitHub workflow Deploy backend, role `ERAP-GitHub-Deploy` | Push to `main`. `scripts/deploy_backend.py`. | OIDC, `PACKAGES` | A bad publish is reverted only if `live` is still that publish | `docs/production-release-runbook.md` and `docs/rollback.md`. |

Operational importance: API, Cognito, Organizations, OrganizationMembers, Resources, Allocations, ResourceExchanges, and OrganizationSubscriptions are critical. Notifications are not the exchange source of truth. Razorpay is the payment source of truth. The S3 bucket is not on the request path.
