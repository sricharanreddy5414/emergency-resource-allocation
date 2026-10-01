# Production launch checklist

Phase 15. This is the gate record. It is not a launch.

Final decision: PHASE 15 = GO. LAUNCH SCOPE = CONTROLLED LIMITED BETA. The earlier closure decision was NO-GO.

Status values are PASS, FAIL, BLOCKED, DEFERRED, or NOT APPLICABLE. A green test suite is not a launch approval.

| Area | Status | Evidence |
|---|---|---|
| CODE | PASS | `python -m pytest -q` passed on the Phase 14 baseline before this record, and again after the documentation change. |
| SECURITY | PASS | `python scripts/security_scan.py` and `python scripts/verify_security_posture.py`. No new secret in Git. Runtime roles still have no administrator or restore permissions. |
| AWS | PASS | Existing API, 15 aliases, 14 protected tables, schedules, and the public-access block were read. No new stack was created. |
| COGNITO | PASS | Pool `eu-north-1_vv7adAAC9`. MFA remains OPTIONAL, software token on, SMS off. Users were not changed. |
| API | PASS | API `4c6dni17l3`, stage `dev`, is the only deployed API. Authorizer `y0hzhr`. Public GET and unauthenticated rejection were checked by the smoke script. |
| TENANT ISOLATION | PASS | Two signed-in non-pilot sessions. Each cross-tenant GET returned HTTP 403 `Organization access denied`. No write was sent. |
| BILLING | PASS | Production mode is deployed on `erap-billing` live version 9. Checkout was not called for the new organization. |
| RAZORPAY | PASS | Production and test secrets stay separate. Production plans are `plan_TiMn4MluXeOMK1` and `plan_TiMpOnO7K5GT0Q`. |
| WEBHOOK | PASS | The production webhook uses the existing public route and the production webhook secret. |
| FRONTEND | PASS | Amplify `https://main.d3enpe7opotop5.amplifyapp.com` is the deployed site. Help text says production checkout uses the production provider and does not fall back to the test provider. |
| DOMAIN | DEFERRED | No custom domain is configured. Launch on the current Amplify hostname is possible later. A custom domain is not a code defect. |
| CORS | PASS | Allowed origin is the Amplify URL. `verify_hardening.py` checks it. `*` is not used with credentials. |
| OBSERVABILITY | PASS | Structured logs from Phase 11B. Retention is 30 days. No new dashboard. |
| DR | PASS | PITR and deletion protection on all 14 tables. `python scripts/verify_recovery.py`. No second restore in this phase. |
| ROLLBACK | PASS | `restore_target` still refuses to move `live` when a newer version owns it. `tests/test_alias_restore.py`. No production rollback was performed. |
| SUPPORT | PASS | `docs/production-support.md`. Support uses organization id, correlation id, and entity ids. |
| LEGAL | PASS | COMPANY APPROVAL = CONFIRMED for the current V1 launch documents. Legal counsel approval is not claimed. |
| PRICING | PASS | This closure uses the catalog: ₹999 monthly and ₹9,999 yearly. The amounts were not changed. |
| CUSTOMER SCOPE | PASS | Controlled limited beta. Organizations are onboarded deliberately. Public registration was not opened. |
| SMOKE TEST | PASS | Unauthenticated billing returned 401. A non-member organization id returned 403. The new organization's billing, resources, requests, locations, and notifications returned 200. |
| MONITORING | PASS | `ERAP-Production-Alarms` keeps its one confirmed email subscription. This closure uses that destination. The address is not recorded here. No new subscription was created. |
| APPROVAL | PASS | COMPANY APPROVAL = CONFIRMED. PHASE 15 = GO for the controlled limited beta. Legal counsel approval is not claimed. |

Do not open ERAP to customers from this checklist.
