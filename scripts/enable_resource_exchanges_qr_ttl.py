"""Enable DynamoDB TTL on ResourceExchanges for QR session items only.

TTL attribute is qr_ttl_epoch. Exchange META, offers, and idempotency items
do not set that attribute, so they are not deleted.
"""

from __future__ import annotations

import json

from aws_cli import aws
from lambda_manifest import REGION

TABLE = "ResourceExchanges"
ATTRIBUTE = "qr_ttl_epoch"


def main():
    current = aws(
        ["dynamodb", "describe-time-to-live", "--table-name", TABLE],
        region=REGION,
    )
    description = current.get("TimeToLiveDescription") or {}
    status = description.get("TimeToLiveStatus") or "DISABLED"
    name = description.get("AttributeName") or ""
    if status in {"ENABLED", "ENABLING"} and name != ATTRIBUTE:
        raise SystemExit(f"ResourceExchanges TTL is already {name} ({status}); not changing it")
    if not (status in {"ENABLED", "ENABLING"} and name == ATTRIBUTE):
        aws(
            [
                "dynamodb",
                "update-time-to-live",
                "--table-name",
                TABLE,
                "--time-to-live-specification",
                f"Enabled=true,AttributeName={ATTRIBUTE}",
            ],
            region=REGION,
        )
    print(json.dumps({"table": TABLE, "ttl_attribute": ATTRIBUTE, "previous_status": status}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
