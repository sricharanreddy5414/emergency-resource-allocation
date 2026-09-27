"""Move alias live to an existing published version. This does not upload code."""

import argparse
import json
import sys

from aws_cli import aws
from lambda_manifest import ALIAS, PACKAGES, REGION, ROOT


def description_for(name, version):
    config = aws(
        ["lambda", "get-function-configuration", "--function-name", name, "--qualifier", version],
        region=REGION,
    )
    return str(config.get("Description") or f"version={version}")[:256]


def point(name, version):
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
            description_for(name, version),
        ],
        region=REGION,
    )
    print(f"{name} live {version}")


def from_summary():
    path = ROOT / "dist" / "deploy-summary.json"
    if not path.is_file():
        print("no deploy summary to restore")
        return 0
    summary = json.loads(path.read_text(encoding="utf-8"))
    restored = 0
    for item in summary.get("functions") or []:
        previous = str(item.get("previous_version") or "")
        if not previous:
            continue
        point(item["name"], previous)
        restored += 1
    print(f"restored {restored}")
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", default="")
    parser.add_argument("--from-summary", action="store_true")
    args = parser.parse_args()
    if args.from_summary:
        return from_summary()
    if not args.version.isdigit() or args.version == "0":
        raise SystemExit("A published version number is required")
    for name in PACKAGES:
        point(name, args.version)
    return 0


if __name__ == "__main__":
    sys.exit(main())
