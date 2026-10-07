"""Publish erap-exchange code and move alias live.

This does not create roles, rewrite IAM, or change Lambda configuration.
UpdateFunctionCode replaces only the unpublished code. PublishVersion then
snapshots that code with the configuration already on the function.
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


FUNCTION = "erap-exchange"


def fingerprint(config):
    """Comparable runtime settings. Code identity is checked separately."""
    variables = ((config or {}).get("Environment") or {}).get("Variables") or {}
    layers = []
    for layer in (config or {}).get("Layers") or []:
        layers.append(layer.get("Arn") if isinstance(layer, dict) else layer)
    return {
        "runtime": (config or {}).get("Runtime"),
        "handler": (config or {}).get("Handler"),
        "role": (config or {}).get("Role"),
        "memory": (config or {}).get("MemorySize"),
        "timeout": (config or {}).get("Timeout"),
        "environment": tuple(sorted(variables.items())),
        "vpc": (config or {}).get("VpcConfig") or {},
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
            raise SystemExit("Exchange code update failed")
        time.sleep(3)
    raise SystemExit("Exchange code update did not finish")


def publish_exchange(aws, archive, commit, *, check=check_zip):
    """Publish one Exchange version and point live at it after the snapshot matches."""
    check(Path(archive), FUNCTION)
    current = aws(
        ["lambda", "get-alias", "--function-name", FUNCTION, "--name", ALIAS],
        region=REGION,
    )
    previous = str(current.get("FunctionVersion") or "")
    if not previous.isdigit() or previous == "0":
        raise SystemExit("Exchange live alias is not a published version")
    before = _configuration(aws, previous)

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
        raise SystemExit("Exchange configuration drifted before publish")

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
        raise SystemExit("Exchange publish did not return a version")
    snapshot = _configuration(aws, version)
    if fingerprint(snapshot) != fingerprint(before):
        raise SystemExit("Published Exchange version does not match the current configuration")
    if snapshot.get("CodeSha256") != latest.get("CodeSha256"):
        raise SystemExit("Published Exchange version does not match the packaged code")

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
        ],
        region=REGION,
    )
    confirmed = aws(
        ["lambda", "get-alias", "--function-name", FUNCTION, "--name", ALIAS],
        region=REGION,
    )
    if str(confirmed.get("FunctionVersion") or "") != version:
        raise SystemExit("Exchange alias did not move")
    return {
        "function": FUNCTION,
        "alias": ALIAS,
        "commit": commit,
        "previous_version": previous,
        "published_version": version,
        "alias_version": version,
        "configuration_preserved": True,
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
    report = publish_exchange(default_aws, archive, commit)
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
