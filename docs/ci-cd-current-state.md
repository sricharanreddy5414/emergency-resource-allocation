# CI/CD current state

This is the deployment baseline observed before the Phase 7 workflows were added. The audit was read-only.

## Repository

The backend lives under `src/`. Each Lambda is a handler plus modules copied to the zip root. There was no `.github/workflows` directory, no Amplify build file in git, and no Terraform, CDK, or CloudFormation project. `requirements.txt` lists `boto3` and `pytest` with no lock file. The frontend is static files in `frontend/` and has no `package.json`.

`scripts/migrate_tenant_scope.py` can report, validate, or apply tenant ownership. Apply was not run. Legacy rows without `organization_id` remain: Resources 4, EmergencyRequests 31, Allocations 22, ResourceStatusHistory 17. Organization tables were empty.

## How code reached AWS

Lambda code was packaged on a workstation and sent with `aws lambda update-function-code`. The nine functions are `get-resources`, `create-request`, `emergency-resource-allocation`, `emergency-resource-auto-release`, `erap-catalog`, `erap-public-resources`, `erap-locations`, `erap-create-organization`, and `erap-get-organization`. Every function had only `$LATEST`. None had a published version or an alias.

API Gateway `4c6dni17l3`, stage `dev`, is updated only when someone creates a deployment. Phase 6 left deployment `p29gcw`. Stage throttling is 20 requests per second, burst 40, and `GET /public/resources` is 5 per second, burst 10. Protected methods use Cognito authorizer `y0hzhr`. `GET /public/resources` has no authorizer. Gateway 401, 4XX, and 5XX responses allow only `https://main.d3enpe7opotop5.amplifyapp.com`.

## Amplify

App `d3enpe7opotop5` in `us-east-1` is connected to `https://github.com/sricharanreddy5414/emergency-resource-allocation`. Branch `main` is stage `PRODUCTION`, auto-build is on, and there are no Amplify environment variables. The build spec publishes `frontend/` with an empty build command. The latest successful job at audit time was commit `4ce9204c5eb5e78f12d54087373af99cbadea738`.

## Identity and configuration

No GitHub OIDC provider existed. Lambda environment variables are table names and `USER_SUB_INDEX` on the organization and location functions. The other functions use default table names in code. No AWS access keys were stored in the repository.

## Tests

The local command is `python -m pytest -q`. Frontend syntax is `node --check frontend/app.js`, plus the inline script in `frontend/index.html`.
