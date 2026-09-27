# CI/CD runbook

## 1. Open a pull request

Branch from `main`, commit, and open a pull request. `.github/workflows/ci.yml` checks out the code, installs `requirements.txt` and `requirements-ci.txt`, and runs:

- `python -m pytest -q`
- `python -m compileall -q src scripts`
- `python scripts/check_frontend.py`
- `python scripts/package_lambdas.py --check`
- `python scripts/check_workflows.py`
- `python scripts/security_scan.py`

CI uses Python 3.14 and Node 22. It does not call AWS. A failing check blocks a healthy merge. Do not delete tests to make it pass.

## 2. What reaches the live backend

Merging to `main` starts two things:

- CI runs again.
- **Deploy backend** assumes `ERAP-GitHub-Deploy` in the `development` environment, runs the tests, uploads the nine Lambda zips, publishes a version, and moves alias `live`.

Pull requests do not deploy.

## 3. Staging

Nothing deploys to staging. The `staging` GitHub environment is reserved and has no AWS stack. See `docs/environments.md`.

## 4. Production promotion

The public Amplify app builds `main` on every push. That is the frontend promotion.

For a controlled backend redeploy, open Actions, select **Release**, choose branch `main`, and run it. Leave the commit empty to release that ref, or paste a full 40-character SHA. The job uses the `production` environment and `ERAP-GitHub-Production`.

Add yourself as a required reviewer on the `production` environment in GitHub: Settings, Environments, `production`, Required reviewers. The repository cannot store that reviewer in git. Until a reviewer is set, the production workflow can be started by anyone with permission to run Actions on `main`.

## 5. Verify a deployment

The deploy, release, and rollback jobs run `python scripts/smoke_test.py` and `python scripts/verify_hardening.py`.

Smoke checks, without creating data:

- `GET /public/resources` returns 200 and does not include `organization_id`
- an oversized page and a bad page token return 400
- `/allocate/resources`, `/resource-types`, and `/requests` return 401
- the Amplify URL returns 200 and references `app.js`

The deploy job also waits up to six minutes for the Amplify job for that commit. A failed Amplify job fails the workflow. A job that is still running is reported and does not by itself fail the backend deploy.

Hardening checks PITR, deletion protection, throttling, CORS, alarms, and authorizer `y0hzhr`.

## 6. Identify the deployed commit

```text
aws lambda get-alias --function-name emergency-resource-allocation --name live --region eu-north-1
```

The alias description is `commit=<sha>`. The GitHub job summary lists the same SHA, the new version, and the previous version.

## 7. Roll back

Use **Rollback backend** with the full SHA, or follow `docs/rollback.md`. Do not delete the function to undo a deploy.

## 8. Do not change these by hand

- Cognito authorizers and which routes are public
- CORS origin and stage throttling
- PITR and deletion protection
- the GitHub role trust policies, except through the JSON files and `scripts/setup_github_oidc.py`
- legacy rows, and do not run `scripts/migrate_tenant_scope.py apply`

## 9. AWS authentication

Deploy jobs request an OIDC token and assume the role for their environment. CI has no AWS credentials. Details are in `docs/github-aws-oidc.md`.

## 10. A failed deployment

Stop. Read the failed step. Do not delete the API, tables, or functions.

If tests failed, fix the code and open a pull request. If the Lambda upload failed partway, run **Rollback backend** with the last good SHA so every function is uploaded again. If smoke tests fail after a publish, roll back the same way. If OIDC fails, compare the Actions error with the trust policy. The usual miss is a workflow filename, a branch other than `main`, or the environment name. If hardening verification fails, the code publish may already have finished; fix the AWS setting that drifted and rerun verification. Do not turn throttling, PITR, or the authorizer off to make the check pass.
