import json
from datetime import datetime, timezone

from botocore.exceptions import ClientError

from access import REQUEST_ROLES, AccessError, authorize, require_location
from common import ALLOWED_ORIGIN


def response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": ALLOWED_ORIGIN,
            "Access-Control-Allow-Headers": "Content-Type,Authorization",
            "Access-Control-Allow-Methods": "OPTIONS,POST",
        },
        "body": json.dumps(body, default=str),
    }


def requests_table():
    import boto3

    return boto3.resource("dynamodb").Table("EmergencyRequests")


def locations_table():
    import os

    import boto3

    return boto3.resource("dynamodb").Table(os.environ.get("LOCATIONS_TABLE", "Locations"))


def lambda_handler(event, context):
    method = (
        event.get("httpMethod")
        or event.get("requestContext", {}).get("http", {}).get("method")
        or ""
    ).upper()

    if method == "OPTIONS":
        return response(200, {"message": "CORS preflight successful"})

    if method != "POST":
        return response(405, {"message": "Method not allowed"})

    try:
        body = event.get("body") or {}

        if isinstance(body, str):
            body = json.loads(body)

        if not isinstance(body, dict):
            return response(400, {"message": "Request body must be a JSON object"})

        _user_sub, membership = authorize(event, body, allowed_roles=REQUEST_ROLES)
        organization_id = membership["organization_id"]
        request_id = str(body.get("request_id", "")).strip()
        resource_type = str(body.get("resource_type", "")).strip().upper()
        location_id = str(body.get("location_id", "")).strip()

        try:
            priority = int(body.get("priority"))
        except (TypeError, ValueError):
            priority = 0

        if not request_id:
            return response(400, {"message": "Request ID is required"})

        if not resource_type:
            return response(400, {"message": "Resource type is required"})

        if priority < 1 or priority > 5:
            return response(400, {"message": "Priority must be between 1 and 5"})

        location = require_location(locations_table(), organization_id, location_id)
        item = {
            "request_id": request_id,
            "ResourceType": resource_type,
            "Location": location.get("name", ""),
            "location_id": location["location_id"],
            "organization_id": organization_id,
            "Priority": priority,
            "Status": "PENDING",
            "CreatedAt": datetime.now(timezone.utc).isoformat(),
        }
        requests_table().put_item(
            Item=item,
            ConditionExpression="attribute_not_exists(request_id)",
        )
        return response(201, {"message": "Request created successfully", "request": item})
    except AccessError as error:
        return response(error.status_code, {"message": error.message})
    except json.JSONDecodeError:
        return response(400, {"message": "Invalid JSON request body"})
    except ClientError as error:
        code = error.response.get("Error", {}).get("Code")
        print("Request error:", code)

        if code == "ConditionalCheckFailedException":
            return response(409, {"message": "Request ID already exists"})

        return response(500, {"message": "Failed to create request"})
    except Exception as error:
        print("Request error:", error.__class__.__name__)
        return response(500, {"message": "Internal server error"})
