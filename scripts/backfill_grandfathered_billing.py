"""Create one explicit grandfathered billing row for the pilot organization.

This is not a bulk migration. It refuses every organization except
ORG-D13B30D99127. It does not call a payment provider, scan, or update an
existing subscription row.
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from botocore.exceptions import ClientError


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]

from aws_cli import aws
from billing.models import (
    SUBSCRIPTION_INDEX_ATTRIBUTES,
    grandfathered_subscription,
    item_for_storage,
)


PILOT_ORGANIZATION_ID = "ORG-D13B30D99127"
ORGANIZATIONS_TABLE = "Organizations"
SUBSCRIPTIONS_TABLE = "OrganizationSubscriptions"


class BackfillError(Exception):
    def __init__(self, message, result="REFUSED"):
        super().__init__(message)
        self.result = result


def require_pilot(organization_id):
    """Accept only the one pilot id. Wildcards and lists are refused."""
    if not isinstance(organization_id, str):
        raise BackfillError("Organization id is required")

    text = organization_id.strip()

    if not text or text != organization_id:
        raise BackfillError("Organization id is required")

    if "," in text or "*" in text or " " in text:
        raise BackfillError("This backfill accepts one organization id")

    if text != PILOT_ORGANIZATION_ID:
        raise BackfillError("Organization is not eligible for this backfill")

    return text


def stored_grandfathered_item(organization_id, now):
    """Use the domain shape and omit blank index keys."""
    item = grandfathered_subscription(organization_id, now)
    stored = item_for_storage(item, SUBSCRIPTION_INDEX_ATTRIBUTES)

    for name in SUBSCRIPTION_INDEX_ATTRIBUTES:
        if name in stored and stored[name] == "":
            raise BackfillError("Blank index keys must be omitted")

    if stored.get("lifecycle_partition") or stored.get("lifecycle_due_at"):
        raise BackfillError("A grandfathered row must stay off the lifecycle index")

    if stored.get("provider_subscription_id"):
        raise BackfillError("A grandfathered row has no provider subscription")

    return stored


def prepare(organization_id, organization, subscription, now):
    require_pilot(organization_id)

    if not organization:
        raise BackfillError("Organization was not found")

    if organization.get("status") != "ACTIVE":
        raise BackfillError("Organization is not active")

    if subscription:
        return {"result": "ALREADY_EXISTS", "item": None}

    return {
        "result": "CREATE",
        "item": stored_grandfathered_item(organization_id, now),
    }


def apply_put(put_subscription, item):
    try:
        return put_subscription(item)
    except ClientError as error:
        if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return "CONCURRENT_CREATE"
        raise


def public_result(mode, organization_id, organization_status, prepared, write_result=None):
    subscription_row = "PRESENT" if prepared["result"] == "ALREADY_EXISTS" else "ABSENT"
    body = {
        "mode": mode,
        "organization_id": organization_id,
        "organization_status": organization_status,
        "subscription_row": subscription_row,
        "result": prepared["result"],
    }

    if prepared["result"] == "CREATE" and mode == "dry-run":
        body["result"] = "would create GRANDFATHERED subscription for " + organization_id
        body["subscription_status"] = "GRANDFATHERED"
        body["plan_id"] = "GRANDFATHERED"
        body["provider_subscription_id"] = "ABSENT"
        body["lifecycle_partition"] = "ABSENT"

    if write_result:
        body["result"] = write_result
        body["subscription_status"] = "GRANDFATHERED"
        body["plan_id"] = "GRANDFATHERED"
        body["provider_subscription_id"] = "ABSENT"
        body["pending_plan_id"] = "ABSENT"
        body["lifecycle_partition"] = "ABSENT"

    return body


def _attribute(value):
    if isinstance(value, bool):
        return {"BOOL": value}

    if isinstance(value, str):
        return {"S": value}

    raise BackfillError("Subscription value cannot be stored")


def _python(value):
    if "S" in value:
        return value["S"]

    if "BOOL" in value:
        return value["BOOL"]

    raise BackfillError("Organization attribute cannot be read")


def _from_item(item):
    if not item:
        return None

    return {key: _python(value) for key, value in item.items()}


def _get(table_name, organization_id):
    payload = aws(
        [
            "dynamodb",
            "get-item",
            "--table-name",
            table_name,
            "--consistent-read",
            "--key",
            json.dumps({"organization_id": {"S": organization_id}}),
        ]
    )
    return _from_item((payload or {}).get("Item"))


def _put(item):
    encoded = {key: _attribute(value) for key, value in item.items()}

    try:
        aws(
            [
                "dynamodb",
                "put-item",
                "--table-name",
                SUBSCRIPTIONS_TABLE,
                "--condition-expression",
                "attribute_not_exists(organization_id)",
                "--item",
                json.dumps(encoded),
            ]
        )
    except SystemExit as error:
        if "ConditionalCheckFailedException" in str(error):
            return "CONCURRENT_CREATE"
        raise

    return "CREATED"


def execute(organization_id, apply, organizations_get, subscriptions_get, subscriptions_put, now):
    organization_id = require_pilot(organization_id)
    organization = organizations_get(organization_id)
    subscription = subscriptions_get(organization_id)
    prepared = prepare(organization_id, organization, subscription, now)
    mode = "apply" if apply else "dry-run"

    if not apply or prepared["result"] != "CREATE":
        return public_result(mode, organization_id, organization.get("status"), prepared)

    write_result = apply_put(subscriptions_put, prepared["item"])
    return public_result(
        mode,
        organization_id,
        organization.get("status"),
        prepared,
        write_result,
    )


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--organization-id", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)

    if args.dry_run == args.apply:
        print("Choose either --dry-run or --apply. No subscription row was written.")
        return 2

    try:
        if args.dry_run:
            result = execute(
                args.organization_id,
                False,
                lambda organization_id: _get(ORGANIZATIONS_TABLE, organization_id),
                lambda organization_id: _get(SUBSCRIPTIONS_TABLE, organization_id),
                _put,
                datetime.now(timezone.utc),
            )
        else:
            result = execute(
                args.organization_id,
                True,
                lambda organization_id: _get(ORGANIZATIONS_TABLE, organization_id),
                lambda organization_id: _get(SUBSCRIPTIONS_TABLE, organization_id),
                _put,
                datetime.now(timezone.utc),
            )
    except BackfillError as error:
        print(json.dumps({"result": error.result, "message": str(error)}))
        return 2

    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
