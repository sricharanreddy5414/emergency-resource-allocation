# Production release runbook

This is how a change on `main` reaches the live API. It does not authorize a commercial launch. See `docs/production-launch-gate.md`.

The release order is PRE-RELEASE, then TEST, then VALIDATION, then APPROVAL, then DEPLOY, then SMOKE, then MONITOR, then ROLLBACK IF REQUIRED. Approval is a human decision recorded in the launch gate. A green test run is not that approval. Rollback uses `docs/rollback.md` and does not move an alias that a newer deploy already owns.

Pushing `main` starts **Deploy backend**. That job publishes the nine functions in `PACKAGES` and the three billing functions, then moves their `live` aliases. Exchange and notification functions stay on their current aliases unless their own deploy scripts are run on purpose. `scripts/set_live_version.py --version` still moves only `PACKAGES`.

Do not run `python scripts/deploy_backend.py` on a workstation while the GitHub job is still in progress. The failure step will not move an alias that a later deploy already owns, and a second writer still makes the release harder to explain.

## 1. Pre-release checks

Read `docs/production-readiness-checklist.md`. Confirm the change does not include secrets, a pilot-organization write, a new product feature, or a DynamoDB restore over a live table.

## 2. Git verification

```text
git status
git branch --show-current
git log -1 --oneline
git rev-parse HEAD
git rev-parse origin/main
```

`HEAD` must equal `origin/main` before you call the release done. The branch is `main`.

## 3. Test suite

```text
python -m pytest -q
```

## 4. Security checks

```text
python scripts/security_scan.py
python scripts/check_workflows.py
python scripts/verify_security_posture.py
```

## 5. Package checks

```text
python scripts/package_lambdas.py --check
```

## 6. Hardening verification

```text
python scripts/verify_hardening.py
```

## 7. Recovery verification

```text
python scripts/verify_recovery.py
```

The GitHub deploy role cannot read EventBridge. This command uses operator credentials. It is not a step inside Deploy backend.

## 8. Production readiness verification

```text
python scripts/verify_production_readiness.py
```

This checks the runbooks, then runs hardening, recovery, and the security posture script. It does not write business data.

## 9. Deployment

For the nine `PACKAGES` functions, push the reviewed commit to `origin/main`. GitHub Actions run CI and Deploy backend. Deploy backend checks out the commit, repeats tests, the secret scan, and the package check, assumes `ERAP-GitHub-Deploy`, runs `python scripts/deploy_backend.py`, then `python scripts/smoke_test.py` and `python scripts/verify_hardening.py`.

Exchange production release is the manual workflow **Release exchange**. Run it from `main` and approve the `production` environment. Leave the commit input empty to release the dispatched `main` SHA, or pass a full SHA that is already contained in `main`. The workflow packages and publishes only `erap-exchange`, then moves alias `live`. It does not call `scripts/deploy_exchange.py`, does not rewrite IAM, and does not change Lambda configuration. `erap-exchange-expiry` and `erap-notifications` stay on their current aliases. `infra/github-production-exchange-policy.json` and the exchange workflow refs in `infra/github-production-trust.json` are the required permission and trust. This workflow does not apply them. Until those IAM changes are applied, Release exchange cannot assume `ERAP-GitHub-Production`.

Exchange rollback is the manual workflow **Rollback exchange**. It accepts only an existing published version number. The current known good version before the F-04 release is `17`.

Expiry and notifications still use their own scripts, and only when that code changed:

```text
python scripts/deploy_exchange_expiry.py
python scripts/deploy_notifications.py
```

There is no `deploy_billing.py`. Do not improvise a billing deploy in a docs-only release.

## 10. Alias verification

After Deploy backend is green, read each `live` alias. The description of a function that job published should start with `commit=` and that commit. Functions outside `PACKAGES` should still show their previous commit.

```text
aws lambda get-alias --function-name get-resources --name live --region eu-north-1
```

Repeat for the function you expect to have changed. `python scripts/verify_recovery.py` checks that all 15 aliases exist.

## 11. API verification

```text
python scripts/verify_hardening.py
```

That reads stage `dev` on API `4c6dni17l3`, the throttle, CORS, authorizer `y0hzhr`, and the public function alias.

## 12. Smoke test

```text
python scripts/smoke_test.py
python scripts/smoke_test_production.py
```

Both are read-only. The production smoke script refuses a URL that names the pilot organization and does not print bodies. Authenticated reads are not part of the script. Do them in the browser while signed in, on `ORG-A66B0A1E4F96` or `ORG-17D0E2939B2D` only.

## 13. Rollback decision

Roll back when the new alias fails smoke or hardening and the previous alias was healthy. Do not roll back when the defect is data, a secret, or Cognito. Those do not move with the alias.

If Deploy backend fails after publishing, it runs `python scripts/set_live_version.py --from-summary`. That restores a function only while `live` is still the version that job published. Details are in `docs/rollback.md`.

Manual rollback is the **Rollback backend** workflow. Pass a version only when that number is the intended version of every function in `PACKAGES`. Otherwise leave the version empty and pass the full 40-character commit only when no published version exists.

## 14. Post-release verification

Repeat sections 6, 7, 8, and 12. Confirm GitHub Deploy backend succeeded, including Verify hardening. A failed hardening step that restored aliases is not a successful release.
