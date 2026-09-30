# Production launch gate

Preparation for earlier phases remains below. The Phase 15 decision is first.

Production launch is NOT authorized by Phase 13.

Phase 14 records the commercial model in `docs/saas-commercial-readiness.md`. Production launch is NOT authorized by Phase 14.

## Phase 15 decision

NO-GO.

Phase 15 did not launch ERAP. The application that is already deployed stays in its current test-mode billing configuration. No production Razorpay plan, key, or webhook was created. No customer was added. No payment was taken.

The questions a launch has to answer:

| Question | Result |
|---|---|
| Is production billing configured? | No. BLOCKED. |
| Are production provider plans configured? | No. The only plan ids in code are test plans `plan_ThiWT35Gf1jyio` and `plan_ThiWTXOzBHl2Qb`. |
| Are production secrets configured? | No. The loader accepts only `erap/billing/razorpay/test`. A key id must start with `rzp_test_`. |
| Is the webhook configured for production? | No. The existing public route checks the test webhook secret. |
| Is tenant isolation live-tested with two sessions? | Yes. Two signed-in non-pilot sessions each received HTTP 403, message `Organization access denied`, when reading the other organization's resources, requests, allocations, locations, exchange, notifications, billing, and members. No write was sent. |
| Did authenticated smoke pass? | Yes for read-only GETs. Each signed-in non-pilot session received HTTP 200 for its own organization, resources, requests, allocations, locations, exchange, notifications, and billing. |
| Is onboarding validated live? | No. Creating an organization writes a permanent trial. That write was not made. |
| Is rollback verified? | Yes, by `tests/test_alias_restore.py` and the code in `scripts/set_live_version.py`. No alias was rolled back. |
| Is observability sufficient for the current system? | Yes for investigation. Alert delivery is not. |
| Is disaster recovery verified? | Yes for PITR, deletion protection, and schedules. A second restore was not run. |
| Are support procedures ready? | Yes. `docs/production-support.md`. |
| Are legal and commercial documents present? | Drafts are present. FINAL COMPANY/LEGAL APPROVAL is not. APPROVAL REQUIRED. |
| Is launch scope approved? | Yes for this closure. Controlled limited beta. Organizations are onboarded deliberately. This is not a public launch. |
| Is production pricing approved? | Yes for this closure. The catalog prices ₹999 monthly and ₹9,999 yearly are the production prices. Production provider plans for those prices are not created yet. |
| Are there unresolved security issues that block launch? | The live cross-tenant read test passed. No new code vulnerability was found in this pass. |
| Are there unresolved critical operational issues? | Production billing is absent. `ERAP-Production-Alarms` has one confirmed email subscription. This closure accepts that existing subscription as the operational alert destination. The address is not stored in the repository. |

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
| Secrets Manager | `erap/billing/razorpay/test` | No production billing secret in code | TEST only |
| Razorpay mode | Test key prefix required | Live mode rejected by code | TEST only |
| Razorpay plans | `plan_ThiWT35Gf1jyio`, `plan_ThiWTXOzBHl2Qb` | Not provisioned | TEST only |
| Webhook secret | Inside the test secret | Not provisioned | TEST only |
| Frontend API URL | The `dev` execute-api host in `frontend/app.js` | No production host | TEST stack |
| Domain | `https://main.d3enpe7opotop5.amplifyapp.com` | No custom domain | Amplify hostname |
| CORS | That Amplify origin | No second origin | Matches the hostname |
| Logging | 30-day CloudWatch retention | Same | In place |
| Schedules | Exchange hourly, billing daily 02:00 UTC, auto-release every 5 minutes | Same | In place |

## Business decisions this phase did not make

- Whether a provider cancellation during `PAST_DUE` should end operational writes. The code still leaves writes allowed. It was not changed.
- Whether refunds may stay a manual support process. Refund automation is not implemented.
- Whether the one confirmed email subscription on `ERAP-Production-Alarms` is the approved launch destination. The address is not stored in this repository.
- Who the first production customers are.
- Whether ₹999 and ₹9,999 are the approved production prices.

## What remains blocked

1. Provision production Razorpay plans, a production secret, and a production webhook outside this repository. Do not paste the values into chat or into source.
2. Provide terms, a privacy policy, and a cancellation or refund statement. Do not treat this file as those documents.
3. Name the launch scope: internal, invited, limited beta, or public.
4. Confirm the existing confirmed email subscription on `ERAP-Production-Alarms` as the launch destination, or name a different one outside this repository. Do not put the address in source.
5. Validate live organization creation only when a disposable organization and a safe cleanup path exist. This pass did not create an organization.

The two-session read test and the signed-in read-only smoke were completed during blocker closure. Details are in `docs/production-launch-blocker-closure.md`. The launch decision remains NO-GO.

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
