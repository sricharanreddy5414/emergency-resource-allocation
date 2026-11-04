"""HTTP routes for delivery tracking, served by emergency-resource-allocation.

Live API Gateway resources for these paths are not created by this module.
"""

import json
import re

from access import AccessError, access_body
from common import ALLOWED_ORIGIN, dumps_json
from delivery_service import (
    DeliveryError,
    confirm_receipt,
    create_delivery,
    dispatch_delivery,
    get_delivery,
    get_history,
    list_deliveries,
    mark_ready,
    record_handover,
    update_status,
    update_transport,
    verify_handover,
)
from observability import error_body, log_result

_DELIVERY_PATH = re.compile(r"/deliveries(?:/([^/]+))?(?:/([^/]+))?(?:/([^/]+))?$")


def is_delivery_path(path):
    text = str(path or "")
    return "/deliveries" in text


def response(status_code, body):
    payload = error_body(status_code, body)
    if isinstance(payload, dict):
        log_result(
            status_code,
            operation="delivery",
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
        "body": dumps_json(payload),
    }


def _parts(path):
    match = _DELIVERY_PATH.search(str(path or ""))
    if not match:
        return None, None, None
    return match.group(1), match.group(2), match.group(3)


def handle_delivery(method, path, body, query, organization_id, actor_sub, actor_role):
    try:
        delivery_id, action, extra = _parts(path)
        body = body or {}
        query = query or {}

        if method == "GET" and not delivery_id:
            return response(200, list_deliveries(organization_id, query))

        if method == "POST" and not delivery_id:
            return response(200, create_delivery(organization_id, actor_sub, actor_role, body))

        if method == "GET" and delivery_id and action == "history" and not extra:
            return response(200, get_history(organization_id, delivery_id))

        if method == "GET" and delivery_id and not action:
            return response(200, get_delivery(organization_id, delivery_id))

        if method == "POST" and delivery_id and action == "transport" and not extra:
            return response(
                200,
                update_transport(organization_id, actor_sub, actor_role, delivery_id, body),
            )

        if method == "POST" and delivery_id and action == "ready" and not extra:
            return response(200, mark_ready(organization_id, actor_sub, actor_role, delivery_id, body))

        if method == "POST" and delivery_id and action == "dispatch" and not extra:
            return response(
                200,
                dispatch_delivery(organization_id, actor_sub, actor_role, delivery_id, body),
            )

        if method == "POST" and delivery_id and action == "status" and not extra:
            return response(200, update_status(organization_id, actor_sub, actor_role, delivery_id, body))

        if method == "POST" and delivery_id and action == "receipt" and not extra:
            return response(
                200,
                confirm_receipt(organization_id, actor_sub, actor_role, delivery_id, body),
            )

        if method == "POST" and delivery_id and action == "handover" and extra == "verify":
            return response(
                200,
                verify_handover(organization_id, actor_sub, actor_role, delivery_id, body),
            )

        if method == "POST" and delivery_id and action == "handover" and not extra:
            return response(
                200,
                record_handover(organization_id, actor_sub, actor_role, delivery_id, body),
            )

        return response(405, {"message": "Method not allowed"})
    except DeliveryError as error:
        return response(error.status_code, {"message": error.message})
    except AccessError as error:
        return response(error.status_code, access_body(error))
    except ValueError as error:
        return response(400, {"message": str(error) or "Invalid request format"})
    except json.JSONDecodeError:
        return response(400, {"message": "Invalid request format"})
