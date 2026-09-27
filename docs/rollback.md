# Rollback

Rollback restores a previous git commit onto the existing functions. It does not restore DynamoDB and it does not delete resources.

## Backend

Live API Gateway integrations invoke the unqualified function (`$LATEST`). Existing Lambda permissions are also on `$LATEST`, not on an alias. Moving alias `live` without uploading that commit's zip would not change the API.

The rollback path is to deploy the known commit again:

```text
git checkout <40-character-sha>
python scripts/deploy_backend.py
python scripts/smoke_test.py
python scripts/verify_hardening.py
```

From GitHub, open Actions, choose **Rollback backend**, run it from `main`, and paste the full SHA. The workflow checks out that SHA, runs tests, assumes `ERAP-GitHub-Production`, and publishes a new version. Alias `live` then points at the restored code. The previous version numbers remain on the function.

The deploy summary lists `previous_version` for each function. That number is the alias target from before the deploy. It is the audit marker. Restoring traffic still requires the git commit that produced it.

## API Gateway

This pipeline does not create an API deployment. The stage deployment at the end of Phase 6 is `p29gcw`.

If a later change publishes a bad API snapshot, point the stage back at the previous deployment id:

```text
aws apigateway update-stage --rest-api-id 4c6dni17l3 --stage-name dev --region eu-north-1 --patch-operations op=replace,path=/deploymentId,value=p29gcw
```

Use the real previous id if it is no longer `p29gcw`. Do not delete deployments. Stage throttling is a stage setting and is not stored inside the deployment snapshot; after any API change, run `python scripts/verify_hardening.py`.

## Frontend

Amplify keeps job history for branch `main`. In the Amplify console, open the app, branch `main`, and redeploy the last successful job. The CLI equivalent is to start that job again from the Amplify console history. Do not delete the app.

A backend rollback does not move the Amplify branch backward. The published site stays on the latest successful `main` build until someone redeploys an older Amplify job.

## Database

Do not roll DynamoDB backward as part of a code rollback. Point-in-time recovery can restore a table to a new table name. Do not restore over the live table. Do not run `scripts/migrate_tenant_scope.py apply` from a rollback. Schema and item changes have to stay compatible with the previous commit.
