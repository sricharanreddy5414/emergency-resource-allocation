# Phase 17 — Emergency request lifecycle

This document describes the connected operational workflow after request creation. It records the actual implementation, not a planned redesign.

## Existing functionality reused

- Resource registration and QUANTITY counters from Phase 15L
- Create Request persistence (`resource_id`, `quantity_requested`, `quantity_unit`)
- Emergency matching (`src/shared/matching.py`) for individual resources
- Quantity and individual allocation transactions
- Delivery tracking (`/allocate/deliveries`): ready, dispatch, status, receipt, handover
- Organization membership authorization (`authorize()`, `OPERATE_ROLES`, `REQUEST_ROLES`)
- In-app notification fan-out (`src/shared/notifications.py`)
- Request list, request detail panel, allocation drawer, deliveries page, compact dashboard

## Status model

Emergency request `Status` is unchanged for allocation:

`PENDING` → `ALLOCATED` → `RELEASED` or `COMPLETED`  
`PENDING` → `CANCELLED` or `REJECTED`

New requests also store `review_status`:

- `PENDING_REVIEW` on create
- `APPROVED` after an operator/admin/owner who is not the creator approves
- `REJECTED` together with `Status=REJECTED`

Legacy rows with no `review_status` remain allocatable so existing emergency tests and live PENDING work continue.

Approval does **not** change inventory. Allocation is a separate POST to `/allocate`.

## Partial allocation

Not supported. One request maps to one allocation id `ALLOC-{request_id}`. If available quantity is less than requested, allocation returns “No suitable resource available”. Remaining quantity is shown from requested minus allocated/received amounts. Do not treat that as a second allocation.

## Dispatch, delivery, receipt

Use the existing delivery records. Do not invent GPS. Completion requires a linked allocation and a delivery in `RECEIVED` or `HANDOVER_COMPLETED`.

## APIs added (request Lambda, same pattern as cancel)

- `POST /requests/approve` `{ request_id }`
- `POST /requests/reject` `{ request_id, reason }`
- `POST /requests/complete` `{ request_id }`

GET `/requests/{id}` (emergency Lambda) now also returns `allocation`, `deliveries`, `quantities`, and `partial_allocation_supported: false`.

Live Gateway must already route `/requests/cancel` to the request Lambda. Approve/reject/complete need the same routing before they work in production. This PR does not deploy Gateway.

## Authorization

- Create/cancel: existing `REQUEST_ROLES`
- Approve/reject/complete: `OPERATOR`, `ADMIN`, `OWNER`
- Creators cannot approve or reject their own requests
- Organization ownership is enforced on the backend
- `created_by` is stored for that check and is not returned in `request_view`

## Notifications

Additional inbox event codes:

- `emergency.request.approved`
- `emergency.request.rejected`
- `emergency.delivery.dispatched`
- `emergency.delivery.received`
- `emergency.request.completed`

These use the existing Notifications table. They are not email/SMS.

## Demo (after merge and frontend deploy)

1. Organization A operator registers quantity resources.
2. Organization A member creates a request (pending approval). Inventory is unchanged.
3. A different operator/admin approves.
4. Preview and confirm allocation. Inventory counters update.
5. Create a delivery from the allocation, mark ready, dispatch, report delivered, confirm receipt.
6. Complete the request from request details.
7. Reject a second request with a reason and confirm it remains listed as Rejected.

Do not use production stock for destructive tests. Live Amplify does not serve this branch until merge and deploy.

## Tests

```
python scripts/check_frontend.py
python -m pytest -q
```

## Remaining limitations

- Quantity PR #40 may still be unmerged; this branch includes that work.
- Approve/reject/complete depend on API Gateway routes equivalent to `/requests/cancel`.
- Partial allocation is intentionally omitted.
- Cross-organization emergency requests are not added; matching stays inside one organization.
- Delivery completion still lives on the deliveries page; request details only display linked records.
- No production E2E was run from this change set.
