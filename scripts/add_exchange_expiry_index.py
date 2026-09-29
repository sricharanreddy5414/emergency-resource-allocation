"""Add sparse ExpiryDueIndex to ResourceExchanges (Phase 7A).

Dry-run by default. Apply with --apply after local gates pass.
Does not Scan. Does not change PK/SK or existing GSIs.
"""

from __future__ import annotations

import json
import sys
import time

from aws_cli import aws
from lambda_manifest import REGION

TABLE = "ResourceExchanges"
INDEX = "ExpiryDueIndex"


def describe():
    return aws(["dynamodb", "describe-table", "--table-name", TABLE], region=REGION)["Table"]


def has_index(table):
    return any(g.get("IndexName") == INDEX for g in (table.get("GlobalSecondaryIndexes") or []))


def main():
    apply = "--apply" in sys.argv
    table = describe()
    if has_index(table):
        print(f"index exists {INDEX}")
        return 0

    payload = {
        "AttributeDefinitions": [
            {"AttributeName": "expiry_due_key", "AttributeType": "S"},
            {"AttributeName": "expiry_due_at", "AttributeType": "S"},
        ],
        "GlobalSecondaryIndexUpdates": [
            {
                "Create": {
                    "IndexName": INDEX,
                    "KeySchema": [
                        {"AttributeName": "expiry_due_key", "KeyType": "HASH"},
                        {"AttributeName": "expiry_due_at", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
            }
        ],
    }
    print(json.dumps(payload, indent=2))
    if not apply:
        print("dry-run only; pass --apply to mutate")
        return 0

    aws(
        [
            "dynamodb",
            "update-table",
            "--table-name",
            TABLE,
            "--attribute-definitions",
            json.dumps(payload["AttributeDefinitions"]),
            "--global-secondary-index-updates",
            json.dumps(payload["GlobalSecondaryIndexUpdates"]),
        ],
        region=REGION,
    )
    print(f"index create started {INDEX}")
    for _ in range(60):
        status = describe()
        gsi = next(g for g in status["GlobalSecondaryIndexes"] if g["IndexName"] == INDEX)
        print("index status", gsi.get("IndexStatus"))
        if gsi.get("IndexStatus") == "ACTIVE":
            return 0
        time.sleep(10)
    raise SystemExit("index did not become ACTIVE in time")


if __name__ == "__main__":
    raise SystemExit(main())
