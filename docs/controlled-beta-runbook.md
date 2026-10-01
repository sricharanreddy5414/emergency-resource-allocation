# Controlled beta operations

Launch scope is a controlled limited beta. Public registration is not open. Organizations are created by a signed-in person through the existing onboarding flow. Invite specific people. Do not publish this site as open registration. The feedback and onboarding checklist is `docs/controlled-beta-feedback.md`. This runbook does not add a cleanup endpoint.

Account `481838970142`. API `4c6dni17l3`, stage `dev`, region `eu-north-1`. Site `https://main.d3enpe7opotop5.amplifyapp.com`. Alias `live`.

## Onboarding

The signed-in person enters an organization name. ERAP creates the organization as ACTIVE, an OWNER membership as ACTIVE, and a TRIALING subscription on FREE_TRIAL for 15 UTC days. Creation does not start a Razorpay subscription and does not take a payment. A repeated client request id returns the same organization. A conflicting id is rejected. The selector then shows the organization name.

## Organization management

OWNER and ADMIN manage members. An invitation grants no access until it is accepted. Removing a member sets that membership inactive. The backend rejects an inactive member and an organization that is not ACTIVE. The browser cannot choose another organization's records.

## Resource setup

Register a resource with a type, a location, and availability. Quantity resources use the existing atomic updates. Do not bypass them. Status words the operator sees are available, reserved, in use, maintenance, damaged, and retired.

## Request workflow

Create a request with a type, a location, and a priority. Allocation matches an available resource. If nothing matches, the request stays pending. Completion and release are recorded on the request and the resource. Do not change the matching engine from this runbook.

## Exchange

The path is request, offer, accept, transfer pending, QR handover, then completion. The provider shows a QR code. The receiver confirms. A replay of the same QR does not transfer again. Cancellation and expiry use the existing exchange worker. Notification failure leaves the exchange result in place.

## Notifications

The inbox shows offer received, accepted, rejected, withdrawn, superseded, transfer started, handover completed, request cancelled, and request expired when those events are emitted. Unread count, mark read, and mark all read are available. An empty inbox is valid.

## Billing

Prices are ₹999 monthly and ₹9,999 yearly. The trial is 15 days. Checkout opens the provider page and does not mark the subscription ACTIVE. A verified provider event changes the status. Production mode uses `erap/billing/razorpay/production` and plans `plan_TiMn4MluXeOMK1` and `plan_TiMpOnO7K5GT0Q`. Test mode uses `erap/billing/razorpay/test` and plans `plan_ThiWT35Gf1jyio` and `plan_ThiWTXOzBHl2Qb`. Do not recreate plans or secrets. Do not print secret values.

## Cancellation

Only an owner can cancel. Cancellation is at the end of the current period. The subscription stays ACTIVE and writes stay allowed until the expiry worker sets CANCELLED. A provider cancellation while the subscription is PAST_DUE does not by itself end writes.

## Manual refunds

There is no automatic refund engine. Support handles a refund request manually. The provider record stays authoritative. Do not ask for a card number or a payment secret. Identify the organization and the billing event id.

## Incidents

Use `docs/incident-response.md` and `docs/failure-runbook.md`. Distinguish an API outage, a Lambda error, a DynamoDB error, a Cognito sign-in failure, a Razorpay or webhook failure, an Amplify frontend deploy, a notification failure, and an Exchange failure. Do not delete tables, the API, Cognito, or Amplify. Do not restore a table over the live table.

## Rollback

`docs/rollback.md`. A failed deploy restores aliases from that job's summary. `set_live_version.py --version` moves only the nine `PACKAGES` functions. Billing, Exchange, and notifications move only when the summary or their own deploy includes them. A code rollback does not roll back organization data.

## Emergency disable

There is no public registration switch to open. To stop a bad release, move `live` back to the previous version and wait for the Amplify job to be rolled back by redeploying the last good frontend commit. Do not disable the Cognito pool and do not delete the controlled-beta organization.
