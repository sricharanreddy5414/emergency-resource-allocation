# Deployment versioning

The deployed identity is the git commit SHA.

## What gets recorded

`scripts/deploy_backend.py` reads `GITHUB_SHA` when GitHub Actions sets it, otherwise `git rev-parse HEAD`.

For each of the nine functions it:

1. uploads the zip built from that commit
2. publishes a Lambda version whose description is `commit=<sha>`
3. points alias `live` at that version, with the same description

`$LATEST` receives the same zip. API Gateway still invokes `$LATEST`, so the alias is the recorded version and the running code.

## How to see what is deployed

```text
aws lambda get-alias --function-name get-resources --name live --region eu-north-1
```

The description contains the commit. Repeat for the other function names in `scripts/lambda_manifest.py`. A matching description across all nine functions means that commit is the recorded release.

Amplify job history shows the frontend commit:

```text
aws amplify list-jobs --app-id d3enpe7opotop5 --branch-name main --region us-east-1 --max-items 5
```

## Artifacts

Zips are built in `dist/` and are not committed. The artifact is reproducible from the commit: `python scripts/package_lambdas.py --output dist`.

There is no separate release number. The SHA is the version.

## What this pipeline does not version

API Gateway deployment `p29gcw` stays in place while this pipeline only changes Lambda code. DynamoDB items are not versioned by the deploy. A data change needs a separate migration, and `scripts/migrate_tenant_scope.py apply` is not part of any workflow.
