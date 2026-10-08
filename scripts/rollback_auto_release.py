"""Point emergency-resource-auto-release alias live at an already published version.

This does not upload code, publish a version, or change configuration.
The alias update is conditional on the revision read before that update.
"""

import json
import os
import re
import sys

from aws_cli import aws as default_aws
from lambda_manifest import ALIAS, REGION


FUNCTION = "emergency-resource-auto-release"
VERSION_PATTERN = re.compile(r"^[1-9][0-9]{0,4}$")


def rollback_auto_release(aws, version):
    chosen = str(version or "").strip()
    if not VERSION_PATTERN.fullmatch(chosen):
        raise SystemExit("A published auto-release version number is required")
    current = aws(
        ["lambda", "get-alias", "--function-name", FUNCTION, "--name", ALIAS],
        region=REGION,
    )
    previous = str(current.get("FunctionVersion") or "")
    revision = str(current.get("RevisionId") or "")
    if not previous.isdigit() or previous == "0":
        raise SystemExit("auto-release live alias is not a published version")
    if not revision:
        raise SystemExit("auto-release live alias has no revision")
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
        raise SystemExit("Target version does not belong to emergency-resource-auto-release")
    again = aws(
        ["lambda", "get-alias", "--function-name", FUNCTION, "--name", ALIAS],
        region=REGION,
    )
    if str(again.get("FunctionVersion") or "") != previous or str(again.get("RevisionId") or "") != revision:
        raise SystemExit("auto-release alias changed before update")
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
            "--revision-id",
            revision,
        ],
        region=REGION,
    )
    confirmed = aws(
        ["lambda", "get-alias", "--function-name", FUNCTION, "--name", ALIAS],
        region=REGION,
    )
    if str(confirmed.get("FunctionVersion") or "") != chosen:
        raise SystemExit("auto-release alias did not move")
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
    report = rollback_auto_release(default_aws, os.environ.get("AUTO_RELEASE_VERSION"))
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
