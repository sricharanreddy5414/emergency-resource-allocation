"""Publish emergency-resource-allocation with delivery modules and move alias live.

This does not create DynamoDB tables, API Gateway routes, rewrite IAM, or
change Lambda configuration. It publishes only:
  emergency-resource-allocation
and requires delivery_model.py, delivery_service.py, and delivery_handler.py
in the zip. It does not publish billing, organization, or other PACKAGES.
"""

import json
import os
import sys
import time
import zipfile
from pathlib import Path

from aws_cli import aws as default_aws
from lambda_manifest import ACCOUNT, ALIAS, REGION, ROOT, package_map
from package_lambdas import build_zip, check_zip
from release_provenance import require_commit_on_main


FUNCTION = "emergency-resource-allocation"
REQUIRED_ZIP_ENTRIES = (
    "delivery_model.py",
    "delivery_service.py",
    "delivery_handler.py",
    "lambda_function.py",
)


def require_region():
    for key in ("AWS_REGION", "AWS_DEFAULT_REGION"):
        value = os.environ.get(key, "").strip()
        if value and value != REGION:
            raise SystemExit(f"Refusing delivery release outside {REGION}")
    return REGION


def fingerprint(config):
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
            raise SystemExit("Delivery code update failed")
        time.sleep(3)
    raise SystemExit("Delivery code update did not finish")


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


def _require_account(config):
    role = str((config or {}).get("Role") or "")
    if f"arn:aws:iam::{ACCOUNT}:role/" not in role:
        raise SystemExit(f"{FUNCTION} is not in account {ACCOUNT}")
    reported = str((config or {}).get("FunctionName") or "")
    if reported and reported != FUNCTION:
        raise SystemExit("configuration does not match emergency-resource-allocation")


def _validate_archive(archive, *, check=check_zip):
    path = Path(archive)
    check(path, FUNCTION)
    with zipfile.ZipFile(path) as packaged:
        names = set(packaged.namelist())
        source = packaged.read("lambda_function.py").decode("utf-8")
    missing = [entry for entry in REQUIRED_ZIP_ENTRIES if entry not in names]
    if missing:
        raise SystemExit(f"{FUNCTION} zip is missing {missing}")
    mapping = package_map()[FUNCTION]
    if mapping.get("delivery_handler.py") != "src/shared/delivery_handler.py":
        raise SystemExit(f"{FUNCTION} does not package delivery_handler.py")
    if "is_delivery_path" not in source or "handle_delivery" not in source:
        raise SystemExit(f"{FUNCTION} handler does not dispatch delivery routes")


def current_live_version(aws):
    require_region()
    current = aws(
        ["lambda", "get-alias", "--function-name", FUNCTION, "--name", ALIAS],
        region=REGION,
    )
    previous = str(current.get("FunctionVersion") or "")
    if not previous.isdigit() or previous == "0":
        raise SystemExit(f"{FUNCTION} live alias is not a published version")
    return previous


def publish_delivery(aws, archive, commit, *, check=check_zip):
    require_region()
    _validate_archive(archive, check=check)
    previous = current_live_version(aws)
    before = _configuration(aws, previous)
    _require_account(before)
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
        raise SystemExit("Delivery configuration drifted before publish")

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
        raise SystemExit("Delivery publish did not return a version")
    snapshot = _configuration(aws, version)
    if fingerprint(snapshot) != fingerprint(before):
        raise SystemExit("Published delivery version does not match the current configuration")
    if snapshot.get("CodeSha256") != latest.get("CodeSha256"):
        raise SystemExit("Published delivery version does not match the packaged code")

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
        raise SystemExit("Delivery alias did not move")
    return {
        "function": FUNCTION,
        "alias": ALIAS,
        "commit": commit,
        "previous_version": previous,
        "published_version": version,
        "alias_version": version,
        "configuration_preserved": True,
        "recorded": recorded,
        "region": REGION,
        "account": ACCOUNT,
        "delivery_modules": list(REQUIRED_ZIP_ENTRIES),
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
    archive = build_zip(FUNCTION, folder / f"{FUNCTION}.zip")
    _validate_archive(archive)
    report = publish_delivery(default_aws, archive, commit)
    summary = folder / "delivery-deploy-summary.json"
    summary.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
