import uuid
from datetime import datetime, timezone

from botocore.exceptions import ClientError

from access import (
    LOCATION_WRITE_ROLES,
    READ_ROLES,
    AccessError,
    authorize,
    query_by_organization,
    require_location,
)
from audit import build_audit_event, record_audit
from common import api_response, parse_json_body


def locations_table():
    import os

    import boto3

    return boto3.resource("dynamodb").Table(
        os.environ.get("LOCATIONS_TABLE", "Locations")
    )


def resources_table():
    import boto3

    return boto3.resource("dynamodb").Table("Resources")


def requests_table():
    import boto3

    return boto3.resource("dynamodb").Table("EmergencyRequests")


def allocations_table():
    import boto3

    return boto3.resource("dynamodb").Table("Allocations")


def audit_table():
    import os

    import boto3

    return boto3.resource("dynamodb").Table(
        os.environ.get("AUDIT_TABLE", "AuditEvents")
    )


def now():
    return datetime.now(timezone.utc).isoformat()


def clean(value, limit=200):
    text = str(value or "").strip()

    if len(text) > limit:
        raise ValueError("A location field is too long")

    return text


def location_view(item):
    return {
        "organization_id": item.get("organization_id"),
        "location_id": item.get("location_id"),
        "name": item.get("name", ""),
        "address": item.get("address", ""),
        "city": item.get("city", ""),
        "state": item.get("state", ""),
        "postal_code": item.get("postal_code", ""),
        "country": item.get("country", ""),
        "latitude": item.get("latitude", ""),
        "longitude": item.get("longitude", ""),
        "status": item.get("status", ""),
        "created_at": item.get("created_at", ""),
        "updated_at": item.get("updated_at", ""),
    }


def path_location_id(event):
    params = event.get("pathParameters") or {}
    return str(params.get("location_id") or "").strip()


def lambda_handler(event, context):
    method = (event.get("httpMethod") or "").upper()

    if method == "OPTIONS":
        return api_response(200, {"message": "OK"})

    table = locations_table()

    try:
        body = {}

        if method in {"POST", "PUT", "PATCH"}:
            body = parse_json_body(event)

        allowed = LOCATION_WRITE_ROLES if method in {"POST", "PUT", "PATCH", "DELETE"} else READ_ROLES
        user_sub, membership = authorize(event, body, allowed_roles=allowed)
        organization_id = membership["organization_id"]

        if method == "GET" and not path_location_id(event):
            locations = [
                location_view(item)
                for item in query_by_organization(table, organization_id, index_name=None)
                if item.get("status") == "ACTIVE"
            ]
            locations.sort(key=lambda item: item["name"])
            return api_response(200, {"locations": locations})

        if method == "GET":
            item = require_location(table, organization_id, path_location_id(event))
            return api_response(200, {"location": location_view(item)})

        if method == "POST":
            name = clean(body.get("name"), 100)

            if not name:
                return api_response(400, {"message": "Location name is required"})

            created_at = now()
            item = {
                "organization_id": organization_id,
                "location_id": "LOC-" + uuid.uuid4().hex[:12].upper(),
                "name": name,
                "address": clean(body.get("address")),
                "city": clean(body.get("city")),
                "state": clean(body.get("state")),
                "postal_code": clean(body.get("postal_code"), 20),
                "country": clean(body.get("country")),
                "latitude": clean(body.get("latitude"), 40),
                "longitude": clean(body.get("longitude"), 40),
                "status": "ACTIVE",
                "created_at": created_at,
                "updated_at": created_at,
                "created_by": user_sub,
            }
            table.put_item(
                Item=item,
                ConditionExpression="attribute_not_exists(location_id)",
            )
            record_audit(
                audit_table(),
                build_audit_event(
                    organization_id,
                    user_sub,
                    membership.get("role"),
                    "location.create",
                    "location",
                    item["location_id"],
                    location_id=item["location_id"],
                ),
            )
            return api_response(201, {"message": "Location created", "location": location_view(item)})

        if method in {"PUT", "PATCH"}:
            current = require_location(table, organization_id, path_location_id(event))
            name = clean(body.get("name", current.get("name")), 100)

            if not name:
                return api_response(400, {"message": "Location name is required"})

            updated = dict(current)
            updated.update(
                {
                    "name": name,
                    "address": clean(body.get("address", current.get("address"))),
                    "city": clean(body.get("city", current.get("city"))),
                    "state": clean(body.get("state", current.get("state"))),
                    "postal_code": clean(body.get("postal_code", current.get("postal_code")), 20),
                    "country": clean(body.get("country", current.get("country"))),
                    "latitude": clean(body.get("latitude", current.get("latitude")), 40),
                    "longitude": clean(body.get("longitude", current.get("longitude")), 40),
                    "updated_at": now(),
                    "organization_id": organization_id,
                }
            )
            table.put_item(Item=updated)
            return api_response(200, {"message": "Location updated", "location": location_view(updated)})

        if method == "DELETE":
            current = require_location(table, organization_id, path_location_id(event))
            location_id = current["location_id"]

            for dependent in (
                resources_table(),
                requests_table(),
                allocations_table(),
            ):
                if query_by_organization(dependent, organization_id, location_id):
                    return api_response(
                        409,
                        {"message": "Location has operational records"},
                    )

            current["status"] = "INACTIVE"
            current["updated_at"] = now()
            table.put_item(Item=current)
            record_audit(
                audit_table(),
                build_audit_event(
                    organization_id,
                    user_sub,
                    membership.get("role"),
                    "location.deactivate",
                    "location",
                    location_id,
                    location_id=location_id,
                ),
            )
            return api_response(200, {"message": "Location deactivated"})

        return api_response(405, {"message": "Method not allowed"})

    except AccessError as error:
        return api_response(error.status_code, {"message": error.message})
    except ValueError as error:
        return api_response(400, {"message": str(error)})
    except ClientError as error:
        code = error.response["Error"]["Code"]
        print("Location error:", code)

        if code == "ConditionalCheckFailedException":
            return api_response(409, {"message": "Location already exists"})

        return api_response(500, {"message": "Unable to update locations"})
    except Exception as error:
        print("Location error:", error.__class__.__name__)
        return api_response(500, {"message": "Unable to update locations"})
