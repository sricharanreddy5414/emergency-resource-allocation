# Pilot runbook

This is the operator guide for the first ERAP organization. Use the website. Do not create rows directly in DynamoDB.

Site: `https://main.d3enpe7opotop5.amplifyapp.com`

Region: `eu-north-1`. API stage: `dev`.

## 1. Creating the first organization

1. Open the site and choose the Cognito sign-in.
2. Sign in with the pilot account that was created in the Cognito console for this organization. Do not reuse a personal account.
3. If the account has no organization, the onboarding form appears.
4. Enter the organization name and submit.
5. The account becomes the organization owner. The rest of the application becomes available after that membership loads.

If onboarding does not appear, sign out and sign in again. Do not insert an organization row by hand.

## 2. Creating locations

1. Open Locations.
2. Add the first operating location with its city and state.
3. Add a second location only when the organization really has one.
4. Select the location you are working in before registering resources or requests.

Inactive locations must not be used for new resources or requests. The API rejects a location that is not active in this organization.

## 3. Creating resource types

1. Open the catalog admin area. Only an owner or admin sees the save controls.
2. Create a resource type with a clear name and the attribute fields operators must fill in.
3. Keep attributes flat. Do not design nested objects for the pilot.

## 4. Creating request types

1. In the same catalog area, create the request type that should match the resource type.
2. Use the same attribute names the matcher expects for that pair.
3. Deactivate a type only when it should no longer be used. Existing rows stay in the table.

## 5. Registering resources

1. Select the organization and location.
2. Choose Register Resource.
3. Enter an id, name, type, location, and the required attributes.
4. Leave visibility on PRIVATE until the public text has been checked.
5. Save. The form closes and the resource appears in the list.

## 6. Setting PRIVATE or PUBLIC

PRIVATE is the default. A public resource is readable without a login.

Before switching a resource to PUBLIC, read `docs/pilot-data-safety.md`. Public text may include a public name, city, state, description, contact, and availability only when availability is explicitly shown.

To change an existing resource, choose Edit on that row, change the allowed fields, and save. Edit does not move the resource to another organization.

## 7. Creating emergency requests

1. Select the request type and location.
2. Set the priority.
3. Fill the attributes the type requires.
4. Submit.

An incomplete or unknown attribute is rejected. Correct the form and submit again. Do not create a second request for the same need until you know the first one failed.

## 8. Matching resources

Matching runs inside this organization. It prefers a compatible available resource at the same location, then another active location in the same organization. It never selects a resource from another organization or a legacy unscoped row.

If nothing matches, the request stays unmatched. Add or free a compatible resource, then try again.

## 9. Allocating resources

An operator, admin, or owner allocates. A member can create a request and cannot allocate.

Allocation marks the resource allocated and writes an allocation row. A second allocation of the same resource is rejected. Do not retry by creating a duplicate resource id.

## 10. Releasing resources

When the assignment is finished, release the resource from its row. The resource becomes available again and the allocation is closed. Auto-release uses the same rules on a schedule. Do not release legacy rows that are not part of this organization.

## 11. Viewing audit events

Important creates, updates, visibility changes, allocations, and releases write an audit event for the organization. Use the audit view in the application when it is available for your role. Audit records store the action and identifiers. They do not store passwords or tokens.

## 12. Checking public discovery

Open the public resources page, or call `GET /public/resources` with no token.

Confirm:

- The PUBLIC pilot resource appears with only the public fields.
- The PRIVATE resource does not appear.
- City, state, type, and availability filters return a smaller list.
- A bad page size or page token returns a controlled error, not a stack trace.

## 13. Handling failures

Read the message on the screen. Typical cases:

- Sign in again when the session has expired.
- You do not have permission when the role cannot do that action.
- The resource is no longer available when someone else allocated it.
- No matching resource when the inventory cannot fill the request.
- Try again later when the service returns a server error.

Do not copy the message into a support ticket if it contains a token. Normal messages do not.

## 14. Contacting the technical operator

Contact the person who administers AWS account `481838970142` and the GitHub repository. Tell them the time, the page, what you clicked, and the short message on the screen. Do not send your password or the browser token.

## 15. Rolling back a backend deployment

Use `docs/rollback.md`. From GitHub, run **Rollback backend** on `main` and enter the previous Lambda version. Leave the commit blank. Do not delete functions, tables, or the API.

## 16. Checking CloudWatch

In `eu-north-1`, open CloudWatch Logs for the ERAP Lambda functions. Successful and failed API calls log a request id, route, operation, status, and duration. Search for the request id from an error message. Logs are kept for 30 days.

## 17. Checking SNS alarms

Topic name: `ERAP-Production-Alarms`.

An operator subscription is added in the SNS console, not in this repository. When an alarm email arrives, follow `docs/incident-runbook.md`. Do not delete the alarm or the topic.

## 18. What operators must never do

- Run `scripts/migrate_tenant_scope.py apply`.
- Edit or delete the legacy tenantless rows.
- Turn off the Cognito authorizer, PITR, deletion protection, or throttling.
- Set CORS to `*`.
- Put passwords, tokens, or private personal data in resource attributes or public text.
- Create an organization by writing DynamoDB directly.
- Enable admin password sign-in to “make testing easier”.
- Force MFA on for every user without a planned enrollment.
- Subscribe a personal mailbox by committing it to the repository.
