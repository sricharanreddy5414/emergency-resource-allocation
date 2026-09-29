"""Idempotently expose everyday resource routes on the existing REST API.

Creates only these paths under /allocate/resources:

  POST/OPTIONS reserve
  POST/OPTIONS reservation-release
  POST/OPTIONS everyday
  POST/OPTIONS everyday/return
  POST/OPTIONS maintenance
  POST/OPTIONS maintenance/complete
  POST/OPTIONS damage
  POST/OPTIONS damage/recover
  POST/OPTIONS retire
  POST/OPTIONS in-use
  POST/OPTIONS in-use/return
  POST/OPTIONS assign
  POST/OPTIONS unassign

Integrations use get-resources:live and Cognito authorizer y0hzhr for POST.
OPTIONS stay Authorization NONE, matching /allocate/resources/release.
"""

from __future__ import annotations

import json
import sys

from aws_cli import aws
from lambda_manifest import ACCOUNT, ALIAS, API_ID, API_STAGE, REGION, ROOT

AUTHORIZER_ID = "y0hzhr"
PARENT_PATH = "/allocate/resources"
FUNCTION = "get-resources"
LIVE_URI = (
    f"arn:aws:apigateway:{REGION}:lambda:path/2015-03-31/functions/"
    f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:{FUNCTION}:{ALIAS}/invocations"
)

ROUTE_SEGMENTS = (
    ("reserve",),
    ("reservation-release",),
    ("everyday",),
    ("everyday", "return"),
    ("maintenance",),
    ("maintenance", "complete"),
    ("damage",),
    ("damage", "recover"),
    ("retire",),
    ("in-use",),
    ("in-use", "return"),
    ("assign",),
    ("unassign",),
)


def list_resources():
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


def by_path(resources):
    return {item.get("path"): item for item in resources if item.get("path")}


def ensure_resource(path_map, parent_id, path_part, full_path):
    existing = path_map.get(full_path)
    if existing:
        print(f"resource exists {full_path} {existing['id']}")
        return existing

    created = aws(
        [
            "apigateway",
            "create-resource",
            "--rest-api-id",
            API_ID,
            "--parent-id",
            parent_id,
            "--path-part",
            path_part,
        ],
        region=REGION,
    )
    path_map[full_path] = created
    print(f"resource created {full_path} {created['id']}")
    return created


def method_exists(resource_id, method):
    try:
        aws(
            [
                "apigateway",
                "get-method",
                "--rest-api-id",
                API_ID,
                "--resource-id",
                resource_id,
                "--http-method",
                method,
            ],
            region=REGION,
        )
        return True
    except SystemExit as error:
        if "NotFoundException" in str(error):
            return False
        raise


def ensure_proxy(resource_id, method, authorization_type, authorizer_id=None):
    if method_exists(resource_id, method):
        current = aws(
            [
                "apigateway",
                "get-method",
                "--rest-api-id",
                API_ID,
                "--resource-id",
                resource_id,
                "--http-method",
                method,
            ],
            region=REGION,
        )
        integration = current.get("methodIntegration") or {}
        auth_ok = current.get("authorizationType") == authorization_type
        authorizer_ok = authorizer_id is None or current.get("authorizerId") == authorizer_id
        uri_ok = integration.get("uri") == LIVE_URI
        type_ok = integration.get("type") == "AWS_PROXY"
        if auth_ok and authorizer_ok and uri_ok and type_ok:
            print(f"method ok {method} {resource_id}")
            return "unchanged"
        raise SystemExit(
            f"method {method} on {resource_id} exists but does not match required configuration"
        )

    put_method = [
        "apigateway",
        "put-method",
        "--rest-api-id",
        API_ID,
        "--resource-id",
        resource_id,
        "--http-method",
        method,
        "--authorization-type",
        authorization_type,
        "--no-api-key-required",
    ]
    if authorizer_id:
        put_method.extend(["--authorizer-id", authorizer_id])

    aws(put_method, region=REGION)
    aws(
        [
            "apigateway",
            "put-integration",
            "--rest-api-id",
            API_ID,
            "--resource-id",
            resource_id,
            "--http-method",
            method,
            "--type",
            "AWS_PROXY",
            "--integration-http-method",
            "POST",
            "--uri",
            LIVE_URI,
        ],
        region=REGION,
    )
    print(f"method created {method} {resource_id}")
    return "created"


def readback(path_map):
    report = []
    for segments in ROUTE_SEGMENTS:
        path = PARENT_PATH + "/" + "/".join(segments)
        item = path_map.get(path)
        if not item:
            raise SystemExit(f"missing resource after configure: {path}")
        for method in ("POST", "OPTIONS"):
            detail = aws(
                [
                    "apigateway",
                    "get-method",
                    "--rest-api-id",
                    API_ID,
                    "--resource-id",
                    item["id"],
                    "--http-method",
                    method,
                ],
                region=REGION,
            )
            integration = detail.get("methodIntegration") or {}
            report.append(
                {
                    "path": path,
                    "method": method,
                    "authorizationType": detail.get("authorizationType"),
                    "authorizerId": detail.get("authorizerId"),
                    "integrationType": integration.get("type"),
                    "uri": integration.get("uri"),
                }
            )
    return report


def verify_existing_release(path_map):
    release = path_map.get("/allocate/resources/release")
    allocate = path_map.get("/allocate")
    if not release or not allocate:
        raise SystemExit("existing /allocate or /allocate/resources/release missing")

    for path, method, expected_auth, expected_authorizer in (
        ("/allocate", "POST", "COGNITO_USER_POOLS", AUTHORIZER_ID),
        ("/allocate/resources/release", "POST", "COGNITO_USER_POOLS", AUTHORIZER_ID),
    ):
        item = path_map[path]
        detail = aws(
            [
                "apigateway",
                "get-method",
                "--rest-api-id",
                API_ID,
                "--resource-id",
                item["id"],
                "--http-method",
                method,
            ],
            region=REGION,
        )
        if detail.get("authorizationType") != expected_auth:
            raise SystemExit(f"{path} {method} authorizer type changed")
        if detail.get("authorizerId") != expected_authorizer:
            raise SystemExit(f"{path} {method} authorizer id changed")
        uri = (detail.get("methodIntegration") or {}).get("uri", "")
        if ":live/invocations" not in uri:
            raise SystemExit(f"{path} {method} is not on live alias")
    print("existing routes preserved")


def main():
    stage = aws(["apigateway", "get-stage", "--rest-api-id", API_ID, "--stage-name", API_STAGE], region=REGION)
    previous_deployment = stage.get("deploymentId")
    resources = list_resources()
    path_map = by_path(resources)
    parent = path_map.get(PARENT_PATH)
    if not parent:
        raise SystemExit(f"missing parent resource {PARENT_PATH}")

    verify_existing_release(path_map)

    paths = []
    created_or_changed = False
    for segments in ROUTE_SEGMENTS:
        path = PARENT_PATH + "/" + "/".join(segments)
        current = parent
        built = PARENT_PATH
        for part in segments:
            built = f"{built}/{part}"
            before = path_map.get(built)
            current = ensure_resource(path_map, current["id"], part, built)
            if before is None:
                created_or_changed = True
        for method, auth, authorizer in (
            ("POST", "COGNITO_USER_POOLS", AUTHORIZER_ID),
            ("OPTIONS", "NONE", None),
        ):
            result = ensure_proxy(current["id"], method, auth, authorizer)
            if result == "created":
                created_or_changed = True
        paths.append(path)

    path_map = by_path(list_resources())
    verify_existing_release(path_map)
    methods = readback(path_map)

    for item in methods:
        if item["method"] == "POST":
            if item["authorizationType"] != "COGNITO_USER_POOLS" or item["authorizerId"] != AUTHORIZER_ID:
                raise SystemExit(f"POST not Cognito-protected: {item['path']}")
            if item["uri"] != LIVE_URI:
                raise SystemExit(f"POST not wired to get-resources:live: {item['path']}")
        if item["method"] == "OPTIONS" and item["authorizationType"] != "NONE":
            raise SystemExit(f"OPTIONS authorization unexpected: {item['path']}")

    if created_or_changed:
        deployment = aws(
            [
                "apigateway",
                "create-deployment",
                "--rest-api-id",
                API_ID,
                "--stage-name",
                API_STAGE,
                "--description",
                "Resource Management 2.0 everyday operations",
            ],
            region=REGION,
        )
        deployment_id = deployment.get("id")
        print(f"deployed {deployment_id}")
    else:
        deployment_id = previous_deployment
        print(f"no route changes; stage remains {deployment_id}")

    record = {
        "api_id": API_ID,
        "stage": API_STAGE,
        "previous_deployment_id": previous_deployment,
        "deployment_id": deployment_id,
        "paths": paths,
        "methods": methods,
        "lambda": f"{FUNCTION}:{ALIAS}",
        "authorizer_id": AUTHORIZER_ID,
        "changed": created_or_changed,
    }
    folder = ROOT / "dist"
    folder.mkdir(exist_ok=True)
    (folder / "everyday-api-routes.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(json.dumps(record, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
