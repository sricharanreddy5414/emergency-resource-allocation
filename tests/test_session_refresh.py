from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_expired_session_refreshes_before_protected_api_calls():
    script = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
    refresh = script[script.index("async function requestRefreshToken"):script.index("async function refreshSession")]
    wrapper = script[script.index("window.fetch = async function"):script.index("function loadAuthenticatedUser")]
    startup = script[script.index("async function initializeAuthentication"):script.index("function showToast")]

    assert 'grant_type: "refresh_token"' in refresh
    assert "client_id:" in refresh
    assert "refresh_token: refreshToken" in refresh
    assert "originalFetch(" in refresh
    assert "console.error(error)" not in refresh
    assert "idTokenNeedsRefresh()" in startup
    assert startup.index("refreshSession()") < startup.rindex("loadAuthenticatedUser()")
    assert "response.status !== 401" in wrapper
    assert wrapper.count("originalFetch(") >= 2
    assert "Bearer " in wrapper
