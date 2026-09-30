"""Enable optional Cognito authenticator MFA without requiring it.

This keeps the existing user pool and app client. It refuses pool-wide
enforcement. It does not print tokens, passwords, or MFA secrets.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from aws_cli import aws  # noqa: E402

POOL = "eu-north-1_vv7adAAC9"
CLIENT = "3je7latr22bqhggoavlva00hp5"
ADMIN_SCOPE = "aws.cognito.signin.user.admin"
KEEP_SCOPES = ["email", "openid", "phone", ADMIN_SCOPE]


def client_update(current):
    scopes = list(current.get("AllowedOAuthScopes") or [])
    for scope in KEEP_SCOPES:
        if scope not in scopes:
            scopes.append(scope)
    payload = {
        "UserPoolId": POOL,
        "ClientId": CLIENT,
        "ClientName": current.get("ClientName"),
        "RefreshTokenValidity": current.get("RefreshTokenValidity"),
        "AccessTokenValidity": current.get("AccessTokenValidity"),
        "IdTokenValidity": current.get("IdTokenValidity"),
        "TokenValidityUnits": current.get("TokenValidityUnits"),
        "SupportedIdentityProviders": current.get("SupportedIdentityProviders"),
        "CallbackURLs": current.get("CallbackURLs"),
        "LogoutURLs": current.get("LogoutURLs"),
        "AllowedOAuthFlows": current.get("AllowedOAuthFlows"),
        "AllowedOAuthScopes": scopes,
        "AllowedOAuthFlowsUserPoolClient": True,
        "PreventUserExistenceErrors": current.get("PreventUserExistenceErrors") or "ENABLED",
        "EnableTokenRevocation": current.get("EnableTokenRevocation", True),
        "EnablePropagateAdditionalUserContextData": current.get(
            "EnablePropagateAdditionalUserContextData", False
        ),
        "AuthSessionValidity": current.get("AuthSessionValidity") or 3,
    }
    if current.get("ExplicitAuthFlows"):
        payload["ExplicitAuthFlows"] = current["ExplicitAuthFlows"]
    if current.get("ReadAttributes"):
        payload["ReadAttributes"] = current["ReadAttributes"]
    if current.get("WriteAttributes"):
        payload["WriteAttributes"] = current["WriteAttributes"]
    return payload


def main():
    if "--enforce" in sys.argv:
        raise SystemExit("Refusing pool-wide MFA enforcement.")
    before = aws(["cognito-idp", "describe-user-pool", "--user-pool-id", POOL])["UserPool"]
    current = aws(
        ["cognito-idp", "describe-user-pool-client", "--user-pool-id", POOL, "--client-id", CLIENT]
    )["UserPoolClient"]
    if before.get("MfaConfiguration") == "ON":
        raise SystemExit("MFA is already required. Refusing to change enforcement.")
    update = client_update(current)
    if "ON" in update["AllowedOAuthScopes"]:
        raise SystemExit("Unexpected scope list.")
    aws(["cognito-idp", "update-user-pool-client", "--cli-input-json", json.dumps(update)])
    aws(
        [
            "cognito-idp",
            "set-user-pool-mfa-config",
            "--user-pool-id",
            POOL,
            "--mfa-configuration",
            "OPTIONAL",
            "--software-token-mfa-configuration",
            "Enabled=true",
        ]
    )
    after_pool = aws(["cognito-idp", "get-user-pool-mfa-config", "--user-pool-id", POOL])
    after_client = aws(
        ["cognito-idp", "describe-user-pool-client", "--user-pool-id", POOL, "--client-id", CLIENT]
    )["UserPoolClient"]
    print(json.dumps({
        "before_mfa": before.get("MfaConfiguration"),
        "after_mfa": after_pool.get("MfaConfiguration"),
        "software_token_enabled": (after_pool.get("SoftwareTokenMfaConfiguration") or {}).get("Enabled"),
        "sms_mfa": after_pool.get("SmsMfaConfiguration"),
        "scopes": after_client.get("AllowedOAuthScopes"),
        "callback_count": len(after_client.get("CallbackURLs") or []),
        "logout_count": len(after_client.get("LogoutURLs") or []),
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
