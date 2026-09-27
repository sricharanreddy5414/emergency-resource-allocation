# Incident runbook

Use the smallest action that restores service. Do not delete tables, the API, Cognito, alarms, or the Amplify app. Do not run `scripts/migrate_tenant_scope.py apply`. Do not restore a DynamoDB table over the live table.

Account `481838970142`, region `eu-north-1`, API `4c6dni17l3`, stage `dev`, alias `live`.

## API unavailable

SYMPTOM: The site loads and every save or list fails, or `GET /public/resources` does not respond.

CHECK: API Gateway stage `dev` is deployed. CloudWatch shows 5XX on the API or Lambda errors. Alias `live` still exists on the nine functions.

SAFE ACTION: If a deploy just finished, roll the alias back to the previous version using `docs/rollback.md`. If the stage points at an older deployment, move it back to deployment `xuwrkf` only when that snapshot is still the known-good alias routing.

ESCALATION: Technical operator for the AWS account.

RECOVERY: Run `python scripts/smoke_test.py` and `python scripts/verify_hardening.py` after the alias or stage is restored.

## Lambda errors

SYMPTOM: CloudWatch alarm for Lambda errors, or a screen message that the request failed.

CHECK: The function log group in `eu-north-1`. Search the request id. Read the status and error code. Confirm the error started after a deploy.

SAFE ACTION: Roll alias `live` to the previous version if the errors start at that deploy. If one request is invalid, correct the form instead of rolling back.

ESCALATION: Technical operator when errors continue after rollback or the log shows a permissions failure.

RECOVERY: Confirm new invocations return the previous success rate. Leave the failed version published so it can be compared.

## Allocation failures

SYMPTOM: Allocate returns a conflict, or the resource stays available when the operator expected it to be allocated.

CHECK: Whether another allocation already took the resource. The audit event for that resource. Lambda errors on `emergency-resource-allocation`.

SAFE ACTION: Refresh the resource list. Allocate a different available resource. Do not create a duplicate resource to force the same id through.

ESCALATION: Technical operator if the conflict persists for a resource that the list shows as available.

RECOVERY: Release only the allocation this organization created, then allocate again.

## Cognito or login issue

SYMPTOM: The sign-in page fails, or the site returns to login immediately.

CHECK: The user exists in pool `eu-north-1_vv7adAAC9` and is enabled. The browser has no leftover session after logout. The authorizer `y0hzhr` is still attached.

SAFE ACTION: Sign out, close the tab, and sign in again. Reset the user through the Cognito console if the operator already manages that user. Do not turn on admin password authentication. Do not disable the authorizer.

ESCALATION: Technical operator if the hosted login page itself is down.

RECOVERY: A successful sign-in loads membership and either onboarding or the organization home.

## Public discovery issue

SYMPTOM: A public resource is missing, or a private resource appears.

CHECK: The resource visibility in the organization. The public list filters. `GET /public/resources` without a token.

SAFE ACTION: Set the resource back to PRIVATE if any private text was exposed. Edit the public fields so they contain only safe text, then set PUBLIC again.

ESCALATION: Technical operator if a private resource remains visible after it is PRIVATE, or if the response contains an organization id or internal id.

RECOVERY: Confirm the private resource is absent and the public resource shows only the allowed fields.

## DynamoDB issue

SYMPTOM: Reads and writes fail, or an alarm reports throttling or system errors.

CHECK: Table status is ACTIVE. PITR is still enabled. Deletion protection is still enabled. The error is not a single bad request.

SAFE ACTION: Wait and retry if the error is transient. Do not delete the table. Do not turn off PITR or deletion protection. Do not scan and rewrite legacy rows.

ESCALATION: Technical operator. If data was lost, restore point-in-time to a new table name and plan a copy. Do not overwrite the live table.

RECOVERY: Application reads succeed and the legacy counts are unchanged.

## Deployment failure

SYMPTOM: GitHub **Deploy backend** fails.

CHECK: The failed step in the Actions log. The job's failure step should move alias `live` back when a summary was written.

SAFE ACTION: Leave `main` on the last good commit if the alias was restored. Fix the code on a branch. Do not deploy a zip from a laptop.

ESCALATION: Technical operator if the alias did not return to the previous version.

RECOVERY: `python scripts/verify_hardening.py` reports the live alias and the authorizer.

## Rollback

SYMPTOM: The current version mis-handles a pilot action and the previous version was good.

CHECK: The published version number before the bad deploy. Alias `live` on each function.

SAFE ACTION: Run **Rollback backend** with that version. Do not delete the bad version. Do not roll DynamoDB back with the code.

ESCALATION: Technical operator if smoke checks fail after the alias move.

RECOVERY: Smoke checks pass and the pilot can repeat the failed action.

## Alarm received

SYMPTOM: A notification from `ERAP-Production-Alarms`.

CHECK: The alarm name and the time window in CloudWatch. Recent deploys. API 5XX and Lambda error graphs.

SAFE ACTION: Follow the section above that matches the alarm. Do not delete the alarm or remove its action. Do not send a test notification to prove the topic.

ESCALATION: Technical operator if the alarm is still in ALARM after the safe action.

RECOVERY: The alarm returns to OK without a manual state change.

## Frontend unavailable

SYMPTOM: The Amplify URL does not load or shows an old broken page.

CHECK: Amplify app `d3enpe7opotop5`, branch `main`, latest job status.

SAFE ACTION: Redeploy the last successful Amplify job from the console history. Do not delete the app or disconnect the repository.

ESCALATION: Technical operator if hosting itself is failing.

RECOVERY: The site loads, sign-in is offered, and a signed-out call to a protected API still returns 401.
