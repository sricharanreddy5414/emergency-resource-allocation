# Owner Authorization — One Future Read-Only Caller-Identity Check

Status: APPROVED FOR ONE READ-ONLY CHECK

## Authorization provenance
The repository owner explicitly approved one future read-only caller-identity check in the current conversation. This file records that authorization locally. Its recorded creation time is not claimed as a server-side timestamp of the original conversation message.

- Local record time UTC: 2026-10-11T16:10:39Z
- Local record time IST: 2026-10-11 21:40:39 +05:30

## Exact authorized command
`aws sts get-caller-identity --profile erap-verification --region eu-north-1 --query Account --output text --no-cli-pager`

Run exactly once, with the existing profile unchanged, from a working directory outside `C:\Azure\erap-phase-14g`.

## Restrictions
- Do not inspect, print, copy, or disclose credentials or tokens.
- Do not use another profile or the ambient credential chain.
- Accept only account ID `917320177579`.
- Stop if the command fails or returns any different account ID, especially protected production account `481838970142`.
- Preserve the actual output, execution time, working directory, exit code, and evidence path.
- An independent reviewer must inspect the new observation.
- This authorization does not authorize additional AWS calls, IAM changes, resource creation, deployment, apply, teardown, OIDC changes, or GitHub changes.
- This record does not itself verify account identity or authorize deployment.

Authorization evidence reference: `docs/evidence/phase-14g-aws-account-review/owner-authorization-fresh-caller-identity-check-corrected-20261011-161039Z.md`