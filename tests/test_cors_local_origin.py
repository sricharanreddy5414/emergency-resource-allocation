"""Local browser CORS allowlist. These tests do not call AWS."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [
    str(ROOT / "src" / "shared"),
    str(ROOT / "src" / "organization"),
]

import common
import get_handler
import observability


def test_localhost_origin_is_echoed():
    observability.begin_request(
        {
            "httpMethod": "OPTIONS",
            "headers": {"origin": "http://localhost:8080"},
        }
    )
    result = common.api_response(200, {"message": "OK"})
    assert result["headers"]["Access-Control-Allow-Origin"] == "http://localhost:8080"
    assert result["headers"]["Access-Control-Allow-Origin"] != "*"


def test_loopback_origin_is_echoed():
    observability.begin_request(
        {
            "httpMethod": "GET",
            "headers": {"Origin": "http://127.0.0.1:8080"},
        }
    )
    result = common.api_response(200, {"organizations": []})
    assert result["headers"]["Access-Control-Allow-Origin"] == "http://127.0.0.1:8080"


def test_unknown_origin_stays_on_amplify():
    observability.begin_request(
        {
            "httpMethod": "GET",
            "headers": {"origin": "https://evil.example"},
        }
    )
    result = common.api_response(200, {"organizations": []})
    assert result["headers"]["Access-Control-Allow-Origin"] == common.ALLOWED_ORIGIN
    assert "evil.example" not in result["headers"]["Access-Control-Allow-Origin"]


def test_organization_lookup_cors_uses_request_origin(monkeypatch):
    import member_admin

    monkeypatch.setattr(get_handler, "list_memberships", lambda user_sub: [])
    monkeypatch.setattr(member_admin, "pending_invitations", lambda event: [])
    monkeypatch.setattr(member_admin, "handle_member_read", lambda event: None)
    result = get_handler.lambda_handler(
        {
            "httpMethod": "GET",
            "headers": {"origin": "http://localhost:8080"},
            "requestContext": {"authorizer": {"claims": {"sub": "cognito-user-1"}}},
        },
        None,
    )
    assert result["statusCode"] == 200
    assert result["headers"]["Access-Control-Allow-Origin"] == "http://localhost:8080"
