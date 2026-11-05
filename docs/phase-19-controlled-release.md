# Phase 19 — Controlled release and live verification

This document is a **release path**, not a claim that production was updated.

Evidence classes used below:

| Class | Meaning |
| --- | --- |
| Verified in source | Present on `feature/phase-18-production-readiness` at `d470149` (PR #42 HEAD) |
| Verified by tests | Confirmed by `python scripts/check_frontend.py` and `python -m pytest -q` in this phase |
| Verified in AWS | Confirmed by a live AWS API call during this phase |
| Verified in production | Confirmed on live Lambdas, Gateway stage `dev`, or Amplify `main` |
| Not verified | Not inspected live, or blocked by missing owner authorization / credentials |

**This phase:** source and tests are verified. AWS and production are **not verified**. No merge of PR #42. No Lambda publish. No Gateway apply. No Amplify deploy.

---

## 1. PR #42 scope

PR: https://github.com/sricharanreddy5414/emergency-resource-allocation/pull/42  
Branch: `feature/phase-18-production-readiness`  
Base: `main`  
State at inspection: **OPEN** (not merged)

Commits on PR #42 that are not on `origin/main`:

| SHA | Scope |
| --- | --- |
| `621a8ac` | Phase 15L quantity workflow (PR #40) |
| `e945474` | Phase 17 request lifecycle (PR #41, includes #40) |
| `d470149` | Phase 18 Gateway route **definitions** and quantity **partial allocation** |

PR #42 therefore **includes** PR #40 and PR #41. Merging #40 or #41 separately after #42 would duplicate quantity and lifecycle work. The safe merge is **#42 only**, after review.

What #42 changes relative to current `main` (source inspection):

- Quantity inventory on register/update (`quantity_total`, `quantity_available`, `quantity_reserved`, `quantity_allocated`, `quantity_unit`).
- Request `quantity` / `quantity_fulfilled` and remaining quantity in the UI.
- Review statuses `PENDING_REVIEW` / `APPROVED` / `REJECTED`.
- Request transitions including `PARTIALLY_ALLOCATED`.
- Approve / reject / complete / cancel handlers on `create-request`.
- Quantity allocation via `TransactWriteItems`; individual allocation path unchanged.
- Frontend remaining quantity, `PARTIALLY_ALLOCATED` filter, dashboard count.
- `scripts/expose_request_lifecycle_routes.py` (idempotent Gateway apply; **not invoked by CI**).
- `lambda_manifest.py` includes `transitions.py` and `request_lifecycle.py` in `create-request` and `emergency-resource-allocation`.

Production `main` does **not** contain these commits. Hosted Amplify tracks `main`, so the live UI is the previous production frontend until `main` is updated.

---

## 2. Release prerequisites

All of the following must be true before any production write:

1. PR #42 review complete. Do **not** merge #40 or #41 separately.
2. Merge #42 to `main` (owner action). GitHub production workflows refuse any ref other than `main`.
3. CI on `main` green (`python -m pytest -q`, `python scripts/package_lambdas.py --check`, secret scan).
4. Owner-authorized GitHub environment `production` and OIDC role `ERAP-GitHub-Production` (`arn:aws:iam::481838970142:role/ERAP-GitHub-Production`).
5. Record current `create-request:live` and `emergency-resource-allocation:live` versions **before** publish (rollback targets).
6. Record current API Gateway resources for REST API `4c6dni17l3` **before** applying lifecycle routes.
7. Do not run `scripts/expose_request_lifecycle_routes.py` until both Lambdas are published with the Phase 18 handlers. Otherwise Gateway would call stale `create-request:live` code.

No DynamoDB table migration is required. `quantity_fulfilled` is an optional attribute. New requests use `review_status=PENDING_REVIEW`. Legacy requests without `review_status` remain allocatable (existing Phase 17 behavior).

---

## 3. Gateway route verification

**Source of truth:** `scripts/expose_request_lifecycle_routes.py`  
REST API: `4c6dni17l3`  
Stage: `dev`  
Region: `eu-north-1`  
Authorizer: Cognito `y0hzhr`  
Integration: AWS_PROXY to `arn:aws:apigateway:eu-north-1:lambda:path/2015-03-31/functions/arn:aws:lambda:eu-north-1:481838970142:function:create-request:live/invocations`

| Path | Methods (planned) | POST auth | OPTIONS auth | Integration |
| --- | --- | --- | --- | --- |
| `/requests` | existing POST + OPTIONS | Cognito `y0hzhr` | NONE | `create-request:live` (must remain) |
| `/requests/cancel` | POST + OPTIONS | Cognito `y0hzhr` | NONE | `create-request:live` |
| `/requests/approve` | POST + OPTIONS | Cognito `y0hzhr` | NONE | `create-request:live` |
| `/requests/reject` | POST + OPTIONS | Cognito `y0hzhr` | NONE | `create-request:live` |
| `/requests/complete` | POST + OPTIONS | Cognito `y0hzhr` | NONE | `create-request:live` |

`planned_routes()` returns exactly `/requests/cancel`, `/requests/approve`, `/requests/reject`, `/requests/complete`. The script verifies existing `/requests` POST still targets `create-request:live` and does not rewrite unrelated resources.

| Check | Class |
| --- | --- |
| Planned routes and constants in source | Verified in source |
| Tests import `planned_routes()` without calling AWS | Verified by tests |
| Live `get-resources` / `get-method` on `4c6dni17l3` | **Not verified** (no AWS read in this phase) |
| Routes present on stage `dev` | **Not verified in AWS** |
| Routes live in production traffic | **Not verified in production** |

Do not treat source or tests as proof that approve/reject/complete exist on the deployed API. Earlier delivery work established that this REST API uses **explicit resources**, not a greedy `{proxy+}`. Missing methods return 403 missing-authentication until they are created.

### Owner read-only inspect (does not change infrastructure)

```text
aws apigateway get-resources --rest-api-id 4c6dni17l3 --region eu-north-1 --limit 500
```

Then for each resource id whose path is `/requests`, `/requests/cancel`, `/requests/approve`, `/requests/reject`, or `/requests/complete`:

```text
aws apigateway get-method --rest-api-id 4c6dni17l3 --resource-id RESOURCE_ID --http-method POST --region eu-north-1
aws apigateway get-method --rest-api-id 4c6dni17l3 --resource-id RESOURCE_ID --http-method OPTIONS --region eu-north-1
```

Expect POST: `AWS_PROXY`, URI ending in `function:create-request:live/invocations`, authorizer `y0hzhr`. Expect OPTIONS: `authorizationType=NONE`.

### Owner apply (write; only after merge + Lambda release)

```text
python scripts/expose_request_lifecycle_routes.py
```

Requires production AWS credentials. The script is idempotent. It is **not** invoked by GitHub Actions. This phase did not run it.

---

## 4. Lambda packaging verification

`python scripts/package_lambdas.py --check` on PR #42 HEAD:

```text
ok get-resources 47222 bytes
ok create-request 43839 bytes
ok emergency-resource-allocation 55424 bytes
ok emergency-resource-auto-release 8134 bytes
ok erap-catalog 33678 bytes
ok erap-public-resources 32380 bytes
ok erap-locations 32641 bytes
ok erap-create-organization 22553 bytes
ok erap-get-organization 13477 bytes
ok erap-billing 25716 bytes
ok erap-billing-webhook 18637 bytes
ok erap-billing-expiry 11060 bytes
ok erap-exchange 70170 bytes
ok erap-exchange-expiry 66215 bytes
ok erap-notifications 19662 bytes
ok erap-reservation-expiry 14582 bytes
```

| Function | Handler entry in zip | Required extra modules | Env / alias |
| --- | --- | --- | --- |
| `create-request` | `lambda_function.py` ← `src/request/handler.py` | `transitions.py`, `request_lifecycle.py` | Publish preserves existing configuration fingerprint; alias `live` |
| `emergency-resource-allocation` | `lambda_function.py` ← `src/allocation/service.py` | `transitions.py`, `request_lifecycle.py`, delivery modules | Same |

`check_zip` refuses `.env`, `.pem`, and filenames containing `secret`. Release scripts (`scripts/release_create_request.py`, `scripts/release_allocation.py`) refuse to run unless `ERAP_ENVIRONMENT=production`, require the commit on `main`, snapshot configuration before code update, and abort if environment variables or other configuration drift.

| Check | Class |
| --- | --- |
| Manifest includes lifecycle modules | Verified in source |
| Zip entries match manifest; no secrets in archive | Verified by tests (`package_lambdas.py --check`) |
| Live `create-request:live` / `emergency-resource-allocation:live` code SHA | **Not verified in AWS** |
| Production handlers serve Phase 18 behavior | **Not verified in production** |

Other Lambdas are packaged unchanged; this release must not publish them unless a separate owner-authorized workflow is used.

---

## 5. Authorization requirements

Verified in source (`src/request/handler.py`, `src/allocation/service.py`, `src/shared/access.py`):

- Write operations use `OPERATE_ROLES` = `{OPERATOR, ADMIN, OWNER}`.
- Request create/cancel remain request-role gated as before.
- Approve/reject/complete require operate roles.
- Self-review: approve/reject of own request returns **403** `"Reviewers cannot approve or reject their own requests"`.
- Organization ownership conditions remain on DynamoDB writes (`organization_id` in transact conditions).
- Quantity claim conditions: tracking `QUANTITY`, not `RETIRED`, `quantity_available >= :qty`.
- Retry / double-allocate: one allocation id `ALLOC-{request_id}`; updates require previous `quantity` and previous `quantity_fulfilled` in the condition expression.
- Partial fill cannot exceed available: claimed amount is `min(remaining_request_quantity, quantity_available)`; zero available still fails.
- Individual `_claim_transact_items` is a separate path and is not replaced by quantity partial allocation.
- Complete requires `Status=ALLOCATED`, remaining quantity 0, and delivery receipt `RECEIVED` or `HANDOVER_COMPLETED`. Duplicate complete of an already completed request returns 200.
- `PARTIALLY_ALLOCATED` cannot complete.

Gateway POST methods are planned with Cognito `y0hzhr`. OPTIONS remain unauthenticated. **Live authorizer attachment is not verified in AWS.**

---

## 6. Deployment commands

Do not run these until PR #42 is merged to `main` and the owner explicitly authorizes production.

### 6.1 Merge (owner)

Merge PR #42 into `main`. Do not merge PR #40 or PR #41 separately.

### 6.2 Publish Lambdas (GitHub, preferred)

From `main`, with environment `production`:

1. Workflow **Release create-request** (`.github/workflows/release-create-request.yml`)
2. Workflow **Release allocation** (`.github/workflows/release-allocation.yml`)

Both require `github.ref == main`, prove the SHA is on `main`, run pytest, secret scan, `package_lambdas.py --check`, assume `ERAP-GitHub-Production`, then `ERAP_ENVIRONMENT=production`.

Equivalent local commands (same guards; still require owner AWS credentials):

```text
set ERAP_ENVIRONMENT=production
set RELEASE_SHA=<40-char SHA of merged main>
set RELEASE_MAIN_REF=origin/main
python scripts/release_create_request.py
python scripts/release_allocation.py
```

On Unix:

```text
ERAP_ENVIRONMENT=production RELEASE_SHA=<sha> RELEASE_MAIN_REF=origin/main python scripts/release_create_request.py
ERAP_ENVIRONMENT=production RELEASE_SHA=<sha> RELEASE_MAIN_REF=origin/main python scripts/release_allocation.py
```

### 6.3 Gateway (owner AWS credentials; not in CI)

```text
python scripts/expose_request_lifecycle_routes.py
```

Then deploy or flush the existing stage if the script’s apply path requires it (the script is the source of truth; do not add unrelated routes).

### 6.4 Frontend (Amplify)

Amplify app `d3enpe7opotop5`, branch `main`, region `us-east-1`, URL `https://main.d3enpe7opotop5.amplifyapp.com`.

There is no in-repo `amplify.yml`. The hosted app is configured to build `main`. After merge, wait for the Amplify job for that commit. Do not claim a new UI until that job succeeds.

CORS `ALLOWED_ORIGIN` in the manifest is that Amplify URL. Localhost is not an allowed origin.

### 6.5 What this phase must not run

- `aws lambda update-function-code`
- `python scripts/expose_request_lifecycle_routes.py`
- Amplify start-job / merge to `main`
- Any other production write

---

## 7. Rollback considerations

| Layer | Action |
| --- | --- |
| `create-request` | Workflow **Rollback create-request** (`.github/workflows/rollback-create-request.yml`) on `main`, input = previous published version recorded before release |
| `emergency-resource-allocation` | Workflow **Rollback allocation** with the previous version |
| Gateway | New methods are additive. Rolling back Lambdas first restores old handler behavior (unknown route still 403). Removing resources is a separate owner-authorized Gateway edit; this repo does not auto-delete them |
| Amplify | Redeploy the last successful `main` job (see `docs/rollback.md`) |
| Data | Quantity counters and `quantity_fulfilled` writes are not automatically reversed. Do not use production stock for destructive verification |

Rollback workflows also require `github.ref == main` and GitHub environment `production`.

---

## 8. Live verification checklist

Use **non-production stock**. Do not treat this checklist as completed in Phase 19.

### After merge, before claiming success

1. Amplify job for the merged SHA is SUCCEED.  
   Class after doing it: Verified in production (frontend).
2. `create-request:live` and `emergency-resource-allocation:live` CodeSha256 match the release report.  
   Class: Verified in AWS.
3. Read-only Gateway inspect (section 3) shows all four POST routes, AWS_PROXY, Cognito, OPTIONS NONE, `/requests` POST unchanged.  
   Class: Verified in AWS.

### Signed-in workflow (not run in this phase)

1. Register a QUANTITY resource (available > 0). Inventory columns persist after reload.
2. Create a request whose quantity exceeds available. Inventory must not decrement on create.
3. Same user cannot approve (403 self-review).
4. A different OPERATOR/ADMIN/OWNER approves.
5. Allocate: status `PARTIALLY_ALLOCATED`, fulfilled = available at claim, remaining shown, counters consistent.
6. Second allocate cannot exceed remaining available; retries do not create a second allocation id.
7. After full fill: `ALLOCATED`, remaining 0.
8. Complete blocked until delivery receipt; then complete.
9. Individual (non-quantity) allocate still claims one resource as before.
10. Dashboard and request list show partially allocated rows. Compact header/sidebar unchanged. One Create request button on the overview (`overviewCreateRequest`); requests page keeps `newRequestBtn`.

| Check | Class in Phase 19 |
| --- | --- |
| UI quantity remaining / PARTIALLY_ALLOCATED in source | Verified in source |
| Frontend syntax | Verified by tests (`frontend syntax passed`, `NO_FRONTEND_BUILD`) |
| Hosted Amplify serves Phase 18 UI | **Not verified in production** |
| Signed-in E2E | **Not verified** |

---

## 9. Known limitations

- PR #42 is open. Production code and Amplify are the previous `main`.
- Gateway lifecycle routes are defined in source only until the owner runs the expose script.
- `expose_request_lifecycle_routes.py` is not invoked by CI by design.
- Live AWS was not queried in this phase (no owner-authorized credentials session for inspection writes; read-only inspect also not executed here).
- Quantity create does not reserve inventory; shortfall is handled at allocate time via partial allocation.
- Partial allocation is QUANTITY-only. Individual matching is unchanged.
- One allocation record per request (`ALLOC-{request_id}`); delivery lookup depends on that id.
- CORS remains Amplify-origin only.
- Phase 14G GAP-07 and unrelated dirty work on other local branches are out of scope.
- This document does not add features.

---

## 10. Final release decision

**DO NOT RELEASE TO PRODUCTION FROM THIS PR.**

| Gate | Result |
| --- | --- |
| Implementation complete on PR #42 | Yes (verified in source) |
| Duplicate quantity/lifecycle vs #40/#41 | No duplicate implementation; stacked commits |
| Automated tests | 1354 passed (verified by tests) |
| Frontend syntax | passed (verified by tests) |
| Lambda zip layout | passed (verified by tests) |
| Safety properties (partial cap, transact, self-review, complete rules, individual path) | Present in source; covered by existing tests |
| Live Gateway | **Not verified** |
| Live Lambdas | **Not verified** |
| Live Amplify | **Not verified** |
| Signed-in E2E | **Not verified** |

**Decision:** source-and-test **READY TO RELEASE** under owner authorization after merging PR #42. Production remains **NOT UPDATED**. Phase 19 records the path only.

Owner-authorized next actions (in order):

1. Merge PR #42 to `main`.
2. Run **Release create-request** then **Release allocation** on `main`.
3. Run `python scripts/expose_request_lifecycle_routes.py`.
4. Confirm Amplify `main` job for the merge SHA.
5. Execute the live checklist in section 8 on non-production stock.
6. Only then call the workflow live.

---

## Test results recorded in Phase 19

Working tree: PR #42 HEAD `d470149`

```text
python scripts/package_lambdas.py --check
(all 16 packages ok; create-request 43839 bytes; emergency-resource-allocation 55424 bytes)

python scripts/check_frontend.py
frontend syntax passed
NO_FRONTEND_BUILD

python -m pytest -q
1354 passed in 13.61s
```

No tests were added or weakened. No production infrastructure was modified.
