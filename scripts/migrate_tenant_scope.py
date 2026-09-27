"""Controlled tenant migration.

Modes:
  report    List records whose ownership is missing or inconsistent. No writes.
  validate  Check an explicit mapping file. No writes.
  apply     Write only after validation succeeds.

The mapping file may name resources and requests. Allocations and history
follow those records only when every organization relationship agrees.
The script never invents an organization or location.
"""

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src" / "shared")]

from migration import MigrationError, apply_writes, discover, validate_plan


TABLES = {
    "resource": "Resources",
    "request": "EmergencyRequests",
    "allocation": "Allocations",
    "history": "ResourceStatusHistory",
}
KEYS = {
    "resource": "resource_id",
    "request": "request_id",
    "allocation": "allocation_id",
    "history": "history_id",
}


def scan_all(table):
    response = table.scan()
    items = response.get("Items", [])

    while "LastEvaluatedKey" in response:
        response = table.scan(ExclusiveStartKey=response["LastEvaluatedKey"])
        items.extend(response.get("Items", []))

    return items


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("report", "validate", "apply"), default="report")
    parser.add_argument("--mapping")
    args = parser.parse_args()

    if args.mode in {"validate", "apply"} and not args.mapping:
        print("A mapping file is required. No records were modified.")
        return 2

    import boto3

    dynamodb = boto3.resource("dynamodb")
    loaded = {kind: scan_all(dynamodb.Table(name)) for kind, name in TABLES.items()}

    if args.mode == "report":
        findings = discover(
            loaded["resource"],
            loaded["request"],
            loaded["allocation"],
            loaded["history"],
        )
        print(json.dumps({"mode": "report", "findings": findings}, indent=2))
        print("No records were modified.")
        return 0

    plan = json.loads(Path(args.mapping).read_text(encoding="utf-8"))
    organizations = scan_all(dynamodb.Table("Organizations"))
    locations = scan_all(dynamodb.Table("Locations"))

    try:
        writes = validate_plan(
            plan,
            loaded["resource"],
            loaded["request"],
            loaded["allocation"],
            loaded["history"],
            organizations,
            locations,
        )
    except MigrationError as error:
        print(error.message)
        print("No records were modified.")
        return 2

    print(json.dumps({"mode": args.mode, "writes": writes}, default=str))

    if args.mode == "validate":
        print("No records were modified.")
        return 0

    records = {
        kind: {item[KEYS[kind]]: item for item in items}
        for kind, items in loaded.items()
    }
    apply_writes(records, writes)

    for kind, entity_id, organization_id, location_id in writes:
        dynamodb.Table(TABLES[kind]).update_item(
            Key={KEYS[kind]: entity_id},
            UpdateExpression="SET organization_id = :organization_id, location_id = :location_id",
            ConditionExpression="attribute_not_exists(organization_id) OR organization_id = :organization_id",
            ExpressionAttributeValues={
                ":organization_id": organization_id,
                ":location_id": location_id,
            },
        )

    print("Migration applied.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
