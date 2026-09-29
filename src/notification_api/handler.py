"""Authenticated Notifications HTTP API (Phase 8B).

Routes:
  GET  /notifications
  GET  /notifications/unread-count
  POST /notifications/{notification_id}/read
  POST /notifications/read-all
"""

import json
import re
import urllib.parse

from access import AccessError, READ_ACCESS, access_body, authorize
from common import ALLOWED_ORIGIN
from notifications import NOTIFICATION_ROLES
import service as notification_service
from observability import begin_request, error_body, load_object, log_result


def response(status_code, body):
    payload = error_body(status_code, body)
    if isinstance(payload, dict):
        log_result(
            status_code,
            operation="notifications",
            error_code=payload.get("error", {}).get("code", ""),
        )
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": ALLOWED_ORIGIN,
            "Access-Control-Allow-Headers": "Content-Type,Authorization",
            "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
        },
        "body": json.dumps(payload, default=str),
    }


def parse_body(event):
    return load_object(event.get("body") or "{}", event.get("isBase64Encoded"))


def _path(event):
    return str(event.get("path") or event.get("resource") or "").rstrip("/") or "/"


def _query(event):
    raw = event.get("queryStringParameters") or {}
    return raw if isinstance(raw, dict) else {}


def _match_read(path):
    match = re.search(r"/notifications/(.+)/read$", path)
    if not match:
        return None
    return urllib.parse.unquote(match.group(1))


def lambda_handler(event, context):
    del context
    begin_request(event)
    method = str(event.get("httpMethod") or "").upper()
    path = _path(event)

    if method == "OPTIONS":
        return response(200, {"message": "OK"})

    body = {}
    if method == "POST":
        try:
            body = parse_body(event)
        except (json.JSONDecodeError, ValueError, TypeError):
            return response(400, {"message": "Invalid JSON body"})

    try:
        user_sub, membership = authorize(
            event,
            body,
            allowed_roles=NOTIFICATION_ROLES,
            access=READ_ACCESS,
        )
        organization_id = membership["organization_id"]

        if method == "GET" and path.endswith("/notifications/unread-count"):
            return response(
                200,
                notification_service.unread_count(organization_id, user_sub, membership),
            )

        if method == "GET" and path.endswith("/notifications"):
            query = _query(event)
            unread_only = str(query.get("unread_only") or "").lower() in {"1", "true", "yes"}
            return response(
                200,
                notification_service.list_notifications(
                    organization_id,
                    user_sub,
                    membership,
                    limit=query.get("limit") or 20,
                    page_token=query.get("page_token"),
                    unread_only=unread_only,
                ),
            )

        if method == "POST" and path.endswith("/notifications/read-all"):
            return response(
                200,
                notification_service.mark_all_read(organization_id, user_sub, membership),
            )

        notification_id = _match_read(path)
        if method == "POST" and notification_id:
            return response(
                200,
                notification_service.mark_read(
                    organization_id, user_sub, membership, notification_id
                ),
            )

        return response(404, {"message": "Not found"})
    except AccessError as error:
        return response(error.status_code, access_body(error))
    except notification_service.NotificationOperationError as error:
        return response(error.status_code, {"message": error.message})
    except Exception:
        return response(500, {"message": "Request failed"})
