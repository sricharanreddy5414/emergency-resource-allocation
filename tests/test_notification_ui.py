"""Phase 8C frontend notification wiring checks."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
HTML = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
CSS = (ROOT / "frontend" / "style.css").read_text(encoding="utf-8")


def test_notifications_api_uses_authenticated_helper():
    assert "NOTIFICATIONS_API_URL" in APP
    assert "Authorization" in APP.split("async function notificationRequest", 1)[1].split("function notificationErrorMessage", 1)[0]
    assert "organization_id" in APP.split("async function notificationRequest", 1)[1].split("function notificationErrorMessage", 1)[0]
    assert "/unread-count" in APP
    assert "/read-all" in APP
    assert 'encodeURIComponent(notificationId) + "/read"' in APP


def test_inbox_states_and_no_visible_id_catalog():
    assert "Loading notifications" in APP
    assert "No notifications" in APP
    assert "Unable to load notifications" in APP
    assert "Notifications are available to owners, admins, and operators." in APP
    assert "notification.title" in APP
    assert "notification.body" in APP
    assert "notification.notification_id" not in APP.split("function renderNotifications", 1)[1].split("function updateNotificationCount", 1)[0]


def test_deep_link_and_badge():
    assert 'navigateTo("exchange")' in APP
    assert "openExchangeRequest(requestId)" in APP
    assert "refreshNotificationBadge" in APP
    assert "notification-count" in HTML
    assert ".notification-count[hidden]" in CSS
    assert ".notification-item.unread" in CSS


def test_read_all_copy_matches_capped_backend():
    assert "Marked a batch as read" in APP
    assert "rounds < 5" in APP
    assert ">Mark read<" in HTML or "Mark read" in HTML


def test_member_role_excluded_from_inbox():
    assert "function canReadNotifications()" in APP
    assert "return canOperateResources();" in APP
    assert 'role === "MEMBER"' not in APP.split("function canOperateResources", 1)[1].split("function resourceRowActions", 1)[0]


def test_session_toast_is_not_persistent_inbox():
    block = APP.split("function addNotification", 1)[1].split("async function notificationRequest", 1)[0]
    assert "showToast" in block
    assert "notifications.unshift" not in block
