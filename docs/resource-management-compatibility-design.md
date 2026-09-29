# Resource management compatibility design

Status: Phase 1 foundation and Phase 2 everyday operations are implemented in code on `feature/resource-management-2`. No migration and no deployment in these phases.

## Phase 2 implementation (everyday operations)

Code lives in `src/shared/everyday_operations.py`, wired from `src/resource/handler.py` on the existing `get-resources` Lambda.

Operations:

- Individual reservation and reservation release (`POST .../reserve`, `POST .../reservation-release`)
- Individual everyday allocation and return (`POST .../everyday`, `POST .../everyday/return`)
- Quantity reservation, allocation, and return on the same routes when `tracking_mode = QUANTITY`

Everyday allocations use `allocation_type = EVERYDAY`, ids `EVERYDAY-...`, active status `OPEN`, finished status `RETURNED`. Emergency rows stay `ALLOC-{request_id}` with status `ALLOCATED`.

Concurrency uses conditional `UpdateItem` and `TransactWriteItems` (resource update plus allocation put/return). Billing and tenant checks reuse `authorize()` from `access.py`.

Emergency compatibility:

- `src/shared/resource_state.py` — `emergency_matchable()` and claim condition helpers
- `src/shared/matching.py` — matcher skips non-individual and non-available operational states
- `src/allocation/service.py` — claim sets `operational_status = ALLOCATED` with expanded condition; failed allocation put restores `AVAILABLE`
- `src/resource/handler.py` — emergency release restores `operational_status = AVAILABLE` when safe

Auto-release continues to query `status = ALLOCATED` only; everyday `OPEN` rows are ignored.

Tests: `tests/test_everyday_resource_operations.py` plus the full existing suite.

API Gateway methods for the new paths are not added in this phase; the handler routes exist for the next infra step.

## Phase 2.5 implementation (hardening)

Added `tests/test_everyday_resource_hardening.py` covering tenant isolation, role and billing gates on the resource handler, individual and quantity concurrency, transact rollback, emergency matcher matrix, auto-release compatibility, public projection safety, validation, history/audit on success only, and emergency allocation regression (`ALLOC-{request_id}`).

No production or pilot data changes. No API Gateway wiring. `scripts/verify_hardening.py` run against account `481838970142` / `eu-north-1` after `aws login`.

## Phase 3 implementation (API Gateway exposure)

Repeatable script: `scripts/expose_everyday_resource_routes.py`.

Wires Cognito-protected `POST` plus `OPTIONS` for the four everyday paths on API `4c6dni17l3` stage `dev`, integrating `get-resources:live` the same way as `/allocate/resources/release`. Existing `/allocate` and `/allocate/resources/release` are verified unchanged. Lambda invoke permission already covers `execute-api:...:4c6dni17l3/*/*`.

Smoke checks for unauthenticated `401` and `OPTIONS` `200` live in `scripts/smoke_test.py`.

## Foundation implementation (Phase 1)

Code lives in `src/shared/resource_state.py` and is packaged with `get-resources`.

Helpers:

- `RESOURCE_OPERATIONAL_STATUSES` — allowed stored everyday states
- `normalize_operational_status` — validate an explicit value
- `normalize_tracking_mode` — default missing to `INDIVIDUAL`
- `effective_operational_status` — read-time rule using stored `operational_status` or `Available`
- `quantity_snapshot` / `validate_quantity_fields` — non-negative integers and `available + reserved + allocated = total`
- `initialize_new_resource_fields` — defaults for new rows only
- `lifecycle_fields_from_body` — blocks lifecycle fields on ordinary `PUT`

New resource defaults:

- `operational_status = AVAILABLE` when omitted
- `tracking_mode = INDIVIDUAL` when omitted
- `QUANTITY` requires `quantity_total`, sets `Available = false`, and initializes `quantity_available = quantity_total`, `quantity_reserved = 0`, `quantity_allocated = 0`

Existing rows are not scanned or backfilled. `PUT /allocate/resources` rejects attempts to change `Available`, `operational_status`, `tracking_mode`, or quantity fields.

Emergency allocation stays the authority for `Available`. Everyday operations sit beside that flag. Pilot rows are not rewritten.

## 1. Existing `Available` semantics

`Available` is the emergency matcher’s claim flag.

`src/shared/matching.py` selects a resource only when `Available` is boolean true, or the string `"true"`. A row that has `status` but no `Available` is treated as available only when that legacy `status` is `AVAILABLE`. Production writes use the boolean.

`src/allocation/service.py` claims the row with:

`Available = true AND organization_id = :organization_id`

and then sets `Available` false. The allocation id remains `ALLOC-{request_id}`. The emergency request moves `PENDING` to `ALLOCATED`.

Release in `src/resource/handler.py` requires an allocation whose `status` is `ALLOCATED` and whose `request_id` belongs to an `ALLOCATED` emergency request. It sets `Available` true and both records to `RELEASED`.

`src/auto_release/handler.py` queries `AllocationStatusIndex` for `status = ALLOCATED` and `allocated_at` before the cutoff. A candidate without a matching emergency request is skipped. It does not free the resource in that case.

`Available = true` therefore means “the emergency matcher may claim this whole row.” It does not mean “everyday status is idle” unless the synchronization rules below hold.

## 2. New `operational_status` semantics

`operational_status` is the everyday lifecycle. It is optional.

Allowed stored values:

`AVAILABLE`, `RESERVED`, `ALLOCATED`, `IN_USE`, `MAINTENANCE`, `DAMAGED`, `RETIRED`

`REGISTERED` and `RETURNED` are not stored. A successful everyday return writes history and then stores `AVAILABLE`.

Read rule for rows that omit the attribute:

| `Available` | Effective everyday status |
|---|---|
| true | `AVAILABLE` |
| false | `ALLOCATED` |

This derivation is read-only. Nothing backfills old rows.

New individual resources are stored with `operational_status = AVAILABLE` and `Available = true`.

## 3. State transition matrix

Only these everyday moves are valid. Anything else is a conflict.

| From | To |
|---|---|
| `AVAILABLE` | `RESERVED`, `ALLOCATED`, `MAINTENANCE`, `DAMAGED`, `RETIRED` |
| `RESERVED` | `ALLOCATED`, `AVAILABLE` |
| `ALLOCATED` | `IN_USE`, `AVAILABLE` |
| `IN_USE` | `AVAILABLE` |
| `MAINTENANCE` | `AVAILABLE` |
| `DAMAGED` | `MAINTENANCE`, `RETIRED` |

`RETIRED` has no outward transition.

Emergency allocation and emergency release are not everyday transitions. They are the existing request flow, with the extra conditions in section 5.

Metadata edit cannot set `operational_status` or `Available`.

## 4. Relationship between `Available` and `operational_status`

The matcher does not read `operational_status`. A reserved laptop with `Available` left true would be claimed by the next emergency request. Reservation, maintenance, damage, retirement, everyday allocation, and in-use must therefore set `Available` false.

| Everyday status | `Available` | Emergency matcher |
|---|---|---|
| `AVAILABLE` | true | May claim the row |
| `RESERVED` | false | Must not claim |
| `ALLOCATED` | false | Must not claim |
| `IN_USE` | false | Must not claim |
| `MAINTENANCE` | false | Must not claim |
| `DAMAGED` | false | Must not claim |
| `RETIRED` | false | Must not claim |
| Quantity pool | false | Must not claim |

`AVAILABLE` is the only everyday state that may set `Available` true.

The emergency claim condition becomes:

`Available = true AND organization_id = :organization_id AND (attribute_not_exists(operational_status) OR operational_status = AVAILABLE) AND (attribute_not_exists(tracking_mode) OR tracking_mode = INDIVIDUAL)`

For every current row, `operational_status` and `tracking_mode` are absent, so this condition matches today’s `Available = true` check.

## 5. Emergency allocation behavior

Unchanged sequence:

1. Match a resource with `Available` true.
2. Conditional update sets `Available` false.
3. Put `ALLOC-{request_id}` if that id does not exist.
4. Move the emergency request from `PENDING` to `ALLOCATED`.
5. Append history `AVAILABLE` to `ALLOCATED`.

The same resource update also sets `operational_status = ALLOCATED` when the claim condition succeeds. That is one `UpdateItem`, not a second write and not a migration. A concurrent reservation fails the condition and cannot be overwritten.

Emergency release still requires the emergency allocation and the emergency request. On success it sets `Available = true` and `operational_status = AVAILABLE` only when `operational_status` is absent or `ALLOCATED`. It must not clear `MAINTENANCE`, `DAMAGED`, or `RETIRED`. Those states cannot be emergency-held if section 4 is enforced, so this is a guard, not a new release path.

`allocation_id` stays `ALLOC-{request_id}`.

## 6. Everyday allocation behavior

Everyday allocation does not create an `EmergencyRequests` row and does not call `POST /allocate`.

`Allocations` can hold it. The table key is only `allocation_id`. Existing ids are `ALLOC-` plus a request id. Everyday ids use a separate prefix, `EVERYDAY-`, so they cannot collide.

Required distinction:

| | Emergency | Everyday |
|---|---|---|
| `allocation_type` | absent, read as `EMERGENCY` | `EVERYDAY` |
| `allocation_id` | `ALLOC-{request_id}` | `EVERYDAY-{unique}` |
| `request_id` | required | absent |
| Active `status` | `ALLOCATED` | `OPEN` |
| Finished `status` | `RELEASED` | `RETURNED` |
| `organization_id`, `location_id` | required | required |

`status` must not be `ALLOCATED` on an everyday row. `AllocationStatusIndex` and `POST /allocate/resources/release` both treat that word as an emergency hold. An everyday `ALLOCATED` row would be loaded by the 30-minute auto-release query. Auto-release would skip it for lack of a request, and manual release could pick it first and then fail because `request_id` is missing.

Everyday fields, all optional except identity and status:

`resource_id`, `organization_id`, `location_id`, `allocation_type`, `quantity`, `purpose`, `assigned_to`, `department`, `team`, `status`, `created_at`, `updated_at`, `created_by`, `expected_return_at`, `returned_at`.

`assigned_to` stores the member subject only. It is not copied to the public projection.

One individual resource has at most one active everyday allocation. The resource condition is `operational_status = AVAILABLE` and `Available = true`, then both become `ALLOCATED` and false. Return sets the resource back to `AVAILABLE` and true, and the allocation to `RETURNED`.

Emergency release ignores `allocation_type = EVERYDAY`. Rows that omit `allocation_type` stay on the emergency path. That is a predicate on the existing filter, not a new release flow.

## 7. Reservation behavior

Reservation is `AVAILABLE` to `RESERVED` for one individual resource.

The conditional update requires `Available = true`, organization ownership, and `operational_status` absent or `AVAILABLE`. It sets `operational_status = RESERVED` and `Available = false`. A second reservation fails that condition.

Clearing a reservation requires `operational_status = RESERVED` and sets `AVAILABLE` and `Available = true`. It does not release an emergency allocation.

Quantity reservation decrements `quantity_available` and increments `quantity_reserved` in one conditional update. It does not flip the row into the emergency matcher.

## 8. Quantity behavior

`tracking_mode` is optional.

| Mode | Meaning |
|---|---|
| absent or `INDIVIDUAL` | Current one-row unit. No quantity math |
| `QUANTITY` | Stock pool. Never an emergency match candidate |

Quantity attributes:

- `quantity_total`
- `quantity_available`
- `quantity_reserved`
- `quantity_allocated`

Invariant: `quantity_total = quantity_available + quantity_reserved + quantity_allocated`.

A quantity mutation uses one `UpdateItem` with `quantity_available >= :requested` and `tracking_mode = QUANTITY`. Failure leaves the numbers unchanged. The result cannot be negative.

Creating a quantity resource stores `Available = false`. The matcher also skips `tracking_mode = QUANTITY`, so a later edit cannot accidentally expose the pool by setting `Available` true.

Existing resources, including `PILOT-MED-001`, have no quantity fields and stay individual units.

## 9. Concurrency strategy

Individual claim, reservation, maintenance, damage, and retire:

`ConditionExpression` on `organization_id`, current `Available`, and current `operational_status` (or its absence).

Emergency claim adds the tracking-mode absence check from section 4. The allocation put remains `attribute_not_exists(allocation_id)`.

Quantity claim:

`quantity_available >= :requested` on the same item update that adjusts the four counters.

No frontend check is authoritative. No transaction is required for a single-item update. A multi-item everyday allocation uses `TransactWriteItems` for the resource update plus the new allocation put, so a failed put cannot leave the resource held. Emergency allocation already has a compensating `Available = true` write if the allocation put fails. That compensation must also restore `operational_status` to `AVAILABLE` when this update starts setting it.

## 10. Assignment

Optional resource fields: `assigned_to`, `assigned_department`, `assigned_team`.

Assignment does not change `Available` or `operational_status`. It appends an audit event with the previous and new values, the actor, and the time. Public discovery does not receive these fields.

## 11. Transfer

No transfer table.

A transfer is allowed only from `AVAILABLE`, and only to an `ACTIVE` location in the same organization. One conditional update changes `location_id` and `Location` while `operational_status` is `AVAILABLE` or absent and `Available` is true. `ResourceStatusHistory` stores `reason = RESOURCE_TRANSFERRED` with source and destination ids. The resource id does not change.

`RETIRED`, `RESERVED`, `ALLOCATED`, `IN_USE`, `MAINTENANCE`, and `DAMAGED` cannot transfer.

Quantity transfer of a partial stock is deferred. Moving a whole quantity row is the same location update. Splitting stock across locations would need a second resource identity and is out of this design.

## 12. Maintenance

`AVAILABLE` to `MAINTENANCE` sets `Available` false.

`MAINTENANCE` to `AVAILABLE` sets `Available` true.

History records actor, time, and optional note. There is no maintenance table.

## 13. Damage

`AVAILABLE` to `DAMAGED` sets `Available` false.

`DAMAGED` may move to `MAINTENANCE` or `RETIRED`. Both keep `Available` false until a later maintenance completion returns the resource to `AVAILABLE`.

The optional `condition` field is not a second status. See section 11 of the product model below.

## 14. Retirement

`RETIRED` stays in `Resources`. It is not deleted.

A retired resource cannot be reserved, everyday-allocated, emergency-matched, or transferred. Authorized members can still read it and its history.

If the resource was `PUBLIC`, retirement removes the public index attributes in the same update, the same way a private visibility write already does. A retired resource is not publicly listed.

## 15. Visibility

Stored values become `PRIVATE`, `PUBLIC`, and `NETWORK`.

`PUBLIC` behavior is unchanged: `visibility_key = PUBLIC` and `discovery_key` feed `PublicDiscoveryIndex`.

`PRIVATE` and `NETWORK` store `visibility` only. They do not write `visibility_key` or `discovery_key`. `GET /public/resources` already returns rows only when `visibility_key` is `PUBLIC`, so network rows cannot appear there.

Resource exchange is not implemented.

Public responses stay limited to type, public name, city, state, description, contact, and optional availability. They do not gain assignment, quantity, serial, notes, or history.

## 16. Backward compatibility

| Existing row | Read result | Emergency behavior |
|---|---|---|
| `Available` true, no `operational_status` | `AVAILABLE` | Still matchable |
| `Available` false, no `operational_status` | `ALLOCATED` | Still not matchable; release still requires the emergency request |
| New individual resource | stored `AVAILABLE`, `Available` true | Matchable until an everyday hold |

List responses stay a JSON array unless `limit` or `page_token` is sent. New fields may appear on resource objects. Clients that ignore unknown fields keep working.

`allocation_type` is absent on current allocation rows and means emergency.

## 17. Migration strategy

No scan, no backfill, no pilot rewrite.

`ORG-D13B30D99127` and `PILOT-MED-001` are not updated by a migration. A later real emergency allocation may set `operational_status` because that is part of the claim update, not because a job walked the table.

## 18. API changes

Existing routes stay:

- `GET` and `POST /allocate/resources`
- `PUT /allocate/resources`
- `POST /allocate/resources/release`
- `GET /allocate/resources/history`
- `POST /allocate`
- `GET /public/resources`

`PUT` continues to ignore attempts to set `Available` or `operational_status`.

New operations follow the current static-path style, on the existing `get-resources` function:

- `POST /allocate/resources/reserve`
- `POST /allocate/resources/release-reservation`
- `POST /allocate/resources/everyday-allocate`
- `POST /allocate/resources/everyday-return`
- `POST /allocate/resources/maintenance`
- `POST /allocate/resources/damage`
- `POST /allocate/resources/retire`
- `POST /allocate/resources/transfer`
- `POST /allocate/resources/assign`

Each body carries `resource_id`. The organization comes from `authorize`, not from a trusted body field. These routes do not exist until a later implementation adds API Gateway methods. They are not added in this design step.

Errors stay `message` plus `error.code`, `error.message`, and `error.request_id`. Invalid transitions use the existing conflict status.

## 19. Frontend changes

The current Resources page remains the only resource destination.

When the operations exist, the detail panel labels two different facts:

- Emergency availability: Available or Not available, from `Available`
- Operational status: the effective everyday status

The list uses the same two labels. Actions are shown only when the backend allows that role and transition. No control is added before its route exists.

Create stays minimal: name, type, and location. Description, tracking mode, quantity, condition, serial number, asset tag, department, team, and visibility stay optional.

A later QR action may encode the authenticated application URL plus `resource_id`. It does not call the public API and does not publish the resource.

## 20. Testing strategy

Add tests before the behavior ships:

1. A row with no `operational_status` reads `AVAILABLE` or `ALLOCATED` from `Available`.
2. A new individual resource stores `operational_status = AVAILABLE`.
3. Emergency allocation still claims `Available` true and writes `ALLOC-{request_id}`.
4. Emergency release still requires the emergency request and restores `Available`.
5. Emergency allocation does not claim `RESERVED`, `MAINTENANCE`, `DAMAGED`, `RETIRED`, or `QUANTITY`.
6. Everyday allocation does not write `EmergencyRequests` and does not use status `ALLOCATED`.
7. An everyday hold is not selected by manual release or auto-release.
8. Two reservations of one individual resource: one succeeds.
9. Two emergency claims of one individual resource: one succeeds.
10. Quantity conditional update rejects a request larger than `quantity_available`.
11. Maintenance, damage, and retirement leave `Available` false.
12. Another organization receives 404.
13. `MEMBER` cannot operate. `OPERATOR` can operate. Catalog rights stay with `ADMIN` and `OWNER`.
14. `CANCELLED` and `EXPIRED` still block operational writes.
15. `PRIVATE` and `NETWORK` are absent from public discovery. `PUBLIC` projection is unchanged.
16. History and audit records contain the transition and actor.

The existing suite must stay green.

## 21. Rollback strategy

The design adds optional attributes and, later, new routes. Rollback of unreleased code is reverting the commit. Deployed code rolls back through the existing alias workflow.

No index or table is created, so there is no index rollback. Rows written with the new attributes remain readable by the old code because old code ignores unknown attributes. An old deployer will not understand `operational_status`, but `Available` will still be correct if section 4 was followed. Everyday `OPEN` allocations are invisible to auto-release because their status is not `ALLOCATED`.

## Product fields that are not a second status

`condition` is optional metadata: `GOOD`, `FAIR`, `DAMAGED`, `UNDER_MAINTENANCE`. It does not drive matching. The type `attributes` map remains the extension point for organization-defined fields. `condition` is top-level only so the UI does not require a schema field that older types lack. It is not copied from `attributes`.

`serial_number` and `asset_tag` are optional strings. This phase does not enforce uniqueness. A unique lookup would need a new GSI, which this design deliberately does not add.

`department` and `responsible_team` are optional labels. They are not an HR model.

## What this design refuses

- A new table
- A new GSI
- A mass write of pilot resources
- Replacing or renaming `Available`
- Fake emergency requests
- Everyday rows with `status = ALLOCATED`
- Quantity pools entering the emergency matcher
- Public exposure of assignment, history, or network resources
