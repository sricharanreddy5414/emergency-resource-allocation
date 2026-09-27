"""Public discovery returns only fields an organization explicitly published."""

import json
import os

import boto3
from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError

from access import AccessError
from common import ALLOWED_ORIGIN
from observability import begin_request, error_body, log_result
from pages import decode_token, encode_token
from visibility import token


PUBLIC_INDEX = "PublicDiscoveryIndex"
MAX_LIMIT = 25


def response(status_code, body):
    payload = error_body(status_code, body)
    if isinstance(payload, dict):
        log_result(status_code, operation="public-discovery", error_code=payload.get("error", {}).get("code", ""))
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": ALLOWED_ORIGIN,
            "Access-Control-Allow-Headers": "Content-Type,Authorization",
            "Access-Control-Allow-Methods": "GET,OPTIONS",
        },
        "body": json.dumps(payload, default=str),
    }


def resources_table():
    return boto3.resource("dynamodb").Table(os.environ.get("RESOURCES_TABLE", "Resources"))


def public_view(item):
    if item.get("visibility_key") != "PUBLIC":
        return None

    if str(item.get("visibility") or "PUBLIC").upper() != "PUBLIC":
        return None

    view = {
        "resource_type": item.get("public_type_name") or "",
        "name": item.get("public_name") or "",
        "city": item.get("public_city") or "",
        "state": item.get("public_state") or "",
        "description": item.get("public_description") or "",
        "contact": item.get("public_contact") or "",
    }

    if item.get("show_availability") is True and item.get("public_status"):
        view["availability"] = item.get("public_status")

    return {key: value for key, value in view.items() if value}


def lambda_handler(event, context):
    begin_request(event)
    method = (event.get("httpMethod") or "GET").upper()

    if method == "OPTIONS":
        return response(200, {"message": "OK"})

    if method != "GET":
        return response(405, {"message": "Method not allowed"})

    try:
        return list_public(event)
    except AccessError as error:
        return response(error.status_code, {"message": error.message})
    except ClientError as error:
        print("Public discovery error:", error.response["Error"]["Code"])
        return response(500, {"message": "Unable to load public resources"})
    except Exception as error:
        print("Public discovery error:", error.__class__.__name__)
        return response(500, {"message": "Unable to load public resources"})


def list_public(event):
    query = event.get("queryStringParameters") or {}

    try:
        limit = int(query.get("limit") or MAX_LIMIT)
    except (TypeError, ValueError):
        raise AccessError(400, "Page size is invalid")

    if limit < 1 or limit > MAX_LIMIT:
        raise AccessError(400, "Page size is invalid")

    resource_type = str(query.get("resource_type") or "").strip()
    city = str(query.get("city") or "").strip()
    state = str(query.get("state") or "").strip()
    availability = str(query.get("availability") or "").strip().upper()

    if len(resource_type) > 60 or len(city) > 60 or len(state) > 60:
        raise AccessError(400, "Filter is invalid")

    if availability and availability not in {"AVAILABLE", "UNAVAILABLE"}:
        raise AccessError(400, "Filter is invalid")

    start = decode_token(query.get("page_token"), ["visibility_key", "discovery_key"])

    if start and start.get("visibility_key") != "PUBLIC":
        raise AccessError(400, "Invalid page token")

    key = Key("visibility_key").eq("PUBLIC")

    if resource_type:
        prefix = token(resource_type) + "#"

        if city:
            prefix += token(city) + "#"

        key = key & Key("discovery_key").begins_with(prefix)

    kwargs = {
        "IndexName": PUBLIC_INDEX,
        "KeyConditionExpression": key,
        "Limit": limit,
    }
    filters = []

    if city and not resource_type:
        filters.append(Attr("public_city").eq(city))

    if state:
        filters.append(Attr("public_state").eq(state))

    if availability:
        filters.append(Attr("public_status").eq(availability))

    if filters:
        expression = filters[0]

        for item in filters[1:]:
            expression = expression & item

        kwargs["FilterExpression"] = expression

    if start:
        kwargs["ExclusiveStartKey"] = start

    result = resources_table().query(**kwargs)
    resources = []

    for item in result.get("Items", []):
        view = public_view(item)

        if view:
            resources.append(view)

    return response(
        200,
        {
            "resources": resources,
            "next_token": encode_token(result.get("LastEvaluatedKey")),
        },
    )
