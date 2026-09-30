# Rollback

Rollback restores a previous git commit onto the existing functions. It does not restore DynamoDB and it does not delete resources.

## Backend

Live API Gateway integrations invoke alias `live`. Moving that alias restores the previous published version without uploading a zip:

```text
python scripts/set_live_version.py --version <previous-version>
python scripts/smoke_test.py
```

The nine functions in `PACKAGES` do not share one version number. `--version` moves every one of them to that same number. Use it only when you have checked `get-alias` and that number is the intended version for each function. Exchange, notifications, and billing aliases are outside `PACKAGES` and this command does not move them.

From GitHub, open **Rollback backend**, run it from `main`, and enter the version number. Leave the commit empty. Use a full SHA only when the good code has no published version. The workflow then uploads that commit and points `live` at the new version.

A failed **Deploy backend** job runs `python scripts/set_live_version.py --from-summary`. For each function in `dist/deploy-summary.json`, it reads the current `live` version and moves it to `previous_version` only when `live` is still the `version` that job published. If `live` is already a different version, the script prints `left <function>` and does not change that alias. GitHub deploy, release, and rollback jobs share the concurrency group `erap-backend-deploy` and wait instead of overlapping. Do not run `scripts/deploy_backend.py` on a workstation while that job is still verifying.

## API Gateway

Routine deploys do not create an API deployment. Phase 8 published deployment `xuwrkf`, which points integrations at alias `live`. The previous snapshot is `p29gcw`.

If a later change publishes a bad API snapshot, point the stage back at the previous deployment id:

```text
aws apigateway update-stage --rest-api-id 4c6dni17l3 --stage-name dev --region eu-north-1 --patch-operations op=replace,path=/deploymentId,value=p29gcw
```

Use the real previous id if it is no longer `p29gcw`. Do not delete deployments. Stage throttling is a stage setting and is not stored inside the deployment snapshot. After any API change, run `python scripts/verify_hardening.py`.

## Frontend

Amplify keeps job history for branch `main`. In the Amplify console, open the app, branch `main`, and redeploy the last successful job. The CLI equivalent is to start that job again from the Amplify console history. Do not delete the app.

A backend rollback does not move the Amplify branch backward. The published site stays on the latest successful `main` build until someone redeploys an older Amplify job.

## Database

Do not roll DynamoDB backward as part of a code rollback. Point-in-time recovery can restore a table to a new table name. Do not restore over the live table. Do not run `scripts/migrate_tenant_scope.py apply` from a rollback. Schema and item changes have to stay compatible with the previous commit.
