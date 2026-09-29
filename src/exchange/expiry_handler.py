"""Scheduled Resource Exchange expiry worker (Phase 7A).

No Cognito. No Scan. Queries ExpiryDueIndex with expiry_due_at <= now so
outages do not drop older due rows.
"""

import os
from datetime import datetime, timezone


def exchanges_table():
    import boto3

    return boto3.resource("dynamodb").Table(
        os.environ.get("EXCHANGES_TABLE", "ResourceExchanges")
    )


def lambda_handler(event, context):
    del event
    # Ensure service table helpers resolve to the scheduled worker table.
    import service
    import lifecycle

    service.exchanges_table = exchanges_table
    invocation_id = getattr(context, "aws_request_id", "") or ""
    return lifecycle.run_expiry(datetime.now(timezone.utc), invocation_id)
