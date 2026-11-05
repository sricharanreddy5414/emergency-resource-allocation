# Phase 18 — Production readiness, Gateway routing, partial allocation

## PR relationship

- PR #40 (`fix/phase-15l-complete-quantity-workflow`) adds request quantity and inventory columns.
- PR #41 (`feature/phase-17-complete-request-lifecycle`) includes #40 and adds review/complete.
- This branch is based on PR #41 HEAD `e945474`. Merge #41 (or this PR) rather than merging #40 separately, to avoid duplicate quantity work.

## Gateway routes

Source of truth: `scripts/expose_request_lifecycle_routes.py`

Planned POST+OPTIONS paths on REST API `4c6dni17l3` / stage `dev`:

| Path | Lambda | Auth |
| --- | --- | --- |
| `/requests` (existing, preserved) | `create-request:live` | Cognito `y0hzhr` |
| `/requests/cancel` | `create-request:live` | Cognito `y0hzhr` |
| `/requests/approve` | `create-request:live` | Cognito `y0hzhr` |
| `/requests/reject` | `create-request:live` | Cognito `y0hzhr` |
| `/requests/complete` | `create-request:live` | Cognito `y0hzhr` |

OPTIONS remain `Authorization: NONE`. POST is AWS_PROXY to `create-request:live`.

The script is **not** invoked by CI and was **not** applied to live Gateway in this change. Owner-authorized production apply is required separately.

## Partial allocation

Supported for QUANTITY resources only.

- Allocation id remains `ALLOC-{request_id}` (one record per request).
- First short-stock claim writes that record and sets request `Status=PARTIALLY_ALLOCATED` and `quantity_fulfilled`.
- Later claims update the same allocation quantity and inventory atomically.
- Full fill sets `Status=ALLOCATED`.
- Individual emergency matching is unchanged.

## Completion

Requires `Status=ALLOCATED`, remaining quantity 0, and delivery receipt `RECEIVED` or `HANDOVER_COMPLETED`. Partial requests cannot complete. Duplicate complete of an already completed request returns 200.

## Deployment required (not executed)

1. Merge this PR (or #41 then this) to main.
2. Release `create-request` and `emergency-resource-allocation` Lambdas via existing production workflows.
3. Run `python scripts/expose_request_lifecycle_routes.py` with production AWS credentials (or an owner-authorized workflow wrapping it).
4. Deploy Amplify `main`.
5. No DynamoDB table migration. `quantity_fulfilled` is a new optional attribute.

## Demo (after deploy)

1. Register a quantity resource with 12 available.
2. Create a request for 20. Inventory unchanged.
3. A different operator approves.
4. Allocate: 12 allocated, remaining 8, status partially allocated.
5. Restock or use remaining available, allocate again to reach 20.
6. Create delivery, dispatch, deliver, confirm receipt.
7. Complete request.

Do not use production stock for destructive tests. Live Amplify is not updated by this PR.
