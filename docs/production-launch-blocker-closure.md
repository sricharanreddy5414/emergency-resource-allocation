# Production launch blocker closure

This record closes the Phase 15 blocker list where a safe check or an existing fact can close it. It does not launch ERAP. The launch decision remains NO-GO.

No production plan, secret, webhook, payment, or organization was created. No business decision was invented.

## Blocker matrix

| Blocker | Current status | Required action | Human decision required? | Safe to proceed? |
|---|---|---|---|---|
| Production Razorpay | DECISION REQUIRED | Approve production pricing, then provision separate plans, a secret, and a webhook outside Git | Yes | No |
| Legal documents | DECISION REQUIRED | Supply approved terms, privacy, and refund or cancellation text | Yes | No |
| Launch scope | DECISION REQUIRED | Name internal, invited, limited beta, or public | Yes | No |
| Production pricing | DECISION REQUIRED | Approve or replace the catalog amounts before any production plan exists | Yes | No |
| Tenant isolation | RESOLVED | None for the read denial already observed | No | Yes |
| Authenticated smoke | RESOLVED | None for the read-only GETs already observed | No | Yes |
| Live onboarding | ONBOARDING VALIDATION BLOCKED | A disposable organization with a safe cleanup path | Yes | No |
| Alerting | DECISION REQUIRED | Confirm the existing subscription or name another destination outside Git | Yes | No |
| PAST_DUE writes | DECISION REQUIRED | Accept or change the current write rule | Yes | No |
| Cancellation policy | DECISION REQUIRED | Approve the current cancel-at-period-end behavior or replace it | Yes | No |
| Refunds | DECISION REQUIRED | Accept a manual support process or request a separate engineering task | Yes | No |
| Custom domain | DEFERRED | None, unless the business later requires a hostname | No | Yes |

## Blocker 1 — production Razorpay

Status: DECISION REQUIRED.

Evidence: `src/billing/provider/razorpay.py` accepts only secret `erap/billing/razorpay/test` and a key id that starts with `rzp_test_`. Any other secret id raises a billing configuration error. The only plan ids in code are `plan_ThiWT35Gf1jyio` and `plan_ThiWTXOzBHl2Qb`. Secrets Manager, by name only, contains `erap/billing/razorpay/test` and no production billing secret.

Action: none. Production plans were not created because production pricing is not approved. Test plan ids were not copied into a production configuration. The test secret was not read and was not printed.

Result: production billing is not provisioned. TEST and production are separated by rejection of every non-test secret and key, not by a second configured environment.

Remaining decision: PRODUCTION PRICING DECISION REQUIRED. After that, production plans, a separate secret, and a separate webhook have to be created in the provider dashboard. Do not paste those values into chat or into source.

## Blocker 2 — legal and commercial documents

Status: DECISION REQUIRED.

A repository search found no terms, privacy policy, or refund or cancellation policy. None were written.

The implemented cancellation behavior is owner cancellation, cancel at period end, status stays `ACTIVE`, operational writes continue, and the expiry worker later sets `CANCELLED`. No approved policy says that this is the commercial rule.

Refund automation is not implemented. No decision records that refunds stay a manual support process.

Remaining decisions:

- LEGAL DOCUMENTS DECISION REQUIRED
- CANCELLATION POLICY DECISION REQUIRED
- Refunds: either record "Refund automation is not implemented; support/manual process required." or request a separate engineering task. This file does not choose.

## Blocker 3 — launch scope

Status: DECISION REQUIRED.

No document names an approved scope of internal, invited customers, controlled beta, limited production, or public. Registration and authentication were not changed.

Remaining decision: LAUNCH SCOPE DECISION REQUIRED.

## Blocker 4 — production pricing

Status: DECISION REQUIRED.

`src/billing/plans.py` still prices `MONTHLY` at ₹999 and `YEARLY` at ₹9,999. Those catalog amounts were not changed. They are not an approval to charge them in production.

Remaining decision: PRODUCTION PRICING DECISION REQUIRED.

## Blocker 5 — tenant isolation

Status: RESOLVED for read-only cross-tenant access.

Two browser sessions were already signed in. Session A had only `ORG-17D0E2939B2D`. Session B had only `ORG-A66B0A1E4F96`. Each organization selector listed that one organization and was disabled, so the selector could not switch tenants. No write, create, or update was sent. The pilot organization was not requested.

Session A calling Session B, every response HTTP 403 with message `Organization access denied`:

- resources
- requests
- allocations
- locations
- exchange requests
- notifications
- billing
- organization members

Session B calling Session A returned the same HTTP 403 and the same message on those eight reads.

Each session's own `GET /organization` returned HTTP 200 and only that session's organization id.

Inactive membership and a non-ACTIVE organization status were not changed and were not live-tested. Existing server tests cover those cases. No customer row was modified.

## Blocker 6 — authenticated smoke and onboarding

Authenticated smoke status: RESOLVED.

Unauthenticated `GET /public/resources` returned HTTP 200 with the Amplify origin. Unauthenticated `GET /organization` returned HTTP 401.

Session A, `ORG-17D0E2939B2D`, own reads:

| Read | HTTP |
|---|---|
| organization | 200 |
| resources | 200 |
| requests | 200 |
| allocations | 200 |
| locations | 200 |
| exchange | 200 |
| notifications | 200 |
| billing | 200, `TRIALING`, plan `FREE_TRIAL` |

Session B, `ORG-A66B0A1E4F96`, own reads:

| Read | HTTP |
|---|---|
| organization | 200 |
| resources | 200 |
| requests | 200 |
| allocations | 200 |
| locations | 200 |
| exchange | 200 |
| notifications | 200 |
| billing | 200, `GRANDFATHERED` |

No resource, request, allocation, offer, exchange, payment, or subscription was created.

Onboarding status: ONBOARDING VALIDATION BLOCKED.

Creating an organization writes a permanent organization, owner membership, and trial. The repository has no safe cleanup for that write, and a delete endpoint was not added. The existing non-pilot organizations were not modified to simulate onboarding. The 15-day trial length remains the unit-tested rule in the billing code. It was not re-measured by creating a trial.

## Blocker 7 — alerting

Status: DECISION REQUIRED.

`ERAP-Production-Alarms` has one subscription. Protocol is email. It is confirmed. The address is not recorded in the repository and is not printed here. No document approves that subscription as the launch destination. No new topic, alarm, or subscription was created.

Remaining decision: ALERT DESTINATION DECISION REQUIRED. Confirm that existing subscription, or name a different destination outside Git.

## PAST_DUE policy

Status: DECISION REQUIRED.

A provider cancellation while the row is `PAST_DUE` is ignored. Operational writes stay allowed. No approved decision changes that rule, so the code was not changed.

## Production billing separation

The running API is `4c6dni17l3`, stage `dev`. That stage is the API the Amplify app calls. A second API was not created.

The frontend sends an application plan. The backend maps `MONTHLY` and `YEARLY` to the test plan ids above. The client cannot supply a provider plan id. Production mode is not configured. Checkout and the webhook loader refuse a secret id other than the test secret.

## Domain, CORS, and Cognito

Domain: DEFERRED. No custom domain is associated with Amplify app `d3enpe7opotop5`. The current launch URL remains `https://main.d3enpe7opotop5.amplifyapp.com`. No document requires a different hostname.

CORS: the allowed origin is that Amplify URL. Hardening verification reported the origin check as ok. A wildcard origin is not used.

Cognito pool `eu-north-1_vv7adAAC9` is unchanged. MFA is OPTIONAL, software token on, SMS off. Password minimum length is 8. Account recovery is verified email, then verified phone. The frontend app client allows the Amplify origin plus localhost callback URLs and the Amplify logout URL. Users were not changed.

The deployed Amplify page contains the sentence that the current provider integration is the Razorpay test integration. An already open browser tab still had the previous help sentence in its cached document.

## Security and rollback

`python scripts/security_scan.py`, `python scripts/verify_security_posture.py`, and `python scripts/verify_hardening.py` passed on this baseline before the documentation update. S3 public access block, PITR, deletion protection, and runtime role limits were part of that read. Webhook signature checks and `access.py` were not weakened.

`restore_target` in `scripts/set_live_version.py` still returns the previous version only when the current alias is the version that same job published. `tests/test_alias_restore.py` covers that refusal. No alias was moved for this record.

## Final status

NO-GO.

Resolved in this pass: live cross-tenant read denial, and authenticated read-only smoke for the two existing non-pilot organizations.

Still required before a launch decision can be GO:

- PRODUCTION PRICING DECISION REQUIRED
- LEGAL DOCUMENTS DECISION REQUIRED
- CANCELLATION POLICY DECISION REQUIRED
- LAUNCH SCOPE DECISION REQUIRED
- ALERT DESTINATION DECISION REQUIRED
- PAST_DUE POLICY DECISION REQUIRED
- ONBOARDING VALIDATION BLOCKED
- Production Razorpay plans, secret, and webhook, after the pricing decision

Do not open ERAP to customers from this file.

## Final closure pass

A later pass searched again for an approved production price, launch scope, legal text, alert destination, and `PAST_DUE` rule. None of those approvals exist. The catalog amounts in `src/billing/plans.py` remain ₹999 monthly and ₹9,999 yearly. `docs/saas-commercial-readiness.md` verifies that catalog. It does not approve those amounts for production charging. Production Razorpay plans, a production secret, and a production webhook were not created. The test secret and the test plan ids were not reused.

`docs/legal-launch-requirements.md` lists the missing terms, privacy policy, cancellation policy, and refund status. It does not supply those documents.

Cancellation stays the implemented owner path: cancel at period end, status stays `ACTIVE`, writes continue, and the expiry worker later sets `CANCELLED`. That is not an approved policy.

Refund automation is still not implemented. No sentence in the repository accepts a manual refund process as the launch rule.

`ERAP-Production-Alarms` still has one confirmed email subscription. The address is not named here and is not an approved destination.

Organization creation still writes a permanent organization, owner membership, and trial. No delete path exists for that write, so no organization was created.

The launch decision remains NO-GO.
