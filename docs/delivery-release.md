# Delivery-only release package (NOT EXECUTED)

**HEAD:** `89f26c6792fd42345cf7ee1875d5cb6feb575707`  
**Branch:** `phase-14g-closure-verification`  
**Governance:** Phase 14G BLOCKED / NO-GO. GAP-07 OPEN. Deployment authorization **NOT GRANTED**.  
**Owner instruction** (`docs/delivery-integration-owner-instruction.md`) authorizes local preparation only. It is **not** an AWS or Amplify grant.

This document is the delivery-only change manifest and authorized-future checklist. It does not apply AWS, commit, or publish.

---

## 1. Whole-file include (new / delivery-only)

| Path | Role |
|---|---|
| `src/shared/delivery_model.py` | Status machine, views, quantities |
| `src/shared/delivery_service.py` | Persistence + validation |
| `src/shared/delivery_handler.py` | HTTP dispatch |
| `frontend/delivery.js` | Authenticated API UI (no sessionStorage) |
| `scripts/expose_delivery_routes.py` | Print-only Gateway plan |
| `infra/deliveries-table.json` | Deliveries table definition (`applied: true` after authorized create) |
| `docs/delivery-tracking.md` | Schema/API notes |
| `docs/delivery-integration-owner-instruction.md` | Owner instruction (not GO) |
| `docs/delivery-release.md` | This package |
| `tests/test_delivery_tracking.py` | Backend tests |
| `tests/test_delivery_ui.py` | Frontend static tests |
| `tests/test_expose_delivery_routes.py` | Dry-run expose tests |

---

## 2. Mixed files — include only these hunks

Line numbers are from the **current working tree** (not HEAD).

### `src/allocation/service.py` — **delivery-only in this file** (include entire dirty delta)

- L34: `from delivery_handler import handle_delivery, is_delivery_path`
- L259–269: `is_delivery_path` branch

No unrelated allocation-logic dirty work in this file.

### `scripts/lambda_manifest.py` — **delivery-only in this file**

- L66–68: zip entries `delivery_model.py`, `delivery_service.py`, `delivery_handler.py` under `emergency-resource-allocation`

Do **not** change `TABLES` (keep 14). Do **not** add a 17th function.

### `scripts/check_frontend.py` — **delivery-only in this file**

- L38: `"delivery.js"` in the syntax list
- L46–47: `delivery.js` / `id="deliveries"` HTML check

### `frontend/style.css` — **delivery-only in this file**

- L1457–1491: `#deliveries` tallies/toolbar, `.delivery-form`, extra `.ops-timeline` rules  
  Note: `.ops-timeline` already exists at L522. The new block is additive CSS, not a functional blocker.

### `frontend/index.html` — **include only**

- L181–190: Deliveries nav button
- L1440–1527: `#deliveries` page
- L2972: `<script src="delivery.js"></script>`

### `frontend/app.js` — **include only**

- L55–56: `DELIVERIES_API_URL`
- L1656–1657: `pageTitles.deliveries`
- L1866–1872: `navigateTo("deliveries")` → `loadDeliveries()`
- L7365–7371: **Create Delivery** button
- L9301–9303: `clearTenantData()` → `loadDeliveries()`
- L12979–12981: `initializeDeliveryTracking()`

---

## 3. Mixed files — exclude (do not ship with delivery)

### `frontend/index.html` — **exclude**

- Account/signup copy (Sign in / registration closed / Create account removed)
- `#billingLead` extra legal/checkout sentences

### `frontend/app.js` — **exclude**

- Signup comment in `initializeAuthentication`
- `currentResourceListSearchQuery`, `reloadResourcesForSearch`, `?q=` on resource inventory
- Resource name substring filter additions
- Resource search `input` listeners switched to `reloadResourcesForSearch`

### Entire files — **exclude**

- `frontend/signup-ui.js`
- `src/billing/checkout.py`
- `src/resource/handler.py`
- `src/shared/resource_page_token.py`
- `tests/signup_phone.mjs`, `tests/test_signup_phone.py`, `tests/test_checkout_availability.py`, `tests/test_controlled_beta_phase16.py`, `tests/closed_registration.mjs`, `tests/test_resource_list_text_search.py`, `tests/test_phase14g_gap07_drafts.py`, `tests/test_phase17_alerting_scope.py`
- Dirty launch docs: `docs/ci-cd-runbook.md`, `docs/environments.md`, `docs/github-aws-oidc.md`, `docs/legal-launch-requirements.md`, `docs/notification-architecture.md`, `docs/production-launch-*.md`, `docs/saas-commercial-readiness.md`, `docs/service-dependency-map.md`
- Untracked: `docs/phase-14g/`, `docs/phase-15-*` … `docs/phase-20-*`, paid-production docs

Those files must remain in the working tree. A future delivery commit must not add them.

---

## 4. Authorized-future checklist

Each step needs its **own** written AWS/governance grant. Owner implementation instruction is **not** sufficient.

| Step | Artifact | Prerequisite / approval | Status |
|---|---|---|---|
| 1. DynamoDB table + GSI | `infra/deliveries-table.json` | Grant to **create** `Deliveries` + `OrganizationLocationIndex` in `eu-north-1` | NOT APPLIED |
| 2. Runtime IAM | GetItem, PutItem, Query on that table (and GSI) on the **allocation function role** | Grant to **attach IAM**. No Scan/DeleteItem/UpdateItem | NOT APPLIED |
| 3. API Gateway | `/allocate/deliveries*` on API `4c6dni17l3` stage `dev`; Cognito `y0hzhr` on GET/POST; OPTIONS none; AWS_PROXY → `emergency-resource-allocation:live` | Grant to **create methods** and **deploy stage `dev`**. Do not run `expose_delivery_routes.py --apply` without that grant (script still refuses mutation) | NOT APPLIED |
| 4. Lambda package + alias | Zip with `delivery_*.py` + service.py branch; alias `live` | Grant to **publish** `emergency-resource-allocation`. Release workflows still require GitHub `production` reviewers | NOT PUBLISHED |
| 5. Amplify frontend | App `d3enpe7opotop5`, branch **`main`**, only delivery hunks + `delivery.js` | Grant to **publish** Amplify. Push to `main` is a publish. Current branch is **not** `main` | NOT PUBLISHED |
| 6. Hosted 14-point smoke | Sign-in → org/location → create → dispatch → transit → delivered → receipt → handover → verify → refresh → second browser → other-org 404 → allocation unchanged | Only after steps 1–5. **Not run** | NOT RUN |
| 7. Rollback | Previous Amplify job; previous Lambda `live` version; do not delete table without a separate grant | Rollback grant if destroying routes/table | N/A until publish |

---

## 5. Compatibility

- Frontend: `https://4c6dni17l3.execute-api.eu-north-1.amazonaws.com/dev/allocate/deliveries`
- GET uses READ roles; POST uses OPERATE roles + billing write gate (existing `authorize()`)
- Frontend without Gateway/Lambda/table shows undeployed error and **empty** rows (no sessionStorage)
- Do not publish frontend before Lambda+table+routes, or the hosted UI will show that error during the presentation

---

## 6. Remaining technical notes (not AWS apply)

- No `allocation_id` GSI (org Query + filter)
- GSI `location_id` must be non-empty (copied from allocation)
- Duplicate `.ops-timeline` CSS (harmless)
- Surgical extract required on mixed HTML/JS before any commit
- Live Amplify job SHA and live `/allocate` resource tree: **unverified**
