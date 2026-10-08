"""Publish emergency-resource-auto-release code and move alias live.

This does not create roles, rewrite IAM, or change Lambda configuration.
UpdateFunctionCode replaces only the unpublished code. PublishVersion then
snapshots that code with the configuration already on the function.
The alias update is conditional on the revision read before that update.
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


FUNCTION = "emergency-resource-auto-release"


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
            raise SystemExit("auto-release code update failed")
        time.sleep(3)
    raise SystemExit("auto-release code update did not finish")


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


def _alias(aws):
    current = aws(
        ["lambda", "get-alias", "--function-name", FUNCTION, "--name", ALIAS],
        region=REGION,
    )
    version = str(current.get("FunctionVersion") or "")
    revision = str(current.get("RevisionId") or "")
    if not version.isdigit() or version == "0":
        raise SystemExit("auto-release live alias is not a published version")
    if not revision:
        raise SystemExit("auto-release live alias has no revision")
    return current, version, revision


def _require_same_alias(aws, previous, revision):
    """Stop when the live alias moved after it was read."""
    _current, version, seen = _alias(aws)
    if version != previous or seen != revision:
        raise SystemExit("auto-release alias changed before update")


def publish_auto_release(aws, archive, commit, *, check=check_zip):
    """Publish one auto-release version and point live at it after the snapshot matches."""
    check(Path(archive), FUNCTION)
    _current, previous, revision = _alias(aws)
    before = _configuration(aws, previous)
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
        raise SystemExit("auto-release configuration drifted before publish")

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
        raise SystemExit("auto-release publish did not return a version")
    snapshot = _configuration(aws, version)
    if fingerprint(snapshot) != fingerprint(before):
        raise SystemExit("Published auto-release version does not match the current configuration")
    if snapshot.get("CodeSha256") != latest.get("CodeSha256"):
        raise SystemExit("Published auto-release version does not match the packaged code")

    _require_same_alias(aws, previous, revision)
    aws(
        [
            "lambda",
            "update-alias",
            "--function-name",
            FUNCTION,
            "--name",
            ALIAS,
            "--function-version",
            version,
            "--description",
            f"commit={commit}"[:256],
            "--revision-id",
            revision,
        ],
        region=REGION,
    )
    confirmed = aws(
        ["lambda", "get-alias", "--function-name", FUNCTION, "--name", ALIAS],
        region=REGION,
    )
    if str(confirmed.get("FunctionVersion") or "") != version:
        raise SystemExit("auto-release alias did not move")
    return {
        "function": FUNCTION,
        "alias": ALIAS,
        "commit": commit,
        "previous_version": previous,
        "published_version": version,
        "alias_version": version,
        "configuration_preserved": True,
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
    report = publish_auto_release(default_aws, archive, commit)
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
