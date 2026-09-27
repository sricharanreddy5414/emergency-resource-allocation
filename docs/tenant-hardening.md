# Tenant hardening

## Identity

Resource, request, allocation, and history primary keys stay globally unique.
The logical tenant identity is `organization_id` plus the existing entity id.
API handlers load a record by its current key, then reject it when `organization_id` does not match the caller's membership.
No primary-key rewrite is performed in this phase.

## States

Resource availability is the boolean `Available`.

Request status:

- `PENDING` may become `ALLOCATED`
- `ALLOCATED` may become `RELEASED`
- `RELEASED` is terminal

Allocation status:

- `ALLOCATED` may become `RELEASED`
- `RELEASED` is terminal

A completed request state is not implemented.

## Location lifecycle

`DELETE /locations/{location_id}` does not remove the row.
It sets `status` to `INACTIVE` only when that location has no resources, requests, or allocations.
Locations with operational records return 409.
New operations require `ACTIVE` locations.

## Organization lifecycle

Organizations are not hard-deleted.
`status` may be `ACTIVE`, `SUSPENDED`, or `ARCHIVED`.
Membership authorization allows only `ACTIVE`.
Missing status is treated as `ACTIVE` so older rows keep working.
No suspend or archive API is added in this phase.

## Migration

`scripts/migrate_tenant_scope.py` supports `report`, `validate`, and `apply`.
`apply` requires an explicit mapping file and writes nothing when validation fails.
Ownership is never invented.
Existing operational rows stay unchanged until a reviewed mapping is applied.
