"""Scheduled individual-reservation expiry. No Cognito and no table scan."""

from datetime import datetime, timezone

from reservation_expiry import run_reservation_expiry


def lambda_handler(event, context):
    del event, context
    return run_reservation_expiry(datetime.now(timezone.utc))
