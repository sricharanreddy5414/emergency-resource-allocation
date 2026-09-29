"""Authenticated Resource Exchange HTTP API (Phase 5C/5D).

Routes:
  POST   /exchange/requests
  GET    /exchange/requests?scope=mine|network
  GET    /exchange/requests/{exchange_request_id}
  POST   /exchange/requests/{exchange_request_id}/offers
  GET    /exchange/requests/{exchange_request_id}/offers
  GET    /exchange/requests/{exchange_request_id}/offers/{offer_id}
  POST   /exchange/requests/{exchange_request_id}/offers/{offer_id}/accept
  GET    /exchange/offers?scope=mine

OFFER CREATE does not hold. ACCEPT creates atomic EXCHANGE hold (no ownership transfer).
"""

import json
import re

from botocore.exceptions import ClientError

from access import (
    AccessError,
    WRITE_ACCESS,
    access_body,
    authorize,
)
from common import ALLOWED_ORIGIN
from exchange_model import EXCHANGE_READ_ROLES, EXCHANGE_WRITE_ROLES
import service
from observability import begin_request, error_body, load_object, log_result


def response(status_code, body):
    payload = error_body(status_code, body)

    if isinstance(payload, dict):
        log_result(status_code, operation="exchange", error_code=payload.get("error", {}).get("code", ""))

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


def _match_request_id(path):
    match = re.search(r"/exchange/requests/(EXREQ-[A-Za-z0-9_-]+)$", path)
    return match.group(1) if match else None


def _match_offers_collection(path):
    match = re.search(r"/exchange/requests/(EXREQ-[A-Za-z0-9_-]+)/offers$", path)
    return match.group(1) if match else None


def _match_offer_item(path):
    match = re.search(
        r"/exchange/requests/(EXREQ-[A-Za-z0-9_-]+)/offers/(EXOFF-[A-Za-z0-9_-]+)$",
        path,
    )
    return (match.group(1), match.group(2)) if match else (None, None)


def _match_accept(path):
    match = re.search(
        r"/exchange/requests/(EXREQ-[A-Za-z0-9_-]+)/offers/(EXOFF-[A-Za-z0-9_-]+)/accept$",
        path,
    )
    return (match.group(1), match.group(2)) if match else (None, None)


def lambda_handler(event, context):
    begin_request(event)
    method = (event.get("httpMethod") or "GET").upper()
    path = _path(event)

    if method == "OPTIONS":
        return response(200, {"message": "OK"})

    try:
        body = parse_body(event) if method in {"POST", "PUT", "PATCH"} else {}
        query = event.get("queryStringParameters") or {}

        if method == "POST" and path.endswith("/exchange/requests"):
            actor_sub, membership = authorize(
                event, body, allowed_roles=EXCHANGE_WRITE_ROLES, access=WRITE_ACCESS
            )
            result = service.create_exchange_request(
                body, membership["organization_id"], actor_sub, membership.get("role")
            )
            return response(201, {"message": "Exchange request created", "request": result})

        if method == "GET" and path.endswith("/exchange/offers"):
            _actor, membership = authorize(event, allowed_roles=EXCHANGE_READ_ROLES)
            scope = str(query.get("scope") or "mine").strip().lower()

            if scope != "mine":
                return response(400, {"message": "scope must be mine"})

            return response(200, service.list_my_offers(membership["organization_id"], query))

        if method == "GET" and path.endswith("/exchange/requests"):
            _actor, membership = authorize(event, allowed_roles=EXCHANGE_READ_ROLES)
            scope = str(query.get("scope") or "network").strip().lower()

            if scope == "mine":
                return response(200, service.list_my_requests(membership["organization_id"], query))

            if scope == "network":
                return response(200, service.list_network_requests(membership["organization_id"], query))

            return response(400, {"message": "scope must be mine or network"})

        accept_request_id, accept_offer_id = _match_accept(path)

        if accept_request_id and accept_offer_id and method == "POST":
            actor_sub, membership = authorize(
                event, body or {}, allowed_roles=EXCHANGE_WRITE_ROLES, access=WRITE_ACCESS
            )
            result = service.accept_offer(
                accept_request_id,
                accept_offer_id,
                membership["organization_id"],
                actor_sub,
                membership.get("role"),
                membership,
            )
            return response(200, result)

        request_id, offer_id = _match_offer_item(path)

        if request_id and offer_id and method == "GET":
            _actor, membership = authorize(event, allowed_roles=EXCHANGE_READ_ROLES)
            result = service.get_offer(
                request_id, offer_id, membership["organization_id"], membership
            )
            return response(200, {"offer": result})

        offers_request_id = _match_offers_collection(path)

        if offers_request_id and method == "GET":
            _actor, membership = authorize(event, allowed_roles=EXCHANGE_READ_ROLES)
            return response(
                200,
                service.list_offers_for_request(
                    offers_request_id, membership["organization_id"], membership
                ),
            )

        if offers_request_id and method == "POST":
            actor_sub, membership = authorize(
                event, body, allowed_roles=EXCHANGE_WRITE_ROLES, access=WRITE_ACCESS
            )
            result = service.create_offer(
                offers_request_id,
                body,
                membership["organization_id"],
                actor_sub,
                membership.get("role"),
            )
            return response(201, {"message": "Exchange offer created", "offer": result})

        single_id = _match_request_id(path)

        if single_id and method == "GET":
            _actor, membership = authorize(event, allowed_roles=EXCHANGE_READ_ROLES)
            result = service.get_exchange_request(
                single_id, membership["organization_id"], membership
            )
            return response(200, {"request": result})

        return response(404, {"message": "Not found"})
    except AccessError as error:
        return response(error.status_code, access_body(error))
    except service.ExchangeOperationError as error:
        body = {"message": error.message}

        if error.code:
            body["code"] = error.code

        return response(error.status_code, body)
    except ClientError as error:
        print("Exchange error:", error.response["Error"]["Code"])
        return response(500, {"message": "Failed to process exchange request"})
    except Exception as error:
        print("Exchange error:", error.__class__.__name__)
        return response(500, {"message": "Failed to process exchange request"})
