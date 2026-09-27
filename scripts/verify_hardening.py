"""Read-only check that Phase 6 controls are still in place."""

import sys

from aws_cli import aws
from lambda_manifest import ALLOWED_ORIGIN, API_ID, API_STAGE, REGION, TABLES


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
    print("ok alarms")
    authorizers = aws(["apigateway", "get-authorizers", "--rest-api-id", API_ID], region=REGION)
    ids = {item["id"] for item in authorizers.get("items", [])}
    if "y0hzhr" not in ids:
        raise SystemExit("Cognito authorizer missing")
    print("ok authorizer")
    return 0


if __name__ == "__main__":
    sys.exit(main())
