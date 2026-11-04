# Delivery tracking

**Runtime code:** local repository (not in live Lambda v83).  
**Table:** `Deliveries` ACTIVE in `eu-north-1`. Runtime IAM `ERAP-Deliveries-Runtime` attached.  
**API Gateway / Lambda publish / Amplify:** not applied.  
**Governance:** Phase 14G BLOCKED / GAP-07 OPEN. Owner instruction: `docs/delivery-integration-owner-instruction.md` (not a GO).

Allocation completion is **not** delivery completion. Exchange QR handover is unchanged.

## DynamoDB

See `infra/deliveries-table.json` (`applied: true`). The live table is empty. Do not treat table creation as a delivery API release.

| Item | Value |
|---|---|
| Table | `Deliveries` (`DELIVERIES_TABLE`) |
| PK | `delivery_id` |
| GSI | `OrganizationLocationIndex` (`organization_id`, `location_id`) |
| Writes | Conditional `put_item` (create + versioned update) |
| History | Append-only `events[]` with `actor_sub` and timestamp |

## API (required, not exposed)

Lambda: `emergency-resource-allocation:live`  
Authorizer: Cognito `y0hzhr`  
GET = `READ_ROLES`; POST = `OPERATE_ROLES` + operational write entitlement.

| Method | Path |
|---|---|
| POST | `/allocate/deliveries` |
| GET | `/allocate/deliveries` |
| GET | `/allocate/deliveries/{id}` |
| GET | `/allocate/deliveries/{id}/history` |
| POST | `/allocate/deliveries/{id}/transport` |
| POST | `/allocate/deliveries/{id}/ready` |
| POST | `/allocate/deliveries/{id}/dispatch` |
| POST | `/allocate/deliveries/{id}/status` |
| POST | `/allocate/deliveries/{id}/receipt` |
| POST | `/allocate/deliveries/{id}/handover` |
| POST | `/allocate/deliveries/{id}/handover/verify` |

Plan printer (no AWS): `python scripts/expose_delivery_routes.py`

## IAM (required on the allocation function role, not applied)

`dynamodb:GetItem`, `PutItem`, `Query` on `Deliveries` and its GSI. No Scan. No DeleteItem.

## Frontend

`frontend/delivery.js` uses Cognito ID token + `DELIVERIES_API_URL`. It does **not** use `sessionStorage` for deliveries. If Gateway routes are missing, the page shows an undeployed error and empty counts.

## Workflow

Allocated → ready for dispatch → dispatched → in transit → delivered / partially delivered → receipt → handover recorded → handover verified → completed.

## Packaging

`scripts/lambda_manifest.py` includes `delivery_*.py` in `emergency-resource-allocation`. That zip is **not** published in this pass.
