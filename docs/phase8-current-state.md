# Phase 8 current state

This is the read-only snapshot taken at the start of Phase 8, before the alias, alarm, and log-retention changes. Commit `413394bdb54b6ac72305fd37ca12430eee4046a1`.

## Deployment

All nine Lambdas had alias `live` at version 2. The alias description was `commit=413394bdb54b6ac72305fd37ca12430eee4046a1`. API Gateway stage `dev` was deployment `p29gcw`. Every Lambda integration URI invoked the unqualified function, which is `$LATEST`. `EmergencyResourceAutoReleaseRule` targeted the unqualified auto-release function. Protected methods used Cognito authorizer `y0hzhr`. `GET /public/resources` had no authorizer. Stage throttle was 20 requests per second with burst 40. Public `GET` was 5 per second with burst 10. CORS on the shared gateway errors allowed only the Amplify origin.

## GitHub

CI and the development deploy were working through OIDC. The only GitHub environment was `development`. It had no protection rules and no branch policy. The `production` environment did not exist. `gh` was not available, so reviewers could not be set from this machine.

## Cognito

User pool `eu-north-1_vv7adAAC9` had 7 users and MFA off. The web app client allows refresh, user auth, and SRP. It does not allow admin password authentication. A script cannot sign in with the existing client without changing that configuration.

## Data and operations

Organization tables were still empty at the previous count. Legacy unscoped rows were still present and were not modified. Point-in-time recovery and deletion protection were enabled. The five `ERAP-*` alarms had no actions and were in `OK`. `EmergencyResourceAllocation-Lambda-Errors` already published to `EmergencyResourceNotifications`. Lambda log groups that existed had no retention limit. Amplify had successfully built `413394b`.

## Tests

The suite baseline was 73 passing tests. Workflows do not call `scripts/migrate_tenant_scope.py`.
