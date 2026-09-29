"""Idempotent API Gateway exposure for Resource Exchange routes (Phase 5G).

Routes (Cognito authorizer y0hzhr; OPTIONS Authorization NONE):

  POST/GET/OPTIONS  /exchange/requests
  GET/OPTIONS       /exchange/requests/{exchange_request_id}
  POST/GET/OPTIONS  /exchange/requests/{exchange_request_id}/offers
  GET/OPTIONS       /exchange/requests/{exchange_request_id}/offers/{offer_id}
  POST/OPTIONS      /exchange/requests/{exchange_request_id}/offers/{offer_id}/accept
  POST/OPTIONS      /exchange/requests/{exchange_request_id}/transfer/start
  POST/OPTIONS      /exchange/requests/{exchange_request_id}/handover/confirm
  GET/OPTIONS       /exchange/offers

Integration target: erap-exchange:live

Dry-run (default): prints planned paths without mutation.
Apply: python scripts/expose_exchange_routes.py --apply
"""

from __future__ import annotations

import json
import sys

from aws_cli import aws
from lambda_manifest import ACCOUNT, ALIAS, API_ID, API_STAGE, REGION, ROOT

AUTHORIZER_ID = "y0hzhr"
FUNCTION = "erap-exchange"
LIVE_URI = (
    f"arn:aws:apigateway:{REGION}:lambda:path/2015-03-31/functions/"
    f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:{FUNCTION}:{ALIAS}/invocations"
)

# (path segments under /exchange, methods that need Cognito)
ROUTES = (
    (("requests",), ("POST", "GET")),
    (("requests", "{exchange_request_id}"), ("GET",)),
    (("requests", "{exchange_request_id}", "offers"), ("POST", "GET")),
    (("requests", "{exchange_request_id}", "offers", "{offer_id}"), ("GET",)),
    (
        ("requests", "{exchange_request_id}", "offers", "{offer_id}", "accept"),
        ("POST",),
    ),
    (("requests", "{exchange_request_id}", "transfer", "start"), ("POST",)),
    (("requests", "{exchange_request_id}", "handover", "confirm"), ("POST",)),
    (("offers",), ("GET",)),
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
    for segments, methods in ROUTES:
        path = "/exchange/" + "/".join(segments)
        item = path_map.get(path)
        if not item:
            raise SystemExit(f"missing resource after configure: {path}")
        for method in (*methods, "OPTIONS"):
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


def verify_unrelated(path_map):
    for path, method, expected_auth, expected_authorizer in (
        ("/allocate", "POST", "COGNITO_USER_POOLS", AUTHORIZER_ID),
        ("/public/resources", "GET", "NONE", None),
    ):
        item = path_map.get(path)
        if not item:
            raise SystemExit(f"existing route missing: {path}")
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
        if expected_authorizer and detail.get("authorizerId") != expected_authorizer:
            raise SystemExit(f"{path} {method} authorizer id changed")
    print("existing unrelated routes preserved")


def plan_only():
    print(
        json.dumps(
            {
                "status": "plan",
                "api_id": API_ID,
                "stage": API_STAGE,
                "function": FUNCTION,
                "authorizer_id": AUTHORIZER_ID,
                "uri": LIVE_URI,
                "paths": ["/exchange/" + "/".join(segments) for segments, _ in ROUTES],
                "note": "Pass --apply after ResourceExchanges exists and erap-exchange:live is deployed.",
            },
            indent=2,
        )
    )
    return 0


def apply():
    # Confirm Lambda live alias exists before mutating API.
    aws(
        ["lambda", "get-alias", "--function-name", FUNCTION, "--name", ALIAS],
        region=REGION,
    )

    stage = aws(
        ["apigateway", "get-stage", "--rest-api-id", API_ID, "--stage-name", API_STAGE],
        region=REGION,
    )
    previous_deployment = stage.get("deploymentId")
    resources = list_resources()
    path_map = by_path(resources)
    root = path_map.get("/")
    if not root:
        raise SystemExit("API root resource missing")

    verify_unrelated(path_map)

    initial_paths = set(path_map)
    exchange = ensure_resource(path_map, root["id"], "exchange", "/exchange")

    paths = []
    changed = "/exchange" not in initial_paths

    for segments, methods in ROUTES:
        current = exchange
        built = "/exchange"
        for part in segments:
            built = f"{built}/{part}"
            before = path_map.get(built)
            current = ensure_resource(path_map, current["id"], part, built)
            if before is None:
                changed = True
        for method in methods:
            result = ensure_proxy(current["id"], method, "COGNITO_USER_POOLS", AUTHORIZER_ID)
            if result == "created":
                changed = True
        result = ensure_proxy(current["id"], "OPTIONS", "NONE", None)
        if result == "created":
            changed = True
        paths.append("/exchange/" + "/".join(segments))

    path_map = by_path(list_resources())
    verify_unrelated(path_map)
    methods = readback(path_map)

    for item in methods:
        if item["method"] == "OPTIONS":
            if item["authorizationType"] != "NONE":
                raise SystemExit(f"OPTIONS authorization unexpected: {item['path']}")
            continue
        if item["authorizationType"] != "COGNITO_USER_POOLS" or item["authorizerId"] != AUTHORIZER_ID:
            raise SystemExit(f"not Cognito-protected: {item['method']} {item['path']}")
        if item["uri"] != LIVE_URI:
            raise SystemExit(f"not wired to erap-exchange:live: {item['method']} {item['path']}")

    if changed:
        deployment = aws(
            [
                "apigateway",
                "create-deployment",
                "--rest-api-id",
                API_ID,
                "--stage-name",
                API_STAGE,
                "--description",
                "Phase 5G Resource Exchange routes",
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
        "changed": changed,
    }
    folder = ROOT / "dist"
    folder.mkdir(exist_ok=True)
    (folder / "exchange-api-routes.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(json.dumps(record, indent=2))
    return 0


def main():
    if "--apply" in sys.argv:
        return apply()
    return plan_only()


if __name__ == "__main__":
    raise SystemExit(main())
