from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _mfa_script():
    script = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
    start = script.index("OPTIONAL AUTHENTICATOR MFA")
    end = script.index("function logoutFromCognito")
    return script, script[start:end]


def test_login_requests_cognito_user_admin_scope_and_keeps_openid():
    script, _section = _mfa_script()
    scopes = script[script.index("const COGNITO_SCOPES"):script.index("APPLICATION STATE")]
    assert "openid" in scopes
    assert "aws.cognito.signin.user.admin" in scopes
    assert "authorization_code" in script
    assert "grant_type: \"refresh_token\"" in script


def test_mfa_uses_cognito_software_token_and_does_not_persist_the_secret():
    _script, section = _mfa_script()
    assert "AssociateSoftwareToken" in section
    assert "VerifySoftwareToken" in section
    assert "SetUserMFAPreference" in section
    assert "SOFTWARE_TOKEN_MFA" in section
    assert "localStorage" not in section
    assert "sessionStorage" not in section
    assert "console.log" not in section
    assert "console.error" not in section
    assert "document.cookie" not in section
    assert "otpauth://totp/" in section
    assert "SecretCode" in section


def test_mfa_errors_are_generic():
    _script, section = _mfa_script()
    assert "Incorrect verification code. Try again." in section
    assert "Your verification session expired. Please sign in again." in section
    assert "Sign out and sign in again to set up an authenticator." in section
    assert "CodeMismatch" in section
    assert "NotAuthorized" in section


def test_profile_has_one_authenticator_panel_and_sign_out_remains():
    html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    assert html.count('id="mfaPanel"') == 1
    assert html.count('id="mfaSetupKey"') == 1
    assert 'id="modalLogoutBtn"' in html
    assert "function clearTokens" in (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")


def test_optional_mfa_script_refuses_pool_wide_enforcement():
    script = (ROOT / "scripts" / "configure_optional_mfa.py").read_text(encoding="utf-8")
    assert "OPTIONAL" in script
    assert "--mfa-configuration\",\n            \"OPTIONAL\"" in script or '"OPTIONAL"' in script
    assert "Refusing pool-wide MFA enforcement." in script
    assert "Enabled=true" in script
    assert "print(" in script
    assert "SecretCode" not in script
    assert "getAccessToken" not in script
    assert "erap_id_token" not in script


def test_backend_authorization_is_unchanged_for_mfa():
    access = (ROOT / "src" / "shared" / "access.py").read_text(encoding="utf-8")
    assert "SOFTWARE_TOKEN_MFA" not in access
    assert "AssociateSoftwareToken" not in access
