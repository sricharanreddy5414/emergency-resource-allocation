"""Organization membership changes.

The acting user is always the Cognito subject on the token.
A requested role is the target member's role, checked against an allowlist.
OWNER is assigned only when an organization is created.
"""

import hashlib
import re
from datetime import datetime, timezone

from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from access import MANAGE_ROLES, AccessError, authorize
from common import api_response

ASSIGNABLE_ROLES = {"ADMIN", "OPERATOR", "MEMBER"}
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
TARGET_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,79}$")


def member_status(item):
    return (item or {}).get("status") or "ACTIVE"


def normalize_email(value):
    email = str(value or "").strip().lower()

    if not email or len(email) > 254 or not EMAIL_PATTERN.fullmatch(email):
        raise ValueError("Email address is invalid")

    return email


def invite_subject(email):
    digest = hashlib.sha256(email.encode("utf-8")).hexdigest()[:32]

    return "invite-" + digest


def verified_email(event):
    claims = (
        (event or {}).get("requestContext", {})
        .get("authorizer", {})
        .get("claims", {})
    )

    if not isinstance(claims, dict):
        return ""

    verified = claims.get("email_verified")

    if verified is not True and str(verified).lower() != "true":
        return ""

    try:
        return normalize_email(claims.get("email"))
    except ValueError:
        return ""


def _now():
    return datetime.now(timezone.utc).isoformat()


def _members():
    from membership import members_table

    return members_table()


def _organizations():
    from membership import organizations_table

    return organizations_table()


def _query_members(organization_id):
    result = _members().query(
        KeyConditionExpression=Key("organization_id").eq(organization_id),
        ConsistentRead=True,
    )

    return list(result.get("Items") or [])


def _active_owner_count(rows):
    return sum(1 for row in rows if row.get("role") == "OWNER" and member_status(row) == "ACTIVE")


def _find(rows, user_sub):
    for row in rows:
        if row.get("user_sub") == user_sub:
            return row

    return None


def _public_member(item):
    view = {
        "user_sub": item.get("user_sub", ""),
        "role": item.get("role", ""),
        "status": member_status(item),
        "created_at": item.get("created_at", ""),
    }
    email = item.get("email") or ""

    if email:
        view["email"] = email

    if item.get("joined_at"):
        view["joined_at"] = item["joined_at"]

    return view


def _audit(table, organization_id, actor_sub, actor_role, action, target, old_role, new_role):
    from audit import build_audit_event, record_audit

    record_audit(
        table,
        build_audit_event(
            organization_id,
            actor_sub,
            actor_role,
            action,
            "membership",
            target,
            metadata={
                "target_user_sub": target,
                "old_role": old_role or "",
                "new_role": new_role or "",
            },
        ),
    )


def _claim(organization_id):
    table = _organizations()
    current = table.get_item(Key={"organization_id": organization_id}).get("Item")

    if not current:
        raise AccessError(404, "Organization not found")

    seen = current.get("membership_epoch")

    try:
        if seen is None:
            table.update_item(
                Key={"organization_id": organization_id},
                UpdateExpression="SET membership_epoch = :next",
                ConditionExpression=(
                    "attribute_exists(organization_id) AND attribute_not_exists(membership_epoch)"
                ),
                ExpressionAttributeValues={":next": 1},
            )
        else:
            table.update_item(
                Key={"organization_id": organization_id},
                UpdateExpression="SET membership_epoch = :next",
                ConditionExpression="attribute_exists(organization_id) AND membership_epoch = :seen",
                ExpressionAttributeValues={":seen": int(seen), ":next": int(seen) + 1},
            )
    except ClientError as error:
        if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise AccessError(409, "Membership change conflicted. Retry.")

        raise


def _put_new(item):
    try:
        _members().put_item(
            Item=item,
            ConditionExpression="attribute_not_exists(user_sub)",
        )
    except ClientError as error:
        if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise AccessError(409, "Member already exists")

        raise


def _update(organization_id, user_sub, fields, expected_role, expected_status):
    values = {
        ":role": fields["role"],
        ":status": fields["status"],
        ":updated_at": fields["updated_at"],
        ":updated_by": fields["updated_by"],
        ":expected_role": expected_role,
        ":expected_status": expected_status,
    }
    expression = "SET #role = :role, #status = :status, updated_at = :updated_at, updated_by = :updated_by"

    if "email" in fields:
        expression += ", email = :email"
        values[":email"] = fields["email"]

    if "joined_at" in fields:
        expression += ", joined_at = :joined_at"
        values[":joined_at"] = fields["joined_at"]

    try:
        _members().update_item(
            Key={"organization_id": organization_id, "user_sub": user_sub},
            UpdateExpression=expression,
            ConditionExpression=(
                "#role = :expected_role AND "
                "(attribute_not_exists(#status) OR #status = :expected_status)"
            ),
            ExpressionAttributeNames={"#role": "role", "#status": "status"},
            ExpressionAttributeValues=values,
        )
    except ClientError as error:
        if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise AccessError(409, "Membership changed. Retry.")

        raise


def _manage(event, body):
    try:
        actor_sub, membership = authorize(event, body, allowed_roles=MANAGE_ROLES)
    except AccessError:
        raise

    return actor_sub, membership


def _target(body, rows):
    target = str(body.get("target_user_sub") or "").strip()

    if not TARGET_PATTERN.fullmatch(target):
        raise AccessError(400, "Member is invalid")

    row = _find(rows, target)

    if not row:
        raise AccessError(404, "Member not found")

    return target, row


def _guard_owner(actor_role, row, rows, new_role=None):
    if row.get("role") == "OWNER" and actor_role != "OWNER":
        raise AccessError(403, "Owner membership cannot be changed")

    removing_owner = row.get("role") == "OWNER" and member_status(row) == "ACTIVE"

    if new_role is not None:
        removing_owner = removing_owner and new_role != "OWNER"

    if removing_owner and _active_owner_count(rows) < 2:
        raise AccessError(409, "The last owner cannot be changed")


def pending_invitations(event):
    email = verified_email(event)

    if not email:
        return []

    from membership import organizations_table

    result = _members().query(
        IndexName="UserSubIndex",
        KeyConditionExpression=Key("user_sub").eq(invite_subject(email)),
    )
    invitations = []

    for item in result.get("Items") or []:
        organization_id = item.get("organization_id")
        user_sub = item.get("user_sub")

        if not organization_id or not user_sub:
            continue

        stored = _members().get_item(
            Key={"organization_id": organization_id, "user_sub": user_sub}
        ).get("Item")

        if not stored or member_status(stored) != "PENDING":
            continue

        organization = organizations_table().get_item(
            Key={"organization_id": organization_id}
        ).get("Item")

        if not organization or organization.get("status") != "ACTIVE":
            continue

        invitations.append(
            {
                "organization_id": organization_id,
                "name": organization.get("name", ""),
                "role": stored.get("role", ""),
                "status": "PENDING",
            }
        )

    invitations.sort(key=lambda item: item["organization_id"])

    return invitations


def handle_member_read(event):
    try:
        _actor, membership = authorize(event, allowed_roles=MANAGE_ROLES)
    except AccessError as error:
        return api_response(error.status_code, {"message": error.message})

    organization_id = membership["organization_id"]
    rows = _query_members(organization_id)
    rows.sort(key=lambda item: (item.get("email") or "", item.get("user_sub") or ""))

    return api_response(
        200,
        {
            "organization_id": organization_id,
            "members": [_public_member(item) for item in rows],
        },
    )


def handle_member_operation(event, body, actor_sub, audit_table):
    operation = str(body.get("operation") or "").strip()

    try:
        if operation == "accept_invitation":
            return _accept(event, body, actor_sub, audit_table)

        if operation == "invite_member":
            return _invite(event, body, actor_sub, audit_table)

        if operation == "change_role":
            return _change_role(event, body, actor_sub, audit_table)

        if operation == "deactivate_member":
            return _set_active(event, body, actor_sub, audit_table, active=False)

        if operation == "reactivate_member":
            return _set_active(event, body, actor_sub, audit_table, active=True)

        return api_response(400, {"message": "Membership operation is invalid"})
    except AccessError as error:
        return api_response(error.status_code, {"message": error.message})
    except ValueError as error:
        return api_response(400, {"message": str(error)})
    except ClientError as error:
        print("Membership change failed:", error.response["Error"]["Code"])

        return api_response(500, {"message": "Unable to update membership"})


def _invite(event, body, actor_sub, audit_table):
    actor_sub, membership = _manage(event, body)
    organization_id = membership["organization_id"]
    email = normalize_email(body.get("email"))
    role = str(body.get("role") or "").strip()

    if role not in ASSIGNABLE_ROLES:
        raise AccessError(400, "Role is invalid")

    rows = _query_members(organization_id)
    subject = invite_subject(email)

    if any(str(row.get("email") or "").lower() == email and member_status(row) in {"ACTIVE", "PENDING"} for row in rows):
        raise AccessError(409, "Member already exists")

    if _find(rows, subject):
        raise AccessError(409, "Member already exists")

    _claim(organization_id)
    created_at = _now()
    _put_new(
        {
            "organization_id": organization_id,
            "user_sub": subject,
            "role": role,
            "status": "PENDING",
            "email": email,
            "created_at": created_at,
            "invited_at": created_at,
            "invited_by": actor_sub,
        }
    )
    _audit(
        audit_table,
        organization_id,
        actor_sub,
        membership.get("role"),
        "MEMBER_INVITED",
        subject,
        "",
        role,
    )

    return api_response(
        201,
        {
            "message": "Invitation created",
            "member": _public_member(
                {
                    "user_sub": subject,
                    "role": role,
                    "status": "PENDING",
                    "email": email,
                    "created_at": created_at,
                }
            ),
        },
    )


def _change_role(event, body, actor_sub, audit_table):
    actor_sub, membership = _manage(event, body)
    organization_id = membership["organization_id"]
    role = str(body.get("role") or "").strip()

    if role not in ASSIGNABLE_ROLES:
        raise AccessError(400, "Role is invalid")

    rows = _query_members(organization_id)
    target, row = _target(body, rows)

    _guard_owner(membership.get("role"), row, rows, new_role=role)

    if target == actor_sub:
        raise AccessError(403, "You cannot change your own membership")

    if member_status(row) == "INACTIVE":
        raise AccessError(409, "Activate the member before changing the role")

    if row.get("role") == role:
        return api_response(200, {"message": "Role unchanged", "member": _public_member(row)})

    previous_role = row.get("role")
    _claim(organization_id)
    _update(
        organization_id,
        target,
        {
            "role": role,
            "status": member_status(row),
            "updated_at": _now(),
            "updated_by": actor_sub,
        },
        row.get("role"),
        member_status(row),
    )
    _audit(
        audit_table,
        organization_id,
        actor_sub,
        membership.get("role"),
        "MEMBER_ROLE_CHANGED",
        target,
        previous_role,
        role,
    )
    changed = dict(row)
    changed["role"] = role

    return api_response(200, {"message": "Role updated", "member": _public_member(changed)})


def _set_active(event, body, actor_sub, audit_table, active):
    actor_sub, membership = _manage(event, body)
    organization_id = membership["organization_id"]
    rows = _query_members(organization_id)
    target, row = _target(body, rows)

    if str(target).startswith("invite-") and active:
        raise AccessError(400, "Accept the invitation to activate this member")

    current = member_status(row)
    desired = "ACTIVE" if active else "INACTIVE"

    if current == "PENDING" and active:
        raise AccessError(400, "Accept the invitation to activate this member")

    if current == desired:
        return api_response(200, {"message": "Membership unchanged", "member": _public_member(row)})

    _guard_owner(membership.get("role"), row, rows, new_role=None if desired == "INACTIVE" else row.get("role"))

    if target == actor_sub:
        raise AccessError(403, "You cannot change your own membership")

    _claim(organization_id)
    _update(
        organization_id,
        target,
        {
            "role": row.get("role"),
            "status": desired,
            "updated_at": _now(),
            "updated_by": actor_sub,
        },
        row.get("role"),
        current,
    )
    action = "MEMBER_REACTIVATED" if active else "MEMBER_DEACTIVATED"
    _audit(
        audit_table,
        organization_id,
        actor_sub,
        membership.get("role"),
        action,
        target,
        row.get("role"),
        row.get("role"),
    )
    changed = dict(row)
    changed["status"] = desired

    return api_response(200, {"message": "Membership updated", "member": _public_member(changed)})


def _accept(event, body, actor_sub, audit_table):
    email = verified_email(event)

    if not email:
        raise AccessError(403, "A verified email is required")

    organization_id = str(body.get("organization_id") or "").strip()

    if not organization_id:
        raise AccessError(400, "Organization is required")

    subject = invite_subject(email)
    rows = _query_members(organization_id)
    pending = _find(rows, subject)

    if not pending or member_status(pending) != "PENDING":
        raise AccessError(404, "Invitation not found")

    if pending.get("email") != email:
        raise AccessError(404, "Invitation not found")

    if pending.get("role") not in ASSIGNABLE_ROLES:
        raise AccessError(403, "Invitation cannot be accepted")

    existing = _find(rows, actor_sub)

    if existing and member_status(existing) == "ACTIVE":
        if existing.get("role") != pending.get("role"):
            raise AccessError(409, "Membership changed. Retry.")

        stored_email = str(existing.get("email") or "").strip().lower()

        if stored_email and stored_email != email:
            raise AccessError(404, "Invitation not found")

        _update(
            organization_id,
            subject,
            {
                "role": pending.get("role"),
                "status": "INACTIVE",
                "updated_at": _now(),
                "updated_by": actor_sub,
            },
            pending.get("role"),
            "PENDING",
        )
        _audit(
            audit_table,
            organization_id,
            actor_sub,
            pending.get("role"),
            "MEMBER_ACTIVATED",
            actor_sub,
            "",
            pending.get("role"),
        )

        return api_response(200, {"message": "Invitation accepted", "member": _public_member(existing)})

    _claim(organization_id)
    created_at = _now()
    _put_new(
        {
            "organization_id": organization_id,
            "user_sub": actor_sub,
            "role": pending.get("role"),
            "status": "ACTIVE",
            "email": email,
            "created_at": pending.get("created_at") or created_at,
            "joined_at": created_at,
            "invited_by": pending.get("invited_by") or "",
        }
    )
    _update(
        organization_id,
        subject,
        {
            "role": pending.get("role"),
            "status": "INACTIVE",
            "updated_at": created_at,
            "updated_by": actor_sub,
        },
        pending.get("role"),
        "PENDING",
    )
    _audit(
        audit_table,
        organization_id,
        actor_sub,
        pending.get("role"),
        "MEMBER_ACTIVATED",
        actor_sub,
        "",
        pending.get("role"),
    )

    return api_response(
        200,
        {
            "message": "Invitation accepted",
            "member": _public_member(
                {
                    "user_sub": actor_sub,
                    "role": pending.get("role"),
                    "status": "ACTIVE",
                    "email": email,
                    "created_at": pending.get("created_at") or created_at,
                    "joined_at": created_at,
                }
            ),
        },
    )
