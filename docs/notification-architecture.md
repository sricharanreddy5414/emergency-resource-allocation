# ERAP Notification Architecture (Phase 8A — Design Lock)

**Status:** Design only. No AWS resources, code, or deployments in this phase.  
**Commit intent:** `docs: design notification architecture`  
**Baseline:** `1bdb4b1` — quantity exchange publish complete.  
**Pilot:** `ORG-D13B30D99127` remains untouched by notification design and future implementation smoke.

---

## 1. Executive summary

ERAP needs **organization-scoped, user-inbox notifications** so the correct members learn about Resource Exchange (and later emergency/allocation) events without scanning AuditEvents or trusting the browser.

**Chosen architecture (V1):**

1. Keep **`AuditEvents`** as the immutable security/ops record (unchanged semantics).
2. Add a dedicated DynamoDB table **`Notifications`** (single-table pk/sk) holding:
   - an **idempotent event outbox** row per logical notification event, and
   - **per-user inbox** rows for recipients.
3. Emit notification intents **after** successful Exchange state transitions (best-effort, like `record_audit`), **never** inside the ownership/hold TransactWrite.
4. **In-app** delivery only in V1; email/SMS/push deferred.
5. Recipients are **ACTIVE organization members** with roles **OWNER / ADMIN / OPERATOR** (never all MEMBERs by default), derived from `OrganizationMembers`, never from client-supplied user lists.

Exchange lifecycle behavior is **not** redesigned. Notification failure must never corrupt resource, quantity, allocation, or exchange state.

---

## 2. Current-state findings

Inspected source and docs (code/deploy manifests outweigh older narrative docs).

### 2.1 What exists today

| Area | Finding |
|---|---|
| **Frontend System → Notifications** | Placeholder. In-memory `notifications[]` in `frontend/app.js`. Session-only toasts via `addNotification(title, message)`. Clear button empties local array. **No API**, no persistence, no unread/read, no Exchange deep links. |
| **Audit** | `src/shared/audit.py` — `build_audit_event` / `record_audit`. Best-effort `PutItem` to `AuditEvents`. `event_id = timestamp + "#" + uuid`. Failure is logged and **does not** fail the caller. |
| **Exchange audits** | Dual-org audits already exist for several transitions (`exchange.request_created`, `offer_created`, `offer_accepted`, `transfer_started`, `handover_confirmed`, `request_cancelled`, `offer_rejected`, `offer_withdrawn`, `expired`, plus provider-side resource/allocation audits). |
| **Access** | `src/shared/access.py` — Cognito `sub` + `OrganizationMembers` via `UserSubIndex`. Roles: MEMBER, OPERATOR, ADMIN, OWNER. Billing gate for writes via `entitlements.py`. |
| **Members** | `OrganizationMembers` PK `(organization_id, user_sub)`. Org member lists query by `organization_id` (no Scan). |
| **Pagination** | `src/shared/pages.py` opaque tokens — reuse for notification lists. |
| **SNS / EventBridge** | SNS `ERAP-Production-Alarms` is **ops paging**, not user inbox. EventBridge Scheduler exists for exchange-expiry / billing-expiry patterns. **No** user-notification bus. |
| **BillingEvents** | Idempotent provider-event store with org GSI — useful **pattern** analogy, wrong domain for inboxes. |
| **Tables** | No Notifications table. Rule in architecture docs: do not add a table until existing ones cannot serve the access pattern. |

### 2.2 Why AuditEvents alone is insufficient

1. **No user targeting** — audits are org/action oriented, not inbox items.
2. **No unread / read_at** — not a UX store.
3. **Non-deterministic IDs** — `timestamp#uuid` cannot safely dedupe retries.
4. **No list API / GSI** documented for “my unread notifications”.
5. Mixing security audit retention with user TTL would force bad choices (delete audit when notification expires, or retain forever and bloat inbox queries).
6. Audit payloads may contain more internal metadata than a user notification should show.

**Conclusion:** Audit remains audit. Notifications need a dedicated store.

### 2.3 Frontend expectations (today → target)

Today: empty-state copy says *“Allocation and request messages from this session appear here.”*  
Target: replace session array with authenticated org-scoped inbox backed by API (see §13–14). Keep the existing section chrome; do not invent a second notification surface in V1.

---

## 3. Goals

- Multi-tenant-safe, organization-scoped in-app notifications for Exchange events.
- Deterministic recipients from verified membership + role policy.
- Idempotent creation under Lambda/API/expiry retries.
- Decouple notification failure from Exchange transaction success.
- Queryable inbox without Scan; pagination; unread count; mark-read.
- Extensible event vocabulary for emergency, lifecycle, billing, QR later.
- Compatible with Cognito authorizer, Amplify origin CORS, and `access.authorize`.

## 4. Non-goals (Phase 8A / V1 implementation)

- No email, SMS, push, Slack, or WhatsApp.
- No change to Exchange state machines, holds, quantity counters, or handover.
- No Cognito MFA, QR codes, or billing plan changes.
- No notification preference UI beyond role-policy defaults.
- No NETWORK broadcast of “new request” to every org on the network.
- No Scan-based delivery or recipient discovery.
- No reuse of `ERAP-Production-Alarms` SNS for user messages.
- No pilot mutation or pilot-targeted test traffic.

---

## 5. Notification event taxonomy

### 5.1 User-facing events (V1 Exchange)

| Code | Trigger | User-facing? | Notes |
|---|---|---|---|
| `exchange.request.created` | Request created | **No** (V1) | Actor already knows; NETWORK blast forbidden. Optional later for same-org collaborators. |
| `exchange.offer.received` | Offer created on request | **Yes** | Notify **requester** org. |
| `exchange.offer.accepted` | Accept + hold | **Yes** | Notify **provider** (and light confirm to requester). |
| `exchange.offer.rejected` | Reject open offer | **Yes** | Notify **provider** org that owned the offer. |
| `exchange.offer.withdrawn` | Provider withdraws | **Yes** | Notify **requester** org. |
| `exchange.offer.superseded` | Competing offer lost | **Yes** | Notify **losing provider** org only. |
| `exchange.transfer.started` | ACCEPTED → TRANSFER_PENDING | **Yes** | Notify **requester** (handover now expected). |
| `exchange.handover.completed` | Individual or quantity confirm | **Yes** | Notify **both** orgs. Covers quantity complete. |
| `exchange.request.cancelled` | Cancel path | **Yes** | Notify **counterparty** (and skip pure actor-echo when only one side). |
| `exchange.request.expired` | Lazy or sweeper expiry | **Yes** | Notify both orgs that had skin in the game (OPEN→EXPIRED: requester; ACCEPTED/TRANSFER_PENDING: both). |
| `exchange.hold.released` | Hold release after cancel/expiry | **No** (V1) | Operational; already implied by cancel/expire notifications. Keep as **audit only**. |
| `exchange.quantity.pool_created` | New EXQTY destination | **No** standalone | Fold into `exchange.handover.completed` payload (`destination_mode=CREATE`). |
| `exchange.quantity.merged` | Merge into destination | **No** standalone | Fold into `exchange.handover.completed` (`destination_mode=MERGE`). |

**Rule:** Not every audit action becomes a notification. Prefer **one actionable notification per user-visible lifecycle beat**.

### 5.2 Audit-only (examples)

`resource.exchange_allocated`, `resource.exchange_hold_released`, low-level quantity counter history, idempotent retry no-ops, billing entitlement denials.

### 5.3 QR handover (Phase 9A)

QR generation, validation failure, expiry, and revocation do **not** create notification events. A QR confirmation that completes handover emits the existing `exchange.handover.completed` event only. See `docs/qr-exchange-architecture.md`.

---

## 6. Recipient matrix

### 6.1 Role policy (deterministic)

| Role | Exchange notifications (V1) |
|---|---|
| **OWNER** | Yes |
| **ADMIN** | Yes |
| **OPERATOR** | Yes |
| **MEMBER** | **No** by default |

Rationale: Exchange is an operational workflow (`EXCHANGE_WRITE_ROLES` today are write-capable operators). Notifying every MEMBER creates spam and leaks operational noise into read-only seats.

**Future (explicit preference rows):** MEMBER can opt in; never the V1 default.

### 6.2 Actor suppression

Do **not** create an inbox row for the **acting `user_sub`** when they triggered the event (they already have the API response / UI toast). Other eligible roles in the same org still receive it.

### 6.3 Per-event targets

| Event | Requester org recipients | Provider org recipients |
|---|---|---|
| `offer.received` | OWNER/ADMIN/OPERATOR (− actor if any) | — |
| `offer.accepted` | OWNER/ADMIN/OPERATOR (− actor) | OWNER/ADMIN/OPERATOR (− actor) |
| `offer.rejected` | — | OWNER/ADMIN/OPERATOR (− actor) |
| `offer.withdrawn` | OWNER/ADMIN/OPERATOR (− actor) | — |
| `offer.superseded` | — | Losing provider OWNER/ADMIN/OPERATOR |
| `transfer.started` | OWNER/ADMIN/OPERATOR (− actor) | OWNER/ADMIN/OPERATOR (− actor) optional light |
| `handover.completed` | OWNER/ADMIN/OPERATOR (− actor) | OWNER/ADMIN/OPERATOR (− actor) |
| `request.cancelled` | Counterparty side(s) OWNER/ADMIN/OPERATOR | Same |
| `request.expired` | As §5 (OPEN: requester; held: both) | As §5 |

Recipient resolution:

```
Query OrganizationMembers WHERE organization_id = :org
Filter status = ACTIVE AND role IN {OWNER, ADMIN, OPERATOR}
Exclude actor_sub
```

No Scan. Cap fan-out (design default **50** recipients/org; if over cap, still notify all OWNER+ADMIN first, then OPERATORs up to cap — product may raise later).

### 6.4 Deactivated / missing recipients

- PENDING invitations: never.
- Inactive membership: never.
- Deactivated organization: do not create new notifications; existing rows remain until TTL (reads still org-authorized).
- Empty recipient set after filters: write **event outbox only** (audit of “attempted notify”), skip inbox rows — not an Exchange failure.

---

## 7. Tenant isolation model

1. Every inbox row carries `organization_id` of the **recipient’s** organization.
2. List/read/mark-read authorize via `access.authorize(..., allowed_roles=READ_ROLES)` and require `membership.organization_id == row.organization_id`.
3. Notification payloads are **projections**, not raw resource dumps:
   - Allowed: exchange_request_id, offer_id, status labels, resource_type_name, quantity, high-level location **names already exposed** on Exchange APIs for that party, destination_mode.
   - Forbidden: other org’s private location coordinates beyond Exchange view, attributes map, unrelated resource inventories, Cognito emails of the other party, billing secrets.
4. Requester must not see provider-private fields; provider must not see requester-private fields beyond existing Exchange offer/request views.
5. Cross-tenant GetItem by guessing `notification_id` returns **404** (same as `require_owned` style), not 403 with existence leak where practical.
6. Pilot org is never used as a notification test tenant in docs or smoke scripts.

---

## 8. Audit vs notification model

| | **AuditEvents** | **Notifications** |
|---|---|---|
| Purpose | Security / ops trail | User-facing actionable inbox |
| Mutability | Append-only | Read state may update; content immutable |
| Failure | Best-effort; must not fail business op | Best-effort; must not fail business op |
| ID | `timestamp#uuid` (today) | Deterministic `event_id` (see §11) |
| Retention | Long-lived (ops) | Shorter TTL (see §16) |
| Audience | Operators / investigators | Org members (role policy) |

**Correspondence:** A user notification **may** reference an audit action name in metadata, but **must not** require a 1:1 audit Put. Dual-org audits can map to two org-scoped notification events with distinct `event_id`s (see §11).

Do **not** duplicate full audit metadata into notifications.

---

## 9. Storage decision

### Options considered

| Option | Verdict |
|---|---|
| **1. AuditEvents only** | Rejected — no inbox, unread, deterministic ID, or safe TTL. |
| **2. Notifications table only (inbox rows, no outbox)** | Weak — hard to prove “event processed” under retries without a canonical event key. |
| **3. Notifications table + outbox entity (same table)** | **Chosen** — matches ResourceExchanges multi-entity style; supports idempotency + inbox. |
| **4. SNS/EventBridge without persistence** | Rejected for V1 UX — no unread inbox, no mark-read, ephemeral. May be added later as a **delivery channel** atop persisted events. |

### Chosen: Option 3 — single table `Notifications`

**Why justified under “avoid new tables until necessary”:** user inbox + unread + mark-read + deterministic dedupe **cannot** be served by AuditEvents, ResourceExchanges, or BillingEvents without corrupting those domains.

---

## 10. Event-generation architecture

### 10.1 Hard constraint

Exchange TransactWrite (holds, ownership, quantity) **must not** include notification Puts. A notification ConditionalCheckFailed or throttle must never roll back Exchange.

### 10.2 Generation pattern (V1)

```
Exchange API / expiry sweeper
  → commit domain TransactWrite (success)
  → record_audit(...)                 # existing best-effort
  → emit_notification_event(...)      # NEW best-effort, post-commit
       1. Put EVENT outbox (idempotent)
       2. Resolve recipients
       3. Put INBOX rows (idempotent per user)
```

Same process as the request Lambda (`erap-exchange` / `erap-exchange-expiry`) keeps V1 operationally simple (no new bus yet).

### 10.3 Alternatives evaluated

| Mechanism | Role in ERAP |
|---|---|
| Direct write inside TransactWrite | **Forbidden** for domain safety. |
| DynamoDB Streams on ResourceExchanges | Strong eventual consistency; good **V1.1** if post-commit emit proves flaky. Adds Lambda + IAM + lag. |
| EventBridge / SNS / SQS | Useful for **email fan-out later**; not required for in-app V1 persistence. |
| Classic transactional outbox in same TransactWrite | Tempting but couples notification table availability to Exchange commit — **avoid** for V1. |

**Decision:** Post-commit best-effort emit (audit-like). Optional later: Stream/EventBridge consumer for retry/email without changing event IDs.

---

## 11. Idempotency strategy

### 11.1 Deterministic `event_id`

```
event_id = "{event_code}#{subject_id}#{recipient_organization_id}"
```

Examples:

- `exchange.offer.received#EXREQ-…#ORG-REQUESTER`
- `exchange.offer.accepted#EXOFF-…#ORG-PROVIDER`
- `exchange.offer.superseded#EXOFF-LOSER-…#ORG-PROVIDER`
- `exchange.handover.completed#EXREQ-…#ORG-REQUESTER`
- `exchange.handover.completed#EXREQ-…#ORG-PROVIDER`
- `exchange.request.expired#EXREQ-…#ORG-…`

`subject_id` is the stable Exchange entity id for that beat (request id or offer id). **Do not** use wall-clock time in `event_id`.

### 11.2 Inbox row key

```
inbox_notification_id = "{event_id}#{user_sub}"
```

Put with `attribute_not_exists(pk)` / `attribute_not_exists` on the item key so Lambda retries, confirm retries, sweeper retries, and competing supersede races do not duplicate.

### 11.3 Explicit non-goals for IDs

- Do not hash timestamps.
- Do not use API Gateway `requestId` as the sole key (differs across retries that still represent one domain event).

---

## 12. Delivery architecture

Separate concerns:

| Stage | V1 behavior |
|---|---|
| **Creation** | Domain code calls `emit_notification_event` after success. |
| **Persistence** | Outbox EVENT + INBOX rows in `Notifications`. |
| **Delivery** | In-app = persistence. Frontend polls/lists. |
| **Read state** | Update INBOX `read_at` / remove unread GSI key. |

**Deferred channels:** email/SMS/push subscribe to EVENT outbox (or EventBridge mirror) without changing inbox schema.

**No external providers** in Phase 8 implementation until a later design addendum.

---

## 13. API contract

Base: existing API Gateway `4c6dni17l3` / stage `dev`, Cognito authorizer, Amplify origin CORS.  
Organization selection: `organization_id` query/body per `access.requested_organization_id` (same as Exchange).

### 13.1 `GET /notifications`

**Auth:** Cognito + ACTIVE membership. Inbox routes allow `OWNER`, `ADMIN`, and `OPERATOR`. `MEMBER` is denied.  
**Query:**

- `organization_id` (required when multi-org)
- `limit` (default 20, max 50)
- `page_token` (opaque; `pages.encode_token`)
- `unread_only` (optional bool)

**Response 200:**

```json
{
  "organization_id": "ORG-…",
  "notifications": [
    {
      "notification_id": "exchange.offer.received#EXREQ-…#ORG-…#USERSUB",
      "event_code": "exchange.offer.received",
      "title": "New exchange offer",
      "body": "An organization offered Smoke Test Equipment (qty 3).",
      "created_at": "2026-…",
      "read_at": null,
      "severity": {
        "kind": "exchange_request",
        "exchange_request_id": "EXREQ-…",
        "offer_id": "EXOFF-…"
      }
    }
  ],
  "next_page_token": null
}
```

### 13.2 `GET /notifications/unread-count`

**Auth:** same.  
**Response 200:** `{ "organization_id": "ORG-…", "unread_count": 3 }`  
Implementation: Query sparse unread GSI (see §22), not Scan. Cap reported count at e.g. 99 for UI badge if desired.

### 13.3 `POST /notifications/{notification_id}/read`

**Auth:** same. Body may include `organization_id`.  
Idempotent: already-read → 200 with same view.  
Wrong org / other user’s id → 404.

### 13.4 `POST /notifications/read-all`

**Auth:** same. Marks unread inbox rows for `(organization_id, user_sub)` as read (batched Updates; paginate internally).  
Response: `{ "organization_id": "ORG-…", "marked_read": 12 }`.

### 13.5 Non-APIs (V1)

- No public unauthenticated notification endpoints.
- No `user_id` query parameter for listing another user’s inbox.
- No delete API beyond TTL (Clear in UI = mark-all-read or hide client-side; server delete optional later).

---

## 14. Frontend integration contract

Existing System → Notifications section (`#notifications`, `#notificationList`, `#notificationCount`, `#clearNotificationsBtn`).

| Concern | Contract |
|---|---|
| Load | On org context ready / section open: `GET /notifications` |
| Badge | `GET /notifications/unread-count` (poll on interval or after Exchange mutations) |
| Render | Replace in-memory `addNotification` session list for **server** items; keep optional local toast for immediate actor feedback |
| Mark read | Click item → `POST …/read` + deep-link to Exchange detail when `severity.kind == exchange_request` |
| Clear | Map to `POST /notifications/read-all` (not local-only wipe) |
| Empty / loading / error | Reuse empty-state; show non-blocking error toast; never invent fake events |
| Responsive | No redesign; reuse `.notification-item` styles |

Session-only `addNotification` calls for local resource CRUD may remain until those domains emit server events; Exchange should prefer server inbox once API exists.

---

## 15. Security model

1. Cognito ID token authorizer on all `/notifications*` methods.
2. `authorize()` derives `user_sub` and membership; frontend `organization_id` is a selector, not trust.
3. Role: `OWNER`, `ADMIN`, and `OPERATOR` may read and mark their own inbox. `MEMBER` is denied. A page token must match that caller's inbox key.
4. Inbox keys include `user_sub`; users cannot mark another member’s notification.
5. Payload projection (§7); strip secrets in emitter (mirror audit redaction for token/password/secret).
6. Compatible with `src/shared/access.py` billing: **reads** follow `is_operational_read_allowed` (trialing/active/past_due/expired/cancelled/grandfathered known statuses). Creating notifications is a **side effect of already-authorized Exchange writes**, not a separate paid entitlement.
7. No Scan IAM on notification Lambdas.

---

## 16. Retention / TTL

| Item | Retention |
|---|---|
| Inbox rows | DynamoDB TTL attribute `expires_at` ≈ **90 days** from `created_at` |
| Outbox EVENT rows | Same 90-day `expires_at` |
| Unread past TTL | Expire anyway (product accepts loss); critical ops remain in AuditEvents |
| AuditEvents | **No TTL from notification design** |

TTL deletion is eventually consistent; design must tolerate ghost reads briefly.

---

## 17. Failure / retry behavior

| Scenario | Behavior |
|---|---|
| Exchange Transact success, notify emit fails | Exchange stays success; log `NOTIFICATION_EMIT_FAILED` with `event_id` + `request_id`; no user rollback |
| Partial inbox fan-out | Outbox EVENT exists; retry emit is idempotent; missing users get Puts on retry |
| DynamoDB throttle | The emit helper does not retry inside the Exchange request. It logs `NOTIFICATION_EMIT_FAILED` with the exception class and DynamoDB error code, then returns. A later idempotent domain retry can finish fan-out. |
| Duplicate Lambda invoke | ConditionExpression prevents duplicate EVENT/INBOX |
| Stale event (request already COMPLETED when late retry) | Still ok if event_id matches original beat; do not invent new codes |
| Missing recipients | Outbox only; metrics `notification_recipients_empty` |
| Deactivated member mid-flight | Skip that user on fan-out |
| EXPIRED subscription org | Do not block emit for events already caused by allowed writes; reads of inbox still allowed if org read allowed |
| Frontend fetch failure | Show error; Exchange data remains source of truth |

**Sweeper (optional V1.1):** scheduled job queries recent Exchange audits or outbox EVENT without fan-out complete flag — only if production metrics show emit loss. Not required to lock V1 schema.

---

## 18. Billing interaction

- No new SKU or notification quota in V1.
- Notification **reads**: allowed whenever operational **read** is allowed (`entitlements.is_operational_read_allowed`).
- Notification **emit**: side effect of Exchange paths that already enforced `WRITE_ACCESS` / entitlements.
- `PAST_DUE` / `TRIALING` / `ACTIVE` / `GRANDFATHERED`: emit continues with Exchange.
- `EXPIRED` / `CANCELLED`: Exchange writes blocked today → no new exchange notifications; historical inbox readable if reads allowed.

---

## 19. Observability

Structured logs (no tokens). Phase 11B writes these as JSON lines. Field names and redaction are in `docs/observability.md`.

- `notification_emit` — event_id, event_code, org_id, recipient_count, request_id, outcome
- `NOTIFICATION_EMIT_FAILED` — event code, subject, organization, request id, exception class, and DynamoDB error code. No tokens, secrets, or raw QR payloads.
- `notification_read` / `notification_read_all`

Metrics below were design notes. Phase 11B did not create them. Organization and user identifiers stay out of metric dimensions.

Metrics (CloudWatch, not implemented):

- `NotificationsEmitted`
- `NotificationsEmitFailures`
- `NotificationsUnreadQuery`
- `NotificationsFanoutRecipients`

Alarms: emit failure rate / Lambda errors on notification API or exchange emit path — wire to existing ops topic later; **do not** create new paging SNS in 8A.

Correlation: reuse `observability.current_request_id()` in emit logs.

---

## 20. Future extensibility

Event code namespace: `{domain}.{entity}.{action}`  

Examples later:

- `emergency.allocation.created`
- `resource.lifecycle.retired`
- `billing.subscription.past_due` (careful: may warrant email)
- `qr.checkin.completed`
- `organization.member.invited`

Channels: add `DeliveryAttempts` child entity or separate worker reading EVENT outbox without changing INBOX keys.

Preferences: future `NotificationPreferences` item under `ORG#… / USER#… / PREFS` — out of V1.

---

## 21. Threat model

| Threat | Mitigation |
|---|---|
| Cross-tenant inbox read | Key includes org + user; authorize membership match |
| Enumeration of notification_ids | 404; ids are high-entropy composites |
| Forged recipient list from client | Ignored; server resolves members |
| Notification spam / NETWORK flood | No network-wide request.created notify |
| Sensitive data in body | Projection allowlist |
| Abuse mark-read on others | user_sub in key + authz |
| Using notifications to probe pilot | Smoke scripts forbid pilot org ids |
| Coupling DoS Exchange via notify | Emit outside transaction; bounded fan-out |
| Replay | Idempotent keys |

---

## 22. Data model

### Table: `Notifications`

- **BillingMode:** `PAY_PER_REQUEST`
- **PITR:** enabled  
- **Deletion protection:** enabled  
- **Encryption:** AWS owned/managed default (match existing tables); CMK only if org-wide standard changes later  

### Keys

| Entity | `pk` | `sk` |
|---|---|---|
| Outbox EVENT | `EVENT#{event_id}` | `META` |
| Inbox item | `INBOX#{organization_id}#{user_sub}` | `AT#{created_at}#{event_id}` |

### Attributes (inbox)

- `entity_type` = `NOTIFICATION`
- `notification_id` (stable string = `{event_id}#{user_sub}`)
- `event_id`, `event_code`
- `organization_id`, `user_sub`
- `title`, `body` (short strings)
- `created_at`, `read_at` (empty/absent if unread)
- `unread_key` = `INBOX#{organization_id}#{user_sub}` when unread; **REMOVE** when read (sparse GSI)
- `href_kind`, `href_exchange_request_id`, `href_offer_id` (optional)
- `expires_at` (TTL number epoch seconds)
- `schema_version` = 1

### Attributes (EVENT outbox)

- `entity_type` = `NOTIFICATION_EVENT`
- `event_id`, `event_code`
- `organization_id` (recipient org this event targets)
- `subject_type`, `subject_id`
- `actor_sub`
- `payload` (small projection map)
- `created_at`, `expires_at`
- `fanout_status` = `COMPLETE` | `PENDING` (optional for V1.1 sweeper)

### GSI (only one)

**`UnreadByUserIndex`**

- HASH: `unread_key`
- RANGE: `created_at`
- Projection: `ALL` (or KEYS_ONLY + GetItem if size becomes an issue — start ALL for simplicity)

Access patterns:

1. List inbox: `Query pk = INBOX#org#sub`, `sk begins_with AT#`, ScanIndexForward=false, paginate.
2. Unread count / unread list: `Query UnreadByUserIndex` where `unread_key = INBOX#org#sub`.
3. Idempotent create EVENT: `Put pk=EVENT#id, sk=META` condition not exists.
4. Idempotent inbox: condition not exists on inbox keys.
5. Mark read: Update set `read_at`, REMOVE `unread_key`.

**No additional GSIs** in V1. No org-wide “all members’ notifications” admin feed in V1.

---

## 23. Required AWS resources (implementation phase — not now)

1. DynamoDB table `Notifications` (+ GSI `UnreadByUserIndex`, TTL on `expires_at`, PITR, deletion protection).
2. API Gateway routes under `/notifications` (and `/notifications/{id}/read`, `/unread-count`, `/read-all`) with Cognito authorizer + CORS Amplify origin.
3. Lambda packaging options (choose at implement):
   - **Preferred V1:** extend `erap-exchange` emit helper + new `erap-notifications` read API Lambda (keeps read IAM separate), **or**
   - single small `erap-notifications` for reads only; emit lives in exchange package as shared module.
4. IAM updates (see §24).
5. CloudWatch log groups / optional metrics — no new SNS topic required for 8A.

**Not required for V1:** EventBridge bus, SQS, SES, SNS user topics, Cognito changes, new Amplify app.

---

## 24. Required IAM permissions

### Emit path (`erap-exchange`, `erap-exchange-expiry`)

- `dynamodb:PutItem`, `dynamodb:UpdateItem` on `Notifications` (+ index ARNs as needed)
- `dynamodb:Query` on `OrganizationMembers` (recipient resolution) — if not already present on role
- Existing AuditEvents Put remains

### Read API (`erap-notifications` or host Lambda)

- `dynamodb:Query`, `dynamodb:GetItem`, `dynamodb:UpdateItem` on `Notifications` and `UnreadByUserIndex`
- `dynamodb:Query` / Get on `OrganizationMembers`, `Organizations`, `OrganizationSubscriptions` as required by `authorize()`

### Explicit denies / omissions

- No `Scan`
- No `DeleteItem` on Notifications in V1 (TTL only)
- No Put on foreign domain tables from notifications read Lambda

---

## 25. Deployment sequence (future implementation)

1. Land shared emitter + read handlers behind feature flag / routes unexposed.
2. Create `Notifications` table (infra JSON + apply script, PITR/protection).
3. Attach IAM.
4. Deploy emit-enabled `erap-exchange` / expiry (alias `live`).
5. Deploy notifications read Lambda; create API Gateway deployment **only then**.
6. Publish Amplify frontend wiring.
7. Non-pilot smoke only; pilot org forbidden.
8. Watch emit failure metrics before declaring complete.

Order preserves: **table → IAM → emit → read API → UI**.

---

## 26. Rollback strategy

1. Remove or disable API Gateway `/notifications*` routes.
2. Point exchange alias to previous version without emitter (or no-op emitter).
3. Leave `Notifications` table in place (do not delete); TTL drains data.
4. Frontend: hide section fetch / fall back to empty state.
5. AuditEvents and Exchange data untouched.

---

## 27. Test strategy

| Layer | Cases |
|---|---|
| Unit | event_id stability; actor exclusion; role filter; payload projection; idempotent Put conditions |
| API | auth 401; wrong org 403; list pagination; unread count; mark-read idempotent; cross-user 404 |
| Exchange integration | After accept/reject/withdraw/cancel/expire/handover — expected EVENT+INBOX counts; Exchange success when notify mocked to fail |
| Concurrency | Double confirm / double cancel → single notification per event_id |
| Frontend check | `check_frontend.py` still passes; markers for API paths once UI ships |
| Gates | pytest ≥ current; security_scan; package; hardening; workflows |
| Live | Non-pilot orgs only; never `ORG-D13B30D99127` |

Design phase expectation: **existing 545 tests remain green** with docs-only change.

---

## 28. Open questions

1. **Cap fan-out at 50:** confirm product OK for large OPERATOR sets.
2. **transfer.started → provider notify:** light confirm vs requester-only (matrix currently allows light provider).
3. **Host Lambda:** dedicated `erap-notifications` vs attach read routes to an existing function — prefer dedicated for least privilege.
4. **V1.1 Streams:** only if emit-loss metrics warrant; do not block V1.
5. **Clear button semantics:** read-all vs hard delete (locked to read-all in §14 unless product insists).
6. **MEMBER opt-in preferences:** schema reserved; UI phase TBD.
7. Exact title/body copy catalog — finalize at implementation with UX review.

---

## 29. Explicit decision log

| # | Decision |
|---|---|
| D1 | AuditEvents ≠ notifications; both coexist. |
| D2 | Dedicated `Notifications` table with EVENT outbox + INBOX entities. |
| D3 | One sparse GSI `UnreadByUserIndex`; no Scan. |
| D4 | Emit post-commit best-effort; never inside Exchange TransactWrite. |
| D5 | V1 channel = in-app only; email/SMS/push deferred. |
| D6 | Recipients = ACTIVE OWNER/ADMIN/OPERATOR; exclude MEMBER by default; exclude actor. |
| D7 | No NETWORK-wide `request.created` notifications. |
| D8 | Fold quantity create/merge into `handover.completed` payload. |
| D9 | Hold-released is audit-only in V1. |
| D10 | Deterministic `event_id` / inbox keys with conditional Puts. |
| D11 | TTL ~90d on notifications; never TTL AuditEvents for this reason. |
| D12 | API under `/notifications*` with existing authorize + org selector. |
| D13 | Frontend System → Notifications becomes the inbox surface. |
| D14 | No AWS resource creation in Phase 8A (this document only). |
| D15 | Pilot org excluded from notification testing forever unless explicitly re-authorized in a later pilot plan. |
| D16 | Exchange lifecycle code unchanged by this design phase. |

---

## Appendix A — Mapping from current Exchange audit actions

| Existing audit action | Notification code |
|---|---|
| `exchange.offer_created` | `exchange.offer.received` → requester org |
| `exchange.offer_accepted` | `exchange.offer.accepted` → both |
| `exchange.offer_rejected` | `exchange.offer.rejected` → provider |
| `exchange.offer_withdrawn` | `exchange.offer.withdrawn` → requester |
| (supersede path) | `exchange.offer.superseded` → losing provider |
| `exchange.transfer_started` | `exchange.transfer.started` |
| `exchange.handover_confirmed` | `exchange.handover.completed` |
| `exchange.request_cancelled` | `exchange.request.cancelled` |
| `exchange.expired` | `exchange.request.expired` |
| `resource.exchange_hold_released` | none (V1) |
| `exchange.request_created` | none (V1) |

---

## Appendix B — Related documents

- `docs/resource-exchange-design.md` — Exchange lifecycle; notifications listed as later phase.
- `docs/CURRENT_RESOURCE_ARCHITECTURE.md` — table discipline; notifications justify a new table under D2.
- `docs/billing-architecture.md` — entitlements for read/write gates.
- `src/shared/audit.py`, `src/shared/access.py`, `frontend/app.js` Notifications section.
