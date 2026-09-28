"""POST /billing/checkout. Cognito authorizes the caller. Payment does not."""

import json
import os
from datetime import datetime, timezone

from access import AccessError, authorize
from billing.checkout import create_checkout
from billing.errors import BillingError
from billing.provider.razorpay import RazorpaySubscriptionProvider, load_test_secret
from common import api_response, parse_json_body
from observability import begin_request


def subscriptions_table():
    import boto3

    return boto3.resource("dynamodb").Table(
        os.environ.get("SUBSCRIPTIONS_TABLE", "OrganizationSubscriptions")
    )


def build_provider():
    import boto3

    client = boto3.client("secretsmanager", region_name=os.environ.get("AWS_REGION", "eu-north-1"))
    return RazorpaySubscriptionProvider(lambda: load_test_secret(client))


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
        _user_sub, membership = authorize(event, body, allowed_roles={"OWNER"})
    except AccessError as error:
        return api_response(error.status_code, {"message": error.message})

    try:
        result = create_checkout(
            body,
            membership["organization_id"],
            subscriptions_table(),
            build_provider,
            now=datetime.now(timezone.utc),
        )
    except BillingError as error:
        return api_response(error.status_code, {"message": error.message})

    return api_response(200, result)
