"""Publish organization and location Lambda code and move alias live.

This publishes only:
  erap-get-organization
  erap-create-organization
  erap-locations

It does not create roles, rewrite IAM, change Lambda configuration, or
publish billing or other PACKAGES functions. UpdateFunctionCode replaces
unpublished code. PublishVersion snapshots that code with the configuration
already on the function.
"""

import json
import os
import sys
import time
from pathlib import Path

from aws_cli import aws as default_aws
from lambda_manifest import ACCOUNT, ALIAS, REGION, ROOT, package_map
from package_lambdas import build_zip, check_zip
from release_provenance import require_commit_on_main


FUNCTIONS = (
    "erap-get-organization",
    "erap-create-organization",
    "erap-locations",
)
REQUIRED_ZIP_ENTRIES = {
    "erap-get-organization": ("common.py", "observability.py", "get_handler.py"),
    "erap-create-organization": ("common.py", "observability.py", "handler.py"),
    "erap-locations": ("common.py", "observability.py", "handler.py"),
}
FORBIDDEN_FUNCTIONS = frozenset(package_map()) - frozenset(FUNCTIONS)


def require_function(name):
    if name not in FUNCTIONS or name in FORBIDDEN_FUNCTIONS:
        raise SystemExit(f"Refusing to publish {name}")
    return name


def require_region():
    for key in ("AWS_REGION", "AWS_DEFAULT_REGION"):
        value = os.environ.get(key, "").strip()
        if value and value != REGION:
            raise SystemExit(f"Refusing organization release outside {REGION}")
    return REGION


def fingerprint(config):
    """Comparable runtime settings. Code identity is checked separately."""
    variables = ((config or {}).get("Environment") or {}).get("Variables") or {}
    layers = []
    for layer in (config or {}).get("Layers") or []:
        layers.append(layer.get("Arn") if isinstance(layer, dict) else layer)
    vpc = (config or {}).get("VpcConfig") or {}
    return {
        "runtime": (config or {}).get("Runtime"),
        "handler": (config or {}).get("Handler"),
        "role": (config or {}).get("Role"),
        "memory": (config or {}).get("MemorySize"),
        "timeout": (config or {}).get("Timeout"),
        "environment": tuple(sorted(variables.items())),
        "vpc": vpc,
        "layers": tuple(layers),
        "architectures": tuple((config or {}).get("Architectures") or []),
    }


def _configuration(aws, name, qualifier=None):
    args = ["lambda", "get-function-configuration", "--function-name", name]
    if qualifier:
        args.extend(["--qualifier", qualifier])
    return aws(args, region=REGION)


def _wait_ready(aws, name):
    for _ in range(40):
        config = _configuration(aws, name)
        status = config.get("LastUpdateStatus") or "Successful"
        if status == "Successful":
            return config
        if status == "Failed":
            raise SystemExit(f"{name} code update failed")
        time.sleep(3)
    raise SystemExit(f"{name} code update did not finish")


def _recorded(config):
    current = fingerprint(config)
    return {
        "role": current["role"],
        "runtime": current["runtime"],
        "handler": current["handler"],
        "memory": current["memory"],
        "timeout": current["timeout"],
        "environment_keys": [key for key, _value in current["environment"]],
        "layers": list(current["layers"]),
        "vpc": current["vpc"],
        "architectures": list(current["architectures"]),
        "description": str((config or {}).get("Description") or ""),
    }


def _require_account(config, name):
    role = str((config or {}).get("Role") or "")
    expected = f"arn:aws:iam::{ACCOUNT}:role/"
    if expected not in role:
        raise SystemExit(f"{name} is not in account {ACCOUNT}")
    reported = str((config or {}).get("FunctionName") or "")
    if reported and reported != name:
        raise SystemExit(f"{name} configuration does not match the requested function")


def _validate_archive(archive, name, *, check=check_zip):
    path = Path(archive)
    check(path, name)
    import zipfile

    with zipfile.ZipFile(path) as packaged:
        names = set(packaged.namelist())
    missing = [entry for entry in REQUIRED_ZIP_ENTRIES[name] if entry not in names]
    if missing:
        raise SystemExit(f"{name} zip is missing {missing}")
    mapping = package_map()[name]
    if mapping.get("common.py") != "src/organization/common.py":
        raise SystemExit(f"{name} does not package organization common.py")
    if mapping.get("observability.py") != "src/shared/observability.py":
        raise SystemExit(f"{name} does not package shared observability.py")


def current_live_versions(aws):
    """Read alias live for the three functions before any code upload."""
    require_region()
    recorded = {}
    for name in FUNCTIONS:
        require_function(name)
        current = aws(
            ["lambda", "get-alias", "--function-name", name, "--name", ALIAS],
            region=REGION,
        )
        previous = str(current.get("FunctionVersion") or "")
        if not previous.isdigit() or previous == "0":
            raise SystemExit(f"{name} live alias is not a published version")
        recorded[name] = previous
    return recorded


def publish_one(aws, archive, commit, name, *, check=check_zip):
    require_region()
    require_function(name)
    _validate_archive(archive, name, check=check)
    current = aws(
        ["lambda", "get-alias", "--function-name", name, "--name", ALIAS],
        region=REGION,
    )
    previous = str(current.get("FunctionVersion") or "")
    if not previous.isdigit() or previous == "0":
        raise SystemExit(f"{name} live alias is not a published version")
    before = _configuration(aws, name, previous)
    _require_account(before, name)
    recorded = _recorded(before)

    aws(
        [
            "lambda",
            "update-function-code",
            "--function-name",
            name,
            "--zip-file",
            "fileb://" + Path(archive).resolve().as_posix(),
        ],
        region=REGION,
    )
    latest = _wait_ready(aws, name)
    if fingerprint(latest) != fingerprint(before):
        raise SystemExit(f"{name} configuration drifted before publish")

    published = aws(
        [
            "lambda",
            "publish-version",
            "--function-name",
            name,
            "--description",
            f"commit={commit}"[:256],
        ],
        region=REGION,
    )
    version = str(published.get("Version") or "")
    if not version.isdigit() or version == "0":
        raise SystemExit(f"{name} publish did not return a version")
    snapshot = _configuration(aws, name, version)
    if fingerprint(snapshot) != fingerprint(before):
        raise SystemExit(f"Published {name} version does not match the current configuration")
    if snapshot.get("CodeSha256") != latest.get("CodeSha256"):
        raise SystemExit(f"Published {name} version does not match the packaged code")

    aws(
        [
            "lambda",
            "update-alias",
            "--function-name",
            name,
            "--name",
            ALIAS,
            "--function-version",
            version,
            "--description",
            f"commit={commit}"[:256],
        ],
        region=REGION,
    )
    confirmed = aws(
        ["lambda", "get-alias", "--function-name", name, "--name", ALIAS],
        region=REGION,
    )
    if str(confirmed.get("FunctionVersion") or "") != version:
        raise SystemExit(f"{name} alias did not move")
    return {
        "function": name,
        "alias": ALIAS,
        "commit": commit,
        "previous_version": previous,
        "published_version": version,
        "alias_version": version,
        "configuration_preserved": True,
        "recorded": recorded,
        "region": REGION,
        "account": ACCOUNT,
    }


def publish_organization_functions(aws, archives, commit, *, check=check_zip):
    if set(archives) != set(FUNCTIONS):
        raise SystemExit("Organization release must include exactly the three allowed functions")
    for name in FUNCTIONS:
        _validate_archive(archives[name], name, check=check)
    previous = current_live_versions(aws)
    reports = []
    for name in FUNCTIONS:
        reports.append(publish_one(aws, archives[name], commit, name, check=check))
    return {
        "alias": ALIAS,
        "commit": commit,
        "region": REGION,
        "account": ACCOUNT,
        "previous_versions": previous,
        "functions": reports,
    }


def main():
    if os.environ.get("ERAP_ENVIRONMENT") != "production":
        raise SystemExit("Refusing to publish alias live outside the production environment")
    require_region()
    commit = require_commit_on_main(
        os.environ.get("RELEASE_SHA"),
        ROOT,
        os.environ.get("RELEASE_MAIN_REF", "origin/main"),
    )
    folder = ROOT / "dist"
    folder.mkdir(exist_ok=True)
    archives = {}
    for name in FUNCTIONS:
        archives[name] = build_zip(name, folder / f"{name}.zip")
    report = publish_organization_functions(default_aws, archives, commit)
    summary = folder / "organization-deploy-summary.json"
    summary.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
