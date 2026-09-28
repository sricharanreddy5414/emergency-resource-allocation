"""POST /billing/webhook. Razorpay's signature is the authentication."""

import os

from billing.errors import BillingError
from billing.provider.razorpay import load_webhook_secret
from billing.webhook import process_webhook
from common import api_response
from observability import begin_request


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


def webhook_secret():
    import boto3

    client = boto3.client(
        "secretsmanager",
        region_name=os.environ.get("AWS_REGION", "eu-north-1"),
    )
    return load_webhook_secret(client)


def lambda_handler(event, context):
    begin_request(event)

    if event.get("httpMethod") not in {None, "POST"}:
        return api_response(405, {"message": "Method not allowed"})

    try:
        secret = webhook_secret()
    except BillingError as error:
        return api_response(error.status_code, {"message": error.message})

    status, body = process_webhook(
        event,
        subscriptions_table(),
        events_table(),
        secret,
    )
    return api_response(status, body)
