# Resource Exchange Network — Architecture Lock (Phase 5A / 5A.1)

**Status:** DESIGN LOCKED — implementation not started.  
**Document phase:** 5A.1 refinement of 5A.  
**Branch baseline:** `feature/resource-management-2` @ `b27e2d537ff00b79c2edadeae5136949e8e9bfba`  
**Audit date:** 2026-09-29  
**Source of truth:** live code under `src/`, `frontend/`, `scripts/lambda_manifest.py`, and tests.

This document supersedes informal 5A drafts where they conflict. It does **not** authorize table creation, GSIs, routes, deploys, or code changes.

---

## 1. Executive summary

ERAP today: same-tenant everyday management, same-tenant emergency allocation, anonymous PUBLIC discovery. Cross-org exchange does not exist.

**Locked V1 architecture**

| Decision | Lock |
|---|---|
| Soft hold on offer create | **Forbidden.** Offer ≠ hold. |
| Hold creation | **Only on offer acceptance**, atomic with state transitions |
| Resource lifecycle statuses | **Unchanged.** No `EXCHANGE_HELD` |
| Exchange commitment | `Allocations` with `allocation_type=EXCHANGE`, status `OPEN` |
| Ownership / location transfer | **Only after confirmed handover** (COMPLETED) |
| NETWORK | Never written to `PublicDiscoveryIndex` |
| Persistence | New table `ResourceExchanges` (META + OFFER items) |
| GSIs | Exactly **3**, each tied to a proven access pattern (§21) |
| Runtime | Dedicated Cognito Lambda `erap-exchange` + `/exchange/*` |
| Emergency / everyday | Must not claim exchange-held stock |
| Billing | Existing operational write gate only |
| Money / ratings / public bidding | Out of scope |

---

## 2. Product scope

Everyday Resource Management  
\+ Emergency Coordination  
\+ **Trusted Organization-to-Organization Resource Exchange**  
\+ Controlled Public Resource Discovery

V1 rules:

- Only verified ERAP organizations (Cognito + ACTIVE membership).  
- Org A posts a need; eligible orgs discover NETWORK requests; Org B offers an eligible resource; A accepts/rejects; accepted exchange tracks handover; ownership stays explicit until handover confirm.  
- Availability protected from concurrent use after accept.  
- Important actions audited.  
- Cross-tenant authorization enforced only by backend.  
- No anonymous users, public bidding, inter-org payments, or ratings.

---

## 3. Non-goals

- Marketplace / unauthenticated discovery of exchange inventory  
- Soft-locking resources at offer time  
- `EXCHANGE_HELD` operational status  
- Ownership change at accept  
- Payments, Razorpay, escrow, refunds  
- Ratings / reputation  
- Auto-matching network needs to stock  
- Cross-org emergency matching  
- QR (later)  
- Pilot `ORG-D13B30D99127` / `PILOT-MED-*` mutation or migration  
- Frontend shell redesign beyond an Operations Exchange area  
- Extra GSIs “for later”

---

## 4. Existing architecture reused

| Area | Code truth | Exchange use |
|---|---|---|
| Auth | Cognito ID token; authorizer `y0hzhr`; `get_user_sub` | Required on all `/exchange/*` |
| Tenant | `authorize()` + ACTIVE membership; `organization_id` selects membership | Always; forged org ids fail |
| Roles | `READ_ROLES`, `OPERATE_ROLES` | §17 matrix |
| Billing writes | `is_operational_write_allowed` — missing/`TRIALING`/`ACTIVE`/`PAST_DUE`/`GRANDFATHERED`; block `CANCELLED`/`EXPIRED` | All exchange writes |
| Resources | PK `resource_id`; `organization_id`; `location_id`; `Available`; `operational_status`; INDIVIDUAL\|QUANTITY | Hold via Available/qty; ownership fields only at COMPLETED |
| Visibility | `PRIVATE`\|`PUBLIC` today; PUBLIC → `PublicDiscoveryIndex` | Add `NETWORK`; **never** set `visibility_key` |
| Everyday | `allocation_type=EVERYDAY`, `EVERYDAY-…` ids | Unchanged; parallel EXCHANGE type |
| Lifecycle | AVAILABLE…RETIRED map in `resource_state.py` | Unchanged set of statuses |
| Emergency | same-org matcher; needs `Available=true` | Loses to EXCHANGE hold |
| Allocations | PK `allocation_id`; org index; auto-release uses `AllocationStatusIndex` | EXCHANGE hold rows; OPEN status |
| History | `ResourceStatusHistory` | Only when resource row actually changes |
| Audit | `AuditEvents` / `build_audit_event` | Dual-org where both parties affected |
| Scheduled jobs | Billing expiry + emergency `auto_release` pattern | Exchange expiry sweeper (later phase) |
| Public API | `GET /public/resources` | Unchanged; excludes NETWORK |

**Not reused as request store:** `EmergencyRequests` (emergency lifecycle only).

---

## 5. NETWORK definition

| Visibility | Audience | Index |
|---|---|---|
| `PRIVATE` | Owning org only | No public/network discovery index |
| `PUBLIC` | Anonymous public discovery | `visibility_key=PUBLIC` → `PublicDiscoveryIndex` |
| `NETWORK` | Eligible verified ERAP orgs for **exchange** | **No** `PublicDiscoveryIndex`. No `visibility_key=PUBLIC`. |

**NETWORK MUST NEVER** be inserted into `PublicDiscoveryIndex`.

### Offer eligibility (checked at offer create *and* re-checked at accept)

1. Provider org owns the resource (`organization_id` match).  
2. `visibility == NETWORK`.  
3. Not `RETIRED`.  
4. Individual: `AVAILABLE` and `Available == true`.  
5. Quantity: `quantity_available >= quantity_offered`.  
6. No open EVERYDAY / emergency ALLOCATED / EXCHANGE OPEN hold conflicting with the offer.  
7. Provider OPERATE+ and operational write billing allowed.

### Network request browse eligibility

1. Authenticated ACTIVE member of an org ≠ requester.  
2. Request `status == OPEN` and not expired.  
3. Operational read policy (§18).

### What another org may see (network / counterpart projections)

Allowed: exchange_request_id, status, resource type **name**, tracking_mode, quantity requested/offered, destination city/state (optional), expiry, provider/requester **organization display name**, resource display name, condition (optional), high-level availability at offer time.

**Never expose in network projections:** serial_number, asset_tag, assigned_to, responsible_team, department internals, member emails/subs, private attributes map, full audit streams, unrelated org resources, Cognito identifiers.

---

## 6. Request lifecycle

### States (locked)

| State | Meaning |
|---|---|
| `OPEN` | Listed on network; offers allowed |
| `ACCEPTED` | One offer accepted; EXCHANGE hold active; transfer not started |
| `TRANSFER_PENDING` | Provider started transfer / handover in progress |
| `COMPLETED` | Handover confirmed; ownership (+ location) transferred; immutable |
| `CANCELLED` | Terminal; holds released if any |
| `EXPIRED` | Terminal; holds released if any |

No `DRAFT`. No `OFFERED` request state (offer count is derived). No `EXCHANGE_HELD` resource status.

### Transitions

| From | To | Actor | Required conditions |
|---|---|---|---|
| — | OPEN | Requester OPERATE+ | Valid destination location; billing write |
| OPEN | ACCEPTED | Requester OPERATE+ | Atomic accept (§8) |
| OPEN | CANCELLED | Requester OPERATE+ | No accepted offer |
| OPEN | EXPIRED | Expiry worker / lazy | `expires_at` passed |
| ACCEPTED | TRANSFER_PENDING | Provider OPERATE+ | Hold still OPEN; request ACCEPTED |
| ACCEPTED | CANCELLED | Requester or Provider OPERATE+ | Release hold atomically |
| ACCEPTED | EXPIRED | Expiry worker | Handover SLA; release hold |
| TRANSFER_PENDING | COMPLETED | Requester OPERATE+ | Atomic ownership+location transfer |
| TRANSFER_PENDING | CANCELLED | Requester or Provider OPERATE+ | Release hold; **no** ownership change |
| TRANSFER_PENDING | EXPIRED | Expiry worker | Same as cancel + release |

**Invalid:** COMPLETED→*; skip to COMPLETED from OPEN; provider COMPLETE; requester TRANSFER_PENDING; accept when not OPEN.

Audit on each transition: §19.

---

## 7. Offer lifecycle

| State | Meaning |
|---|---|
| `OPEN` | Waiting on requester; **resource not held** |
| `ACCEPTED` | Selected; paired with request ACCEPTED + hold |
| `REJECTED` | Explicit reject; request stays OPEN |
| `WITHDRAWN` | Provider withdrew while OPEN |
| `SUPERSEDED` | Sibling offer lost the accept race |
| `EXPIRED` | Past expiry while still OPEN |
| `CANCELLED` | Parent request cancelled while offer OPEN |

### Decision 1 — NO soft hold at offer creation (LOCKED)

```
OFFER CREATED  ≠  RESOURCE HELD
```

Creating an offer does **not** change `Available`, `operational_status`, quantity counters, or Allocations.

Until acceptance, the provider’s resource remains usable for reservation, everyday allocate, emergency allocate, maintenance, etc. If those succeed first, later acceptance fails safely (§8).

Reason: speculative offers must not lock inventory.

---

## 8. Acceptance / hold semantics (LOCKED)

### Decision 2 — Atomic hold only at acceptance

Accept must **atomically** verify and commit:

1. Offer `status == OPEN`  
2. Request `status == OPEN`  
3. Resource still owned by `provider_organization_id`  
4. Resource still eligible (§5)  
5. Not retired / reserved / allocated / in-use / maintenance / damaged  
6. Not already under another EXCHANGE OPEN hold  
7. Quantity still sufficient when QUANTITY  
8. Then create EXCHANGE hold + request ACCEPTED + offer ACCEPTED + supersede sibling OPEN offers  

### Transactional concept (not implemented yet)

Prefer a single DynamoDB `TransactWriteItems` (or equivalent all-or-nothing pattern) including at least:

| Item | Operation | Condition (concept) |
|---|---|---|
| Request META | SET status=ACCEPTED, accepted_offer_id, … | `status = OPEN` |
| Accepted offer | SET status=ACCEPTED | `status = OPEN` |
| Each other OPEN offer on request | SET status=SUPERSEDED | `status = OPEN` (or batch with care) |
| Resources (individual) | SET Available=false, operational_status=ALLOCATED | org + Available=true + AVAILABLE status |
| Resources (quantity) | SET quantity_available-=n, quantity_allocated+=n | org + quantity_available>=n |
| Allocations | Put EXCHANGE OPEN hold | `attribute_not_exists(allocation_id)` |

If any condition fails → **entire accept fails**; no ACCEPTED request without hold; no hold without ACCEPTED request; no partial SUPERSEDED-only side effects.

### Decision 3 — No `EXCHANGE_HELD` lifecycle status (LOCKED)

Operational statuses remain:

`AVAILABLE | RESERVED | ALLOCATED | IN_USE | MAINTENANCE | DAMAGED | RETIRED`

Exchange commitment is **`Allocations.allocation_type = EXCHANGE`** with `status = OPEN`, plus the normal unavailability side effects:

- Individual: `Available = false`, `operational_status = ALLOCATED` (same pattern as everyday allocate).  
- Quantity: move units `available → allocated` (same pattern as everyday quantity allocate).

**Why this preserves compatibility**

| Path | Why safe |
|---|---|
| Emergency matcher | Requires `Available == true`; held individual fails |
| Everyday allocate / reserve | Conditional on AVAILABLE / Available true / qty available |
| Lifecycle maint/damage/retire | Must treat EXCHANGE OPEN like everyday OPEN hold (`_require_no_active_holds` extended in impl) |
| Existing transition map | No new status to teach every caller |
| Auto-release | Must **not** treat EXCHANGE like emergency ALLOCATED expiry without exchange-aware rules (impl: exclude `allocation_type=EXCHANGE` from emergency auto-release) |

---

## 9. Ownership model (LOCKED)

### Decision 4 — Acceptance does **not** transfer ownership

```
OPEN offer
  → requester accepts
  → atomic EXCHANGE hold
  → request ACCEPTED
  → provider starts transfer → TRANSFER_PENDING
  → requester confirms handover
  → request COMPLETED
  → ownership transfer (+ location)
```

Until COMPLETED: `Resources.organization_id` remains the **provider**.

At COMPLETED, record and apply:

| Field | Value |
|---|---|
| previous owner | provider `organization_id` |
| new owner | requester `organization_id` |
| source organization | provider |
| destination organization | requester |
| source location | resource `location_id` at hold time |
| destination location | request `destination_location_id` |
| transfer timestamp | `completed_at` |
| confirming actor | requester `sub` + role |

COMPLETED is immutable for normal APIs (no cancel, no second handover).

---

## 10. Location transfer (LOCKED)

### Decision 5

| Phase | `organization_id` | `location_id` / `Location` |
|---|---|---|
| Before handover | Provider | Provider source location |
| After confirmed handover | Requester | Requester destination location |

Do **not** change the Locations table model (`organization_id` + `location_id`).

**Atomic with ownership:** same transaction/step as COMPLETED must:

1. Re-read destination location: exists, `organization_id == requester`, `status == ACTIVE`.  
2. If invalid/inactive → **fail COMPLETED**; leave request in TRANSFER_PENDING; hold remains; no ownership change.  
3. If valid → set resource `organization_id`, `location_id`, `Location` (name copy), restore individual to AVAILABLE (or apply quantity complete accounting), close EXCHANGE allocation.

After transfer, force visibility to **PRIVATE** under new owner unless explicitly re-published (avoids NETWORK/PUBLIC leaking under wrong tenant assumptions). Clear PUBLIC index attributes if present.

---

## 11. Individual resources

- At most one ACCEPTED/TRANSFER_PENDING exchange per `resource_id`.  
- Offer create: metadata only.  
- Accept: Available false + ALLOCATED + `allocation_id` e.g. `EXCHANGE-{exchange_request_id}` (not `ALLOC-*`, not `EVERYDAY-*`).  
- Complete: ownership/location update; allocation → terminal (`RELEASED` or `COMPLETED`); resource AVAILABLE under requester.  
- Cancel/expire after accept: reverse hold → AVAILABLE under provider; allocation closed.

---

## 12. Quantity resources

### Decision 8 — accounting (LOCKED)

Offer must include `quantity_offered` (≥ 1).

**No new permanent counter** (no `quantity_exchange_held` column).

Exchange-held quantity uses existing **`quantity_allocated`** (same as everyday quantity allocate):

| Event | Provider effect |
|---|---|
| Offer create | none |
| Accept | `quantity_available -= n`; `quantity_allocated += n` iff `quantity_available >= n` |
| Complete | `quantity_allocated -= n`; `quantity_total -= n`; requester pool `quantity_total += n`, `quantity_available += n` (create pool if missing) |
| Cancel/expire after accept | reverse accept: `quantity_allocated -= n`; `quantity_available += n` |

**Invariants (always):**

```
quantity_available >= 0
quantity_reserved >= 0
quantity_allocated >= 0
quantity_available + quantity_reserved + quantity_allocated == quantity_total
```

Accept/complete/cancel must use conditional updates (and transactions across exchange META + resource rows) so totals cannot go negative or desync.

---

## 13. Emergency / everyday concurrency

### Decision 6 — concurrency matrix (LOCKED)

| Resource situation | Emergency allocate | Everyday reserve/allocate | Exchange accept | Maint/damage/retire |
|---|---|---|---|---|
| AVAILABLE, no holds | may win | may win | may win | allowed |
| OPEN offer only (no hold) | may win | may win | **fails if stock taken** | allowed |
| EXCHANGE OPEN hold | **must fail** | **must fail** | **must fail** (2nd) | **must fail** (hold check) |
| EVERYDAY OPEN / emergency ALLOCATED | existing rules | existing rules | **must fail** | existing hold rules |
| RESERVED / IN_USE / MAINT / DAMAGED / RETIRED | fail / N/A | fail / N/A | fail | per lifecycle |

**Before accept:** normal ops remain possible; stale offers lose at accept.  
**After accept:** no workflow may silently override the EXCHANGE hold.

---

## 14. Competing offers

### Decision 7 (LOCKED)

- One request may have many OPEN offers.  
- Resources behind OPEN offers are **not** held.  
- On successful accept of offer X:  
  - X → ACCEPTED  
  - other OPEN offers on that request → SUPERSEDED  
  - **no** Resource/Allocation mutation for SUPERSEDED offers  
- Accept transaction makes dual ACCEPTED impossible (request `status=OPEN` condition).

---

## 15. Expiry

### Decision 9 (LOCKED)

Reuse scheduled-job philosophy (`auto_release` / billing expiry style): EventBridge → Lambda sweeper + **lazy expiry** on read/write paths.

| Object | Who sets `expires_at` | Default guidance | On expiry |
|---|---|---|---|
| OPEN request | Requester on create (server may cap max TTL) | e.g. 7d max | → EXPIRED; OPEN offers → EXPIRED; no resource changes |
| OPEN offer | Provider optional; capped by request expiry | ≤ request expiry | → EXPIRED; no resource changes |
| ACCEPTED | Server sets `handover_expires_at` at accept | e.g. 72h | → EXPIRED; **release EXCHANGE hold**; audit |
| TRANSFER_PENDING | May refresh or keep handover deadline | same clock | → EXPIRED; **release hold**; no ownership transfer |

Events: `exchange.expired` (audit). Resource history only if a hold is released (`RESOURCE_EXCHANGE_HOLD_RELEASED`).

Do **not** implement the worker in 5A.1.

---

## 16. Cancellation

### Decision 10 (LOCKED)

| State | Cancel allowed? | Effect |
|---|---|---|
| OPEN | Requester yes | Request CANCELLED; OPEN offers CANCELLED; no holds |
| ACCEPTED | Requester **or** provider yes | CANCELLED + atomic hold release |
| TRANSFER_PENDING | Requester **or** provider yes | CANCELLED + hold release; **no** ownership/location change |
| COMPLETED | **No** normal cancel | Immutable |
| CANCELLED / EXPIRED | No-op / 409 | — |

No refunds (no money). No admin correction workflow in V1.

---

## 17. Authorization matrix

### Decision 12 (LOCKED)

Roles: MEMBER / OPERATOR / ADMIN / OWNER. Backend always binds actions to **caller's membership org**, then checks requester vs provider role on the item.

#### Requester organization

| Action | MEMBER | OPERATOR | ADMIN | OWNER |
|---|---|---|---|---|
| Create request | no | yes | yes | yes |
| List my requests | yes | yes | yes | yes |
| View my request (+ offers) | yes | yes | yes | yes |
| Cancel (OPEN/ACCEPTED/TRANSFER_PENDING) | no | yes | yes | yes |
| Accept / reject offer | no | yes | yes | yes |
| Confirm handover | no | yes | yes | yes |
| View exchange history (participant) | yes | yes | yes | yes |

#### Offering / provider organization

| Action | MEMBER | OPERATOR | ADMIN | OWNER |
|---|---|---|---|---|
| List network OPEN requests | yes | yes | yes | yes |
| View OPEN network request projection | yes | yes | yes | yes |
| Create / withdraw OPEN offer | no | yes | yes | yes |
| View own offer | yes | yes | yes | yes |
| Start transfer (→ TRANSFER_PENDING) | no | yes | yes | yes |
| Cancel after accept / transfer_pending | no | yes | yes | yes |
| View exchange history (participant) | yes | yes | yes | yes |

#### Non-participants

| Action | Result |
|---|---|
| Mutate | 403/404 |
| Read non-OPEN detail | 404 |
| Read OPEN network list projection | allowed if eligible |

Set resource `visibility=NETWORK` via existing resource write path (OPERATE+), not a separate exchange admin desk in V1.

---

## 18. Billing enforcement

### Decision 13 (LOCKED)

Reuse `authorize(..., access="write")` / `is_operational_write_allowed`. No exchange SKU.

| Status | Network/list reads | Exchange writes (create/accept/transfer/complete/cancel/offer) |
|---|---|---|
| Missing row (treated GRANDFATHERED) | yes | yes |
| TRIALING | yes | yes |
| ACTIVE | yes | yes |
| PAST_DUE | yes | yes |
| GRANDFATHERED | yes | yes |
| CANCELLED | yes (existing read policy) | **blocked** `BILLING_REQUIRED` |
| EXPIRED | yes (existing read policy) | **blocked** `BILLING_REQUIRED` |

---

## 19. Audit and history

### Decision 14 (LOCKED)

**Audit** (`AuditEvents`) — organizational stream(s):

| Action |
|---|
| `exchange.request_created` |
| `exchange.request_cancelled` |
| `exchange.offer_created` |
| `exchange.offer_withdrawn` |
| `exchange.offer_rejected` |
| `exchange.offer_accepted` |
| `exchange.offer_superseded` |
| `exchange.transfer_started` |
| `exchange.handover_confirmed` |
| `exchange.completed` |
| `exchange.expired` |

Write **two** audit rows (requester org + provider org) when both are parties.

**Exchange history** — timeline fields on META / derived from offer items (not ResourceStatusHistory).

**Resource lifecycle history** (`ResourceStatusHistory`) — only when the resource row changes:

| Reason | When |
|---|---|
| `RESOURCE_EXCHANGE_HELD` | Accept hold applied |
| `RESOURCE_EXCHANGE_HOLD_RELEASED` | Cancel/expire after hold |
| `RESOURCE_EXCHANGE_TRANSFERRED` | COMPLETED ownership/location |

Do **not** fabricate everyday/emergency/maintenance lifecycle reasons for exchange.

---

## 20. Database model

### Decision 15 — new table `ResourceExchanges` (not created yet)

**Why new table:** `EmergencyRequests` is emergency-only; `Allocations` is the hold ledger, not a multi-offer negotiation store; `Resources` cannot host cross-tenant request discovery safely.

**Keys**

| Attribute | Role |
|---|---|
| `pk` | Partition key |
| `sk` | Sort key |

**Item shapes**

| Kind | pk | sk |
|---|---|---|
| Request META | `EXREQ#{exchange_request_id}` | `META` |
| Offer | `EXREQ#{exchange_request_id}` | `OFFER#{offer_id}` |
| Idempotency | `IDEM#{organization_id}#{idempotency_key}` | `CREATE_REQUEST` \| `CREATE_OFFER` \| … |

META attributes (core): status, requester_organization_id, resource type fields, tracking_mode, quantity_requested, destination_location_id, expires_at, handover_expires_at, accepted_offer_id, accepted_resource_id, accepted_provider_organization_id, version, created_at/by, updated_at/by, completed_at, confirming_actor_sub.

OFFER attributes (core): offer_id, provider_organization_id, resource_id, quantity_offered, status, source_location_id, safe resource_snapshot, expires_at, created_at/by.

**Allocations hold row (existing table)**

- `allocation_id`: `EXCHANGE-{exchange_request_id}`  
- `allocation_type`: `EXCHANGE`  
- `status`: `OPEN` → terminal on complete/cancel/expire  
- Links: both org ids, resource_id, offer_id, exchange_request_id, quantity  

---

## 21. GSI access-pattern proof

Only GSIs with a **required** access pattern. Speculative indexes removed.

### GSI 1 — `NetworkOpenRequestIndex`

| | |
|---|---|
| PK | `network_list_key` (sparse attribute present **only** on OPEN META; value constant `OPEN`) |
| SK | `created_at` |
| Query | `network_list_key = OPEN` (+ optional time window) |
| Caller | Any eligible authenticated org browsing network needs |
| Authz | Cognito + membership; filter out own `requester_organization_id` in app |
| Result | OPEN request projections |
| Why not existing | `EmergencyRequests` / `Resources` / `PublicDiscoveryIndex` are wrong domain or would expose PUBLIC |

On leave OPEN → remove/blank `network_list_key` so item leaves the sparse index.

### GSI 2 — `RequesterOrgIndex`

| | |
|---|---|
| PK | `requester_organization_id` |
| SK | `created_at` |
| Query | all META for “my requests” |
| Caller | Requester org members |
| Authz | `authorize` org must equal PK |
| Result | requester’s exchange requests |
| Why not existing | No exchange entity in current tables |

### GSI 3 — `ProviderOrgOfferIndex`

| | |
|---|---|
| PK | `provider_organization_id` |
| SK | `created_at` |
| Query | “my offers” across requests |
| Caller | Provider org members |
| Authz | `authorize` org must equal PK |
| Result | offer items |
| Why not existing | Offers are under `EXREQ#…` partitions; cannot list by provider without this GSI or a scan |

### Explicitly **not** designed

- ResourceId exchange GSI — V1 uses Allocations OPEN EXCHANGE (+ get resource)  
- Public/network resource browse GSI — V1 discovers **requests**, not a global NETWORK catalog  
- Offer-by-status global index — not required for V1 UX  

---

## 22. API proposal

Dedicated Lambda `erap-exchange`, Cognito authorizer. Additive routes only.

### Phase 5C implemented routes (API foundation)

| Method | Path | Purpose |
|---|---|---|
| POST | `/exchange/requests` | Create OPEN NETWORK request |
| GET | `/exchange/requests?scope=mine\|network` | List mine or network OPEN |
| GET | `/exchange/requests/{exchange_request_id}` | Requester detail or network projection |
| POST | `/exchange/requests/{exchange_request_id}/offers` | Create OPEN offer (**no hold**) |
| GET | `/exchange/requests/{exchange_request_id}/offers` | List offers (requester all / provider own) |
| GET | `/exchange/requests/{exchange_request_id}/offers/{offer_id}` | Get one offer |
| GET | `/exchange/offers?scope=mine` | Provider offers via ProviderOrgOfferIndex |

**Phase 5C semantics (locked behavior, not a redesign):**

- Requests on this API must use NETWORK visibility (PUBLIC/PRIVATE rejected).
- Network discovery uses `NetworkOpenRequestIndex`; own org excluded from `scope=network`.
- Pagination: existing `page_token` / `next_token` (`pages.py`), limit 1–50.
- **OFFER CREATION DOES NOT HOLD THE RESOURCE** — no Resources/Allocations/quantity/history mutation.
- Type matching: case-insensitive `resource_type_name` vs provider resource `Type`/`name`; cross-org type ids are not equivalent.
- Offer eligibility: `AVAILABLE` only; quantity offers cannot exceed available or requested qty; stock may change before Phase 5D accept.
- Writes: OPERATOR/ADMIN/OWNER + billing write gate. Reads: MEMBER+.
- Idempotency key on create request/offer; fingerprint conflict → 409.
- Audit: `exchange.request_created`, `exchange.offer_created`.
- `scripts/expose_exchange_routes.py` and table infra remain **not applied** (no AWS mutation in 5C).

### Phase 5D implemented route (atomic accept + hold)

| Method | Path | Purpose |
|---|---|---|
| POST | `/exchange/requests/{exchange_request_id}/offers/{offer_id}/accept` | Atomic accept + EXCHANGE hold |

**ACCEPTANCE = RESOURCE HOLD. ACCEPTANCE ≠ OWNERSHIP TRANSFER. ACCEPTANCE ≠ HANDOVER.**

**Phase 5D semantics:**

- Only the **requester** org (OWNER/ADMIN/OPERATOR + billing write gate) may accept.
- Provider / unrelated org cannot accept (404/403 per existing isolation).
- Preconditions: request OPEN, offer OPEN and belongs to request, resource still eligible (re-checked; offer create never held stock).
- **INDIVIDUAL:** `Available true→false`, `AVAILABLE→ALLOCATED` under `EMERGENCY_CLAIM_CONDITION` (same race boundary as emergency allocate).
- **QUANTITY:** `quantity_available -= n`, `quantity_allocated += n` with `quantity_available >= n`; `quantity_total` unchanged. No full-pool ALLOCATED for partial qty.
- Allocation: existing Allocations table; `allocation_id = EXCHANGE-{offer_id}`; `allocation_type = EXCHANGE`; `status = OPEN`; refs request/offer/provider/requester/resource/qty/location.
- Request `OPEN→ACCEPTED`; accepted offer `OPEN→ACCEPTED`; other OPEN offers on the request `OPEN→SUPERSEDED` (no resource writes for superseded).
- Ownership, `location_id`, visibility, PublicDiscoveryIndex: **unchanged**.
- Audit: `exchange.offer_accepted` (requester) + `resource.exchange_allocated` (provider). History: `RESOURCE_EXCHANGE_ALLOCATED`.
- Idempotent retry: same offer already ACCEPTED → 200 existing state; different offer → 409.
- Concurrent accept / emergency / everyday / reserve: loser gets **409**; no partial mutation.

**Transaction-size strategy (DynamoDB TransactWriteItems ≤ 100 items):**

Accept transaction always includes: META + accepted offer + resource + allocation Put (± up to **40** competing SUPERSEDED updates in-transaction). Remaining OPEN siblings are superseded with conditional follow-up updates (`status = OPEN` only). Documented max in-transaction supersede: `MAX_SUPERSEDE_IN_ACCEPT_TRANSACTION = 40`. Critical hold consistency never depends on the follow-up path.

Deferred to later phases: cancel, reject, withdraw, transfer start, handover confirm, expiry.

### Later-phase routes (not implemented in 5D)

| Method | Path | Purpose |
|---|---|---|
| POST | `/exchange/requests/{id}/cancel` | Cancel |
| POST | `/exchange/offers/{id}/reject` | Reject |
| POST | `/exchange/offers/{id}/withdraw` | Withdraw |
| POST | `/exchange/requests/{id}/transfer/start` | Transfer start |
| POST | `/exchange/requests/{id}/handover/confirm` | Handover + ownership |

Errors: 401 / 403 (+ `BILLING_REQUIRED`) / 404 (hide cross-tenant) / 409 / 400.

---

## 23. Idempotency

### Decision 16 (LOCKED)

| Operation | Mechanism |
|---|---|
| Create request / offer | Required `idempotency_key`; conditional put on `IDEM#…` item; replay returns original ids |
| Accept | Condition request OPEN + offer OPEN; replay when already ACCEPTED for same offer → 200 same body; different offer → 409 |
| Transfer start | Condition ACCEPTED; replay if already TRANSFER_PENDING → 200 |
| Handover confirm | Condition TRANSFER_PENDING; if COMPLETED for same exchange → 200; never second ownership write |
| Cancel | Condition on cancellable status; if already CANCELLED → 200 |

**Double accept cannot create two holds** (request status condition + allocation_id uniqueness).  
**Double handover cannot transfer twice** (status COMPLETED condition / org already requester).

---

## 24. Transaction boundaries

| Operation | Atomic boundary (must succeed together) | Outside transaction (best-effort) |
|---|---|---|
| Accept | META + offer(s) + Resource + Allocations Put | Audit (dual), metrics |
| Cancel/expire after hold | META + Allocation close + Resource release | Audit |
| Transfer start | META status only | Audit |
| Handover confirm | META COMPLETED + Resource ownership/location (+ qty counterpart) + Allocation close | Audit |

Prefer `TransactWriteItems` for accept, post-hold cancel/expire, and handover confirm.

---

## 25. Failure handling

### Decision 17 (LOCKED)

| Failure | Handling |
|---|---|
| Any TransactWrite condition fail | No partial commit; return 409/400 |
| Resource succeeds but exchange fails | **Must not happen** if transactional; if impl mistakenly splits writes, compensating release required — design forbids split accept |
| Ownership update fails | COMPLETED not written; remain TRANSFER_PENDING |
| Destination location invalid | Fail confirm; no ownership change |
| Audit write fails | State already committed; log `Audit write skipped` (existing `record_audit` philosophy); do **not** roll back business state; do **not** report success without durable exchange state |
| Notification fails (future) | Does not affect exchange state |

Misleading “success” without durable META/Resource/Allocation consistency is forbidden.

---

## 26. Threat model

### Decision 18 (LOCKED)

| Threat | Mitigation |
|---|---|
| Cross-tenant leakage | Projections; 404 for non-participants; no serial/asset by default |
| Malicious `organization_id` | `authorize()` membership only |
| Unauthorized accept | Requester org must own META |
| Offer on foreign resource | Resource org condition at create + accept |
| Resource double-spend | Accept conditions + single EXCHANGE allocation id |
| Quantity double-spend | `quantity_available >= n` in transaction |
| Race vs emergency/everyday/reserve | Available/qty conditions; hold blocks later claims |
| Duplicate API retries | Idempotency + status conditions |
| Stale OPEN offers | Accept re-validates; expiry |
| Replayed handover | COMPLETED condition |
| Unauthorized ownership transfer | Only handover confirm path; provider org condition on pre-image |
| NETWORK → PUBLIC accident | NETWORK must not set `visibility_key`; public handler rejects non-PUBLIC |
| Speculative inventory lock | **No hold on offer create** |

---

## 27. Frontend design

Operations nav addition: **Exchange** with tabs:

Network requests · My requests · My offers · Incoming offers · Active transfers

Screens: create request; network list; offer composer (eligible NETWORK+AVAILABLE stock); accept/reject; transfer start; handover confirm; read-only timeline.

No Management exchange-policy desk in V1. No shell redesign.

---

## 28. Implementation phases

| Phase | Scope |
|---|---|
| **5B** | NETWORK visibility foundation + design-aligned types/tests; **no** exchange APIs yet |
| **5C** | Exchange API foundation: request + offer create/list (**no** holds; table infra not applied) |
| **5D** | Atomic accept + EXCHANGE Allocations holds + competing SUPERSEDED (**this phase**) |
| **5E** | Transfer start + handover confirm + ownership/location (+ qty complete) |
| **5F** | Frontend Exchange experience |
| **5G** | Expiry sweeper + lazy expiry + hold release |
| **5H** | Cancel / reject / withdraw + remaining lifecycle |
| **5I** | Security/concurrency hardening vs emergency/everyday/lifecycle |
| **5J** | Authenticated non-pilot live smoke |

No phase starts until this lock is approved.

---

## 29. Rollback strategy

1. Do not expose `/exchange/*` until ready; route script reversible.  
2. Alias rollback for `erap-exchange`.  
3. Leave table in place if created; stop writers.  
4. Stuck OPEN holds: cancel/expire path releases Resources/qty.  
5. NETWORK resources remain non-public if `visibility_key` absent.  
6. No Cognito/Razorpay rollback surface.

---

## 30. Open questions — genuine remainders only

Most 5A questions are **closed** by 5A.1. Remaining:

1. **Cross-org type matching:** exact `resource_type_name` equality vs provider-local compatible type ids when offering.  
2. **Organization display name field** for network projections (which `Organizations` attribute is safe).  
3. **Quantity complete at handover:** always upsert requester QUANTITY pool (recommended) vs fail if pool missing — confirm product preference.  
4. **Handover SLA default durations** (request TTL vs post-accept handover TTL numeric values).  

Everything else in Decisions 1–18 is **locked**.

---

## Appendix — Validation checklist (5A.1)

| Check | Status |
|---|---|
| Ownership transfer only after confirmed handover | Locked §9–10 |
| Offers do not create holds | Locked §7 Decision 1 |
| Acceptance creates atomic hold | Locked §8 |
| NETWORK never uses PublicDiscoveryIndex | Locked §5 |
| Emergency/everyday cannot consume exchange-held stock | Locked §13 |
| Quantity invariants preserved without new counter | Locked §12 |
| Cross-tenant ops authz-bound | Locked §17 |
| Each GSI has access-pattern proof; extras removed | Locked §21 |
| No `EXCHANGE_HELD` status | Locked §8 Decision 3 |
| No application/AWS/data changes in 5A.1 | Document-only update |

---

## Appendix — Inspection note

Read-only confirmation against code: `visibility.py` (PRIVATE/PUBLIC only), `resource_state.py` statuses, `access.py` / `entitlements.py`, `everyday_operations.py` / `lifecycle_operations.py`, `matching.py` same-org, `auto_release` `AllocationStatusIndex`, `lambda_manifest.TABLES`, frontend Operations nav. No AWS mutations performed for this document update.
