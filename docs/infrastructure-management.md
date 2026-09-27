# Infrastructure management

The running ERAP resources stay as they are. Phase 7 adds only the GitHub OIDC provider and two IAM roles.

## Managed by the pipeline

| Resource | Change |
|---|---|
| GitHub OIDC provider `token.actions.githubusercontent.com` | created once |
| `ERAP-GitHub-Deploy` | created once |
| `ERAP-GitHub-Production` | created once |
| Lambda code, published versions, and alias `live` | updated on deploy |

`python scripts/setup_github_oidc.py` applies `infra/github-deploy-trust.json`, `infra/github-production-trust.json`, and `infra/github-deploy-policy.json`. Run it again after those files change. It does not create tables, APIs, or users.

## Left outside infrastructure code

These already exist and are not imported or recreated:

- DynamoDB tables and their indexes, PITR, and deletion protection
- API Gateway `4c6dni17l3`, stage `dev`, authorizer `y0hzhr`
- Cognito user pool `eu-north-1_vv7adAAC9`
- The nine application Lambdas and their execution roles
- EventBridge rule `EmergencyResourceAutoReleaseRule`
- CloudWatch alarms from Phase 6
- Amplify app `d3enpe7opotop5`

Importing them into CloudFormation or Terraform can replace or delete a resource if the template does not match. That import was not run.

## Future staging

A staging stack is not defined in code. Adding one later should be new resources with new names, not a copy that overwrites `dev`.

## Manual changes to avoid

Do not edit a Lambda in the console without a matching git commit. The next deploy will publish whatever is on `main`. Do not remove authorizers, throttling, PITR, or deletion protection while changing the API. Do not attach a broader policy to the GitHub roles.
