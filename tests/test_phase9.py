"""Pilot operations checks that do not call AWS or Cognito."""

from pathlib import Path

import src.shared.observability as observability


ROOT = Path(__file__).resolve().parents[1]


def test_successful_responses_log_safe_metadata(capsys):
    observability.begin_request(
        {"requestContext": {"requestId": "req-ok"}, "httpMethod": "PUT", "path": "/resources"}
    )
    observability.log_result(200, operation="resource")
    logged = capsys.readouterr().out

    assert '"request_id": "req-ok"' in logged
    assert '"route": "PUT /resources"' in logged
    assert '"operation": "resource"' in logged
    assert '"result": 200' in logged
    assert "duration_ms" in logged
    assert "Bearer" not in logged
    assert "eyJ" not in logged
    assert "Authorization" not in logged


def test_options_preflight_is_not_logged(capsys):
    observability.begin_request(
        {"requestContext": {"requestId": "req-options"}, "httpMethod": "OPTIONS", "path": "/resources"}
    )
    observability.log_result(200, operation="resource")

    assert capsys.readouterr().out == ""


def test_resource_edit_uses_existing_update_api():
    app = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
    handler = (ROOT / "src" / "resource" / "handler.py").read_text(encoding="utf-8")

    assert "resource-edit-btn" in app
    assert 'method: editing ? "PUT" : "POST"' in app
    assert "closeRegisterModal()" in app
    assert "registerResourceModal" not in app
    assert "erap_selected_organization_id" in app
    assert "organization_id = :organization_id" in handler
    assert "visibility.change" in handler
