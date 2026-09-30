"""Read-only production posture checks. Does not print secret values."""

import sys

from aws_cli import aws
from lambda_manifest import REGION, ROOT, package_map

BUCKET = "emergency-resource-allocation-sricharan-2026"
POOL = "eu-north-1_vv7adAAC9"
SHARED_ROLE = "emergency-resource-allocation-role-12qymvku"
FORBIDDEN = {
    "dynamodb:Scan",
    "dynamodb:DeleteItem",
    "dynamodb:BatchWriteItem",
    "dynamodb:RestoreTableToPointInTime",
    "dynamodb:*",
    "backup:*",
}
SENSITIVE_ENV = ("SECRET", "PASSWORD", "WEBHOOK", "PRIVATE_KEY", "API_KEY", "CREDENTIAL")


def _fail(message):
    raise SystemExit(message)


def _actions(document):
    found = set()
    for statement in document.get("Statement") or []:
        action = statement.get("Action")
        if isinstance(action, str):
            found.add(action)
        else:
            found.update(action or [])
    return found


def check_repository():
    for path in (ROOT / "src").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if '"Access-Control-Allow-Origin": "*"' in text or "'Access-Control-Allow-Origin': '*'" in text:
            _fail(f"wildcard CORS in {path.relative_to(ROOT)}")
    print("ok repository cors")


def check_s3():
    block = aws(["s3api", "get-public-access-block", "--bucket", BUCKET], region=REGION)
    config = block.get("PublicAccessBlockConfiguration") or {}
    required = ("BlockPublicAcls", "IgnorePublicAcls", "BlockPublicPolicy", "RestrictPublicBuckets")
    if not all(config.get(name) is True for name in required):
        _fail("S3 public access block is incomplete")
    try:
        aws(["s3api", "get-bucket-policy", "--bucket", BUCKET], region=REGION)
    except SystemExit as error:
        if "NoSuchBucketPolicy" not in str(error):
            raise
    else:
        _fail("S3 bucket policy is present")
    print("ok s3")


def check_cognito():
    mfa = aws(["cognito-idp", "get-user-pool-mfa-config", "--user-pool-id", POOL], region=REGION)
    software = (mfa.get("SoftwareTokenMfaConfiguration") or {}).get("Enabled")
    if mfa.get("MfaConfiguration") != "OPTIONAL" or software is not True or mfa.get("SmsMfaConfiguration"):
        _fail("Cognito MFA configuration changed")
    print("ok cognito")


def check_lambdas():
    for name in sorted(package_map()):
        config = aws(["lambda", "get-function-configuration", "--function-name", name], region=REGION)
        env = (config.get("Environment") or {}).get("Variables") or {}
        for key in env:
            if any(word in key.upper() for word in SENSITIVE_ENV):
                _fail(f"{name} environment name looks sensitive")
    print("ok lambda env")


def check_runtime_roles():
    roles = []
    marker = None
    while True:
        args = ["iam", "list-roles"]
        if marker:
            args.extend(["--marker", marker])
        listed = aws(args) or {}
        roles.extend(listed.get("Roles") or [])
        marker = listed.get("Marker")
        if not listed.get("IsTruncated"):
            break
    names = [
        role["RoleName"]
        for role in roles
        if role["RoleName"].startswith("ERAP-") or role["RoleName"] == SHARED_ROLE
    ]
    names = [name for name in names if "GitHub" not in name and "Scheduler" not in name]
    if SHARED_ROLE not in names:
        _fail("shared runtime role missing")
    for name in sorted(names):
        attached = aws(["iam", "list-attached-role-policies", "--role-name", name]) or {}
        managed = {item["PolicyName"] for item in attached.get("AttachedPolicies") or []}
        if "AdministratorAccess" in managed:
            _fail(f"{name} has AdministratorAccess")
        inline = aws(["iam", "list-role-policies", "--role-name", name]) or {}
        for policy in inline.get("PolicyNames") or []:
            document = aws(["iam", "get-role-policy", "--role-name", name, "--policy-name", policy])["PolicyDocument"]
            if isinstance(document, str):
                import json

                document = json.loads(document)
            bad = _actions(document) & FORBIDDEN
            if bad:
                _fail(f"{name} has {sorted(bad)}")
    print(f"ok runtime roles {len(names)}")


def main():
    check_repository()
    check_s3()
    check_cognito()
    check_lambdas()
    check_runtime_roles()
    return 0


if __name__ == "__main__":
    sys.exit(main())
