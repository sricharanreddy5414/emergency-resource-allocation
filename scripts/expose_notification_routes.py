"""Idempotent API Gateway exposure for Notifications routes (Phase 8B).

Routes (Cognito authorizer y0hzhr; OPTIONS Authorization NONE):

  GET/OPTIONS   /notifications
  GET/OPTIONS   /notifications/unread-count
  POST/OPTIONS  /notifications/read-all
  POST/OPTIONS  /notifications/{notification_id}/read

Integration target: erap-notifications:live

Dry-run (default): prints planned paths without mutation.
Apply: python scripts/expose_notification_routes.py --apply
"""

from __future__ import annotations

import json
import sys

from aws_cli import aws
from lambda_manifest import ACCOUNT, ALIAS, API_ID, API_STAGE, REGION, ROOT

AUTHORIZER_ID = "y0hzhr"
FUNCTION = "erap-notifications"
LIVE_URI = (
    f"arn:aws:apigateway:{REGION}:lambda:path/2015-03-31/functions/"
    f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:{FUNCTION}:{ALIAS}/invocations"
)

ROUTES = (
    ((), ("GET",)),
    (("unread-count",), ("GET",)),
    (("read-all",), ("POST",)),
    (("{notification_id}", "read"), ("POST",)),
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


def apply():
    resources = list_resources()
    path_map = by_path(resources)
    root = path_map.get("/")
    if not root:
        raise SystemExit("API root resource missing")

    notifications = ensure_resource(path_map, root["id"], "notifications", "/notifications")
    unread = ensure_resource(
        path_map, notifications["id"], "unread-count", "/notifications/unread-count"
    )
    read_all = ensure_resource(
        path_map, notifications["id"], "read-all", "/notifications/read-all"
    )
    notif_id = ensure_resource(
        path_map, notifications["id"], "{notification_id}", "/notifications/{notification_id}"
    )
    read = ensure_resource(
        path_map,
        notif_id["id"],
        "read",
        "/notifications/{notification_id}/read",
    )

    targets = {
        "/notifications": (notifications, ("GET",)),
        "/notifications/unread-count": (unread, ("GET",)),
        "/notifications/read-all": (read_all, ("POST",)),
        "/notifications/{notification_id}/read": (read, ("POST",)),
    }

    changed = False
    for path, (resource, methods) in targets.items():
        for method in methods:
            result = ensure_proxy(resource["id"], method, "COGNITO_USER_POOLS", AUTHORIZER_ID)
            changed = changed or result == "created"
        result = ensure_proxy(resource["id"], "OPTIONS", "NONE")
        changed = changed or result == "created"
        del path

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
                "Phase 8B notifications routes",
            ],
            region=REGION,
        )
        print(f"deployed stage {API_STAGE} id {deployment.get('id')}")
    else:
        print("no API changes; stage left unchanged")

    report = {
        "status": "applied",
        "function": FUNCTION,
        "uri": LIVE_URI,
        "paths": list(targets),
    }
    folder = ROOT / "dist"
    folder.mkdir(exist_ok=True)
    (folder / "notification-routes.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


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
                "paths": [
                    "/notifications",
                    "/notifications/unread-count",
                    "/notifications/read-all",
                    "/notifications/{notification_id}/read",
                ],
            },
            indent=2,
        )
    )
    return 0


def main():
    if "--apply" in sys.argv:
        return apply()
    return plan_only()


if __name__ == "__main__":
    raise SystemExit(main())
