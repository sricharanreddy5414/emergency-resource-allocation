"""Signup country selector and phone normalization."""

import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def node_executable():
    found = shutil.which("node")
    if found:
        return found
    portable = Path(r"C:\Users\sri charan reddy\AppData\Local\Temp\erap-node\node-v22.20.0-win-x64\node.exe")
    if portable.is_file():
        return str(portable)
    return None


def test_signup_phone_country_selector():
    node = node_executable()
    assert node, "Node is required to execute the signup phone tests"
    result = subprocess.run(
        [node, str(ROOT / "tests" / "signup_phone.mjs")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_normal_visit_offers_create_account_before_cognito():
    app = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
    html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    block = app.split(
        'if (!getAccessToken() && !sessionStorage.getItem("erap_refresh_token"))',
        1,
    )[1].split("if (idTokenNeedsRefresh()", 1)[0]
    assert "showAccountEntry()" in block
    assert "showSignupScreen()" in block
    assert "loginWithCognito()" not in block
    assert 'id="accountSignIn"' in html
    assert 'id="accountCreate"' in html
    assert "Create account" in html
    style = (ROOT / "frontend" / "style.css").read_text(encoding="utf-8")
    assert "flex-wrap: wrap" in style
    assert "@media (max-width: 560px)" in style


def test_signup_errors_stay_separate_from_authenticator_setup():
    signup = (ROOT / "frontend" / "signup-ui.js").read_text(encoding="utf-8")
    app = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
    assert "async function cognitoIdentityCall(" in signup
    assert "async function cognitoIdentityCall(" not in app
    assert "async function mfaCognitoCall(" in app
    assert "Authenticator setup could not be completed" not in signup
