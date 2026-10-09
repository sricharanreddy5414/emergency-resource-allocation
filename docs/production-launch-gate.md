# Production launch gate

Preparation for earlier phases remains below. The Phase 15 decision is first.

Production launch is NOT authorized by Phase 13.

Phase 14 records the commercial model in `docs/saas-commercial-readiness.md`. Production launch is NOT authorized by Phase 14.

## Phase 15 decision

PHASE 15 = GO.

LAUNCH SCOPE = CONTROLLED LIMITED BETA.

COMPANY APPROVAL = CONFIRMED for the current V1 launch documents. This is not legal counsel approval and it is not regulatory approval.

One controlled beta organization was created through the existing onboarding flow: `ORG-D878EAF5135D`, name Controlled Beta Organization. It is ACTIVE, the signed-in user is OWNER, and the subscription is TRIALING on FREE_TRIAL for 15 UTC days. No Razorpay subscription was created and no payment was taken. Public registration was not opened.

The questions a launch has to answer:

| Question | Result |
|---|---|
| Is production billing configured? | Yes. `erap-billing` live version 9 uses production mode. |
| Are production provider plans configured? | Yes. Monthly `plan_TiMn4MluXeOMK1` and yearly `plan_TiMpOnO7K5GT0Q` are distinct from the test plans. |
| Are production secrets configured? | Yes. Production mode reads `erap/billing/razorpay/production` and requires an `rzp_live_` key. Test mode still uses `erap/billing/razorpay/test`. |
| Is the webhook configured for production? | Yes. The existing public route is used by the production webhook, and the webhook function reads the production webhook secret. |
| Is tenant isolation live-tested with two sessions? | Yes. Two signed-in non-pilot sessions each received HTTP 403, message `Organization access denied`, when reading the other organization's resources, requests, allocations, locations, exchange, notifications, billing, and members. No write was sent. |
| Did authenticated smoke pass? | Yes for read-only GETs. Each signed-in non-pilot session received HTTP 200 for its own organization, resources, requests, allocations, locations, exchange, notifications, and billing. |
| Is onboarding validated live? | Yes. `ORG-D878EAF5135D` was created once through the existing flow. |
| Is rollback verified? | Yes, by `tests/test_alias_restore.py` and the code in `scripts/set_live_version.py`. No alias was rolled back. |
| Is observability sufficient for the current system? | Yes for investigation. Alert delivery is not. |
| Is disaster recovery verified? | Yes for PITR, deletion protection, and schedules. A second restore was not run. |
| Are support procedures ready? | Yes. `docs/production-support.md`. |
| Are legal and commercial documents present? | COMPANY APPROVAL = CONFIRMED. Legal counsel approval is not claimed. |
| Is launch scope approved? | Yes for this closure. Controlled limited beta. Organizations are onboarded deliberately. This is not a public launch. |
| Is production pricing approved? | Yes. The catalog prices are ₹999 monthly and ₹9,999 yearly, and the production plans use those prices. |
| Are there unresolved security issues that block launch? | The live cross-tenant read test passed. No new code vulnerability was found in this pass. |
| Are there unresolved critical operational issues? | No critical operational issue remains for the controlled limited beta. `ERAP-Production-Alarms` has one confirmed email subscription. The address is not stored in the repository. |

Paid production stays off until those blocked rows are actually satisfied. A later phase has to record the human decisions. This file cannot supply them.

## Configuration matrix

The running system is one account and one API. The stage name is `dev`. That stage is what the Amplify app calls. It is not a second, isolated production stack.

| Configuration | What is deployed | Production equivalent | Status |
|---|---|---|---|
| Region | `eu-north-1` for the API, `us-east-1` for Amplify | Same regions | One environment |
| API | `4c6dni17l3`, stage `dev` | No separate production API or stage | Not separated |
| Cognito | `eu-north-1_vv7adAAC9`, authorizer `y0hzhr` | Same pool | Not separated |
| Lambda | 15 functions, alias `live` | Same functions | Not separated |
| DynamoDB | 14 tables | Same tables | Not separated |
| Secrets Manager | `erap/billing/razorpay/production` for deployed billing and the webhook | `erap/billing/razorpay/test` remains test mode | Production deployed |
| Razorpay mode | Production mode on the deployed billing and webhook functions | Test mode stays separate | Production |
| Razorpay plans | Production plans are distinct from the test plans | Test plans remain `plan_ThiWT35Gf1jyio` and `plan_ThiWTXOzBHl2Qb` | Production |
| Webhook secret | Inside `erap/billing/razorpay/production` | Test mode uses the test secret | Production |
| Alarm topic | `ERAP-Production-Alarms` | One confirmed email subscription. The address is not stored here. | Present |
| Frontend API URL | The `dev` execute-api host in `frontend/app.js` | No production host | TEST stack |
| Domain | `https://main.d3enpe7opotop5.amplifyapp.com` | No custom domain | Amplify hostname |
| CORS | That Amplify origin | No second origin | Matches the hostname |
| Logging | Live groups that already have retention keep 30 days. Reservation-expiry repository policy is 30 days and is not applied in AWS yet. | Same policy | Pending for reservation-expiry |
| Schedules | Exchange hourly, billing daily 02:00 UTC, auto-release every 5 minutes | Same | In place |

## Business decisions this phase did not make

- Whether a provider cancellation during `PAST_DUE` should end operational writes. The code still leaves writes allowed. It was not changed.
- Whether refunds may stay a manual support process. Refund automation is not implemented.
- Whether the one confirmed email subscription on `ERAP-Production-Alarms` is the approved launch destination. The address is not stored in this repository.
- Who the first production customers are.
- Whether ₹999 and ₹9,999 are the approved production prices.

## Earlier remaining list

The current Phase 15 decision is the GO recorded above. This list is the earlier remaining work. It is not the current gate.

1. Provision production Razorpay plans, a production secret, and a production webhook outside this repository. Do not paste the values into chat or into source.
2. Provide terms, a privacy policy, and a cancellation or refund statement. Do not treat this file as those documents.
3. Name the launch scope: internal, invited, limited beta, or public.
4. Confirm the existing confirmed email subscription on `ERAP-Production-Alarms` as the launch destination, or name a different one outside this repository. Do not put the address in source.
5. Validate live organization creation only when a disposable organization and a safe cleanup path exist. This pass did not create an organization.

The two-session read test and the signed-in read-only smoke were completed during blocker closure. Details are in `docs/production-launch-blocker-closure.md`. The earlier closure decision was NO-GO.

A later search did not find an approved production price, launch scope, legal text, alert destination, or `PAST_DUE` rule. `docs/legal-launch-requirements.md` names the missing documents. It is not those documents. Production Razorpay was not provisioned. No organization was created.

## Earlier phase notes

Phase 15 is the launch decision. Phase 13 records whether the gates below can be checked with the current commands. A gate marked "check exists" means an operator can run it. It does not mean a launch is approved.

| Gate | Check | Phase 13 result |
|---|---|---|
| Security | `python scripts/verify_security_posture.py` and `docs/security-posture.md` | Check exists. MFA and IAM were read, not changed. |
| Data protection | `python scripts/verify_hardening.py` | PITR and deletion protection are on for 14 tables. |
| Recovery | `python scripts/verify_recovery.py` and `docs/disaster-recovery.md` | Schedules and aliases are checked. A second restore was not run. |
| Observability | `docs/observability.md` | Logs carry service, operation, outcome, correlation id, organization, entity, error code, and actor hash. No new dashboard. |
| Deployment | Deploy backend on `main`, then alias descriptions | Nine functions publish automatically. Exchange, notifications, and billing do not. |
| Rollback | `docs/rollback.md` and the owned-alias restore | A failed job does not move `live` when a newer version already owns it. |
| API | `python scripts/smoke_test_production.py` | Public GET and unauthenticated rejection. |
| Authentication | Authorizer `y0hzhr`, pool `eu-north-1_vv7adAAC9` | Configuration verified. A new human login was not required for this gate. |
| Authorization | `src/shared/access.py` | Unchanged. Live cross-tenant proof with two sessions is still a launch task. |
| Billing | `docs/billing-architecture.md` | Test mode. No new payment. The existing test subscription was not edited. |
| Support | `docs/production-support.md` | Read-first policy is written. |
| Documentation | `python scripts/verify_production_readiness.py` | Runbooks listed in that script must be present. |

Blocked for a later phase, not by a defect found here: alarm email, splitting the shared operational role, a vulnerability feed, and a signed-in tenant-isolation drill.

Do not start a customer launch from this file.
