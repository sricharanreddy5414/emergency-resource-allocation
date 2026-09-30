"""Read-only check that Phase 6 controls are still in place."""

import sys

from aws_cli import aws
from lambda_manifest import ALLOWED_ORIGIN, API_ID, API_STAGE, REGION, TABLES
from recovery_expectations import AUTO_RELEASE, SCHEDULES, alias_functions


ALARMS = {
    "ERAP-ApiGateway-5XX",
    "ERAP-Allocation-Throttles",
    "ERAP-AutoRelease-Errors",
    "ERAP-Public-Lambda-Errors",
    "ERAP-Resources-SystemErrors",
    "EmergencyResourceAllocation-Lambda-Errors",
}


def main():
    for table in TABLES:
        backups = aws(["dynamodb", "describe-continuous-backups", "--table-name", table], region=REGION)
        status = backups["ContinuousBackupsDescription"]["PointInTimeRecoveryDescription"]["PointInTimeRecoveryStatus"]
        described = aws(["dynamodb", "describe-table", "--table-name", table], region=REGION)["Table"]
        if described["TableStatus"] != "ACTIVE" or status != "ENABLED" or not described.get("DeletionProtectionEnabled"):
            raise SystemExit(f"{table} protection changed")
        print(f"ok table {table}")
    stage = aws(["apigateway", "get-stage", "--rest-api-id", API_ID, "--stage-name", API_STAGE], region=REGION)
    settings = stage.get("methodSettings") or {}
    default = settings.get("*/*") or {}
    public = settings.get("~1public~1resources/GET") or {}
    if default.get("throttlingRateLimit") != 20 or default.get("throttlingBurstLimit") != 40:
        raise SystemExit("stage throttle changed")
    if public.get("throttlingRateLimit") != 5 or public.get("throttlingBurstLimit") != 10:
        raise SystemExit("public throttle changed")
    print(f"ok throttle deployment {stage.get('deploymentId')}")
    for response_type in ("UNAUTHORIZED", "DEFAULT_4XX", "DEFAULT_5XX"):
        gateway = aws(
            ["apigateway", "get-gateway-response", "--rest-api-id", API_ID, "--response-type", response_type],
            region=REGION,
        )
        origin = (gateway.get("responseParameters") or {}).get("gatewayresponse.header.Access-Control-Allow-Origin", "")
        if origin != f"'{ALLOWED_ORIGIN}'":
            raise SystemExit(f"{response_type} CORS changed")
    print("ok cors")
    alarms = aws(["cloudwatch", "describe-alarms", "--alarm-name-prefix", "ERAP"], region=REGION)
    names = {item["AlarmName"] for item in alarms.get("MetricAlarms", [])}
    legacy = aws(
        ["cloudwatch", "describe-alarms", "--alarm-names", "EmergencyResourceAllocation-Lambda-Errors"],
        region=REGION,
    )
    names.update(item["AlarmName"] for item in legacy.get("MetricAlarms", []))
    missing = ALARMS - names
    if missing:
        raise SystemExit(f"missing alarms {sorted(missing)}")
    silent = [
        item["AlarmName"]
        for item in [*alarms.get("MetricAlarms", []), *legacy.get("MetricAlarms", [])]
        if item["AlarmName"] in ALARMS and not item.get("AlarmActions")
    ]
    if silent:
        raise SystemExit(f"alarms without a notification target: {silent}")
    print("ok alarms")
    authorizers = aws(["apigateway", "get-authorizers", "--rest-api-id", API_ID], region=REGION)
    ids = {item["id"] for item in authorizers.get("items", [])}
    if "y0hzhr" not in ids:
        raise SystemExit("Cognito authorizer missing")
    print("ok authorizer")
    public = next(item for item in _resources() if item.get("path") == "/public/resources")
    integration = aws(
        [
            "apigateway",
            "get-integration",
            "--rest-api-id",
            API_ID,
            "--resource-id",
            public["id"],
            "--http-method",
            "GET",
        ],
        region=REGION,
    )
    if ":live/invocations" not in (integration.get("uri") or ""):
        raise SystemExit("public API does not invoke alias live")
    print("ok live alias")
    _check_recovery()
    return 0


def _check_recovery():
    for name, expected in SCHEDULES.items():
        item = aws(["scheduler", "get-schedule", "--name", name], region=REGION)
        target = ((item.get("Target") or {}).get("Arn")) or ""
        if item.get("State") != "ENABLED" or item.get("ScheduleExpression") != expected["expression"]:
            raise SystemExit(f"schedule changed {name}")
        if not target.endswith(expected["target_suffix"]):
            raise SystemExit(f"schedule target changed {name}")
        print(f"ok schedule {name}")
    rule = aws(["events", "describe-rule", "--name", AUTO_RELEASE["name"]], region=REGION)
    if rule.get("State") != "ENABLED" or rule.get("ScheduleExpression") != AUTO_RELEASE["expression"]:
        raise SystemExit("auto-release rule changed")
    targets = aws(["events", "list-targets-by-rule", "--rule", AUTO_RELEASE["name"]], region=REGION)
    arns = [item.get("Arn") or "" for item in targets.get("Targets") or []]
    if not any(arn.endswith(AUTO_RELEASE["target_suffix"]) for arn in arns):
        raise SystemExit("auto-release target changed")
    print("ok auto-release")
    for name in alias_functions():
        alias = aws(["lambda", "get-alias", "--function-name", name, "--name", "live"], region=REGION)
        if not str(alias.get("FunctionVersion") or "").isdigit():
            raise SystemExit(f"{name} live alias missing")
    print(f"ok aliases {len(alias_functions())}")


def _resources():
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


if __name__ == "__main__":
    sys.exit(main())
