"""Point organization/location aliases live at already published versions.

This does not upload code, publish a version, or change configuration.
It moves only:
  erap-get-organization
  erap-create-organization
  erap-locations
"""

import json
import os
import re
import sys

from aws_cli import aws as default_aws
from lambda_manifest import ACCOUNT, ALIAS, REGION
from release_organization import FUNCTIONS, require_function, require_region


VERSION_PATTERN = re.compile(r"^[1-9][0-9]{0,4}$")
ENV_KEYS = {
    "erap-get-organization": "ERAP_GET_ORGANIZATION_VERSION",
    "erap-create-organization": "ERAP_CREATE_ORGANIZATION_VERSION",
    "erap-locations": "ERAP_LOCATIONS_VERSION",
}


def versions_from_environment(environ=None):
    source = environ if environ is not None else os.environ
    raw = str(source.get("ORGANIZATION_ROLLBACK_VERSIONS") or "").strip()
    if raw:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as error:
            raise SystemExit("Organization rollback versions must be JSON") from error
        if not isinstance(parsed, dict):
            raise SystemExit("Organization rollback versions must be a JSON object")
        if set(parsed) != set(FUNCTIONS):
            raise SystemExit("Organization rollback must name exactly the three allowed functions")
        return {name: str(parsed[name]).strip() for name in FUNCTIONS}
    versions = {}
    for name in FUNCTIONS:
        versions[name] = str(source.get(ENV_KEYS[name]) or "").strip()
    if not all(versions.values()):
        raise SystemExit("A published version is required for each organization function")
    return versions


def rollback_one(aws, name, version):
    require_region()
    require_function(name)
    chosen = str(version or "").strip()
    if not VERSION_PATTERN.fullmatch(chosen):
        raise SystemExit(f"A published {name} version number is required")
    current = aws(
        ["lambda", "get-alias", "--function-name", name, "--name", ALIAS],
        region=REGION,
    )
    previous = str(current.get("FunctionVersion") or "")
    snapshot = aws(
        [
            "lambda",
            "get-function-configuration",
            "--function-name",
            name,
            "--qualifier",
            chosen,
        ],
        region=REGION,
    )
    if snapshot.get("FunctionName") != name:
        raise SystemExit(f"Target version does not belong to {name}")
    role = str(snapshot.get("Role") or "")
    if f"arn:aws:iam::{ACCOUNT}:role/" not in role:
        raise SystemExit(f"{name} is not in account {ACCOUNT}")
    description = str(snapshot.get("Description") or f"version={chosen}")[:256]
    aws(
        [
            "lambda",
            "update-alias",
            "--function-name",
            name,
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
        ["lambda", "get-alias", "--function-name", name, "--name", ALIAS],
        region=REGION,
    )
    if str(confirmed.get("FunctionVersion") or "") != chosen:
        raise SystemExit(f"{name} alias did not move")
    return {
        "function": name,
        "alias": ALIAS,
        "previous_version": previous,
        "alias_version": chosen,
        "published": False,
        "region": REGION,
        "account": ACCOUNT,
    }


def rollback_organization_functions(aws, versions):
    if set(versions) != set(FUNCTIONS):
        raise SystemExit("Organization rollback must name exactly the three allowed functions")
    reports = []
    for name in FUNCTIONS:
        reports.append(rollback_one(aws, name, versions[name]))
    return {"alias": ALIAS, "region": REGION, "account": ACCOUNT, "functions": reports}


def main():
    if os.environ.get("ERAP_ENVIRONMENT") != "production":
        raise SystemExit("Refusing to publish alias live outside the production environment")
    require_region()
    report = rollback_organization_functions(default_aws, versions_from_environment())
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
