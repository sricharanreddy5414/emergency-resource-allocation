"""Tenant authorization. Cognito claims and DynamoDB membership are authoritative."""

from boto3.dynamodb.conditions import Key

from common import get_user_sub
from membership import list_memberships

READ_ROLES = {"MEMBER", "OPERATOR", "ADMIN", "OWNER"}
REQUEST_ROLES = {"MEMBER", "OPERATOR", "ADMIN", "OWNER"}
OPERATE_ROLES = {"OPERATOR", "ADMIN", "OWNER"}
LOCATION_WRITE_ROLES = {"ADMIN", "OWNER"}
ORGANIZATION_INDEX = "OrganizationLocationIndex"
RESOURCE_HISTORY_INDEX = "ResourceIdIndex"


class AccessError(Exception):
    def __init__(self, status_code, message):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def requested_organization_id(event, body=None):
    query = (event or {}).get("queryStringParameters") or {}
    if not isinstance(query, dict):
        query = {}

    body = body or {}
    if not isinstance(body, dict):
        body = {}

    value = query.get("organization_id") or body.get("organization_id")

    if value is None:
        return None

    value = str(value).strip()

    return value or None


def authorize(event, body=None, allowed_roles=READ_ROLES, members=None, organizations=None):
    """Return (user_sub, membership). Fail closed."""
    user_sub = get_user_sub(event)

    if not user_sub:
        raise AccessError(401, "Authentication required")

    memberships = list_memberships(
        user_sub,
        members=members,
        organizations=organizations,
    )

    if not memberships:
        raise AccessError(403, "Organization membership is required")

    requested = requested_organization_id(event, body)

    if requested:
        membership = next(
            (
                item
                for item in memberships
                if item.get("organization_id") == requested
            ),
            None,
        )

        if not membership:
            raise AccessError(403, "Organization access denied")
    elif len(memberships) == 1:
        membership = memberships[0]
    else:
        raise AccessError(400, "Organization selection is required")

    role = membership.get("role")
    status = membership.get("status") or "ACTIVE"

    if status != "ACTIVE":
        raise AccessError(403, "Organization access denied")

    if role not in allowed_roles:
        raise AccessError(403, "You are not allowed to perform this action")

    return user_sub, membership


def require_location(locations_table, organization_id, location_id):
    if not location_id or not str(location_id).strip():
        raise AccessError(400, "Location is required")

    location_id = str(location_id).strip()
    item = locations_table.get_item(
        Key={
            "organization_id": organization_id,
            "location_id": location_id,
        }
    ).get("Item")

    if (
        not item
        or item.get("organization_id") != organization_id
        or item.get("status") != "ACTIVE"
    ):
        raise AccessError(404, "Location not found")

    return item


def require_owned(item, organization_id):
    """Hide records that are missing or belong to another organization."""
    if not item or item.get("organization_id") != organization_id:
        raise AccessError(404, "Record not found")

    return item


def query_by_organization(table, organization_id, location_id=None, index_name=ORGANIZATION_INDEX):
    key = Key("organization_id").eq(organization_id)

    if location_id:
        key = key & Key("location_id").eq(location_id)

    items = []
    start_key = None

    while True:
        query = {
            "KeyConditionExpression": key,
        }

        if index_name:
            query["IndexName"] = index_name

        if start_key:
            query["ExclusiveStartKey"] = start_key

        result = table.query(**query)
        items.extend(result.get("Items", []))
        start_key = result.get("LastEvaluatedKey")

        if not start_key:
            return items


def query_history(table, resource_id):
    items = []
    start_key = None
    key = Key("resource_id").eq(resource_id)

    while True:
        query = {
            "IndexName": RESOURCE_HISTORY_INDEX,
            "KeyConditionExpression": key,
        }

        if start_key:
            query["ExclusiveStartKey"] = start_key

        result = table.query(**query)
        items.extend(result.get("Items", []))
        start_key = result.get("LastEvaluatedKey")

        if not start_key:
            return items
