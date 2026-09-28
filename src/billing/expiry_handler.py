"""Scheduled billing lifecycle. No Cognito user and no payment provider."""

import os
from datetime import datetime, timezone

from billing.expiry import run_expiry


def subscriptions_table():
    import boto3

    return boto3.resource("dynamodb").Table(
        os.environ.get("SUBSCRIPTIONS_TABLE", "OrganizationSubscriptions")
    )


def lambda_handler(event, context):
    del event
    invocation_id = getattr(context, "aws_request_id", "") or ""
    return run_expiry(subscriptions_table(), datetime.now(timezone.utc), invocation_id)
