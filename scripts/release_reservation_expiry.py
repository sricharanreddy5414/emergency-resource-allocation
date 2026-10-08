"""Publish erap-reservation-expiry code and point alias live at that version.

The first release creates alias live. A later release moves the existing
alias. This does not create the due-time index, rewrite IAM, change the
schedule, or change Lambda configuration. UpdateFunctionCode replaces only
the unpublished code. PublishVersion then snapshots that code with the
configuration already on the function.
"""

import json
import os
import sys
import time
from pathlib import Path

from aws_cli import aws as default_aws
from lambda_manifest import ALIAS, REGION, ROOT
from package_lambdas import build_zip, check_zip
from release_provenance import require_commit_on_main


FUNCTION = "erap-reservation-expiry"
APPROVED_ROLE = "arn:aws:iam::481838970142:role/ERAP-Reservation-Expiry-Lambda-Role"
APPROVED_RUNTIME = "python3.14"
APPROVED_HANDLER = "handler.lambda_handler"
APPROVED_MEMORY = 256
APPROVED_TIMEOUT = 60
APPROVED_ARCHITECTURES = ("x86_64",)


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


def _missing_live_alias(error):
    """True only for the expected missing-alias response from GetAlias."""
    text = str(error)
    return "ResourceNotFoundException" in text and "GetAlias" in text


def _network_attached(config):
    vpc = (config or {}).get("VpcConfig") or {}
    return bool(vpc.get("SubnetIds") or vpc.get("SecurityGroupIds"))


def _matches_approved_baseline(config):
    """True when $LATEST still matches the repository infrastructure definition."""
    current = fingerprint(config)
    return (
        (config or {}).get("FunctionName") == FUNCTION
        and current["runtime"] == APPROVED_RUNTIME
        and current["handler"] == APPROVED_HANDLER
        and current["role"] == APPROVED_ROLE
        and current["memory"] == APPROVED_MEMORY
        and current["timeout"] == APPROVED_TIMEOUT
        and current["environment"] == ()
        and current["layers"] == ()
        and current["architectures"] == APPROVED_ARCHITECTURES
        and not _network_attached(config)
    )


def _live_alias(aws):
    """Return the published live version, or None when that alias does not exist."""
    try:
        current = aws(
            ["lambda", "get-alias", "--function-name", FUNCTION, "--name", ALIAS],
            region=REGION,
        )
    except SystemExit as error:
        if _missing_live_alias(error):
            return None
        raise
    if not isinstance(current, dict):
        raise SystemExit("Reservation expiry live alias response was empty")
    version = str(current.get("FunctionVersion") or "")
    if not version.isdigit() or version == "0":
        raise SystemExit("Reservation expiry live alias is not a published version")
    return version


def _configuration(aws, qualifier=None):
    args = ["lambda", "get-function-configuration", "--function-name", FUNCTION]
    if qualifier:
        args.extend(["--qualifier", qualifier])
    return aws(args, region=REGION)


def _wait_ready(aws):
    for _ in range(40):
        config = _configuration(aws)
        status = config.get("LastUpdateStatus") or "Successful"
        if status == "Successful":
            return config
        if status == "Failed":
            raise SystemExit("Reservation expiry code update failed")
        time.sleep(3)
    raise SystemExit("Reservation expiry code update did not finish")


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


def _promote(aws, previous, version, commit):
    description = f"commit={commit}"[:256]
    action = "create-alias" if previous is None else "update-alias"
    aws(
        [
            "lambda",
            action,
            "--function-name",
            FUNCTION,
            "--name",
            ALIAS,
            "--function-version",
            version,
            "--description",
            description,
        ],
        region=REGION,
    )


def publish_reservation_expiry(aws, archive, commit, *, check=check_zip):
    """Publish one erap-reservation-expiry version and point live at it."""
    check(Path(archive), FUNCTION)
    previous = _live_alias(aws)
    before = _configuration(aws) if previous is None else _configuration(aws, previous)
    if previous is None and not _matches_approved_baseline(before):
        raise SystemExit("Reservation expiry configuration drifted before publish")
    recorded = _recorded(before)

    aws(
        [
            "lambda",
            "update-function-code",
            "--function-name",
            FUNCTION,
            "--zip-file",
            "fileb://" + Path(archive).resolve().as_posix(),
        ],
        region=REGION,
    )
    latest = _wait_ready(aws)
    if fingerprint(latest) != fingerprint(before):
        raise SystemExit("Reservation expiry configuration drifted before publish")
    if previous is None and not _matches_approved_baseline(latest):
        raise SystemExit("Reservation expiry configuration drifted before publish")

    published = aws(
        [
            "lambda",
            "publish-version",
            "--function-name",
            FUNCTION,
            "--description",
            f"commit={commit}"[:256],
        ],
        region=REGION,
    )
    version = str(published.get("Version") or "")
    if not version.isdigit() or version == "0":
        raise SystemExit("Reservation expiry publish did not return a version")
    snapshot = _configuration(aws, version)
    if fingerprint(snapshot) != fingerprint(before):
        raise SystemExit("Published reservation expiry version does not match the current configuration")
    if snapshot.get("CodeSha256") != latest.get("CodeSha256"):
        raise SystemExit("Published reservation expiry version does not match the packaged code")
    if previous is None and not _matches_approved_baseline(snapshot):
        raise SystemExit("Published reservation expiry version does not match the current configuration")

    _promote(aws, previous, version, commit)
    confirmed = _live_alias(aws)
    if confirmed != version:
        raise SystemExit("Reservation expiry alias did not move")
    return {
        "function": FUNCTION,
        "alias": ALIAS,
        "commit": commit,
        "previous_version": previous,
        "published_version": version,
        "alias_version": version,
        "configuration_preserved": True,
        "first_release": previous is None,
        "recorded": recorded,
    }


def main():
    if os.environ.get("ERAP_ENVIRONMENT") != "production":
        raise SystemExit("Refusing to publish alias live outside the production environment")
    commit = require_commit_on_main(
        os.environ.get("RELEASE_SHA"),
        ROOT,
        os.environ.get("RELEASE_MAIN_REF", "origin/main"),
    )
    folder = ROOT / "dist"
    folder.mkdir(exist_ok=True)
    archive = build_zip(FUNCTION, folder / f"{FUNCTION}.zip")
    report = publish_reservation_expiry(default_aws, archive, commit)
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
