# GitHub Actions and AWS OIDC

GitHub Actions assumes an IAM role. There are no long-lived access keys for CI.

## Provider

URL: `https://token.actions.githubusercontent.com`

Audience: `sts.amazonaws.com`

Account: `481838970142`

The provider is created by `python scripts/setup_github_oidc.py`.

## Roles

| Role | GitHub environment | Workflow |
|---|---|---|
| `ERAP-GitHub-Deploy` | `development` | `.github/workflows/deploy-backend.yml` on `main` |
| `ERAP-GitHub-Production` | `production` | `.github/workflows/release.yml` and `.github/workflows/rollback.yml` |

CI (`.github/workflows/ci.yml`) has no AWS role and cannot assume one. Its token permission is `contents: read`.

## Trust

Both trust policies require all of the following:

- audience `sts.amazonaws.com`
- repository `sricharanreddy5414/emergency-resource-allocation`
- git ref `refs/heads/main`
- the environment subject for that role
- the workflow file on `main`

Another repository, a pull request, or a different workflow file cannot assume the role. The policy files are `infra/github-deploy-trust.json` and `infra/github-production-trust.json`.

## Permissions

Both roles use `infra/github-deploy-policy.json`.

They can update code, publish a version, and move the `live` alias on the nine existing functions. They can read API Gateway `4c6dni17l3`, describe the ten tables and their backups, describe CloudWatch alarms in `eu-north-1`, and read Amplify jobs for app `d3enpe7opotop5`.

They cannot delete functions, change function configuration, write DynamoDB, scan tables, change API methods, or create IAM users.

## How a workflow authenticates

The job sets `permissions: id-token: write` and uses `aws-actions/configure-aws-credentials` with `role-to-assume`. The action requests a GitHub OIDC token and calls `AssumeRoleWithWebIdentity`. The AWS SDK then uses that session. The workflow does not print the token.

## Rotate or revoke

To revoke GitHub access, delete the role or remove its trust policy:

```text
aws iam delete-role-policy --role-name ERAP-GitHub-Deploy --policy-name ERAP-Deploy-Policy
aws iam delete-role --role-name ERAP-GitHub-Deploy
```

Repeat for `ERAP-GitHub-Production`. Deleting the OIDC provider revokes both roles. There is no access key to rotate.

Run `python scripts/setup_github_oidc.py` again after editing the JSON if the role should be updated.

## Security

Do not add `AWS_ACCESS_KEY_ID` or `AWS_SECRET_ACCESS_KEY` to the repository or to GitHub secrets for this pipeline. Do not widen the trust subject to `repo:*`. Production deploys still need the GitHub environment reviewers described in `docs/ci-cd-runbook.md`.
