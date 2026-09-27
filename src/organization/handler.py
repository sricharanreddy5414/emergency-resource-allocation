import json
import os
import uuid
from datetime import datetime, timezone

import boto3

dynamodb = boto3.resource("dynamodb")
organizations_table = dynamodb.Table(
    os.environ.get("ORGANIZATIONS_TABLE", "Organizations")
)
members_table = dynamodb.Table(
    os.environ.get("ORGANIZATION_MEMBERS_TABLE", "OrganizationMembers")
)


def response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type,Authorization",
            "Access-Control-Allow-Methods": "OPTIONS,POST"
        },
        "body": json.dumps(body)
    }


def get_user_sub(event):
    claims = (
        event.get("requestContext", {})
        .get("authorizer", {})
        .get("claims", {})
    )

    return claims.get("sub")


def lambda_handler(event, context):
    if event.get("httpMethod") == "OPTIONS":
        return response(200, {"message": "OK"})

    if event.get("httpMethod") != "POST":
        return response(405, {"message": "Method not allowed"})

    user_sub = get_user_sub(event)

    if not user_sub:
        return response(401, {"message": "Authentication required"})

    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return response(400, {"message": "Invalid JSON body"})

    organization_name = str(body.get("name", "")).strip()

    if not organization_name:
        return response(400, {"message": "Organization name is required"})

    if len(organization_name) > 100:
        return response(400, {"message": "Organization name is too long"})

    organization_id = "ORG-" + uuid.uuid4().hex[:12].upper()
    created_at = datetime.now(timezone.utc).isoformat()

    organizations_table.put_item(
        Item={
            "organization_id": organization_id,
            "name": organization_name,
            "owner_sub": user_sub,
            "created_at": created_at,
            "status": "ACTIVE"
        },
        ConditionExpression="attribute_not_exists(organization_id)"
    )

    members_table.put_item(
        Item={
            "organization_id": organization_id,
            "user_sub": user_sub,
            "role": "OWNER",
            "created_at": created_at
        }
    )

    return response(
        201,
        {
            "message": "Organization created successfully",
            "organization": {
                "organization_id": organization_id,
                "name": organization_name,
                "role": "OWNER",
                "status": "ACTIVE",
                "created_at": created_at
            }
        }
    )
