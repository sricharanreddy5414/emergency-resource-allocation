# Environments

ERAP has one AWS backend. The API stage is named `dev`, and the Amplify production site calls that stage. A second stack was not created.

## Development

GitHub environment: `development`.

A push to `main` runs `.github/workflows/deploy-backend.yml`. It assumes `ERAP-GitHub-Deploy` and updates the nine existing Lambda functions in `eu-north-1`. Amplify also builds `main` by itself and publishes `https://main.d3enpe7opotop5.amplifyapp.com`.

This is the environment used for day-to-day testing. It is also what the public site calls, because there is no separate production API.

## Staging

GitHub environment `staging` is not a deploy target, and no staging AWS resources exist.

Staging infrastructure is intentionally deferred because the current platform has no production tenant data and creating a second full AWS stack would add unnecessary complexity at this stage.

## Production

GitHub environment: `production`.

`.github/workflows/release.yml` and `.github/workflows/rollback.yml` assume `ERAP-GitHub-Production` and deploy the same nine functions. They run only from a manual dispatch on `main`, and the GitHub environment is the approval gate. They do not run on pull requests.

The Amplify branch `main` is already marked `PRODUCTION` in Amplify and builds on every push to `main`. That existing behavior was left in place. Backend production promotion does not create a second frontend.

## Regions and configuration

| Item | Value |
|---|---|
| Backend region | `eu-north-1` |
| API | `4c6dni17l3`, stage `dev` |
| Cognito pool | `eu-north-1_vv7adAAC9` |
| Authorizer | `y0hzhr` |
| Frontend region | `us-east-1` |
| Amplify app | `d3enpe7opotop5` |
| Allowed browser origin | `https://main.d3enpe7opotop5.amplifyapp.com` |

Secrets are not stored in git. GitHub Actions uses OIDC. Lambda configuration stays table names and the existing index name. The frontend has no Amplify environment variables and no backend secrets.
