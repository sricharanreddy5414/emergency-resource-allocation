import hashlib
import json
import uuid
from datetime import datetime, timezone

from botocore.exceptions import ClientError

from common import (
    MAX_ORGANIZATION_NAME_LENGTH,
    api_response,
    get_user_sub,
    parse_json_body,
)
from observability import begin_request
from membership import members_table, organizations_table
from audit import build_audit_event, record_audit


def audit_table():
    import os

    import boto3

    return boto3.resource("dynamodb").Table(os.environ.get("AUDIT_TABLE", "AuditEvents"))

CLIENT_REQUEST_ID_ALPHABET = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
)


def normalize_client_request_id(value):
    if value is None:
        return None

    text = str(value).strip()

    if not text:
        return None

    if (
        len(text) < 8
        or len(text) > 80
        or any(character not in CLIENT_REQUEST_ID_ALPHABET for character in text)
    ):
        raise ValueError("Invalid organization request")

    return text


def organization_id_for_request(user_sub, client_request_id):
    digest = hashlib.sha256(
        f"{user_sub}:{client_request_id}".encode("utf-8")
    ).hexdigest()

    return "ORG-" + digest[:12].upper()


def organization_payload(organization_id, name, created_at, status="ACTIVE"):
    return {
        "message": "Organization created successfully",
        "organization": {
            "organization_id": organization_id,
            "name": name,
            "role": "OWNER",
            "status": status,
            "created_at": created_at,
        },
    }


def lambda_handler(event, context):
    begin_request(event)
    if event.get("httpMethod") == "OPTIONS":
        return api_response(200, {"message": "OK"})

    if event.get("httpMethod") != "POST":
        return api_response(405, {"message": "Method not allowed"})

    user_sub = get_user_sub(event)

    if not user_sub:
        return api_response(401, {"message": "Authentication required"})

    try:
        body = parse_json_body(event)
    except (json.JSONDecodeError, ValueError):
        return api_response(400, {"message": "Invalid JSON body"})

    raw_name = body.get("name", "")

    if raw_name is None:
        raw_name = ""

    organization_name = str(raw_name).strip()

    if not organization_name:
        return api_response(400, {"message": "Organization name is required"})

    if len(organization_name) > MAX_ORGANIZATION_NAME_LENGTH:
        return api_response(400, {"message": "Organization name is too long"})

    try:
        client_request_id = normalize_client_request_id(
            body.get("client_request_id")
        )
    except ValueError:
        return api_response(400, {"message": "Invalid organization request"})

    if client_request_id:
        organization_id = organization_id_for_request(
            user_sub,
            client_request_id,
        )
    else:
        organization_id = "ORG-" + uuid.uuid4().hex[:12].upper()

    created_at = datetime.now(timezone.utc).isoformat()
    org_table = organizations_table()
    mem_table = members_table()
    created = True
    status = "ACTIVE"

    try:
        org_table.put_item(
            Item={
                "organization_id": organization_id,
                "name": organization_name,
                "owner_sub": user_sub,
                "created_at": created_at,
                "status": status,
            },
            ConditionExpression="attribute_not_exists(organization_id)",
        )
    except ClientError as error:
        if error.response["Error"]["Code"] != "ConditionalCheckFailedException":
            print("Organization create failed:", error.response["Error"]["Code"])
            return api_response(500, {"message": "Unable to create organization"})

        existing = org_table.get_item(
            Key={"organization_id": organization_id}
        ).get("Item")

        if not existing or existing.get("owner_sub") != user_sub:
            return api_response(
                409,
                {"message": "Organization request conflicts with an existing organization"},
            )

        created = False
        organization_name = existing.get("name", organization_name)
        created_at = existing.get("created_at", created_at)
        status = existing.get("status", "ACTIVE")

    try:
        mem_table.put_item(
            Item={
                "organization_id": organization_id,
                "user_sub": user_sub,
                "role": "OWNER",
                "created_at": created_at,
            },
            ConditionExpression="attribute_not_exists(organization_id)",
        )
    except ClientError as error:
        if error.response["Error"]["Code"] != "ConditionalCheckFailedException":
            print("Membership create failed:", error.response["Error"]["Code"])
            return api_response(500, {"message": "Unable to create organization"})

    payload = organization_payload(
        organization_id,
        organization_name,
        created_at,
        status,
    )

    if not created:
        payload["message"] = "Organization already created"
    else:
        record_audit(
            audit_table(),
            build_audit_event(
                organization_id,
                user_sub,
                "OWNER",
                "organization.create",
                "organization",
                organization_id,
            ),
        )

    return api_response(201 if created else 200, payload)
