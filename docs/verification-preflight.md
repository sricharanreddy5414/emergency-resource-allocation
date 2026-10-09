# Verification preflight

`config/erap-verification-manifest.json` is a local allow-list for a future isolated ERAP verification account. The checked-in `account_id` is `917320177579`. The protected production account is `481838970142`. Recording that verification account ID does not prove that verification infrastructure exists.

API IDs, Cognito IDs, Amplify identifiers, Lambda, table, and role ARNs, route integration ARNs, authorizer IDs, and the frontend origin remain `UNRESOLVED`. All six required approvals remain `pending`. `teardown_enabled` is `false`. The checked-in file is expected to fail `scripts/verification_preflight.py`. Cloud provisioning and deployment remain NO-GO.

The validator is a local, fail-closed readiness gate. It checks that document only. It does not call AWS, create resources, publish an alias, or delete anything. `refuse_teardown()` raises even if a caller asks for cleanup. Passing local tests does not authorize deployment.

This validator is not a deployment authorization system.

## Public validation contract

- `ready()` is the validation predicate for a future deployment gate. It returns success only after every required check passes.
- `load_manifest()` only parses JSON. It must never be used as validation or as authorized deployment input.
- `preflight()` returns the error list. A non-empty result must not be ignored.
- Catching `VerificationBlocked` must terminate the future operation. It is not permission to continue.
- A passing `ready()` does not authorize account access, deployment, provisioning, or teardown.

`scripts/deploy_backend.py` and the current deploy workflows do not call the validator. They remain unwired and must not be used to provision a verification stack. Wire them only in a separately reviewed integration phase.

API Gateway routes, the Cognito authorizer, CORS, and the Amplify frontend are separate resources in the manifest. Deploying the eight Lambda functions does not create them. The required routes are the methods the planned checks use. Their integration ARNs stay unresolved until a real verification API is recorded.

The manifest names verification account `917320177579`. A dedicated verification stack in that account is still unconfirmed. Do not treat protected production account `481838970142`, API `4c6dni17l3`, stage `dev`, alias `emergency-resource-allocation:live`, Cognito pool `eu-north-1_vv7adAAC9`, authorizer `y0hzhr`, or Amplify app `d3enpe7opotop5` as verification targets.

The three manual live checks remain NOT RUN:

1. Emergency inbox Open on an existing emergency notification.
2. Cross-organization request read, which must stay unavailable and reveal no fields.
3. Exchange inbox Open on an existing exchange notification.

Run:

```text
python scripts/verification_preflight.py
```

Unresolved resource identifiers and pending approvals cause a nonzero exit. Provisioning and teardown stay blocked. Cloud deployment remains NO-GO.
