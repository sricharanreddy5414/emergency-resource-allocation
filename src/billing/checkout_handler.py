"""POST /billing/checkout. Cognito authorizes the caller. Payment does not."""

import json
import os
from datetime import datetime, timezone

from access import AccessError, access_body, authorize
from billing.checkout import create_checkout
from billing.errors import BillingError
from billing.provider.razorpay import checkout_binding, open_provider
from common import api_response, parse_json_body
from observability import begin_request


def subscriptions_table():
    import boto3

    return boto3.resource("dynamodb").Table(
        os.environ.get("SUBSCRIPTIONS_TABLE", "OrganizationSubscriptions")
    )


def billing_client():
    import boto3

    return boto3.client("secretsmanager", region_name=os.environ.get("AWS_REGION", "eu-north-1"))


def build_provider():
    provider, _links = open_provider(billing_client())
    return provider


def lambda_handler(event, context):
    begin_request(event)

    if event.get("httpMethod") == "OPTIONS":
        return api_response(200, {"message": "OK"})

    if event.get("httpMethod") != "POST":
        return api_response(405, {"message": "Method not allowed"})

    try:
        body = parse_json_body(event)
    except (json.JSONDecodeError, ValueError, TypeError):
        return api_response(400, {"message": "Invalid JSON body"})

    try:
        _user_sub, membership = authorize(event, body, allowed_roles={"OWNER"}, access="billing")
    except AccessError as error:
        return api_response(error.status_code, access_body(error))

    try:
        provider_factory, links = checkout_binding(billing_client, build_provider)
        result = create_checkout(
            body,
            membership["organization_id"],
            subscriptions_table(),
            provider_factory,
            links=links,
            now=datetime.now(timezone.utc),
        )
    except BillingError as error:
        return api_response(error.status_code, {"message": error.message})

    return api_response(200, result)
