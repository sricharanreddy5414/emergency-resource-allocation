# Production hardening

This document describes the controls added so ERAP can be operated for real organizations. It does not replace the tenant or universal-resource documents.

## Authentication

Protected routes use the Cognito authorizer `y0hzhr`. Lambdas read only the Cognito `sub`. Client `user_sub`, `role`, and `organization_id` are not proof of identity. Missing authentication is HTTP 401 from API Gateway. A signed-in user without membership receives 403. A record in another organization returns 404 with no tenant details.

The browser stores the ID token in `sessionStorage` and sends it as `Authorization`. It is not written to the URL. Live request and allocation payloads are not printed to the console.

## Authorization

| Action | OWNER | ADMIN | OPERATOR | MEMBER |
|---|---|---|---|---|
| Create organization | yes | yes | yes | yes |
| Manage locations | yes | yes | no | no |
| Manage resource and request types | yes | yes | no | no |
| Read types | yes | yes | yes | yes |
| Register, update, and release resources | yes | yes | yes | no |
| Create and update requests | yes | yes | yes | yes |
| Allocate | yes | yes | yes | no |
| Publish a resource | yes | yes | yes | no |
| Public discovery | public route, no role | | | |

Frontend buttons hide some actions. The Lambda checks the role again.

## Tenant isolation

Every operational read loads by key or by the organization index, then checks `organization_id`. Public discovery reads only `PublicDiscoveryIndex`. Legacy unscoped rows are not in those indexes and are not assigned an owner.

## Input limits

JSON bodies are limited to 8192 bytes and a nesting depth of 5. Resource and request ids must match `[A-Za-z0-9][A-Za-z0-9_-]{0,63}`. Attribute schemas remain limited to 12 scalar fields. Public list size is at most 25. Catalog pages are at most 50. Resource pages are at most 100.

## Errors and correlation

API Gateway `requestId` is the request id. Error bodies keep the existing `message` field and add:

```json
{"error": {"code": "NOT_FOUND", "message": "Record not found", "request_id": "..."}}
```

Responses do not include stack traces, table names, or DynamoDB error text. Lambdas emit one JSON log line with `request_id`, route, status, duration, and error code. Tokens are not logged.

## Rate limits

Stage `dev` uses API Gateway throttling. The stage default is 20 requests per second with a burst of 40. `GET /public/resources` is 5 requests per second with a burst of 10. These are stage limits, not per-user or per-organization limits. A usage plan per tenant is not configured.

## CORS

Browser responses and the shared 401, default 4XX, and default 5XX gateway responses allow `https://main.d3enpe7opotop5.amplifyapp.com`. `OPTIONS` stays unauthenticated. `Access-Control-Allow-Origin: *` is not used for these responses.

## DynamoDB protection

All current tables use on-demand billing. DynamoDB encrypts data at rest with the AWS owned key. Point-in-time recovery and deletion protection are enabled on every ERAP table. Recovery of a table would be a point-in-time restore to a new table, followed by an application cutover. That restore was not executed against the dev data. The migration script is the only intentional scan, and it is an offline command.

## Lambda reliability

`get-resources`, `create-request`, and `emergency-resource-allocation` use a 15 second timeout and 256 MB. Auto-release keeps a 30 second timeout and uses 256 MB. Allocation claims a resource with a conditional write. A second claim receives a conflict and does not create a second allocation. Auto-release queries `AllocationStatusIndex`, skips rows without an organization, and treats an already released row as success.

## Audit

Audit writes are best-effort. A failed audit record does not fail the user operation and does not return the audit error to the client. Audit items do not store tokens.

## Monitoring

CloudWatch alarms cover API 5XX responses, allocation throttles, auto-release errors, public-discovery errors, and DynamoDB system errors on `Resources`. An existing allocation error alarm was left in place. Alarms do not yet notify an email or paging topic.

## States

Resource availability is the boolean `Available`. Requests move `PENDING` to `ALLOCATED` to `RELEASED`. Allocations move `ALLOCATED` to `RELEASED`. Resource types, request types, and locations use `ACTIVE` and `INACTIVE`. Organizations may be `ACTIVE`, `SUSPENDED`, or `ARCHIVED`; only `ACTIVE` memberships are authorized. `RELEASED` is terminal.

## Pagination

Resource, request, catalog, and public lists accept `limit` and an opaque `page_token`. Tokens are rejected when the key set or tenant does not match. Public tokens must stay on `visibility_key=PUBLIC`. A missing limit on the resource list still returns the existing JSON array, capped at 100 rows. Public pages stop at 25. Catalog pages stop at 50.

## IAM

`get-resources`, `create-request`, `emergency-resource-allocation`, and `emergency-resource-auto-release` share one execution role. That role can get, put, and update the four operational tables, query the organization-location indexes plus `AllocationStatusIndex`, `ResourceIdIndex`, and `UserSubIndex`, read organizations, locations, and type tables, and put audit events. It cannot scan. The public function can only query `PublicDiscoveryIndex`. Catalog can write type rows and audit events, and cannot delete items. Organization functions can write organizations, members, and locations. Splitting the shared operational role was left for a later phase so a permission change would not break allocation or release.

## Performance

On 27 September 2026, five calls to `GET /public/resources` from this workstation returned HTTP 200 in 707–838 ms, including network time. An oversized `limit` and an invalid page token each returned HTTP 400 in about 570 ms. Lambda duration and allocation throughput were not measured, because this phase did not create a signed-in tenant.

## Incidents

The new alarms record failures in CloudWatch and do not page anyone. `AlarmActions` is empty. A table restore would be a point-in-time restore to a new table name, then a cutover. Do not restore over the live tables. Legacy unscoped rows are still present and are excluded from tenant queries.

## Known limits

There is no per-tenant throttle, no alarm notification target, and no tested signed-in browser session in this phase. Legacy operational rows remain unscoped and excluded from tenant queries. Attribute values are scalars only. The resource screen creates resources and does not edit an existing one.
