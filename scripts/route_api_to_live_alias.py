"""Point API Gateway and auto-release at the live alias.

Permissions are added before any integration change. A failed smoke test
can restore the previous API deployment id recorded by this script.
"""

import json
import sys

from aws_cli import aws
from alias_uri import live_invocation_uri
from lambda_manifest import ACCOUNT, ALIAS, API_ID, API_STAGE, PACKAGES, REGION, ROOT


RULE = "EmergencyResourceAutoReleaseRule"
SOURCE_API = f"arn:aws:execute-api:{REGION}:{ACCOUNT}:{API_ID}/*/*"
SOURCE_RULE = f"arn:aws:events:{REGION}:{ACCOUNT}:rule/{RULE}"


def allow(function_name, statement_id, principal, source_arn):
    try:
        aws(
            [
                "lambda",
                "add-permission",
                "--function-name",
                function_name,
                "--qualifier",
                ALIAS,
                "--statement-id",
                statement_id,
                "--action",
                "lambda:InvokeFunction",
                "--principal",
                principal,
                "--source-arn",
                source_arn,
            ],
            region=REGION,
        )
        print(f"permission {function_name} {statement_id}")
    except SystemExit as error:
        if "ResourceConflictException" not in str(error):
            raise
        print(f"permission exists {function_name} {statement_id}")


def resources():
    found = []
    position = None
    while True:
        args = ["apigateway", "get-resources", "--rest-api-id", API_ID, "--limit", "500"]
        if position:
            args.extend(["--position", position])
        page = aws(args, region=REGION)
        found.extend(page.get("items", []))
        position = page.get("position")
        if not position:
            return found


def retarget_integrations():
    changed = 0
    for resource in resources():
        for method in resource.get("resourceMethods") or {}:
            integration = aws(
                [
                    "apigateway",
                    "get-integration",
                    "--rest-api-id",
                    API_ID,
                    "--resource-id",
                    resource["id"],
                    "--http-method",
                    method,
                ],
                region=REGION,
            )
            updated = live_invocation_uri(integration.get("uri", ""), PACKAGES)
            if not updated:
                continue
            aws(
                [
                    "apigateway",
                    "update-integration",
                    "--rest-api-id",
                    API_ID,
                    "--resource-id",
                    resource["id"],
                    "--http-method",
                    method,
                    "--patch-operations",
                    json.dumps([{"op": "replace", "path": "/uri", "value": updated}]),
                ],
                region=REGION,
            )
            changed += 1
            print(f"integration {method} {resource.get('path')}")
    return changed


def retarget_rule():
    listed = aws(["events", "list-targets-by-rule", "--rule", RULE], region=REGION)
    targets = []
    for target in listed.get("Targets", []):
        arn = target.get("Arn", "")
        if arn.endswith(f"function:emergency-resource-auto-release") and not arn.endswith(":live"):
            target["Arn"] = arn + ":live"
            print("event target alias")
        targets.append(target)
    if targets:
        aws(
            ["events", "put-targets", "--rule", RULE, "--targets", json.dumps(targets)],
            region=REGION,
        )


def main():
    stage = aws(["apigateway", "get-stage", "--rest-api-id", API_ID, "--stage-name", API_STAGE], region=REGION)
    previous = stage.get("deploymentId")
    for name in PACKAGES:
        allow(name, "apigateway-invoke-live", "apigateway.amazonaws.com", SOURCE_API)
    allow("emergency-resource-auto-release", "events-auto-release-live", "events.amazonaws.com", SOURCE_RULE)
    changed = retarget_integrations()
    retarget_rule()
    deployment = aws(
        [
            "apigateway",
            "create-deployment",
            "--rest-api-id",
            API_ID,
            "--stage-name",
            API_STAGE,
            "--description",
            "Route API traffic through Lambda alias live",
        ],
        region=REGION,
    )
    record = {"previous_deployment_id": previous, "deployment_id": deployment.get("id"), "integrations": changed}
    (ROOT / "dist").mkdir(exist_ok=True)
    (ROOT / "dist" / "alias-cutover.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(json.dumps(record))
    return 0


if __name__ == "__main__":
    sys.exit(main())
