# Production launch gate

Preparation only. This document does not launch ERAP.

Production launch is NOT authorized by Phase 13.

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

Do not start that launch from this file.
