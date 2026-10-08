"""Point create-request alias live at an already published version.

This does not upload code, publish a version, or change configuration.
"""

import json
import os
import re
import sys

from aws_cli import aws as default_aws
from lambda_manifest import ALIAS, REGION


FUNCTION = "create-request"
VERSION_PATTERN = re.compile(r"^[1-9][0-9]{0,4}$")


def rollback_create_request(aws, version):
    chosen = str(version or "").strip()
    if not VERSION_PATTERN.fullmatch(chosen):
        raise SystemExit("A published create-request version number is required")
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
        raise SystemExit("Target version does not belong to create-request")
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
        raise SystemExit("create-request alias did not move")
    return {
        "function": FUNCTION,
        "alias": ALIAS,
        "previous_version": previous,
        "alias_version": chosen,
        "published": False,
    }


def main():
    if os.environ.get("ERAP_ENVIRONMENT") != "production":
        raise SystemExit("Refusing to publish alias live outside the production environment")
    report = rollback_create_request(default_aws, os.environ.get("CREATE_REQUEST_VERSION"))
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
