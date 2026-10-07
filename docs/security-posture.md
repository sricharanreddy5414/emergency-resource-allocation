# Security posture

Phase 12 inspected the system after notifications hardening, observability, and disaster recovery. This is the recorded posture. It is not a penetration test.

## 1. Authentication

Cognito pool `eu-north-1_vv7adAAC9` in `eu-north-1` is the only user directory. API Gateway authorizer `y0hzhr` validates the caller token before a protected Lambda runs. Live MFA is `OPTIONAL`. Software-token MFA is enabled. SMS MFA is disabled. That configuration was read and left unchanged. There are no Cognito Lambda triggers. Passwords and authenticator secrets are not in Git.

The application reads the Cognito `sub` from the authorizer claims. It does not accept a client-supplied subject as identity.

## 2. Authorization

`src/shared/access.py` is the server-side boundary. The check is: Cognito `sub`, then `UserSubIndex`, then an active membership, then the requested organization, then organization status, then the route's role allowlist, then billing write access when the route requires a write. A body or query `organization_id` is only a selection among memberships the caller already has. It is not proof of membership by itself. Inactive membership, a missing membership, a role outside the allowlist, and a billing block are denied. Those responses do not reveal whether some other organization exists.

## 3. Tenant isolation

DynamoDB is not public. Every production read or write goes through a Lambda that has already resolved the caller's organization. Restoring a table copies every tenant, which is why a restore target must stay disconnected from the API. The pilot organization was not read or written in this phase.

## 4. IAM

Runtime roles do not have `AdministratorAccess`, `dynamodb:Scan`, `dynamodb:DeleteItem`, `dynamodb:BatchWriteItem`, `RestoreTableToPointInTime`, or `backup:*`. The four original operational functions share `emergency-resource-allocation-role-12qymvku`. Its inline actions are `GetItem`, `PutItem`, `Query`, `UpdateItem`, `TransactWriteItems`, and `events:PutEvents`, plus `GetItem` on the subscription table. Resources are specific tables and indexes, not `*`.

`ERAP-GitHub-Deploy` and `ERAP-GitHub-Production` are separate from runtime roles. They can describe tables and publish the nine main functions. They cannot restore a table. Splitting the shared operational role was left as it is, because changing it can break allocate and release.

## 5. Secrets

The Razorpay test secret name is `erap/billing/razorpay/test`. The production secret name is `erap/billing/razorpay/production`. Both values are in Secrets Manager. They are not in Git, not in a Lambda environment variable, and not in a package. Checkout and the webhook load the secret for `ERAP_BILLING_MODE` at runtime. Logs drop webhook secrets, signatures, and raw bodies. No secret value was printed.

Production billing mode is enabled on `erap-billing` and `erap-billing-webhook`. Both functions read `erap/billing/razorpay/production`. `erap-billing-expiry` does not. Each billing role may call `secretsmanager:GetSecretValue` on the test and production secret ARNs. Neither role can rotate, write, or delete the secret. The production secret has no resource policy. `describe-secret` does not report a customer KMS key, rotation, a rotation Lambda, or a previous version. The only staging label is `AWSCURRENT`.

The production secret is one JSON object. The fields are `key_id`, `key_secret`, `webhook_secret`, `monthly_plan_id`, and `yearly_plan_id`. Checkout and cancellation use `key_id` and `key_secret` as HTTP Basic credentials. The webhook uses `webhook_secret` alone, and `signatures_match` accepts one secret. Production plan ids come from the same object. Replacing that object without the plan fields makes checkout fail closed.

Automatic rotation is not safe. Razorpay documents Live API key generation and regeneration in the Dashboard, including OTP, and a choice to deactivate the previous key immediately or after 24 hours. The ERAP integration has no Razorpay API for creating or revoking keys, so Secrets Manager cannot generate the replacement. Razorpay also documents that a changed webhook secret signs later events, while retries of earlier events still need the previous secret for up to 24 hours. Continued non-2xx responses can disable the webhook. ERAP cannot verify with two webhook secrets. A rotation Lambda would therefore risk checkout authentication, webhook verification, or both, and it could drop the plan ids if it rewrote the whole secret. No rotation Lambda and no IAM change are recommended. Classification: ROTATION NOT SAFE TO AUTOMATE — MANUAL CONTROL REQUIRED.

A person rotates the API key only when an operator decides to do it. This document does not do it.

1. A Razorpay Owner or Admin regenerates the Live key from Account & Settings → API Keys and chooses deactivation after 24 hours. The key secret is shown once. It must not be pasted into chat, Git, a ticket, a log, or a Lambda environment variable.
2. The operator builds a new JSON object with the same five field names. `webhook_secret`, `monthly_plan_id`, and `yearly_plan_id` stay unchanged. Only `key_id` and `key_secret` change. `key_id` must still start with `rzp_live_`.
3. The operator checks that shape privately. Do not print the values. Do not create a Razorpay payment or subscription to test the key, and do not read or change a protected subscription.
4. The operator stores that JSON as a new version of `erap/billing/razorpay/production`. The next `GetSecretValue` reads `AWSCURRENT`. No function deploy is required for the read. The Razorpay webhook configuration stays unchanged.
5. Checkout on the new key is the authentication check. A failed checkout during the 24-hour window is rolled back by moving `AWSCURRENT` to the previous version. Immediate deactivation is not the normal path, because that rollback cannot make Razorpay accept the old key again.
6. Webhook deliveries must keep returning success for a valid signature. The webhook secret is not part of this API-key change. Do not edit it in the Dashboard in the same step.
7. After checkout works on the new key, the Dashboard deactivation removes the old key. Do not delete the Secrets Manager secret.

The webhook secret is independent of the API key. Do not rotate it until verification can accept the previous secret and the new secret together. Without that, new events and retries of older events cannot both pass, and a day of failures can disable the webhook. That verifier change is not part of this assessment.

Emergency rollback of an API-key version is `UpdateSecretVersionStage` back to the previous version while the old Razorpay key is still inside its 24-hour window. Do not roll back by editing plans, subscriptions, or the webhook URL. Responsibility sits with the Razorpay Owner or Admin for the provider credential and with the operator who can write the existing secret for the Secrets Manager version. Production credentials must never be pasted into chat or source control.

## 6. API Gateway

API `4c6dni17l3`, stage `dev`, deployment `tr1rz2`. Protected methods use authorizer `y0hzhr`. `GET /public/resources` and `POST /billing/webhook` are the unauthenticated routes. The webhook still requires a valid Razorpay signature. Default throttle is 20 requests per second, burst 40. Public resource `GET` is 5 per second, burst 10. `scripts/verify_hardening.py` checks those values. No route was changed.

## 7. CORS

Packaged handlers send `Access-Control-Allow-Origin` for `https://main.d3enpe7opotop5.amplifyapp.com` only. Gateway error responses use that same origin. Credentials are not combined with `*`. The unused root file `lambda_function.py` still contains a wildcard origin. It is not in `scripts/lambda_manifest.py` and it is not a deployed function.

## 8. DynamoDB

All 14 tables use the DynamoDB-owned key, point-in-time recovery, and deletion protection. There is no public table endpoint. Application roles query and update known keys. They do not scan.

## 9. S3

Bucket `emergency-resource-allocation-sricharan-2026` is not named by the application. Before this phase its public access block was off and its only bucket-policy statement allowed anonymous `s3:GetObject` on the whole bucket. The ACL itself granted only the bucket owner.

The public statement was removed and all four public-access-block settings were turned on. Objects were not listed or downloaded. Versioning and AES256 encryption were already on. There is no lifecycle rule.

## 10. Lambda

All 15 functions run Python 3.14 on x86_64, with no VPC, no layers, no dead-letter queue, and tracing mode `PassThrough`. Environment variables that exist are table and index names only: `ORGANIZATIONS_TABLE`, `ORGANIZATION_MEMBERS_TABLE`, `USER_SUB_INDEX`, and `LOCATIONS_TABLE`. No environment name contains a secret, password, webhook, or API key. Timeouts are 10 to 60 seconds. Memory is 256 MB.

## 11. EventBridge

`erap-exchange-expiry-hourly` targets `erap-exchange-expiry:live`. `erap-billing-expiry-daily` targets `erap-billing-expiry:live`. `EmergencyResourceAutoReleaseRule` runs every 5 minutes and targets `emergency-resource-auto-release:live`. No extra targets were present. Schedules were not changed.

## 12. CloudWatch

Phase 11B structured logs drop authorization headers, bearer tokens, JWTs, passwords, MFA values, webhook secrets, signatures, raw QR tokens, and raw Cognito subjects. The subject in CloudWatch is a SHA-256 prefix. DynamoDB audit rows still store the raw subject. Log groups are kept for 30 days. Alarms publish to SNS, and no email endpoint is subscribed.

## 13. GitHub Actions

Workflows set `contents: read`. Deploy and rollback also set `id-token: write` so they can assume the GitHub OIDC roles. Actions are pinned to major versions: `actions/checkout@v4`, `actions/setup-python@v5`, `actions/setup-node@v4`, and `aws-actions/configure-aws-credentials@v4`. A failed deploy runs `scripts/set_live_version.py --from-summary`. That restore moves `live` back only when the alias is still the version that same job published. If a later deploy already moved the alias, the restore leaves it. GitHub jobs in the concurrency group `erap-backend-deploy` wait for each other. A local `deploy_backend.py` run during that wait is the case the ownership check covers.

## 14. Dependencies

`requirements.txt` pins `boto3==1.43.98`, `botocore==1.43.98`, and `pytest==9.1.1`. `requirements-ci.txt` pins `pyyaml==6.0.3`. Those pins match the versions already used for tests. Lambda packages are small and do not vendor boto3; the runtime supplies it. No dependency was upgraded. The repository secret scan looks for access keys, private keys, Razorpay key and webhook shapes, bearer tokens, and credential assignments. It reports the file, line, and detector name, not the matched value. It does not query a vulnerability database.

## 15. Security tests

`tests/test_security_posture.py` checks packaged CORS, workflow permissions, and this document. Existing tests cover authorization denial, webhook signature rejection, QR token redaction, and secret redaction. `python scripts/verify_security_posture.py` reads S3, Cognito, Lambda environment names, and runtime role actions.

## 16. Configuration drift

| Item | Class |
|---|---|
| Anonymous S3 `GetObject` | UNEXPECTED. Removed. |
| Public access block off | UNEXPECTED. Turned on. |
| Cognito MFA optional with software token | EXPECTED. Unchanged. |
| Table names in Lambda environment | EXPECTED. |
| Wildcard CORS in root `lambda_function.py` | Unused file. Not deployed. |
| API throttle and authorizer | EXPECTED. Matches `verify_hardening.py`. |
| Pinned `boto3` and `pytest` | Repository install only. Not in the Lambda zip. |

Git was not overwritten from AWS, and AWS was not rebuilt from Git, except the S3 public-access change above.

## 17. Known limitations

The shared operational role can still write the original resource, request, allocation, and history tables. Manual rollback with one `--version` still applies that number to every function in `PACKAGES`, and those functions do not share one version number. Client `ValueError` text is returned on HTTP 400. Those strings are validation messages, not stack traces. Unexpected errors return a fixed message and log only the exception class. There is no vulnerability feed for dependencies. S3 object contents were not inspected.

## 18. Deferred improvements

Role split for the four operational functions. Removing or rewriting the unused root `lambda_function.py`. Adding an email subscriber for alarms. Code signing, reserved concurrency, and a dead-letter queue were not justified by a defect found here.
