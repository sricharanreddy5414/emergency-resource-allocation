import json
import sys
from pathlib import Path

import pytest

from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [
    str(ROOT / "src" / "shared"),
    str(ROOT / "src" / "organization"),
]

import access
import get_handler
import handler
import membership
from access import AccessError


OWNER = "owner-sub"
ADMIN = "admin-sub"
OPERATOR = "operator-sub"
MEMBER = "member-sub"
OTHER = "other-sub"
ORG = "ORG-A"
ORG_B = "ORG-B"


def conditional_error():
    return ClientError(
        {"Error": {"Code": "ConditionalCheckFailedException", "Message": "exists"}},
        "UpdateItem",
    )


def pairs(expression):
    if expression is None or not hasattr(expression, "get_expression"):
        return []

    data = expression.get_expression()
    values = data.get("values", ())

    if data.get("operator") == "=" and len(values) == 2:
        return [(getattr(values[0], "name", None), values[1])]

    found = []

    for value in values:
        found.extend(pairs(value))

    return found


class OrganizationStore:
    def __init__(self):
        self.items = {
            ORG: {"organization_id": ORG, "name": "Org A", "status": "ACTIVE"},
            ORG_B: {"organization_id": ORG_B, "name": "Org B", "status": "ACTIVE"},
        }

    def put_item(self, Item, ConditionExpression=None):
        if ConditionExpression and Item["organization_id"] in self.items:
            raise conditional_error()

        self.items[Item["organization_id"]] = dict(Item)

    def get_item(self, Key):
        item = self.items.get(Key["organization_id"])

        return {"Item": item} if item else {}

    def update_item(self, Key, UpdateExpression, ConditionExpression, ExpressionAttributeValues, **kwargs):
        item = self.items.get(Key["organization_id"])

        if item is None or "attribute_exists(organization_id)" not in ConditionExpression:
            raise conditional_error()

        if "attribute_not_exists(membership_epoch)" in ConditionExpression:
            if "membership_epoch" in item:
                raise conditional_error()

            item["membership_epoch"] = ExpressionAttributeValues[":next"]

            return

        if item.get("membership_epoch") != ExpressionAttributeValues[":seen"]:
            raise conditional_error()

        item["membership_epoch"] = ExpressionAttributeValues[":next"]


class MemberStore:
    def __init__(self, rows):
        self.rows = rows
        self.scans = 0

    def query(self, **kwargs):
        found = list(self.rows)

        for name, value in pairs(kwargs.get("KeyConditionExpression")):
            found = [row for row in found if row.get(name) == value]

        return {"Items": found}

    def get_item(self, Key):
        for row in self.rows:
            if row.get("organization_id") == Key["organization_id"] and row.get("user_sub") == Key["user_sub"]:
                return {"Item": row}

        return {}

    def put_item(self, Item, ConditionExpression=None):
        if ConditionExpression and any(
            row.get("organization_id") == Item["organization_id"] and row.get("user_sub") == Item["user_sub"]
            for row in self.rows
        ):
            raise conditional_error()

        self.rows.append(dict(Item))

    def update_item(self, Key, ExpressionAttributeValues, **kwargs):
        row = self.get_item(Key).get("Item")

        if not row or row.get("role") != ExpressionAttributeValues[":expected_role"]:
            raise conditional_error()

        actual = row.get("status", "ACTIVE")

        if actual != ExpressionAttributeValues[":expected_status"]:
            raise conditional_error()

        row["role"] = ExpressionAttributeValues[":role"]
        row["status"] = ExpressionAttributeValues[":status"]
        row["updated_at"] = ExpressionAttributeValues[":updated_at"]
        row["updated_by"] = ExpressionAttributeValues[":updated_by"]

    def scan(self, **kwargs):
        self.scans += 1
        raise AssertionError("member API must not scan")


class AuditStore:
    def __init__(self):
        self.items = []

    def put_item(self, Item):
        self.items.append(Item)


def event(body=None, subject=OWNER, method="POST", email="", verified=True, query=None):
    claims = {"sub": subject}

    if email:
        claims["email"] = email
        claims["email_verified"] = verified

    payload = {
        "httpMethod": method,
        "requestContext": {"authorizer": {"claims": claims}},
    }

    if body is not None:
        payload["body"] = json.dumps(body)

    if query is not None:
        payload["queryStringParameters"] = query

    return payload


def body_of(result):
    return json.loads(result["body"])


def seed(monkeypatch, rows):
    organizations = OrganizationStore()
    members = MemberStore(rows)
    audits = AuditStore()
    monkeypatch.setattr(membership, "organizations_table", lambda: organizations)
    monkeypatch.setattr(membership, "members_table", lambda: members)
    monkeypatch.setattr(handler, "organizations_table", lambda: organizations)
    monkeypatch.setattr(handler, "members_table", lambda: members)
    monkeypatch.setattr(handler, "audit_table", lambda: audits)

    return organizations, members, audits


def owner_row(subject=OWNER, organization_id=ORG, role="OWNER", status=None, email=""):
    row = {
        "organization_id": organization_id,
        "user_sub": subject,
        "role": role,
        "created_at": "2026-09-28T00:00:00+00:00",
    }

    if status:
        row["status"] = status

    if email:
        row["email"] = email

    return row


def test_missing_auth_does_not_write(monkeypatch):
    _organizations, members, audits = seed(monkeypatch, [owner_row()])
    anonymous = event(body={"operation": "invite_member", "email": "a@example.com", "role": "MEMBER"})
    anonymous["requestContext"] = {}

    result = handler.lambda_handler(anonymous, None)

    assert result["statusCode"] == 401
    assert len(members.rows) == 1
    assert audits.items == []


def test_owner_invites_member_and_ignores_spoofed_actor(monkeypatch):
    organizations, members, audits = seed(monkeypatch, [owner_row()])

    result = handler.lambda_handler(
        event(
            body={
                "operation": "invite_member",
                "organization_id": ORG,
                "email": "Member@Example.com",
                "role": "MEMBER",
                "user_sub": OTHER,
                "actor_role": "MEMBER",
            }
        ),
        None,
    )
    created = members.rows[-1]

    assert result["statusCode"] == 201
    assert created["email"] == "member@example.com"
    assert created["role"] == "MEMBER"
    assert created["status"] == "PENDING"
    assert created["user_sub"].startswith("invite-")
    assert created["invited_by"] == OWNER
    assert organizations.items[ORG]["membership_epoch"] == 1
    assert audits.items[0]["action"] == "MEMBER_INVITED"
    assert "token" not in audits.items[0]["metadata"]
    assert "password" not in json.dumps(audits.items[0])


def test_duplicate_invitation_is_rejected(monkeypatch):
    _organizations, members, _audits = seed(monkeypatch, [owner_row()])
    payload = {"operation": "invite_member", "organization_id": ORG, "email": "member@example.com", "role": "OPERATOR"}
    assert handler.lambda_handler(event(body=payload), None)["statusCode"] == 201

    duplicate = handler.lambda_handler(event(body=payload), None)

    assert duplicate["statusCode"] == 409
    assert len([row for row in members.rows if row["role"] == "OPERATOR"]) == 1


def test_owner_role_cannot_be_assigned(monkeypatch):
    _organizations, members, _audits = seed(monkeypatch, [owner_row()])

    result = handler.lambda_handler(
        event(body={"operation": "invite_member", "organization_id": ORG, "email": "a@example.com", "role": "OWNER"}),
        None,
    )

    assert result["statusCode"] == 400
    assert body_of(result)["message"] == "Role is invalid"
    assert len(members.rows) == 1


def test_invalid_role_is_rejected(monkeypatch):
    _organizations, members, _audits = seed(monkeypatch, [owner_row()])
    result = handler.lambda_handler(
        event(body={"operation": "invite_member", "organization_id": ORG, "email": "a@example.com", "role": "SUPERUSER"}),
        None,
    )

    assert result["statusCode"] == 400
    assert len(members.rows) == 1


def test_operator_and_member_cannot_manage_members(monkeypatch):
    rows = [
        owner_row(),
        owner_row(OPERATOR, role="OPERATOR"),
        owner_row(MEMBER, role="MEMBER"),
    ]
    _organizations, members, _audits = seed(monkeypatch, rows)
    payload = {"operation": "invite_member", "organization_id": ORG, "email": "a@example.com", "role": "MEMBER"}

    operator = handler.lambda_handler(event(body=payload, subject=OPERATOR), None)
    member = handler.lambda_handler(event(body=payload, subject=MEMBER), None)

    assert operator["statusCode"] == 403
    assert member["statusCode"] == 403
    assert len(members.rows) == 3


def test_cross_organization_change_is_rejected(monkeypatch):
    rows = [owner_row(), owner_row(OTHER, ORG_B)]
    organizations, members, _audits = seed(monkeypatch, rows)

    result = handler.lambda_handler(
        event(
            body={
                "operation": "deactivate_member",
                "organization_id": ORG_B,
                "target_user_sub": OTHER,
            }
        ),
        None,
    )

    assert result["statusCode"] == 403
    assert organizations.items[ORG_B].get("membership_epoch") is None
    assert all(row.get("status", "ACTIVE") == "ACTIVE" for row in members.rows)


def test_admin_cannot_change_owner_or_promote_owner(monkeypatch):
    rows = [owner_row(), owner_row(ADMIN, role="ADMIN")]
    _organizations, members, _audits = seed(monkeypatch, rows)

    demote = handler.lambda_handler(
        event(
            body={"operation": "change_role", "organization_id": ORG, "target_user_sub": OWNER, "role": "MEMBER"},
            subject=ADMIN,
        ),
        None,
    )
    promote = handler.lambda_handler(
        event(
            body={"operation": "invite_member", "organization_id": ORG, "email": "new@example.com", "role": "OWNER"},
            subject=ADMIN,
        ),
        None,
    )

    assert demote["statusCode"] == 403
    assert promote["statusCode"] == 400
    assert members.rows[0]["role"] == "OWNER"


def test_admin_can_change_non_owner_role(monkeypatch):
    rows = [owner_row(), owner_row(ADMIN, role="ADMIN"), owner_row(MEMBER, role="MEMBER")]
    _organizations, _members, audits = seed(monkeypatch, rows)

    result = handler.lambda_handler(
        event(
            body={"operation": "change_role", "organization_id": ORG, "target_user_sub": MEMBER, "role": "OPERATOR"},
            subject=ADMIN,
        ),
        None,
    )

    assert result["statusCode"] == 200
    assert audits.items[0]["action"] == "MEMBER_ROLE_CHANGED"
    assert audits.items[0]["metadata"]["old_role"] == "MEMBER"
    assert audits.items[0]["metadata"]["new_role"] == "OPERATOR"


def test_last_owner_cannot_be_deactivated_or_demoted(monkeypatch):
    organizations, members, _audits = seed(monkeypatch, [owner_row(), owner_row(MEMBER, role="MEMBER")])

    deactivate = handler.lambda_handler(
        event(body={"operation": "deactivate_member", "organization_id": ORG, "target_user_sub": OWNER}),
        None,
    )
    demote = handler.lambda_handler(
        event(body={"operation": "change_role", "organization_id": ORG, "target_user_sub": OWNER, "role": "ADMIN"}),
        None,
    )

    assert deactivate["statusCode"] == 409
    assert demote["statusCode"] == 409
    assert members.rows[0]["role"] == "OWNER"
    assert organizations.items[ORG].get("membership_epoch") is None


def test_second_owner_can_demote_another_owner(monkeypatch):
    rows = [owner_row(), owner_row(OTHER, role="OWNER")]
    _organizations, members, audits = seed(monkeypatch, rows)

    result = handler.lambda_handler(
        event(body={"operation": "change_role", "organization_id": ORG, "target_user_sub": OTHER, "role": "ADMIN"}),
        None,
    )

    assert result["statusCode"] == 200
    assert members.rows[1]["role"] == "ADMIN"
    assert audits.items[0]["action"] == "MEMBER_ROLE_CHANGED"


def test_self_role_change_is_rejected(monkeypatch):
    rows = [owner_row(), owner_row(OTHER, role="OWNER")]
    _organizations, members, _audits = seed(monkeypatch, rows)

    result = handler.lambda_handler(
        event(body={"operation": "change_role", "organization_id": ORG, "target_user_sub": OWNER, "role": "ADMIN"}),
        None,
    )

    assert result["statusCode"] == 403
    assert members.rows[0]["role"] == "OWNER"


def test_deactivate_and_reactivate_member(monkeypatch):
    rows = [owner_row(), owner_row(MEMBER, role="MEMBER", email="member@example.com")]
    _organizations, members, audits = seed(monkeypatch, rows)

    deactivated = handler.lambda_handler(
        event(body={"operation": "deactivate_member", "organization_id": ORG, "target_user_sub": MEMBER}),
        None,
    )
    reactivated = handler.lambda_handler(
        event(body={"operation": "reactivate_member", "organization_id": ORG, "target_user_sub": MEMBER}),
        None,
    )

    assert deactivated["statusCode"] == 200
    assert body_of(deactivated)["member"]["status"] == "INACTIVE"
    assert reactivated["statusCode"] == 200
    assert body_of(reactivated)["member"]["status"] == "ACTIVE"
    assert [item["action"] for item in audits.items] == ["MEMBER_DEACTIVATED", "MEMBER_REACTIVATED"]


def test_verified_user_accepts_invitation(monkeypatch):
    _organizations, members, audits = seed(monkeypatch, [owner_row()])
    handler.lambda_handler(
        event(body={"operation": "invite_member", "organization_id": ORG, "email": "member@example.com", "role": "MEMBER"}),
        None,
    )

    accepted = handler.lambda_handler(
        event(
            body={"operation": "accept_invitation", "organization_id": ORG, "role": "OWNER", "user_sub": OTHER},
            subject=MEMBER,
            email="member@example.com",
        ),
        None,
    )
    active = next(row for row in members.rows if row["user_sub"] == MEMBER)
    pending = next(row for row in members.rows if row["user_sub"].startswith("invite-"))

    assert accepted["statusCode"] == 200
    assert active["role"] == "MEMBER"
    assert active["status"] == "ACTIVE"
    assert pending["status"] == "INACTIVE"
    assert audits.items[-1]["action"] == "MEMBER_ACTIVATED"


def test_unverified_email_cannot_accept(monkeypatch):
    _organizations, members, _audits = seed(monkeypatch, [owner_row()])
    handler.lambda_handler(
        event(body={"operation": "invite_member", "organization_id": ORG, "email": "member@example.com", "role": "ADMIN"}),
        None,
    )

    result = handler.lambda_handler(
        event(
            body={"operation": "accept_invitation", "organization_id": ORG},
            subject=MEMBER,
            email="member@example.com",
            verified=False,
        ),
        None,
    )

    assert result["statusCode"] == 403
    assert not any(row["user_sub"] == MEMBER for row in members.rows)


def test_inactive_membership_is_rejected(monkeypatch):
    seed(
        monkeypatch,
        [owner_row(status="INACTIVE")],
    )

    with pytest.raises(AccessError) as error:
        access.authorize(event(), {})

    assert error.value.status_code == 403


def test_owner_can_list_members_and_member_cannot(monkeypatch):
    seed(monkeypatch, [owner_row(), owner_row(MEMBER, role="MEMBER", email="member@example.com")])

    listed = get_handler.lambda_handler(event(method="GET", query={"view": "members", "organization_id": ORG}), None)
    denied = get_handler.lambda_handler(
        event(method="GET", subject=MEMBER, query={"view": "members", "organization_id": ORG}),
        None,
    )
    payload = body_of(listed)

    assert listed["statusCode"] == 200
    assert denied["statusCode"] == 403
    assert payload["organization_id"] == ORG
    assert "invited_by" not in json.dumps(payload)
    assert {item["role"] for item in payload["members"]} == {"OWNER", "MEMBER"}


def test_pending_invitation_is_visible_only_to_verified_email(monkeypatch):
    seed(monkeypatch, [owner_row()])
    handler.lambda_handler(
        event(body={"operation": "invite_member", "organization_id": ORG, "email": "member@example.com", "role": "OPERATOR"}),
        None,
    )

    visible = get_handler.lambda_handler(
        event(method="GET", subject=MEMBER, email="member@example.com"),
        None,
    )
    hidden = get_handler.lambda_handler(
        event(method="GET", subject=MEMBER, email="member@example.com", verified=False),
        None,
    )

    assert body_of(visible)["pending_invitations"][0]["role"] == "OPERATOR"
    assert "pending_invitations" not in body_of(hidden)
    assert body_of(visible)["organizations"] == []


def test_membership_epoch_conflict_does_not_create_member(monkeypatch):
    organizations, members, _audits = seed(monkeypatch, [owner_row()])
    organizations.items[ORG]["membership_epoch"] = 4
    original = organizations.get_item

    def stale(Key):
        found = original(Key)
        item = dict(found["Item"])
        item["membership_epoch"] = 3

        return {"Item": item}

    organizations.get_item = stale
    result = handler.lambda_handler(
        event(body={"operation": "invite_member", "organization_id": ORG, "email": "a@example.com", "role": "MEMBER"}),
        None,
    )

    assert result["statusCode"] == 409
    assert len(members.rows) == 1
    assert organizations.items[ORG]["membership_epoch"] == 4


def test_existing_organization_creation_still_assigns_owner(monkeypatch):
    _organizations, members, _audits = seed(monkeypatch, [])

    result = handler.lambda_handler(event(body={"name": "New Org"}), None)
    created = members.rows[0]

    assert result["statusCode"] == 201
    assert created["role"] == "OWNER"
    assert created["status"] == "ACTIVE"
    assert created["user_sub"] == OWNER


def test_frontend_member_controls_do_not_offer_owner_promotion():
    html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
    panel = html[html.index('id="organizationMembersPanel"'):html.index('id="resources"')]
    invite = script[script.index("async function submitMemberInvite"):script.index("async function changeMemberRole")]

    assert "Invite member" in panel
    assert "<option value=\"OWNER\">" not in panel
    assert 'operation: "invite_member"' in invite
    assert "target_user_sub" not in invite
    assert "canManageMembers" in script
