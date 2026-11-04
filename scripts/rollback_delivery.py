"""Point emergency-resource-allocation alias live at an already published version.

This does not upload code, publish a version, or change configuration.
It is the rollback for a delivery-module publish of emergency-resource-allocation.
"""

import json
import os
import re
import sys

from aws_cli import aws as default_aws
from lambda_manifest import ACCOUNT, ALIAS, REGION
from release_delivery import FUNCTION, require_region


VERSION_PATTERN = re.compile(r"^[1-9][0-9]{0,4}$")


def rollback_delivery(aws, version):
    require_region()
    chosen = str(version or "").strip()
    if not VERSION_PATTERN.fullmatch(chosen):
        raise SystemExit("A published emergency-resource-allocation version number is required")
    current = aws(
        ["lambda", "get-alias", "--function-name", FUNCTION, "--name", ALIAS],
        region=REGION,
    )
    previous = str(current.get("FunctionVersion") or "")
    snapshot = aws(
        [
            "lambda",
            "get-function-configuration",
            "--function-name",
            FUNCTION,
            "--qualifier",
            chosen,
        ],
        region=REGION,
    )
    if snapshot.get("FunctionName") != FUNCTION:
        raise SystemExit("Target version does not belong to emergency-resource-allocation")
    role = str(snapshot.get("Role") or "")
    if f"arn:aws:iam::{ACCOUNT}:role/" not in role:
        raise SystemExit(f"{FUNCTION} is not in account {ACCOUNT}")
    description = str(snapshot.get("Description") or f"version={chosen}")[:256]
    aws(
        [
            "lambda",
            "update-alias",
            "--function-name",
            FUNCTION,
            "--name",
            ALIAS,
            "--function-version",
            chosen,
            "--description",
            description,
        ],
        region=REGION,
    )
    confirmed = aws(
        ["lambda", "get-alias", "--function-name", FUNCTION, "--name", ALIAS],
        region=REGION,
    )
    if str(confirmed.get("FunctionVersion") or "") != chosen:
        raise SystemExit("Delivery alias did not move")
    return {
        "function": FUNCTION,
        "alias": ALIAS,
        "previous_version": previous,
        "alias_version": chosen,
        "published": False,
        "region": REGION,
        "account": ACCOUNT,
    }


def main():
    if os.environ.get("ERAP_ENVIRONMENT") != "production":
        raise SystemExit("Refusing to publish alias live outside the production environment")
    require_region()
    report = rollback_delivery(default_aws, os.environ.get("DELIVERY_VERSION"))
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
