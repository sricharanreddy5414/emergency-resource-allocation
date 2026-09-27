"""Idempotent report of records that still have no organization scope.

This script does not modify data unless --apply is passed.
Do not pass --apply without an explicit reviewed organization and location.
"""

import argparse
import sys


TABLES = ("Resources", "EmergencyRequests", "Allocations", "ResourceStatusHistory")


def unscoped_items(table):
    response = table.scan(
        FilterExpression="attribute_not_exists(organization_id)"
    )
    items = response.get("Items", [])

    while "LastEvaluatedKey" in response:
        response = table.scan(
            FilterExpression="attribute_not_exists(organization_id)",
            ExclusiveStartKey=response["LastEvaluatedKey"],
        )
        items.extend(response.get("Items", []))

    return items


def key_names(table):
    return [key["AttributeName"] for key in table.key_schema]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--organization-id")
    parser.add_argument("--location-id")
    args = parser.parse_args()

    if args.apply:
        print("Refusing to assign existing records automatically.")
        print("Provide a reviewed migration after confirming the target organization and location.")
        return 2

    import boto3

    dynamodb = boto3.resource("dynamodb")

    for name in TABLES:
        table = dynamodb.Table(name)
        items = unscoped_items(table)
        keys = key_names(table)
        print(f"{name}: {len(items)} record(s) have no organization_id")

        for item in items:
            identity = {key: item.get(key) for key in keys}
            print(f"  unchanged {identity}")

    print("No records were modified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
