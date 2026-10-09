# Production readiness checklist

Phase 13. Status is READY, BLOCKED, DEFERRED, or NOT APPLICABLE. READY means the control was inspected and a check passed in this phase, or the deployed behavior is covered by the test suite and a live read-only verifier. A live mutation was not repeated here. The 28 September 2026 snapshot that used to live in this file is history in Git.

Production launch is not decided by this list. See `docs/production-launch-gate.md`.

## SECURITY

| Item | Status | Evidence |
|---|---|---|
| Authentication configuration | READY | Authorizer `y0hzhr`. MFA OPTIONAL, software token on, SMS off. `verify_security_posture.py`. |
| Signed-in application shell | READY | `https://main.d3enpe7opotop5.amplifyapp.com` loaded the operations page with the organization selector. It was not the login form. |
| Organization selection this phase | DEFERRED | The selector was still disabled and showed awaiting organization. No organization was chosen, and no write was made. |
| Authorization | READY | `src/shared/access.py` and existing tests. Model unchanged. |
| Tenant isolation live cross-read | DEFERRED | Needs two signed-in sessions. Tests cover denial. Pilot was not queried. |
| IAM | READY | Runtime roles have no administrator or restore permissions. `verify_security_posture.py`. |
| Secrets | READY | Secret name only. Scan passed. Value not printed. |
| CORS | READY | Amplify origin only. `verify_hardening.py`. |

## DATA

| Item | Status | Evidence |
|---|---|---|
| PITR | READY | All 14 tables. `verify_hardening.py`. |
| Deletion protection | READY | Same check. |
| Recovery procedure | READY | `docs/disaster-recovery.md`. Notifications restore was done in Phase 11C and was not repeated. |

## APPLICATION

| Item | Status | Evidence |
|---|---|---|
| Resources | READY | Deployed aliases and tests. No new live write. |
| Requests | READY | Same. |
| Allocation | READY | Same. Auto-release rule verified. |
| Exchange | READY | Alias and hourly schedule verified. No exchange row edited. |
| Notifications | READY | Alias, TTL design, unread index documented. No new event type. |
| QR | READY | Covered by tests. No new QR session. |
| Billing | READY | Status sets and webhook rejection covered by code and tests. No payment and no edit of the existing test subscription. |

## INFRASTRUCTURE

| Item | Status | Evidence |
|---|---|---|
| API Gateway | READY | API `4c6dni17l3` stage `dev`, current deployment `59k29o`. The Phase 13 baseline was `tr1rz2`. |
| Lambda | READY | 15 functions, Python 3.14, alias `live`. |
| Cognito | READY | Pool exists. Users and MFA unchanged. |
| DynamoDB | READY | 14 tables protected. |
| EventBridge | READY | Two schedules and the auto-release rule. |
| CloudWatch | READY | Live log groups that already have retention keep 30 days. The repository policy for reservation-expiry is 30 days, and that live retention is not applied yet. Structured fields in `docs/observability.md`. |
| S3 | READY | Public access block on. Bucket is not on the request path. |
| Alarm email | READY | `ERAP-Production-Alarms` has one confirmed email subscription. The address is not recorded here. |
| Dashboards and custom metrics | NOT APPLICABLE | Out of scope for this phase. |

## DEPLOYMENT

| Item | Status | Evidence |
|---|---|---|
| Tests | READY | `python -m pytest -q` at baseline, 627 passed, before Phase 13 files. |
| Package check | READY | `python scripts/package_lambdas.py --check`. |
| Security check | READY | `python scripts/security_scan.py` and posture script. |
| Hardening | READY | `python scripts/verify_hardening.py`. |
| Recovery | READY | `python scripts/verify_recovery.py`. |
| Readiness script | READY | `python scripts/verify_production_readiness.py` after this commit's checks. |
| Automatic deploy of nine functions | READY | Push to `main` runs Deploy backend. |
| Alias verification | READY | `get-alias` plus recovery script. |
| Smoke test | READY | `python scripts/smoke_test.py` and `python scripts/smoke_test_production.py`. |
| Owned-alias restore | READY | `scripts/set_live_version.py` restores only the version that job published. |
| One version number for every function | DEFERRED | `--version` still applies one number to all of `PACKAGES`. |

## OPERATIONS

| Item | Status | Evidence |
|---|---|---|
| Incident response | READY | `docs/incident-response.md`. |
| Rollback | READY | `docs/rollback.md`. |
| Failure handling | READY | `docs/failure-runbook.md`. |
| Support | READY | `docs/production-support.md`. |
| On-call vendor and status page | NOT APPLICABLE | Not part of ERAP. |
