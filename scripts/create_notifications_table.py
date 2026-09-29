"""Create the locked Notifications table from infra/notifications-table.json.

Idempotent: if the table already exists, verifies schema/GSI/PITR/deletion
protection/TTL and exits without redesign.
"""

from __future__ import annotations

import json
import sys
import time

from aws_cli import aws
from lambda_manifest import REGION, ROOT

SPEC_PATH = ROOT / "infra" / "notifications-table.json"
REQUIRED_GSIS = {"UnreadByUserIndex"}


def load_spec():
    data = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    tables = data.get("tables") or []
    if len(tables) != 1 or tables[0].get("TableName") != "Notifications":
        raise SystemExit("infra/notifications-table.json must define Notifications only")
    return data, tables[0]


def describe():
    try:
        return aws(["dynamodb", "describe-table", "--table-name", "Notifications"], region=REGION)[
            "Table"
        ]
    except SystemExit as error:
        if "ResourceNotFoundException" in str(error):
            return None
        raise


def wait_active():
    for _ in range(60):
        table = describe()
        if table and table.get("TableStatus") == "ACTIVE":
            gsis = table.get("GlobalSecondaryIndexes") or []
            if gsis and all(item.get("IndexStatus") == "ACTIVE" for item in gsis):
                return table
        time.sleep(5)
    raise SystemExit("Notifications did not become ACTIVE")


def verify(table):
    key_attrs = {(item["AttributeName"], item["KeyType"]) for item in table["KeySchema"]}
    if key_attrs != {("pk", "HASH"), ("sk", "RANGE")}:
        raise SystemExit(f"unexpected key schema: {table['KeySchema']}")

    gsi_names = {item["IndexName"] for item in (table.get("GlobalSecondaryIndexes") or [])}
    if gsi_names != REQUIRED_GSIS:
        raise SystemExit(f"unexpected GSIs: {sorted(gsi_names)}")

    billing = (table.get("BillingModeSummary") or {}).get("BillingMode") or table.get("BillingMode")
    if billing != "PAY_PER_REQUEST":
        raise SystemExit(f"unexpected billing mode: {billing}")

    if not table.get("DeletionProtectionEnabled"):
        raise SystemExit("deletion protection is not enabled")

    backups = aws(
        ["dynamodb", "describe-continuous-backups", "--table-name", "Notifications"],
        region=REGION,
    )
    pitr = backups["ContinuousBackupsDescription"]["PointInTimeRecoveryDescription"][
        "PointInTimeRecoveryStatus"
    ]
    if pitr != "ENABLED":
        raise SystemExit(f"PITR not enabled: {pitr}")

    ttl = aws(
        ["dynamodb", "describe-time-to-live", "--table-name", "Notifications"],
        region=REGION,
    )
    ttl_desc = ttl.get("TimeToLiveDescription") or {}
    if ttl_desc.get("TimeToLiveStatus") not in {"ENABLED", "ENABLING"}:
        raise SystemExit(f"TTL not enabled: {ttl_desc}")
    if ttl_desc.get("AttributeName") != "expires_at":
        raise SystemExit(f"TTL attribute unexpected: {ttl_desc}")

    return {
        "table": "Notifications",
        "status": table["TableStatus"],
        "pk_sk": ["pk", "sk"],
        "gsis": sorted(gsi_names),
        "billing_mode": billing,
        "pitr": pitr,
        "deletion_protection": True,
        "ttl_attribute": "expires_at",
    }


def create(spec_table):
    payload = {
        "AttributeDefinitions": spec_table["AttributeDefinitions"],
        "KeySchema": spec_table["KeySchema"],
        "GlobalSecondaryIndexes": [
            {
                "IndexName": item["IndexName"],
                "KeySchema": item["KeySchema"],
                "Projection": item["Projection"],
            }
            for item in spec_table["GlobalSecondaryIndexes"]
        ],
        "BillingMode": "PAY_PER_REQUEST",
        "DeletionProtectionEnabled": True,
        "TableName": "Notifications",
    }
    aws(
        ["dynamodb", "create-table", "--cli-input-json", json.dumps(payload)],
        region=REGION,
    )
    wait_active()
    aws(
        [
            "dynamodb",
            "update-continuous-backups",
            "--table-name",
            "Notifications",
            "--point-in-time-recovery-specification",
            "PointInTimeRecoveryEnabled=true",
        ],
        region=REGION,
    )
    aws(
        [
            "dynamodb",
            "update-time-to-live",
            "--table-name",
            "Notifications",
            "--time-to-live-specification",
            "Enabled=true,AttributeName=expires_at",
        ],
        region=REGION,
    )


def mark_applied():
    data = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    data["applied"] = True
    data["note"] = (
        "Notifications created in eu-north-1 for Phase 8B. "
        "Do not redesign PK/SK or add GSIs. runtime_iam reflects least-privilege "
        "(DeleteItem and Scan omitted)."
    )
    SPEC_PATH.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def main():
    _, spec_table = load_spec()
    existing = describe()
    created = False
    if existing is None:
        print("creating Notifications")
        create(spec_table)
        created = True
    else:
        print("Notifications already exists; verifying")
        ttl = aws(
            ["dynamodb", "describe-time-to-live", "--table-name", "Notifications"],
            region=REGION,
        )
        status = (ttl.get("TimeToLiveDescription") or {}).get("TimeToLiveStatus")
        if status not in {"ENABLED", "ENABLING"}:
            aws(
                [
                    "dynamodb",
                    "update-time-to-live",
                    "--table-name",
                    "Notifications",
                    "--time-to-live-specification",
                    "Enabled=true,AttributeName=expires_at",
                ],
                region=REGION,
            )

    table = wait_active()
    report = verify(table)
    report["created"] = created
    if created or not json.loads(SPEC_PATH.read_text(encoding="utf-8")).get("applied"):
        mark_applied()
    folder = ROOT / "dist"
    folder.mkdir(exist_ok=True)
    (folder / "notifications-table.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
