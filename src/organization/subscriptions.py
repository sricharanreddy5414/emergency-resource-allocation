"""Trial row for a newly created organization.

The write is conditional and idempotent. This module does not scan
existing organizations and does not call a payment provider.
"""

import os

from botocore.exceptions import ClientError

from billing.errors import BillingError
from billing.models import SUBSCRIPTION_INDEX_ATTRIBUTES, item_for_storage, new_trial_subscription, parse_utc


def subscriptions_table():
    import boto3

    return boto3.resource("dynamodb").Table(
        os.environ.get("SUBSCRIPTIONS_TABLE", "OrganizationSubscriptions")
    )


def ensure_trial_subscription(table, organization_id, created_at):
    """Insert the 15-day trial once. An existing row is left unchanged.

    Returns (item, inserted). item is None when the conditional write lost
    the race and the row cannot be read back.
    """
    started = parse_utc(created_at, "created_at")
    item = new_trial_subscription(organization_id, started)

    try:
        table.put_item(
            Item=item_for_storage(item, SUBSCRIPTION_INDEX_ATTRIBUTES),
            ConditionExpression="attribute_not_exists(organization_id)",
        )
    except ClientError as error:
        if error.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise

        existing = table.get_item(
            Key={"organization_id": organization_id}
        ).get("Item")

        if not existing:
            return None, False

        return existing, False
    except BillingError:
        raise

    return item, True
