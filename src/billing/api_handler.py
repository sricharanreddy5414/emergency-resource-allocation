"""Authenticated billing routes. The webhook stays on its own function."""

import json
import os
from datetime import datetime, timezone

from access import AccessError, access_body, authorize
from billing.cancel import request_cancellation
from billing.checkout import create_checkout
from billing.errors import BillingError
from billing.events import list_events
from billing.plans import customer_plans
from billing.provider.razorpay import RazorpaySubscriptionProvider, load_test_secret
from billing.summary import read_billing
from common import api_response, parse_json_body
from observability import begin_request


VIEW_ROLES = {"OWNER", "ADMIN"}
OWNER_ROLES = {"OWNER"}
ROUTES = {
    ("GET", "/billing"): "summary",
    ("GET", "/billing/plans"): "plans",
    ("POST", "/billing/checkout"): "checkout",
    ("POST", "/billing/cancel"): "cancel",
    ("GET", "/billing/events"): "events",
}


def subscriptions_table():
    import boto3

    return boto3.resource("dynamodb").Table(
        os.environ.get("SUBSCRIPTIONS_TABLE", "OrganizationSubscriptions")
    )


def events_table():
    import boto3

    return boto3.resource("dynamodb").Table(
        os.environ.get("BILLING_EVENTS_TABLE", "BillingEvents")
    )


def build_provider():
    import boto3

    client = boto3.client("secretsmanager", region_name=os.environ.get("AWS_REGION", "eu-north-1"))
    return RazorpaySubscriptionProvider(lambda: load_test_secret(client))


def lambda_handler(event, context):
    begin_request(event)
    method = str(event.get("httpMethod") or "").upper()
    path = _path(event)

    if method == "OPTIONS":
        return api_response(200, {"message": "OK"})

    if path not in {item[1] for item in ROUTES}:
        return api_response(404, {"message": "Not found"})

    if (method, path) not in ROUTES:
        return api_response(405, {"message": "Method not allowed"})

    body = {}

    if method == "POST":
        try:
            body = parse_json_body(event)
        except (json.JSONDecodeError, ValueError, TypeError):
            return api_response(400, {"message": "Invalid JSON body"})

    roles = OWNER_ROLES if (method, path) in {("POST", "/billing/checkout"), ("POST", "/billing/cancel")} else VIEW_ROLES

    try:
        _user_sub, membership = authorize(event, body, allowed_roles=roles, access="billing")
        organization_id = membership["organization_id"]
        result = _dispatch(method, path, body, organization_id)
    except AccessError as error:
        return api_response(error.status_code, access_body(error))
    except BillingError as error:
        return api_response(error.status_code, {"message": error.message})

    return api_response(200, result)


def _dispatch(method, path, body, organization_id):
    action = ROUTES[(method, path)]
    now = datetime.now(timezone.utc)

    if action == "summary":
        return read_billing(subscriptions_table(), organization_id)

    if action == "plans":
        return {"plans": customer_plans()}

    if action == "checkout":
        return create_checkout(body, organization_id, subscriptions_table(), build_provider, now=now)

    if action == "cancel":
        return request_cancellation(body, organization_id, subscriptions_table(), build_provider, now=now)

    return list_events(events_table(), organization_id)


def _path(event):
    path = str((event or {}).get("path") or (event or {}).get("rawPath") or "")
    path = path.split("?", 1)[0]

    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]

    return path
