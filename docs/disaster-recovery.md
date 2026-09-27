# Disaster recovery

Use this order. Do not restore a DynamoDB table over the live table, and do not run `scripts/migrate_tenant_scope.py apply` as part of recovery.

## 1. Confirm what failed

Check the alarm topic `ERAP-Production-Alarms`, the API, and alias `live`:

```text
aws lambda get-alias --function-name emergency-resource-allocation --name live --region eu-north-1
```

The description is `commit=<sha>`. The same version should be on all nine functions.

## 2. Restore backend traffic

API Gateway invokes `alias live`. Moving the alias restores the previous published code without uploading a zip:

```text
python scripts/set_live_version.py --version <previous-version>
python scripts/smoke_test.py
```

GitHub **Rollback backend** accepts that version number and uses the `production` environment. If the good code was never published, pass the full commit SHA instead. That path uploads the zip, publishes a new version, and moves `live`.

A failed deploy workflow moves `live` back to the `previous_version` values in `dist/deploy-summary.json` when those values exist.

## 3. Restore API Gateway configuration

Code rollback does not change routes. If a bad API snapshot is deployed, point the stage at the previous deployment id. The Phase 8 cutover left `p29gcw` as the previous id and created `xuwrkf` for alias traffic. Use the id from before the bad change:

```text
aws apigateway update-stage --rest-api-id 4c6dni17l3 --stage-name dev --region eu-north-1 --patch-operations op=replace,path=/deploymentId,value=<previous-id>
```

Then run `python scripts/verify_hardening.py`. Throttling, authorizers, and CORS must still match the production settings.

## 4. Restore the frontend

Amplify keeps successful jobs for branch `main`. Redeploy the last good job from the Amplify console. Do not delete the app. A backend alias change does not move the Amplify branch.

## 5. Restore data

Point-in-time recovery is enabled on every ERAP table, with deletion protection. Restore to a new table name, check the items, then cut the application over only after that check. Do not overwrite `Resources`, `EmergencyRequests`, `Allocations`, or `ResourceStatusHistory` in place.

Legacy rows have no `organization_id`. A restore or migration must not invent owners for them. `apply` stays a manual command with an explicit mapping file.

## 6. Tenant data

There is no production organization data yet. When tenants exist, a code rollback must stay compatible with the current item shape. A data restore is a separate decision from a code rollback, because it can hide allocations made after the restore time.
