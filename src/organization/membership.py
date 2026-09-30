import os

from boto3.dynamodb.conditions import Key


def user_sub_index_name():
    return os.environ.get("USER_SUB_INDEX", "UserSubIndex")


def organizations_table():
    import boto3

    return boto3.resource("dynamodb").Table(
        os.environ.get("ORGANIZATIONS_TABLE", "Organizations")
    )


def members_table():
    import boto3

    return boto3.resource("dynamodb").Table(
        os.environ.get(
            "ORGANIZATION_MEMBERS_TABLE",
            "OrganizationMembers",
        )
    )


def select_current_organization(organizations):
    """Pick a display default without assuming one user has only one organization.

    Active memberships win. Otherwise the full list is used.
    The lowest organization_id is the stable default so a switcher can be added later.
    """
    if not organizations:
        return None

    active = [
        organization
        for organization in organizations
        if organization.get("status") == "ACTIVE"
    ]
    pool = active or list(organizations)

    return sorted(pool, key=lambda organization: organization.get("organization_id", ""))[0]


def list_memberships(user_sub, members=None, organizations=None):
    """Return organization memberships for a Cognito subject.

    user_sub must come from verified claims. This function does not scan.
    """
    if not user_sub:
        return []

    members = members or members_table()
    organizations = organizations or organizations_table()
    index_name = user_sub_index_name()

    items = []
    start_key = None

    while True:
        query = {
            "IndexName": index_name,
            "KeyConditionExpression": Key("user_sub").eq(user_sub),
        }

        if start_key:
            query["ExclusiveStartKey"] = start_key

        result = members.query(**query)
        items.extend(result.get("Items", []))
        start_key = result.get("LastEvaluatedKey")

        if not start_key:
            break

    memberships = []

    for item in items:
        organization_id = item.get("organization_id")

        if not organization_id:
            continue

        organization = organizations.get_item(
            Key={"organization_id": organization_id}
        ).get("Item")

        if not organization:
            continue

        # UserSubIndex projects keys and role, not membership status.
        # Read the base row so a deactivated member is not treated as active.
        user_sub_key = item.get("user_sub") or user_sub
        stored = members.get_item(
            Key={"organization_id": organization_id, "user_sub": user_sub_key}
        ).get("Item")

        if not stored:
            continue

        member_status = stored.get("status") or "ACTIVE"

        if member_status != "ACTIVE":
            continue

        memberships.append(
            {
                "organization_id": organization_id,
                "name": organization.get("name", ""),
                "role": stored.get("role", ""),
                "status": organization.get("status", ""),
            }
        )

    memberships.sort(key=lambda organization: organization["organization_id"])

    return memberships


def find_membership(user_sub, organization_id, members=None):
    """Look up one membership. The organization id is a lookup key, not proof of access."""
    if not user_sub or not organization_id:
        return None

    members = members or members_table()
    result = members.query(
        IndexName=user_sub_index_name(),
        KeyConditionExpression=(
            Key("user_sub").eq(user_sub)
            & Key("organization_id").eq(organization_id)
        ),
    )
    items = result.get("Items", [])

    return items[0] if items else None
