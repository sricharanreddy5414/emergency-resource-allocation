# Organization member management

Membership is stored in `OrganizationMembers`. The partition key is `organization_id` and the sort key is `user_sub`. `UserSubIndex` uses `user_sub` and then `organization_id`. Cognito groups are not roles. The acting user is always `requestContext.authorizer.claims.sub`.

Creating an organization still writes one `OWNER` membership with `status` `ACTIVE`. Existing owner rows that have no `status` remain active. This feature does not rewrite those rows.

## Roles

| Action | MEMBER | OPERATOR | ADMIN | OWNER |
|---|---|---|---|---|
| Read organization data and create or update requests | yes | yes | yes | yes |
| Register, update, release, and allocate resources | no | yes | yes | yes |
| Manage locations, resource types, and request types | no | no | yes | yes |
| Invite, change, deactivate, and reactivate members | no | no | non-owner members only | yes |

`ADMIN`, `OPERATOR`, and `MEMBER` are the only roles that can be assigned after an organization exists. The member API rejects `role` `OWNER`. An admin cannot change an owner. Nobody can change their own membership. The last active owner cannot be demoted or deactivated.

An admin managing another admin is intentional. The admin column means non-owner members: another `ADMIN`, an `OPERATOR`, or a `MEMBER`. An admin cannot remove or change an `OWNER`, and cannot remove or change their own membership.

More than one ACTIVE `OWNER` membership can exist. An owner can change or remove a different owner only when another ACTIVE owner would remain. An owner cannot remove or change themselves. The member table shows that same capability: controls appear on another owner's row only for a signed-in owner when another active owner remains. An admin does not see owner controls. The signed-in user's own row has no role or removal control.

An inactive or pending membership does not authorize organization operations.

## Invitation

There is no separate mail service in this repository. An owner or admin invites an email address. The API writes a `PENDING` row whose key is `invite-` plus a hash of that email. It does not create a Cognito user, store a password, or mark an email verified.

The invited person signs in through the existing Cognito page and verifies their email there. `GET /organization` then includes `pending_invitations` when the ID token has the same email and `email_verified` is true. Accepting creates an `ACTIVE` membership for that Cognito `sub` and marks the invitation inactive. A client-supplied role or subject is ignored.

## API

Both operations use the existing `/organization` resource. No new API Gateway route is required.

`GET /organization?view=members&organization_id=...` lists members for an owner or admin.

`POST /organization` accepts `operation`:

- `invite_member` with `email` and `role`
- `change_role` with `target_user_sub` and `role`
- `deactivate_member` with `target_user_sub`
- `reactivate_member` with `target_user_sub`
- `accept_invitation` with `organization_id`

The organization id is accepted only when it matches the caller's active membership, except for accepting an invitation. Acceptance still requires a pending row for the token email.

Each successful change claims `membership_epoch` on the organization with a conditional write, then updates the membership with a condition on the previous role and status. A conflicting change returns 409.

## Audit

Successful changes write `MEMBER_INVITED`, `MEMBER_ACTIVATED`, `MEMBER_ROLE_CHANGED`, `MEMBER_DEACTIVATED`, or `MEMBER_REACTIVATED`. The event uses the existing audit fields. Metadata contains `target_user_sub`, `old_role`, and `new_role`. It does not contain tokens, passwords, or authorization headers.

## Frontend

The Admin Dashboard shows Organization Members to an owner or admin. The selector offers `ADMIN`, `OPERATOR`, and `MEMBER`. It does not offer `OWNER`. Hidden buttons are not authorization: the API enforces the same role, self, and last-owner rules.

## Live role validation

Implementing this workflow does not mark ADMIN, OPERATOR, or MEMBER live validation as passed. That requires a real signed-in user to receive each role through invitation and acceptance, then exercise the allowed and denied actions. The existing pilot owner membership is not changed by deployment.
