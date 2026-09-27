import re
import uuid
from datetime import datetime, timezone

from botocore.exceptions import ClientError

from access import (
    READ_ROLES,
    AccessError,
    authorize,
    query_by_organization,
)
from attributes import validate_matching_config, validate_schema
from audit import build_audit_event, record_audit
from common import ALLOWED_ORIGIN, api_response, parse_json_body
from pages import decode_token, encode_token

import json


CATALOG_WRITE_ROLES = {"ADMIN", "OWNER"}
NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _-]{1,59}$")
COLLECTIONS = {
    "resource-types": {
        "table_env": "RESOURCE_TYPES_TABLE",
        "table_name": "ResourceTypes",
        "id_name": "resource_type_id",
        "prefix": "RT-",
        "label": "Resource type",
    },
    "request-types": {
        "table_env": "REQUEST_TYPES_TABLE",
        "table_name": "RequestTypes",
        "id_name": "request_type_id",
        "prefix": "RQ-",
        "label": "Request type",
    },
}


def response(status_code, body):
    payload = api_response(status_code, body)
    payload["headers"]["Access-Control-Allow-Methods"] = "GET,POST,PUT,PATCH,DELETE,OPTIONS"
    return payload


def table_for(kind):
    import os

    import boto3

    spec = COLLECTIONS[kind]
    return boto3.resource("dynamodb").Table(os.environ.get(spec["table_env"], spec["table_name"]))


def audit_table():
    import os

    import boto3

    return boto3.resource("dynamodb").Table(os.environ.get("AUDIT_TABLE", "AuditEvents"))


def now():
    return datetime.now(timezone.utc).isoformat()


def collection_from_path(path):
    for name in COLLECTIONS:
        if f"/{name}" in path:
            return name

    return None


def clean_text(value, limit, required=False):
    text = str(value or "").strip()

    if required and not text:
        raise AccessError(400, "A required field is missing")

    if len(text) > limit:
        raise AccessError(400, "A field is too long")

    return text


def type_view(item, id_name):
    return {
        "organization_id": item.get("organization_id"),
        "resource_type_id": item.get("resource_type_id", ""),
        "request_type_id": item.get("request_type_id", ""),
        "name": item.get("name", ""),
        "description": item.get("description", ""),
        "category": item.get("category", ""),
        "status": item.get("status", ""),
        "attributes_schema": item.get("attributes_schema") or {"fields": []},
        "matching_config": item.get("matching_config") or {},
        "visibility_default": item.get("visibility_default", "PRIVATE"),
        "default_priority": item.get("default_priority", 3),
        "created_at": item.get("created_at", ""),
        "updated_at": item.get("updated_at", ""),
    }


def public_fields(body):
    visibility = str(body.get("visibility_default") or "PRIVATE").strip().upper()

    if visibility not in {"PRIVATE", "PUBLIC"}:
        raise AccessError(400, "Visibility is invalid")

    return visibility


def build_record(body, organization_id, actor_sub, spec, existing=None):
    name = clean_text(body.get("name"), 60, required=True)

    if not NAME_PATTERN.match(name):
        raise AccessError(400, f"{spec['label']} name is invalid")

    description = clean_text(body.get("description"), 300)
    category = clean_text(body.get("category"), 40)
    schema = validate_schema(body.get("attributes_schema"))
    matching = validate_matching_config(body.get("matching_config"), schema)
    timestamp = now()
    record = dict(existing or {})
    record.update(
        {
            "organization_id": organization_id,
            spec["id_name"]: (existing or {}).get(spec["id_name"]) or spec["prefix"] + uuid.uuid4().hex[:12].upper(),
            "name": name,
            "description": description,
            "category": category,
            "status": (existing or {}).get("status", "ACTIVE"),
            "attributes_schema": schema,
            "matching_config": matching,
            "created_at": (existing or {}).get("created_at", timestamp),
            "updated_at": timestamp,
            "created_by": (existing or {}).get("created_by", actor_sub),
        }
    )

    if spec["id_name"] == "resource_type_id":
        record["visibility_default"] = public_fields(body if "visibility_default" in body else {"visibility_default": record.get("visibility_default", "PRIVATE")})

    if spec["id_name"] == "request_type_id":
        try:
            priority = int(body.get("default_priority", record.get("default_priority", 3)))
        except (TypeError, ValueError):
            raise AccessError(400, "Priority is invalid")

        if priority < 1 or priority > 5:
            raise AccessError(400, "Priority is invalid")

        record["default_priority"] = priority

    return record


def load_type(kind, organization_id, type_id):
    spec = COLLECTIONS[kind]
    item = table_for(kind).get_item(
        Key={"organization_id": organization_id, spec["id_name"]: type_id}
    ).get("Item")

    if not item or item.get("organization_id") != organization_id:
        raise AccessError(404, f"{spec['label']} not found")

    return item


def lambda_handler(event, context):
    method = (event.get("httpMethod") or "GET").upper()
    path = event.get("path") or ""
    kind = collection_from_path(path)

    if method == "OPTIONS":
        return response(200, {"message": "OK"})

    if not kind:
        return response(404, {"message": "Not found"})

    spec = COLLECTIONS[kind]

    try:
        body = parse_json_body(event) if method in {"POST", "PUT", "PATCH"} else {}
        roles = CATALOG_WRITE_ROLES if method in {"POST", "PUT", "PATCH", "DELETE"} else READ_ROLES
        user_sub, membership = authorize(event, body, allowed_roles=roles)
        organization_id = membership["organization_id"]
        params = event.get("pathParameters") or {}
        type_id = str(params.get(spec["id_name"]) or "").strip()

        if method == "GET" and not type_id:
            return list_types(event, kind, organization_id)

        if method == "GET":
            return response(200, {"type": type_view(load_type(kind, organization_id, type_id), spec["id_name"])})

        if method == "POST":
            return create_type(kind, organization_id, user_sub, membership.get("role"), body)

        if method in {"PUT", "PATCH"}:
            return update_type(kind, organization_id, type_id, user_sub, membership.get("role"), body)

        if method == "DELETE":
            return deactivate_type(kind, organization_id, type_id, user_sub, membership.get("role"))

        return response(405, {"message": "Method not allowed"})
    except AccessError as error:
        return response(error.status_code, {"message": error.message})
    except ClientError as error:
        print("Catalog error:", error.response["Error"]["Code"])
        return response(500, {"message": "Unable to update configuration"})
    except ValueError:
        return response(400, {"message": "Invalid JSON body"})
    except Exception as error:
        print("Catalog error:", error.__class__.__name__)
        return response(500, {"message": "Unable to update configuration"})


def list_types(event, kind, organization_id):
    spec = COLLECTIONS[kind]
    query = event.get("queryStringParameters") or {}

    try:
        limit = int(query.get("limit") or 50)
    except (TypeError, ValueError):
        raise AccessError(400, "Page size is invalid")

    if limit < 1 or limit > 50:
        raise AccessError(400, "Page size is invalid")

    start = decode_token(query.get("page_token"), ["organization_id", spec["id_name"]])

    if start and start.get("organization_id") != organization_id:
        raise AccessError(400, "Invalid page token")

    from boto3.dynamodb.conditions import Key

    kwargs = {
        "KeyConditionExpression": Key("organization_id").eq(organization_id),
        "Limit": limit,
    }

    if start:
        kwargs["ExclusiveStartKey"] = start

    result = table_for(kind).query(**kwargs)
    items = [
        type_view(item, spec["id_name"])
        for item in result.get("Items", [])
        if item.get("organization_id") == organization_id
    ]
    return response(
        200,
        {
            "types": items,
            "next_token": encode_token(result.get("LastEvaluatedKey")),
        },
    )


def create_type(kind, organization_id, actor_sub, actor_role, body):
    spec = COLLECTIONS[kind]
    record = build_record(body, organization_id, actor_sub, spec)
    existing_names = {
        str(item.get("name", "")).lower()
        for item in query_by_organization(table_for(kind), organization_id, index_name=None)
        if item.get("status") == "ACTIVE"
    }

    if record["name"].lower() in existing_names:
        return response(409, {"message": f"{spec['label']} already exists"})

    table_for(kind).put_item(
        Item=record,
        ConditionExpression=f"attribute_not_exists({spec['id_name']})",
    )
    record_audit(
        audit_table(),
        build_audit_event(
            organization_id,
            actor_sub,
            actor_role,
            f"{kind[:-1]}.create",
            kind[:-1],
            record[spec["id_name"]],
        ),
    )
    return response(201, {"message": f"{spec['label']} created", "type": type_view(record, spec["id_name"])})


def update_type(kind, organization_id, type_id, actor_sub, actor_role, body):
    spec = COLLECTIONS[kind]
    current = load_type(kind, organization_id, type_id)
    status = str(body.get("status") or current.get("status") or "ACTIVE").upper()

    if status not in {"ACTIVE", "INACTIVE"}:
        raise AccessError(400, "Status is invalid")

    record = build_record(body, organization_id, actor_sub, spec, existing=current)
    record["status"] = status
    record[spec["id_name"]] = current[spec["id_name"]]
    table_for(kind).put_item(Item=record)
    record_audit(
        audit_table(),
        build_audit_event(
            organization_id,
            actor_sub,
            actor_role,
            f"{kind[:-1]}.update",
            kind[:-1],
            type_id,
            metadata={"status": status},
        ),
    )
    return response(200, {"message": f"{spec['label']} updated", "type": type_view(record, spec["id_name"])})


def deactivate_type(kind, organization_id, type_id, actor_sub, actor_role):
    spec = COLLECTIONS[kind]
    current = load_type(kind, organization_id, type_id)
    current["status"] = "INACTIVE"
    current["updated_at"] = now()
    table_for(kind).put_item(Item=current)
    record_audit(
        audit_table(),
        build_audit_event(
            organization_id,
            actor_sub,
            actor_role,
            f"{kind[:-1]}.deactivate",
            kind[:-1],
            type_id,
        ),
    )
    return response(200, {"message": f"{spec['label']} deactivated"})
