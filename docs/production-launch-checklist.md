# Production launch checklist

Phase 15. This is the gate record. It is not a launch.

Final decision: NO-GO.

Status values are PASS, FAIL, BLOCKED, DEFERRED, or NOT APPLICABLE. A green test suite is not a launch approval.

| Area | Status | Evidence |
|---|---|---|
| CODE | PASS | `python -m pytest -q` passed on the Phase 14 baseline before this record, and again after the documentation change. |
| SECURITY | PASS | `python scripts/security_scan.py` and `python scripts/verify_security_posture.py`. No new secret in Git. Runtime roles still have no administrator or restore permissions. |
| AWS | PASS | Existing API, 15 aliases, 14 protected tables, schedules, and the public-access block were read. No new stack was created. |
| COGNITO | PASS | Pool `eu-north-1_vv7adAAC9`. MFA remains OPTIONAL, software token on, SMS off. Users were not changed. |
| API | PASS | API `4c6dni17l3`, stage `dev`, is the only deployed API. Authorizer `y0hzhr`. Public GET and unauthenticated rejection were checked by the smoke script. |
| TENANT ISOLATION | PASS | Two signed-in non-pilot sessions. Each cross-tenant GET returned HTTP 403 `Organization access denied`. No write was sent. |
| BILLING | BLOCKED | Application billing works in Razorpay test mode. Production billing is not provisioned. |
| RAZORPAY | BLOCKED | The provider client accepts only a key id that starts with `rzp_test_` and only secret `erap/billing/razorpay/test`. No production plans or secret exist in this repository. |
| WEBHOOK | BLOCKED | The test webhook path verifies signatures. A production webhook secret and dashboard endpoint were not configured. |
| FRONTEND | PASS | Amplify `https://main.d3enpe7opotop5.amplifyapp.com` is the deployed site. Help text says the provider integration is the Razorpay test integration. |
| DOMAIN | DEFERRED | No custom domain is configured. Launch on the current Amplify hostname is possible later. A custom domain is not a code defect. |
| CORS | PASS | Allowed origin is the Amplify URL. `verify_hardening.py` checks it. `*` is not used with credentials. |
| OBSERVABILITY | PASS | Structured logs from Phase 11B. Retention is 30 days. No new dashboard. |
| DR | PASS | PITR and deletion protection on all 14 tables. `python scripts/verify_recovery.py`. No second restore in this phase. |
| ROLLBACK | PASS | `restore_target` still refuses to move `live` when a newer version owns it. `tests/test_alias_restore.py`. No production rollback was performed. |
| SUPPORT | PASS | `docs/production-support.md`. Support uses organization id, correlation id, and entity ids. |
| LEGAL | BLOCKED | V1 drafts exist under `docs/legal/`. FINAL COMPANY/LEGAL APPROVAL is not in the repository. |
| PRICING | PASS | This closure uses the catalog: ₹999 monthly and ₹9,999 yearly. The amounts were not changed. |
| CUSTOMER SCOPE | PASS | Controlled limited beta. Organizations are onboarded deliberately. Public registration was not opened. |
| SMOKE TEST | PASS | Unauthenticated public GET returned 200 and unauthenticated organization GET returned 401. Signed-in reads for both non-pilot organizations returned 200. Live organization creation was not performed. |
| MONITORING | PASS | `ERAP-Production-Alarms` keeps its one confirmed email subscription. This closure uses that destination. The address is not recorded here. No new subscription was created. |
| APPROVAL | BLOCKED | Phase 15 decision is NO-GO. Paid production is not authorized. |

Do not open ERAP to customers from this checklist.
