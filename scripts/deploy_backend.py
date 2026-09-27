"""Publish the existing Lambda functions from the current git commit.

Traffic stays on $LATEST. This script updates code, publishes a version,
and points the live alias at that version. It does not change API Gateway,
DynamoDB, Cognito, or Amplify.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from aws_cli import aws
from lambda_manifest import ALIAS, PACKAGES, REGION, ROOT
from package_lambdas import build_zip, check_zip


def git_commit():
    supplied = os.environ.get("GITHUB_SHA", "").strip()
    if supplied:
        return supplied
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    return result.stdout.strip()


def wait_ready(name):
    for _ in range(40):
        config = aws(["lambda", "get-function-configuration", "--function-name", name], region=REGION)
        status = config.get("LastUpdateStatus")
        if status == "Successful":
            return
        if status == "Failed":
            reason = config.get("LastUpdateStatusReason", "update failed")
            raise SystemExit(f"{name} update failed: {reason}")
        time.sleep(3)
    raise SystemExit(f"{name} update did not finish")


def previous_alias(name):
    try:
        found = aws(["lambda", "get-alias", "--function-name", name, "--name", ALIAS], region=REGION)
    except SystemExit as error:
        if "ResourceNotFoundException" in str(error):
            return ""
        raise
    return str(found.get("FunctionVersion") or "")


def point_alias(name, version, commit):
    description = f"commit={commit}"[:256]
    try:
        aws(["lambda", "get-alias", "--function-name", name, "--name", ALIAS], region=REGION)
    except SystemExit as error:
        if "ResourceNotFoundException" not in str(error):
            raise
        aws(
            [
                "lambda",
                "create-alias",
                "--function-name",
                name,
                "--name",
                ALIAS,
                "--function-version",
                version,
                "--description",
                description,
            ],
            region=REGION,
        )
        return
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
            description,
        ],
        region=REGION,
    )


def deploy_one(folder, name, commit):
    archive = build_zip(name, folder / f"{name}.zip")
    check_zip(archive, name)
    previous = previous_alias(name)
    aws(
        [
            "lambda",
            "update-function-code",
            "--function-name",
            name,
            "--zip-file",
            "fileb://" + archive.resolve().as_posix(),
        ],
        region=REGION,
    )
    wait_ready(name)
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
    version = str(published["Version"])
    wait_ready(name)
    point_alias(name, version, commit)
    return {"name": name, "version": version, "previous_version": previous, "alias": ALIAS}


def main():
    commit = git_commit()
    environment = os.environ.get("ERAP_ENVIRONMENT", "development")
    folder = ROOT / "dist"
    folder.mkdir(exist_ok=True)
    results = []
    for name in PACKAGES:
        print(f"deploying {name}")
        results.append(deploy_one(folder, name, commit))
        print(f"published {name} version {results[-1]['version']}")
    summary = {
        "environment": environment,
        "commit": commit,
        "alias": ALIAS,
        "functions": results,
        "api_changed": False,
    }
    (folder / "deploy-summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({"commit": commit, "functions": len(results)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
