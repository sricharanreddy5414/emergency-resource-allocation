# QR resource exchange architecture

Phase 9A design lock. This document does not change runtime behavior. No QR is generated, no camera code is added, and no AWS resource is created by this phase.

Source of truth for current handover behavior is the code: `src/exchange/service.py` (`start_transfer`, `confirm_handover`), `src/exchange/quantity_handover.py`, `src/exchange/handler.py`, `src/exchange/lifecycle.py`, `src/shared/exchange_model.py`, `src/shared/access.py`, and `frontend/app.js` (`renderExchangeLifecycleActions`). Older notes that say a QR should encode `resource_id` are superseded for Exchange handover.

## 1. Executive summary

QR handover is a short-lived, one-time physical-verification step on top of the existing Exchange lifecycle. The provider, already in `TRANSFER_PENDING`, asks the server to mint an opaque token. The QR image contains only that token. The requester scans it, authenticates, previews the transfer, and explicitly confirms. Confirmation calls the same atomic individual or quantity handover that `POST /exchange/requests/{id}/handover/confirm` uses today.

Scanning a QR never transfers ownership, quantity, location, or organization access. The backend still checks Cognito identity, active membership, `OWNER`/`ADMIN`/`OPERATOR` role, billing write entitlement, organization status, Exchange state, offer, allocation hold, resource ownership, and destination. Manual confirmation stays available.

## 2. Current-state findings

| Fact | Where |
|---|---|
| Provider start is `POST /exchange/requests/{id}/transfer/start`. It moves `ACCEPTED` → `TRANSFER_PENDING` and does not change ownership or location. Repeat calls while pending return "Transfer already started". | `start_transfer` |
| Requester confirm is `POST /exchange/requests/{id}/handover/confirm`. It requires `TRANSFER_PENDING`, requester organization, open `EXCHANGE` allocation, and the resource still owned by the provider. | `confirm_handover` |
| Individual confirm is one `TransactWriteItems`: META `COMPLETED` conditioned on `TRANSFER_PENDING`, resource owner/location/visibility `PRIVATE` conditioned on provider + `ALLOCATED` + `Available=false`, allocation `RELEASED` conditioned on open exchange hold. History `RESOURCE_EXCHANGE_TRANSFERRED` is best-effort after commit. | `confirm_handover` |
| Quantity confirm requires `quantity` equal to the hold, then a 4-item transaction: META complete, provider `quantity_allocated` and `quantity_total` decrement, destination merge or new `EXQTY-*` PRIVATE pool, allocation release. It does not move the whole provider pool. | `confirm_quantity_handover` |
| Destination location is stored on the request at create time. Confirm currently **may** override it from the body. Quantity confirm may also pass `destination_resource_id` to merge; omission creates a new pool. | `service.py`, `quantity_handover.py` |
| Write roles are `OPERATOR`, `ADMIN`, `OWNER`. `MEMBER` is read-only. Writes use `access="write"`, which allows `TRIALING`, `ACTIVE`, `PAST_DUE`, and `GRANDFATHERED`, and blocks `CANCELLED` and `EXPIRED`. A missing subscription row is grandfathered. | `access.py`, `entitlements.py` |
| Inactive membership and a non-`ACTIVE` organization are denied before the handover mutation. | `authorize`, `_require_active_organization` |
| Exchange handover deadline is `handover_expires_at`, default 72 hours from accept. Expiry releases the hold and does not transfer ownership. | `DEFAULT_HANDOVER_TTL_HOURS`, `lifecycle.py` |
| Audits `exchange.transfer_started`, `exchange.handover_confirmed`, and `resource.ownership_transferred` are best-effort and must not roll back the transaction. | `record_audit` |
| Notifications `transfer.started` and `handover.completed` are post-commit and must not roll back Exchange. | `exchange_notify.py` |
| `ResourceExchanges` keys are `pk`/`sk` (`EXREQ#…` / `META`, `OFFER#…`, `IDEM#…`). There is no TTL on that table. IAM for `erap-exchange` already allows Get, Put, Update, Query, and TransactWrite on the table and Query on its existing GSIs. No Scan. | `exchange_model.py`, `deploy_exchange.py` |
| Stage throttle is 20 rps / burst 40, shared, not per tenant. | `docs/production-hardening.md` |
| The Exchange UI starts transfer only for the provider in `ACCEPTED`, and confirms handover only for the requester in `TRANSFER_PENDING`, with location and optional quantity-pool fields. | `renderExchangeLifecycleActions` |

## 3. Goals

- Make in-person handover faster for individual resources and quantity resources.
- Preserve merge into an existing destination pool and creation of a new PRIVATE destination pool.
- Keep one ownership-transfer implementation.
- Keep tenant isolation, billing, idempotency, concurrency, audit, history, and notifications.
- Resist replay, screenshot reuse, and cross-organization confirmation.
- Leave the manual confirm path working.

## 4. Non-goals

- QR as an authorization credential.
- Anonymous handover.
- Offline ownership or quantity transfer.
- A new quantity, lot, or resource table.
- A second ownership-transfer state machine.
- Email, SMS, push, MFA, notification preferences, or billing plan changes.
- Camera or QR-library implementation in this phase.
- Encoding `organization_id`, `resource_id`, or `exchange_request_id` in the image.
- Replacing `POST …/handover/confirm`.

## 5. Existing handover workflow

1. Requester creates a request with a destination location. No hold yet.
2. Provider offers an eligible resource. No hold yet.
3. Requester accepts. One atomic hold is created. Status becomes `ACCEPTED`. `handover_expires_at` is set (72h).
4. Provider calls transfer start. Status becomes `TRANSFER_PENDING`. Ownership does not change.
5. Requester calls handover confirm.
   - Individual: full resource moves to the requester, destination location, `AVAILABLE` / `PRIVATE`.
   - Quantity: held units only move. Destination is an existing pool (`destination_resource_id`) or a new `EXQTY-*` pool.
6. Allocation becomes `RELEASED`. Request becomes `COMPLETED`.
7. History and audits are written best-effort. `handover.completed` is emitted best-effort.

Cancel, reject, withdraw, and expiry release holds and do not transfer ownership. `COMPLETED` is not confirmed again except as an idempotent "already completed" response for the requester organization.

## 6. QR purpose

The QR is a bearer **locator for a server-side handover session**, not a resource identity, not an Exchange id, and not a signed authorization grant.

| Option | Decision |
|---|---|
| A. Resource identity QR | Rejected. A stable `resource_id` is not one-time, not tied to one hold, and discloses inventory identity to anyone who photographs the label. It would invite a second transfer path. |
| B. Exchange identity QR | Rejected as the secret. `EXREQ-*` is already known to participants and is 64 bits of hex, but possession of the id must not be treated as confirmation. A QR that is only the request id is a deep link. Deep links stay inside the authenticated Exchange screen. |
| C. One-time handover token | Adopted as the consumption rule. |
| D. Short-lived handover session | Adopted as the lifetime rule. |
| E. Signed QR payload | Rejected for V1. A signature does not prevent replay until expiry unless the server stores a `jti`. That storage removes the reason to be stateless. This design does not add a signing key or KMS. |
| F. Opaque random token mapped server-side | **Selected**, together with C and D. |

The image is assumed public the moment it is shown. Security comes from server state, authentication of the requester, and the existing conditional transaction.

## 7. QR lifecycle

```
TRANSFER_PENDING
  → provider generates session (ACTIVE, ≤15 minutes)
  → requester previews (no state change)
  → requester confirms
  → same atomic handover + session CONSUMED
  → COMPLETED
```

Other ends:

| Event | Session |
|---|---|
| Provider generates again | Previous `ACTIVE` session becomes `REVOKED`. New `ACTIVE` session replaces it. |
| Provider or requester cancels the exchange | Implementation revokes the active session in the same transaction that cancels, or the next preview/confirm sees a non-pending exchange and refuses. |
| Exchange expiry | Hold release as today. Session is not consumable. |
| QR clock passes | Session is `EXPIRED` on read. Exchange can remain `TRANSFER_PENDING`. |
| Manual confirm succeeds | Active session is consumed or revoked in that same transaction so a later scan cannot confirm. |
| Handover succeeds via QR | Session `CONSUMED` in that same transaction. |

Generation is allowed only while status is `TRANSFER_PENDING`, the caller is the accepted provider organization, the offer is `ACCEPTED`, and the allocation is still the open exchange hold. Generation does not start the transfer. The provider uses the existing Start Transfer action first.

## 8. Token model

- Purpose: bind one physical handover attempt to one Exchange request.
- Id format: `HQRS-` + 16 hex characters (public session id, not a secret).
- Secret: `secrets.token_urlsafe(32)` (256 bits). Python `secrets` uses the OS CSPRNG. No custom cipher.
- Stored value: SHA-256 hex of the token only. The raw token is returned once in the generate response and is never written to DynamoDB, AuditEvents, notifications, or logs.
- Compare: hash the presented token and compare with `hmac.compare_digest` against the stored hash.
- Rotation: a new generate revokes the previous active session. Old tokens fail closed.
- Consumption: one conditional update from `ACTIVE` to `CONSUMED` inside the handover `TransactWriteItems`.

## 9. QR payload

Encoded string, not a JSON object and not a URL:

```
erap-hq.v1.<token>
```

| Include | Exclude |
|---|---|
| Version prefix `erap-hq.v1` | Cognito access or ID tokens |
| Opaque token | Passwords, API keys, organization secrets |
| | `organization_id`, `user_sub`, `resource_id` |
| | `exchange_request_id`, `offer_id`, allocation id |
| | Resource name, serial, quantity, location, notes |
| | Destination ids |
| | Signature or long-lived credential |

The prefix lets a future client reject unknown versions. It is not authorization. Anyone who sees the image learns only that some ERAP handover token exists until it expires or is revoked.

The token is not placed in a query string. Clients send it in the JSON body of preview and confirm so it is not stored in browser history. Handlers must not log the body. Existing `log_result` logs route, status, organization, and entity id only; QR routes must pass `session_id`, never the token.

## 10. Storage architecture

No new table. No new GSI. No Scan.

`ResourceExchanges` already stores several entity types under `pk`/`sk`. Two additional item shapes:

**Lookup item** (secret index, GetItem by hash):

| Attribute | Value |
|---|---|
| `pk` | `HQRS#` + SHA-256 hex of the token |
| `sk` | `SESSION` |
| `entity_type` | `HANDOVER_QR_SESSION` |
| `session_id` | `HQRS-…` |
| `exchange_request_id` | server-written |
| `status` | `ACTIVE`, `CONSUMED`, `REVOKED`, `EXPIRED` |
| `expires_at` | ISO UTC |
| `issued_at`, `issued_by` | provider actor |
| `provider_organization_id`, `requester_organization_id` | copied from META at issue |
| `offer_id`, `resource_id`, `tracking_mode`, `quantity` | copied from the accepted hold |
| `qr_ttl_epoch` | Number, DynamoDB TTL, only on these items |

**Pointer item** (one active session per request):

| Attribute | Value |
|---|---|
| `pk` | `EXREQ#EXREQ-…` (same partition as META) |
| `sk` | `HANDOVER#QR` |
| `entity_type` | `HANDOVER_QR_POINTER` |
| `active_token_hash` | current hash, or absent when none |
| `session_id`, `status`, `expires_at` | mirror of the active session |
| `generation_count` | incremented on each successful issue |
| `version` | optimistic concurrency for rotation |

Access patterns, all GetItem or TransactWrite:

1. Issue: Get META, Get pointer, TransactWrite revoke-old + put-new-session + update-pointer.
2. Preview/confirm: hash the token, GetItem `HQRS#<hash>`.
3. Manual confirm / cancel: Get pointer, include session update in the existing transaction when an `ACTIVE` session exists.

`HQRS#` cannot collide with `EXREQ#` or `IDEM#`.

TTL attribute name is `qr_ttl_epoch` (epoch seconds). META, offers, and idempotency items must never set it. Enabling TTL on `ResourceExchanges` is an implementation step and only deletes items that carry this attribute. Consumed and revoked sessions keep `qr_ttl_epoch` at consume-time + 7 days so support can see the session id. Audit rows are not TTL'd with the session.

PITR and deletion protection on `ResourceExchanges` stay as they are. Capacity stays on-demand. Encryption stays the table's existing AWS-managed encryption. No client-side encryption and no Secrets Manager.

A dedicated table was rejected: the access pattern is a single GetItem by a high-entropy key plus a pointer next to META. A GSI was rejected for the same reason.

## 11. API contracts

All three routes are Cognito-authenticated, `EXCHANGE_WRITE_ROLES`, `access="write"`. `organization_id` is the existing query or body selector resolved by `authorize()`. The token is never a substitute for that selector.

### `POST /exchange/requests/{exchange_request_id}/handover/qr`

Provider issues or rotates a session.

Request body: `{}`.

Success `200`:

```json
{
  "message": "Handover QR issued",
  "session_id": "HQRS-…",
  "expires_at": "2026-09-30T12:15:00+00:00",
  "qr_payload": "erap-hq.v1.<token>"
}
```

The raw token appears only here. A repeated call while `TRANSFER_PENDING` revokes the previous session and returns a new payload. It is not idempotent and must not echo the previous token.

Errors:

| Case | Result |
|---|---|
| Not the provider organization | `404 Record not found` |
| Not `TRANSFER_PENDING` | `409` with the same class of message as transfer start (`already completed` or `not accepted` / not pending) |
| Offer or allocation ineligible | `409` |
| `generation_count` would exceed 30 for this transfer | `429` message `Handover QR generation limit reached`. Manual confirm still works. |
| Role, billing, inactive org | existing `403` |

### `POST /exchange/handover/qr/preview`

Requester inspects a scanned token. No DynamoDB mutation except a lazy `EXPIRED` mark, which is optional and must not complete a handover.

Request body: `{ "token": "…" }`. No request id.

Success `200` only when the caller is an active operate-role member of the **requester** organization and the session is `ACTIVE` and unexpired and the exchange is still `TRANSFER_PENDING`:

```json
{
  "session_id": "HQRS-…",
  "expires_at": "…",
  "exchange_request_id": "EXREQ-…",
  "tracking_mode": "INDIVIDUAL",
  "resource_type_name": "…",
  "quantity": null,
  "destination_location_id": "…",
  "destination_mode": null
}
```

Quantity responses set `quantity` to the **held** amount and `destination_mode` to `PENDING_CONFIRM`. They do not include provider pool totals, serials, private notes, actor subs, or the token hash.

Errors for unknown, revoked, consumed, expired, wrong organization, provider scanning their own code, or a token whose exchange is not pending: **`404` message `Handover QR is not valid`**. Do not say whether an exchange exists. The legitimate requester sees a specific expiry or state message only in the cases in section 12.

### `POST /exchange/handover/qr/confirm`

Request body:

```json
{
  "token": "…",
  "quantity": 5,
  "destination_resource_id": "optional-for-quantity-merge"
}
```

`quantity` is required for quantity mode and must equal the hold, matching `parse_required_transfer_quantity`. `destination_location_id` in this body is **rejected with `400`** if present and different from META. If absent, the server uses META. `destination_resource_id` is allowed only for quantity merge and is validated with the existing destination checks. Omitting it creates a new pool, as today.

Success uses the existing handover response shape (`message`, request, offer, allocation, ownership flags) plus `session_id` and `qr_consumed: true`.

A second confirm after success, by the requester organization, returns the existing "Handover already completed" result when META is `COMPLETED`. It does not transfer again.

Preview and confirm are separate. Preview never calls `confirm_handover`.

## 12. Authentication and authorization

Order for preview and confirm:

1. Cognito `user_sub` required (`401` if missing).
2. Active membership in the selected organization (`403` if inactive or not a member).
3. Role in `OPERATE_ROLES` (`403` for `MEMBER`).
4. Billing write entitlement (`403` `BILLING_REQUIRED` for `CANCELLED` or `EXPIRED`).
5. Organization `status=ACTIVE`.
6. Load session by token hash. Missing → generic `404`.
7. Caller organization must equal `requester_organization_id`. Otherwise generic `404`, including the provider who issued the QR.
8. If the caller is the requester and the session is expired → `409 Handover QR has expired`.
9. If the caller is the requester and the session is valid but the exchange is `COMPLETED`, `CANCELLED`, or `EXPIRED` → the existing handover conflict messages, because that caller can already open the exchange.
10. Confirm then runs the current eligibility checks (resource owner, hold, destination).

No anonymous or `NONE` auth route. A public "does this QR look real" endpoint is out of scope because it would become an oracle.

## 13. Individual-resource workflow

At confirm, after the checks above, call the existing individual branch of `confirm_handover` with destination location forced from META:

- Resource still owned by the provider organization.
- `operational_status=ALLOCATED` and `Available=false`.
- Allocation still open, type `EXCHANGE`, same request id.
- META still `TRANSFER_PENDING`.
- One transaction updates META, resource ownership/location/`PRIVATE`, allocation release, and session `CONSUMED` with `status=ACTIVE` and `expires_at` still in the future.
- No partial transfer.
- History `RESOURCE_EXCHANGE_TRANSFERRED` and the existing two audits stay after commit.
- `handover.completed` fires as it does today, with `tracking_mode=INDIVIDUAL`.

The resource id inside the session must match `accepted_resource_id`. A mismatch is `409` and does not transfer.

## 14. Quantity-resource workflow

The session stores the held quantity and the provider `resource_id`. It does not represent the provider pool.

Confirm:

- `quantity` in the body equals the hold and the session quantity.
- `assert_source_eligible` still requires `quantity_allocated` and `quantity_total` to cover that hold, not the whole pool.
- The existing 4-item quantity transaction runs, plus the session consume item.
- Allocation releases once. META stores `quantity_transferred` as today.
- The session consume condition makes a second confirm fail the transaction.

Quantity counters, merge math, and `EXQTY-*` creation stay in `quantity_handover.py`. QR code must not duplicate that function.

## 15. Quantity merge workflow

Merge remains "requester names `destination_resource_id` at confirm". The QR does not contain that id, so photographing the QR cannot retarget the pool.

`assert_destination_merge_eligible` still requires: same requester organization, `QUANTITY`, location equal to the request destination, `AVAILABLE`, compatible type, readable snapshot. Failure is the existing `409` or `404` and does not consume the session unless the transaction itself committed. If the transaction aborts, the session stays `ACTIVE` until expiry or rotation.

Changing destination pool means a new confirm request with a different id, still by the requester, still checked server-side. The QR image does not change.

## 16. Quantity create workflow

If `destination_resource_id` is omitted, the server allocates `EXQTY-*` and writes a PRIVATE pool at the META destination location, `created_via=EXCHANGE_QUANTITY_HANDOVER`, as today.

Immutable after issue, for the QR path:

| Field | Rule |
|---|---|
| `exchange_request_id`, `offer_id`, `resource_id`, held `quantity` | Frozen on the session. Confirm refuses if META/offer/allocation drifted. |
| `destination_location_id` | Always META. QR confirm cannot override it. |
| New pool id | Chosen by the server at confirm, not by the client and not by the QR. |
| Visibility | `PRIVATE`. |

The manual confirm endpoint may still accept a destination location override until a later product change. That override is not added to the QR API.

## 17. Expiry

| Candidate | Verdict |
|---|---|
| 1 minute | Too short once camera permission and a visual check are included. |
| 5 minutes | Acceptable, but easy to expire while people walk the last meters. |
| **15 minutes** | **Selected.** Long enough for one in-person handoff, short enough that a forwarded screenshot is stale quickly. |
| 30 minutes | Wider replay window than the physical act needs. |
| One transfer session with no clock | A screenshot would work until handover completes, which can be up to 72 hours. |
| Until `handover_expires_at` | Same problem. That clock is the Exchange SLA, not the QR secret lifetime. |

QR expiry is `min(issued_at + 15 minutes, handover_expires_at)`. An Exchange can stay `TRANSFER_PENDING` after the QR dies. The provider generates again. Each generation revokes the previous token. Exchange expiry continues to release the hold and block confirm.

Lazy check: if `expires_at` is past, preview and confirm treat the session as expired even if status still says `ACTIVE`. A conditional update may set `EXPIRED` outside the handover transaction. That update must not change META.

## 18. Replay protection

A token becomes valid only when the issue transaction commits an `ACTIVE` session. Consumption is the session update inside the handover transaction:

```
Condition: #status = ACTIVE AND expires_at = :expected_expiry
```

`expires_at` equality detects a rotation that replaced the row. Two confirms cannot both pass. The loser gets `TransactionCanceledException`, re-reads META, and either returns "already completed" or `409 Handover state conflict`. No second ownership write.

After success the hash item is `CONSUMED`. Presenting it again does not start a new handover. After cancel or Exchange expiry, confirm fails on META state even if a buggy session row were still `ACTIVE`, because META's `TRANSFER_PENDING` condition is the final authority. After provider regeneration, the old hash is `REVOKED`.

## 19. Concurrency

| Race | Result |
|---|---|
| Two previews | Both read-only. No transfer. |
| Two QR confirms | One transaction wins. One transfer. |
| Manual confirm while QR session is active | Manual transaction includes session revoke/consume. QR confirm then loses on session or on META. |
| QR confirm after manual completion | META is `COMPLETED`. Requester receives already-completed. No second transfer. |
| Cancel during confirm | Cancel and confirm both condition on non-terminal vs `TRANSFER_PENDING`. One wins. |
| Exchange expiry during confirm | Expiry conditions conflict with confirm. One wins. Expired means no transfer. |
| Resource or quantity changes during confirm | Existing resource and quantity conditions abort the transaction. Session stays active if the transaction aborts. |
| Two generations | Pointer `version` condition. One issue wins. The other retries or returns `409`. |

The handover transaction remains the authority. QR state is an extra condition, not a parallel completer.

## 20. Cancellation

Requester cancel of `TRANSFER_PENDING` already releases the hold and sets `CANCELLED`. The implementation adds a session update to `REVOKED` in that transaction when a pointer exists. A scan afterward is the generic `404` for outsiders and a non-pending conflict for the requester.

Provider cannot confirm. Provider can still generate only while pending; after cancel, generate returns `409`.

## 21. Manual fallback

`POST /exchange/requests/{id}/transfer/start` and `POST /exchange/requests/{id}/handover/confirm` stay. QR is optional.

Both confirms call the same individual and quantity functions. The QR wrapper adds token resolution, destination lock for location, and the session item in the transaction. It does not copy the resource update expression into a new module.

If QR issue fails, the parties use the existing Confirm Handover form. If the camera is unavailable, they use that form. If generation hits the cap, they use that form.

## 22. Threat model

See section 29 for the full table. Residual risk that remains accepted for V1: a requester-organization operator who is physically present, or who receives the token within 15 minutes, can confirm, because that is the same trust as the manual button. A stolen requester Cognito session can already confirm manually. QR does not expand that.

## 23. Audit model

Mandatory business/security audits, best-effort, no token, no hash:

| Action | When | Org |
|---|---|---|
| `exchange.handover_qr_issued` | Successful issue or rotation | Provider |
| `exchange.handover_qr_revoked` | Rotation, cancel, or manual completion invalidates an active session | Provider, or the actor who cancelled/confirmed |

Metadata: `session_id`, `exchange_request_id`, `expires_at`, `generation_count`. Rotation includes `replaced_session_id`.

Not separate audits:

| Case | Why |
|---|---|
| Preview success | High volume, no state change. Metric only. |
| Random token failure | Would let a client flood `AuditEvents`. Metric + log with `session_id` absent. |
| QR expired | Derived from `expires_at`. Metric only. |
| Confirmation success | Existing `exchange.handover_confirmed` and `resource.ownership_transferred`. Add `handover_channel=QR` and `session_id` to that metadata. |
| Confirmation via manual path | Same audits with `handover_channel=MANUAL`. |

Failed confirms that are real state conflicts stay on the existing error path and do not add a new audit type in V1.

## 24. Notification integration

No QR notification type and no new emitter.

| Action | Notification |
|---|---|
| Issued, rotated, expired, revoked, preview failed | None |
| Confirm completes handover | Existing `handover.completed` to both organizations, excluding the actor, same payload rules as Phase 8 |
| Confirm is a no-op already completed | Existing idempotent notify behavior, unchanged |

`transfer.started` remains the signal that the requester should be ready. The QR itself is in person.

## 25. Frontend UX

Mobile first, inside the current Exchange detail actions. No shell redesign. No new navigation item.

**Provider, `TRANSFER_PENDING`, operate role**

- Show "Handover QR" beside the existing status copy.
- Generate calls the issue API and renders the payload with a QR drawing implementation chosen in the build phase.
- Show expiry countdown from `expires_at`.
- Regenerate replaces the image and warns that the previous code no longer works.
- Hide the token string by default. A "Copy code" control is allowed for the manual fallback and must not put the token in the URL.
- Cancel exchange stays the existing control. There is no separate "delete QR" beyond regenerate and cancel.

**Requester**

- "Scan handover QR" opens a preview step.
- After a successful preview, show type, tracking mode, held quantity (quantity only), destination location name, and expiry. Do not show provider private resource fields.
- Quantity: keep the existing pool selector (create PRIVATE pool vs merge). Location is shown read-only from the request.
- Primary button is Confirm. Secondary is Cancel, which discards the preview and does not call confirm.
- Success uses the existing completed state and toast.
- Expired: "This handover code expired. Ask the provider to generate a new one." Manual confirm remains on the page.
- Invalid / wrong organization: "This handover code is not valid."
- Already completed: existing completed panel.

Provider Start Transfer and requester Confirm Handover stay visible so QR is additive.

Deep links from notifications stay `exchange_request` links. They do not carry QR tokens.

## 26. Camera and browser considerations

Not implemented in 9A.

Later evaluation, in order:

1. Chromium `BarcodeDetector` plus `getUserMedia` for desktop Chrome and Android Chrome, when `BarcodeDetector.getSupportedFormats()` includes `qr_code`.
2. iOS Safari often has no `BarcodeDetector`. A small client-side decoder is the likely fallback, added only in the implementation phase, with a reviewed dependency.
3. Permission denial, insecure context, or no camera: stay on manual confirm and optional paste of `erap-hq.v1.…`.
4. The decoder output is untrusted text. The client sends it as `token` only after checking the prefix. The server still authenticates and authorizes.

No library is added in the design phase.

## 27. Offline behavior

Handover is an online transaction. A scan that cannot reach the API shows a retryable error and changes nothing: no ownership write, no quantity write, no `COMPLETED`, no session consume.

There is no offline queue that later posts a confirm without the user reviewing the preview again. A stored token may be retried until it expires; the server re-checks every attempt.

## 28. Billing behavior

Issue, preview, and confirm use `WRITE_ACCESS`.

| Subscription | QR |
|---|---|
| Missing row (`GRANDFATHERED`) | Allowed, same as other Exchange writes |
| `TRIALING`, `ACTIVE`, `PAST_DUE` | Allowed |
| `CANCELLED`, `EXPIRED` | `403 BILLING_REQUIRED` |
| Unknown status | Denied by `is_operational_write_allowed` |

No QR fee, meter, or Razorpay change. Preview uses write entitlement on purpose so a lapsed organization cannot stage a confirm it cannot finish.

## 29. Observability

Logs use the existing JSON line: `request_id`, route, status, duration, `organization_id`, `entity_type=handover_qr`, `entity_id=session_id`. Never log `qr_payload`, `token`, or `active_token_hash`.

Metrics, emitted as structured log fields the implementation can alarm on later (no new metrics product required for the design):

| Field `qr_result` | Meaning |
|---|---|
| `issued` | Generation committed |
| `preview_ok` | Requester preview succeeded |
| `preview_rejected` | Generic invalid, including wrong org |
| `expired` | Requester presented an expired session |
| `consumed` | Confirm consumed the session |
| `confirm_conflict` | Transaction cancelled |
| `generation_limited` | Cap reached |

Correlation is API Gateway `requestId` (already `begin_request`) plus `session_id` and `exchange_request_id` after the session is known. Before a hash hit, logs omit exchange id so a failed guess does not write arbitrary ids into a success-shaped record. The attempted token is still not logged.

## 30. Data retention

| Data | Retention |
|---|---|
| Active session | Until consume, revoke, or 15-minute expiry, then `qr_ttl_epoch` |
| Consumed or revoked session item | 7 days after the terminal status, then TTL delete |
| Pointer item | Same TTL once no active session remains; while `TRANSFER_PENDING` it stays so generation count survives regenerations |
| `AuditEvents` | Unchanged. QR TTL must not delete audits |
| Exchange META, offers, allocations, resource history | Unchanged when a QR expires |

Exchange expiry and resource history do not depend on the session row.

## 31. IAM

Implementation stays on `erap-exchange` and the existing role in `scripts/deploy_exchange.py`.

Required actions on `ResourceExchanges` are already granted: `GetItem`, `PutItem`, `UpdateItem`, `Query`, `TransactWriteItems`. No `Scan`, no `DeleteItem`, no table wildcard, no new resource ARN. Enabling TTL is a control-plane change in the implementation phase (`UpdateTimeToLive` is not granted to the Lambda).

No new Lambda. No Secrets Manager. No KMS key. No Cognito change. No API Gateway authorizer change (`COGNITO_USER_POOLS`, same as other exchange routes). Notifications and expiry Lambdas do not read QR items.

## 32. Deployment sequence

This phase deploys nothing. A later implementation phase should:

1. Ship code on `erap-exchange` with routes dark until the alias cutover.
2. Enable `qr_ttl_epoch` TTL on `ResourceExchanges` before the first issue, and verify META items do not carry that attribute.
3. Expose the three routes with the existing Cognito authorizer and `NONE` CORS options, following `expose` scripts used for exchange.
4. Publish the `live` alias.
5. Ship the frontend only after the API is on `live`.
6. Do not point the pilot organization at a QR test.

Rollback is alias rollback plus leaving inert session items to TTL. No table drop.

## 33. Rollback strategy

- Revert the `erap-exchange` alias to the previous version. Manual handover is that version's behavior.
- Remove or leave the API routes. Orphan `HQRS#` items expire by TTL.
- Do not delete `ResourceExchanges`.
- Do not revert META, allocations, or resources because of a QR defect unless a handover transaction itself mis-fired. Those rows are the existing handover, not QR-only data.
- Frontend can hide the QR buttons without a backend rollback if the API is failing. Manual buttons remain.

## 34. Test strategy

Implementation tests, not written in 9A:

- Issue denied for requester, member, inactive org, wrong provider, non-pending status, and generation cap.
- Payload has the version prefix and no request id, resource id, or organization id.
- DynamoDB items store the hash, not the raw token.
- Preview generic `404` for stranger org, provider self-scan, garbage token, revoked token, consumed token.
- Requester expiry message does not include resource details.
- Confirm individual and quantity (merge and create) each succeed once, then the second confirm does not move ownership or counters.
- Concurrent confirm: one success.
- Manual confirm consumes or revokes the session.
- Cancel and exchange expiry block confirm.
- Destination location override on the QR confirm body is `400`.
- Quantity body cannot exceed the hold.
- Audit metadata has `session_id` and no token.
- Notification spy: issue emits nothing; confirm emits only `handover.completed`.
- Billing `EXPIRED` is `403` on all three routes.
- Existing handover tests stay green when no QR session exists.

Phase 9A runs the current suite only, to prove the design commit did not change behavior.

## 35. Open questions

1. Cross-organization resource type matching remains the open Exchange question. QR uses whatever accept already stored.
2. Safe organization display name on the preview is deferred. V1 preview uses resource type, quantity, and destination location, which the requester already knows.
3. Whether manual confirm should also stop overriding `destination_location_id` is a product change beyond QR. QR confirm already refuses an override.
4. The on-device QR renderer and iOS decoder library are chosen in the implementation phase.
5. Per-organization API throttles are still not configured. The generation cap is the QR-specific control until that exists.

## 36. Explicit decision log

| Topic | Decision |
|---|---|
| QR purpose | One-time physical handover session for an existing `TRANSFER_PENDING` exchange. Not a security boundary. |
| Token model | Opaque `secrets.token_urlsafe(32)`, stored as SHA-256 only. |
| Payload | `erap-hq.v1.<token>` only. |
| Storage | Two item types on `ResourceExchanges`. No new table. No new GSI. No Scan. |
| Expiry | 15 minutes, also capped by `handover_expires_at`. Independent of the 72-hour handover SLA. |
| Generator | Accepted provider organization, operate role, only in `TRANSFER_PENDING`, after transfer start. |
| Scanner | Requester organization, operate role, authenticated. Provider self-scan and other orgs get a generic `404`. |
| Confirmation | Required. Preview does not complete handover. |
| Individual | Same conditional ownership transaction, plus session consume. No partial transfer. |
| Quantity | Same 4-item hold consumption for the offered quantity only, plus session consume. |
| Merge / create | Unchanged rules. Location frozen to META on the QR path. Pool id chosen at confirm by the requester or by the server, not by the QR. |
| Replay | Conditional `ACTIVE` → `CONSUMED` inside the handover transaction. Rotation revokes the old hash. |
| Audit | Issue and revoke are mandatory. Confirm reuses handover audits with `handover_channel` and `session_id`. No token. No per-guess audit. |
| Notifications | None for QR lifecycle. Completed handover uses `handover.completed`. |
| Offline | No transfer without a successful online confirm. |
| Billing | Existing write entitlement. No new price. |
| Manual fallback | Existing start and confirm routes remain and share the atomic handover functions. |
| Rate limit | 30 issues per transfer on the pointer, plus the existing stage throttle. No new limiter service. |

## Threat model table

| Threat | Attack | Impact | Mitigation | Residual risk |
|---|---|---|---|---|
| Screenshot sharing | Forward a photo of the QR | Holder can attempt confirm until expiry | 15-minute TTL, one-time consume, requester auth and org check | A requester operator who receives the photo in time can confirm, which matches manual authority |
| Photo reuse | Replay a saved image after handover | Second transfer | Session `CONSUMED` in the same transaction as META | None for a second transfer if the transaction commits |
| Replay | Repeat confirm API | Double completion | META `TRANSFER_PENDING` condition plus session `ACTIVE` condition | Loser sees conflict or already-completed |
| Interception | Shoulder-surf or camera over the shoulder | Token disclosure | Short TTL, opaque payload, no extra PII in the image | Same as screenshot sharing |
| Token guessing | Blind `token` values | Confirm someone else's handover | 256-bit token, hash lookup, generic `404`, no per-guess audit | Negligible. Stage throttle slows the network, not a single determined subnet forever; entropy is the control |
| Brute force | High-rate preview | Cost and log noise | 256-bit space, shared API throttle, generation cap is separate | No per-IP limiter in V1. Guessing does not succeed |
| Log leakage | Token printed in Lambda logs | Bearer reuse | Do not log body, payload, or hash. Log `session_id` only after a real session | A future debug print could regress this. Tests should forbid the raw prefix in log fixtures |
| URL leakage | Token in query, Referer, history | Bearer reuse | Body only. Not a link. Notifications do not carry it | Copy-paste into chat is outside the product |
| Wrong organization | Member of org B scans org A's code | Cross-tenant handover | Requester org match, else generic `404` | Attacker learns the code is not valid for them, not whether the exchange exists |
| Wrong user | Another operator in a third org | Same | Membership organization must be the requester | Another operator **inside** the requester org may confirm. That is current Exchange policy |
| Provider scans own QR | Provider previews or confirms | Provider completes as requester | Provider org fails the requester check with generic `404` | Provider can still use transfer start, not confirm |
| `MEMBER` scans | Low-privilege member | Unauthorized confirm | `OPERATE_ROLES` on preview and confirm | `403` reveals the role gate, not the exchange |
| Inactive member | Disabled membership | Confirm | `authorize` rejects non-`ACTIVE` | None beyond existing auth |
| Suspended organization | Org status not `ACTIVE` | Confirm | `_require_active_organization` | Same as other writes |
| Concurrent scans | Two confirms at once | Double transfer | One TransactWrite wins | The loser may see a conflict toast |
| Double confirmation | Retry after success | Double transfer | Idempotent already-completed for the requester when META is `COMPLETED` | A stranger with the old token still gets generic `404` |
| Cancel during scan | Cancel races confirm | Transfer after cancel | Both condition on META status | One outcome only |
| Expiry during scan | Clock passes mid-preview | Stale confirm | Confirm re-checks `expires_at` | User must regenerate |
| Completion during scan | Other party confirms manually | Double transfer | Shared META condition; manual path revokes the session | None if both are in one transaction each |
| Resource state changes | Resource released, retired, or moved | Wrong asset transferred | Existing resource conditions | User sees `409`. Session remains until expiry if the transaction aborts |
| Quantity changes | Hold no longer covered | Extra units leave the pool | Existing `quantity_allocated` / `quantity_total` conditions and body must equal hold | Same `409` |
| Provider account compromise | Attacker generates QR | Social-engineer a requester | Requester still must confirm. Token does not skip destination or hold checks | A compromised provider can already start transfer. QR does not add a silent complete |
| Requester account compromise | Attacker confirms | Immediate handover | Same as manual confirm. Short token TTL does not stop a live session | Pre-existing. MFA is out of scope |
| Malicious frontend | Extra body fields, swapped destination | Redirect units | Server ignores QR location override; merge id revalidated; client is not trusted | Manual route still has today's location override |
| API replay | Captured confirm request | Second complete | One-time session and META condition | HTTPS termination logs must not store the body. Current app logs do not |
| Stale QR | Yesterday's code | Late transfer | 15-minute expiry distinct from 72-hour handover | Provider must be trained to regenerate |
| Generation abuse | Mint thousands of tokens | Storage and confusion | Pointer cap 30 per transfer; each issue revokes the previous | Cap does not cover other exchanges. Stage throttle still applies |
| Excessive generation | Refresh loop | Operators locked out of QR | Cap returns `429`; manual confirm remains | A buggy client can burn the cap |
| QR content disclosure | Decode the image | Inventory or tenant leak | Payload is version + token only | The fact that a handover is happening is visible to a bystander |
| Anonymous scan | Call preview without Cognito | Unauthenticated complete | Authorizer on the route. No public QR API | None for completion |
| Hash leak | Read `HQRS#` key from a table export | Offline guess still required | Key is the hash, not the token. Confirm needs the preimage | A table export plus a captured token matches. Table access is already privileged |
| TTL misconfiguration | TTL attribute on META | Exchange rows deleted | Dedicated `qr_ttl_epoch`, set only on QR items. Implementation test must assert META has no TTL attribute | Operator error if a later change reuses `expires_at` as the table TTL |

## Implementation note

Phase 9A stops at this document. Do not add routes, items, camera code, or frontend buttons until a later phase explicitly implements this lock.
