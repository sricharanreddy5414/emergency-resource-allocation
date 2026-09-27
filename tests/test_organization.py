import json
import sys
from pathlib import Path

from botocore.exceptions import ClientError

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "shared"))
sys.path.insert(0, str(ROOT / "src" / "organization"))

import get_handler
import handler
import membership


@pytest.fixture(autouse=True)
def disable_live_audit(monkeypatch):
    monkeypatch.setattr(handler, "audit_table", lambda: None)


CLAIMS_SUB = "cognito-user-1"
OTHER_SUB = "someone-else"


def event(method="POST", body=None, subject=CLAIMS_SUB, query=None):
    payload = {
        "httpMethod": method,
        "requestContext": {
            "authorizer": {
                "claims": {
                    "sub": subject,
                }
            }
        },
    }

    if body is not None:
        payload["body"] = body if isinstance(body, str) else json.dumps(body)

    if query is not None:
        payload["queryStringParameters"] = query

    return payload


def response_body(result):
    return json.loads(result["body"])


def conditional_error():
    return ClientError(
        {
            "Error": {
                "Code": "ConditionalCheckFailedException",
                "Message": "exists",
            }
        },
        "PutItem",
    )


def condition_pairs(expression):
    if expression is None or not hasattr(expression, "get_expression"):
        return []

    data = expression.get_expression()
    values = data.get("values", ())

    if data.get("operator") == "=" and len(values) == 2:
        return [(getattr(values[0], "name", None), values[1])]

    pairs = []

    for value in values:
        pairs.extend(condition_pairs(value))

    return pairs


class OrganizationStore:
    def __init__(self):
        self.items = {}
        self.puts = []

    def put_item(self, Item, ConditionExpression=None):
        self.puts.append(Item)
        key = Item["organization_id"]

        if ConditionExpression and key in self.items:
            raise conditional_error()

        self.items[key] = dict(Item)

    def get_item(self, Key):
        item = self.items.get(Key["organization_id"])
        return {"Item": item} if item else {}


class MemberStore:
    def __init__(self, rows=None, pages=None):
        self.rows = rows or []
        self.pages = pages
        self.puts = []
        self.queries = []
        self.scans = 0

    def put_item(self, Item, ConditionExpression=None):
        key = (Item["organization_id"], Item["user_sub"])
        existing = {
            (row["organization_id"], row["user_sub"])
            for row in self.rows
        }

        if ConditionExpression and key in existing:
            raise conditional_error()

        self.puts.append(dict(Item))
        self.rows.append(dict(Item))

    def query(self, **kwargs):
        self.queries.append(kwargs)

        if self.pages is not None:
            if kwargs.get("ExclusiveStartKey"):
                return {"Items": self.pages[1]}

            return {
                "Items": self.pages[0],
                "LastEvaluatedKey": {"user_sub": "next"},
            }

        pairs = condition_pairs(kwargs.get("KeyConditionExpression"))
        rows = list(self.rows)

        for name, value in pairs:
            rows = [row for row in rows if row.get(name) == value]

        return {"Items": rows}

    def scan(self, **kwargs):
        self.scans += 1
        raise AssertionError("membership lookup must not scan")


def use_stores(monkeypatch, organizations, members):
    monkeypatch.setattr(handler, "organizations_table", lambda: organizations)
    monkeypatch.setattr(handler, "members_table", lambda: members)
    monkeypatch.setattr(membership, "organizations_table", lambda: organizations)
    monkeypatch.setattr(membership, "members_table", lambda: members)


def test_authenticated_organization_lookup(monkeypatch):
    organizations = OrganizationStore()
    organizations.items["ORG-1"] = {
        "organization_id": "ORG-1",
        "name": "City Hospital",
        "status": "ACTIVE",
    }
    members = MemberStore(
        rows=[
            {
                "organization_id": "ORG-1",
                "user_sub": CLAIMS_SUB,
                "role": "OWNER",
            }
        ]
    )
    use_stores(monkeypatch, organizations, members)

    result = get_handler.lambda_handler(
        event(
            "GET",
            query={"user_sub": OTHER_SUB, "organization_id": "ORG-OTHER"},
        ),
        None,
    )

    body = response_body(result)

    assert result["statusCode"] == 200
    assert body == {
        "organizations": [
            {
                "organization_id": "ORG-1",
                "name": "City Hospital",
                "role": "OWNER",
                "status": "ACTIVE",
            }
        ]
    }
    assert members.scans == 0
    assert members.queries[0]["IndexName"] == "UserSubIndex"
    assert "user_sub" not in body["organizations"][0]


def test_no_organization_membership(monkeypatch):
    organizations = OrganizationStore()
    members = MemberStore()
    use_stores(monkeypatch, organizations, members)

    result = get_handler.lambda_handler(event("GET"), None)

    assert result["statusCode"] == 200
    assert response_body(result) == {"organizations": []}
    assert members.scans == 0


def test_membership_lookup_uses_index_and_pagination():
    members = MemberStore(
        pages=[
            [
                {
                    "organization_id": "ORG-B",
                    "user_sub": CLAIMS_SUB,
                    "role": "MEMBER",
                }
            ],
            [
                {
                    "organization_id": "ORG-A",
                    "user_sub": CLAIMS_SUB,
                    "role": "OWNER",
                }
            ],
        ]
    )
    organizations = OrganizationStore()
    organizations.items["ORG-A"] = {
        "organization_id": "ORG-A",
        "name": "Alpha",
        "status": "ACTIVE",
    }
    organizations.items["ORG-B"] = {
        "organization_id": "ORG-B",
        "name": "Beta",
        "status": "ARCHIVED",
    }

    found = membership.list_memberships(
        CLAIMS_SUB,
        members=members,
        organizations=organizations,
    )

    assert [item["organization_id"] for item in found] == ["ORG-A", "ORG-B"]
    assert len(members.queries) == 2
    assert members.scans == 0

    direct = MemberStore(
        rows=[
            {
                "organization_id": "ORG-A",
                "user_sub": CLAIMS_SUB,
                "role": "OWNER",
            }
        ]
    )

    assert membership.find_membership(
        CLAIMS_SUB,
        "ORG-A",
        members=direct,
    )["role"] == "OWNER"
    assert membership.find_membership(
        CLAIMS_SUB,
        "ORG-MISSING",
        members=direct,
    ) is None
    assert direct.scans == 0


def test_select_current_organization_keeps_multiple_memberships():
    organizations = [
        {
            "organization_id": "ORG-B",
            "name": "Beta",
            "role": "MEMBER",
            "status": "ACTIVE",
        },
        {
            "organization_id": "ORG-A",
            "name": "Alpha",
            "role": "OWNER",
            "status": "ARCHIVED",
        },
        {
            "organization_id": "ORG-C",
            "name": "City",
            "role": "OWNER",
            "status": "ACTIVE",
        },
    ]

    selected = membership.select_current_organization(organizations)

    assert selected["organization_id"] == "ORG-B"
    assert membership.select_current_organization([]) is None


def test_invalid_organization_request_is_rejected(monkeypatch):
    organizations = OrganizationStore()
    members = MemberStore()
    use_stores(monkeypatch, organizations, members)

    result = handler.lambda_handler(
        event(
            body={
                "name": "City Hospital",
                "client_request_id": "bad id",
                "user_sub": OTHER_SUB,
            }
        ),
        None,
    )

    assert result["statusCode"] == 400
    assert response_body(result)["message"] == "Invalid organization request"
    assert organizations.puts == []
    assert members.puts == []


def test_missing_organization_name(monkeypatch):
    organizations = OrganizationStore()
    members = MemberStore()
    use_stores(monkeypatch, organizations, members)

    result = handler.lambda_handler(event(body={"name": "   "}), None)

    assert result["statusCode"] == 400
    assert response_body(result)["message"] == "Organization name is required"
    assert organizations.puts == []


def test_organization_name_too_long(monkeypatch):
    organizations = OrganizationStore()
    members = MemberStore()
    use_stores(monkeypatch, organizations, members)

    result = handler.lambda_handler(
        event(body={"name": "A" * 101}),
        None,
    )

    assert result["statusCode"] == 400
    assert response_body(result)["message"] == "Organization name is too long"


def test_authenticated_organization_creation_ignores_client_identity(monkeypatch):
    organizations = OrganizationStore()
    members = MemberStore()
    use_stores(monkeypatch, organizations, members)

    result = handler.lambda_handler(
        event(
            body={
                "name": "  City Hospital  ",
                "client_request_id": "request-1234",
                "user_sub": OTHER_SUB,
                "organization_id": "ORG-CLIENT",
                "role": "ADMIN",
            }
        ),
        None,
    )
    body = response_body(result)
    created = next(iter(organizations.items.values()))

    assert result["statusCode"] == 201
    assert created["owner_sub"] == CLAIMS_SUB
    assert created["organization_id"] != "ORG-CLIENT"
    assert created["name"] == "City Hospital"
    assert members.puts[0]["user_sub"] == CLAIMS_SUB
    assert members.puts[0]["role"] == "OWNER"
    assert body["organization"]["role"] == "OWNER"
    assert "owner_sub" not in body["organization"]

    replay = handler.lambda_handler(
        event(
            body={
                "name": "Different Name",
                "client_request_id": "request-1234",
                "user_sub": OTHER_SUB,
            }
        ),
        None,
    )
    replay_body = response_body(replay)

    assert replay["statusCode"] == 200
    assert replay_body["organization"]["organization_id"] == created["organization_id"]
    assert replay_body["organization"]["name"] == "City Hospital"
    assert len(organizations.items) == 1
    assert len(members.puts) == 1


def test_unauthorized_access_does_not_write(monkeypatch):
    organizations = OrganizationStore()
    members = MemberStore()
    use_stores(monkeypatch, organizations, members)
    anonymous = event(body={"name": "City Hospital"})
    anonymous["requestContext"] = {}

    created = handler.lambda_handler(anonymous, None)
    lookup = event("GET")
    lookup["requestContext"] = {}
    listed = get_handler.lambda_handler(lookup, None)

    assert created["statusCode"] == 401
    assert listed["statusCode"] == 401
    assert organizations.puts == []
    assert members.puts == []
    assert members.queries == []


def test_user_sub_comes_from_cognito_claims(monkeypatch):
    organizations = OrganizationStore()
    members = MemberStore()
    use_stores(monkeypatch, organizations, members)

    handler.lambda_handler(
        event(
            body={
                "name": "Claims Hospital",
                "user_sub": OTHER_SUB,
            }
        ),
        None,
    )

    assert organizations.puts[0]["owner_sub"] == CLAIMS_SUB
    assert members.puts[0]["user_sub"] == CLAIMS_SUB
    assert OTHER_SUB not in json.dumps(organizations.puts + members.puts)


def test_conflicting_idempotency_key_does_not_attach_to_another_owner(monkeypatch):
    organizations = OrganizationStore()
    members = MemberStore()
    use_stores(monkeypatch, organizations, members)
    organization_id = handler.organization_id_for_request(
        CLAIMS_SUB,
        "request-1234",
    )
    organizations.items[organization_id] = {
        "organization_id": organization_id,
        "name": "Taken",
        "owner_sub": OTHER_SUB,
        "status": "ACTIVE",
        "created_at": "2026-09-27T00:00:00+00:00",
    }

    result = handler.lambda_handler(
        event(
            body={
                "name": "City Hospital",
                "client_request_id": "request-1234",
            }
        ),
        None,
    )

    assert result["statusCode"] == 409
    assert members.puts == []
