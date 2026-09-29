# Current resource architecture

Audit date: 2026-09-29. Source of truth is the code in `src/`, `frontend/`, `scripts/lambda_manifest.py`, and `docs/universal-resource-model.md`. Older files such as `docs/database-design.md` describe only the original three-table shape and are incomplete.

This document does not change runtime behavior.

## 1. Current resource model

A resource is one allocatable unit. Identity is a client-supplied `resource_id`. There is no quantity, serial number, asset tag, condition, department, team, or assignee column.

Stored fields written by `src/resource/handler.py`:

| Field | Role |
|---|---|
| `resource_id` | Primary key. Pattern `[A-Za-z0-9][A-Za-z0-9_-]{0,63}` |
| `organization_id` | Tenant. Set from membership, not trusted as proof by itself |
| `location_id` | Must be an ACTIVE location in the same organization |
| `Location` | Copy of the location name |
| `resource_type_id` | Must be an ACTIVE organization resource type |
| `Type` | Copy of the resource type name. Legacy matcher still reads this |
| `name` | Display name, max 80 characters. Defaults to the type name |
| `Available` | Boolean. `true` means selectable. `false` means allocated |
| `attributes` | Map validated against the type schema. At most 12 string, number, or boolean fields |
| `visibility` | `PRIVATE` or `PUBLIC`. Default `PRIVATE` |

Public projection, only when visibility is `PUBLIC`:

`visibility_key`, `discovery_key`, `public_type_name`, `public_name`, `public_description`, `public_contact`, `public_city`, `public_state`, `show_availability`, and `public_status` when the organization opts to show availability.

`NETWORK` is not implemented.

Resource types live in `ResourceTypes`. Partition key `organization_id`, sort key `resource_type_id`. Types are organization-defined. `category`, `description`, `attributes_schema`, and `matching_config` exist. `DELETE` marks a type `INACTIVE`. Owners and admins write types. Operators and members can read them.

## 2. Current APIs

API `4c6dni17l3`, stage `dev`, region `eu-north-1`.

| Method and path | Function | Behavior |
|---|---|---|
| `GET /allocate/resources` | `get-resources` | List the caller's organization. Optional `location_id`, `resource_type_id`, `visibility`, `status` (`AVAILABLE` or `ALLOCATED`), `limit`, `page_token` |
| `POST /allocate/resources` | `get-resources` | Register one resource. Conditional put on `resource_id` |
| `PUT /allocate/resources` | `get-resources` | Replace metadata and visibility. Does not change `Available` |
| `POST /allocate/resources/release` | `get-resources` | Release only when an `ALLOCATED` allocation and an `ALLOCATED` emergency request exist |
| `GET /allocate/resources/history` | `get-resources` | Status history for one owned resource |
| `POST /allocate` | `emergency-resource-allocation` | Create or match an emergency request, then allocate one available resource |
| `GET /allocate/allocations` | `emergency-resource-allocation` | List allocations for the organization |
| `GET /requests` | `create-request` | Emergency requests |
| `GET /public/resources` | `erap-public-resources` | Unauthenticated public projection |
| `GET/POST/PUT/PATCH/DELETE /resource-types` | `erap-catalog` | Organization resource types |

There is no reserve, assign, transfer, maintenance, damage, retire, or quantity endpoint. There is no `GET` of one resource by id except through the list and history calls.

Authorization uses `src/shared/access.py`. `user_sub` comes only from Cognito claims. `organization_id` in the body or query selects among the caller's memberships. It is not accepted from an unauthenticated client.

Writes require `OPERATOR`, `ADMIN`, or `OWNER`, and `access="write"`. Reads allow `MEMBER` as well. Catalog writes require `ADMIN` or `OWNER`.

Billing uses `src/billing/entitlements.py`. Operational writes are allowed for a missing subscription row, `TRIALING`, `ACTIVE`, `PAST_DUE`, and `GRANDFATHERED`. `CANCELLED` and `EXPIRED` block those writes. Reads stay available.

## 3. Current DynamoDB schema

Existing tables that must stay:

`Organizations`, `OrganizationMembers`, `Locations`, `Resources`, `EmergencyRequests`, `Allocations`, `ResourceStatusHistory`, `ResourceTypes`, `RequestTypes`, `AuditEvents`, `OrganizationSubscriptions`, `BillingEvents`.

`Resources`:

- Primary key: `resource_id`
- `OrganizationLocationIndex`: `organization_id` + `location_id`
- `PublicDiscoveryIndex`: `visibility_key` + `discovery_key`

`ResourceStatusHistory`:

- Queried through `ResourceIdIndex` on `resource_id`
- Items record `previous_status`, `new_status`, `changed_at`, `reason`, `allocation_id`, `request_id`

`Allocations`:

- Primary key: `allocation_id`, shaped as `ALLOC-{request_id}`
- Queried by `OrganizationLocationIndex`
- Status is `ALLOCATED` or `RELEASED`

`EmergencyRequests`:

- Primary key: `request_id`
- Status is `PENDING`, `ALLOCATED`, or `RELEASED`

`AuditEvents` records `resource.create`, `resource.update`, `visibility.change`, `resource.release`, and `allocation.create`.

Lists query an organization index. They do not scan the table. Release still reads every allocation in the organization and filters by `resource_id` in memory. Public discovery queries `visibility_key = PUBLIC` and filters city, state, and availability in memory after the key condition.

## 4. Current status lifecycle

There is no multi-state resource status. The operational flag is `Available`.

```
Available true
    -> allocation conditional update sets Available false
    -> history previous_status AVAILABLE, new_status ALLOCATED

Available false
    -> release requires an active allocation and an allocated request
    -> Available true
    -> allocation and request become RELEASED
    -> history previous_status ALLOCATED, new_status AVAILABLE
```

`src/shared/transitions.py` governs requests and allocations only:

- Request: `PENDING -> ALLOCATED -> RELEASED`
- Allocation: `ALLOCATED -> RELEASED`

Allocation claims a resource with `ConditionExpression Available = true AND organization_id = :organization_id`. A concurrent second claim fails that condition and the allocator tries another resource. Release uses `Available = false` plus the allocation and request status conditions. Those three writes are sequential, not one transaction. A failure after the resource update can leave them temporarily apart. The allocator rolls the resource back to available if the allocation or request condition fails.

`matching.py` selects a resource only when `Available` is true, the organization matches, the type or compatible type ids match, and required numeric attributes meet their minimum. Same location is preferred unless configured otherwise.

An automatic release path exists in `src/auto_release/handler.py` for expired allocations. It uses the same available flag.

`PUT` cannot move a resource between available and allocated. The create call may set the initial `Available` value. Default is true.

## 5. Current authorization rules

| Action | MEMBER | OPERATOR | ADMIN | OWNER |
|---|---|---|---|---|
| List resources, history, allocations, requests | yes | yes | yes | yes |
| Register, update, release a resource | no | yes | yes | yes |
| Allocate through an emergency request | no | yes | yes | yes |
| Manage resource types | no | no | yes | yes |
| Public discovery | no login | no login | no login | no login |

Another organization's resource returns 404 from `require_owned`. Public responses omit `organization_id`, `resource_id`, attributes, assignments, and history. Private resources omit the public index keys, so they are not on `PublicDiscoveryIndex`.

## 6. Current frontend resource experience

`frontend/index.html` and `frontend/app.js` already have a Resources page, not a separate product.

The list shows search, status (`ALL`, `AVAILABLE`, `ALLOCATED`), type, location, and visibility filters, plus a selected-resource side panel. Counts distinguish registered, available, and allocated.

Create and edit collect resource id, name, resource type, visibility, and public name, description, contact, and show-availability when public. Location comes from the selected organization location. Advanced attributes follow the type schema.

Release is offered when the derived status is `ALLOCATED`. History is loaded from `GET /allocate/resources/history`.

Navigation already includes Resources, Requests, Allocations, Locations, Resource Types, and Billing. There is no QR view.

## 7. Current limitations

- One row is one unit. Quantity cannot be split, and negative quantity is not a concept yet.
- Status cannot represent reserved, in use, maintenance, damaged, or retired.
- Allocation cannot exist without an emergency request. `allocation_id` is `ALLOC-{request_id}`.
- Release refuses a resource that is unavailable but has no active request allocation.
- There is no reservation lock other than `Available = false`.
- Changing location is a metadata edit, not a recorded transfer.
- There is no assignee, department, team, condition, serial number, or asset tag field. A type schema can store some of those as generic attributes, with no uniqueness check.
- Search is client-side over the loaded page. The API does not query by name, serial, or asset tag.
- Resource ids are chosen by the client and are globally unique because `resource_id` is the table key.
- Public and private are the only visibility values.
- History records allocation and release only.
- Release is not a single DynamoDB transaction.
- `docs/database-design.md` omits organization, type, visibility, and index fields that production code uses.

## 8. Proposed Resource Management 2.0 changes

Keep the emergency path. Extend the same resource row.

Compatible direction:

- Keep `Available` as the allocator's claim flag. `true` only while the resource can be selected by the existing matcher.
- Add an optional `operational_status` whose default for current rows is derived: `AVAILABLE` when `Available` is true, `ALLOCATED` when it is false.
- Map everyday states onto that flag. `RESERVED`, `ALLOCATED`, `IN_USE`, `MAINTENANCE`, `DAMAGED`, and `RETIRED` keep `Available` false so the current allocator cannot select them. `AVAILABLE` and a completed `RETURNED` set `Available` true.
- Do not let a metadata edit set `operational_status`. Status changes stay on explicit operations with a server-side transition map.
- Add optional metadata: `description`, `quantity_on_hand`, `quantity_allocated`, `unit`, `condition`, `serial_number`, `asset_tag`, `department`, `responsible_team`. Absent fields mean the current single-unit resource.
- Quantity resources need a conditional update so `quantity_on_hand` cannot go below zero. Individual resources keep the existing `Available` condition.
- Reservation, direct allocation, return, transfer, maintenance, damage, and retire should be new operations on the existing resource function, not a second allocation table. Direct allocation must still write `Allocations` and `ResourceStatusHistory`. It must not require inventing a fake emergency request unless the existing allocator is reused unchanged.
- `NETWORK` may be stored and rejected by the public index. It must not set `visibility_key`.
- QR should encode the existing `resource_id` and open the authenticated resource panel. The public endpoint must keep omitting private fields and resource ids.
- Search and filters beyond the current query should use existing keys first. A new name or asset index needs its own access-pattern design before any table change.

Do not replace `Available`, do not rename `Type` or `Location`, and do not change `ALLOC-{request_id}` for emergency allocations.

## 9. Backward compatibility strategy

- Existing items without new attributes keep working. Reads derive status from `Available`.
- `POST /allocate` matching continues to require `Available = true`.
- `POST /allocate/resources/release` continues to require an allocated emergency request.
- List responses stay a JSON array unless `limit` or `page_token` is present.
- Public discovery fields stay limited to the current public projection.
- Pilot organization `ORG-D13B30D99127` is not migrated, rewritten, or used as test data.
- No new table until an access pattern cannot be served by `Resources`, `Allocations`, `ResourceStatusHistory`, or `AuditEvents`.

## 10. AWS resources that already exist and must be preserved

Do not recreate these.

- Account `481838970142`, backend region `eu-north-1`
- REST API `4c6dni17l3`, stage `dev`
- Cognito pool `eu-north-1_vv7adAAC9`, authorizer `y0hzhr`
- Amplify app `d3enpe7opotop5`, branch `main`
- Operational Lambdas and `live` aliases: `get-resources`, `create-request`, `emergency-resource-allocation`, `emergency-resource-auto-release`, `erap-catalog`, `erap-public-resources`, `erap-locations`, `erap-create-organization`, `erap-get-organization`
- Billing Lambdas, which are packaged but not deployed by the main workflow: `erap-billing`, `erap-billing-webhook`, `erap-billing-expiry`
- DynamoDB tables listed in section 3, including their current indexes
- GitHub Actions workflows `ci.yml`, `deploy-backend.yml`, `rollback.yml`, `release.yml`

`deploy-backend.yml` publishes only `PACKAGES` in `scripts/lambda_manifest.py`. A resource-handler change rides that workflow. Billing functions do not.
