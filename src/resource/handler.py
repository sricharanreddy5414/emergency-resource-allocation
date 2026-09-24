import json
import base64
import boto3
from datetime import datetime, timezone

dynamodb = boto3.resource("dynamodb")
resources_table = dynamodb.Table("Resources")
history_table = dynamodb.Table("ResourceStatusHistory")
allocations_table = dynamodb.Table("Allocations")
requests_table = dynamodb.Table("EmergencyRequests")
eventbridge = boto3.client("events")


def response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type",
            "Access-Control-Allow-Methods": "GET,POST,OPTIONS"
        },
        "body": json.dumps(body, default=str)
    }


def parse_body(event):
    body = event.get("body") or "{}"

    if event.get("isBase64Encoded") and isinstance(body, str):
        body = base64.b64decode(body).decode("utf-8")

    if isinstance(body, str):
        body = json.loads(body)

    return body


def lambda_handler(event, context):

    method = (
        event.get("httpMethod")
        or event.get("requestContext", {})
        .get("http", {})
        .get("method")
        or "GET"
    )

    path = (
        event.get("path")
        or event.get("rawPath")
        or ""
    )

    # ---------------------------------------------------------
    # CORS PREFLIGHT
    # ---------------------------------------------------------
    if method == "OPTIONS":
        return response(
            200,
            {"message": "CORS OK"}
        )

    # ---------------------------------------------------------
    # RELEASE RESOURCE
    # ---------------------------------------------------------
    if method == "POST" and path.endswith("/release"):

        try:
            body = parse_body(event)

            resource_id = body.get("resource_id")

            if not resource_id:
                return response(
                    400,
                    {"message": "resource_id is required"}
                )

            resource_result = resources_table.get_item(
                Key={
                    "resource_id": resource_id
                }
            )

            resource = resource_result.get("Item")

            if not resource:
                return response(
                    404,
                    {"message": "Resource not found"}
                )

            available = resource.get(
                "Available",
                False
            )

            if (
                available is True
                or str(available).lower() == "true"
            ):
                return response(
                    409,
                    {"message": "Resource is already available"}
                )

            # Find the active allocation for this resource.
            allocation_result = allocations_table.scan(
                FilterExpression=(
                    "resource_id = :resource_id "
                    "AND #status = :allocated"
                ),
                ExpressionAttributeNames={
                    "#status": "status"
                },
                ExpressionAttributeValues={
                    ":resource_id": resource_id,
                    ":allocated": "ALLOCATED"
                }
            )

            active_allocations = allocation_result.get(
                "Items",
                []
            )

            if not active_allocations:
                return response(
                    409,
                    {
                        "message":
                        "No active allocation found for resource"
                    }
                )

            # Prefer the newest allocation when allocated_at exists.
            active_allocations.sort(
                key=lambda item:
                    item.get("allocated_at", ""),
                reverse=True
            )

            allocation = active_allocations[0]

            allocation_id = allocation.get(
                "allocation_id"
            )

            request_id = allocation.get(
                "request_id"
            )

            if not allocation_id or not request_id:
                return response(
                    409,
                    {
                        "message":
                        "Active allocation data is incomplete"
                    }
                )

            released_at = datetime.now(
                timezone.utc
            ).isoformat()

            # Release the resource only if it is still allocated.
            resources_table.update_item(
                Key={
                    "resource_id": resource_id
                },
                UpdateExpression=(
                    "SET Available = :available"
                ),
                ConditionExpression=(
                    "attribute_exists(resource_id) "
                    "AND Available = :allocated"
                ),
                ExpressionAttributeValues={
                    ":available": True,
                    ":allocated": False
                }
            )

            # Mark the active allocation as released.
            allocations_table.update_item(
                Key={
                    "allocation_id": allocation_id
                },
                UpdateExpression=(
                    "SET #status = :released, "
                    "released_at = :released_at"
                ),
                ConditionExpression=(
                    "attribute_exists(allocation_id) "
                    "AND #status = :allocated"
                ),
                ExpressionAttributeNames={
                    "#status": "status"
                },
                ExpressionAttributeValues={
                    ":released": "RELEASED",
                    ":allocated": "ALLOCATED",
                    ":released_at": released_at
                }
            )

            # Mark the associated emergency request as released.
            requests_table.update_item(
                Key={
                    "request_id": request_id
                },
                UpdateExpression=(
                    "SET #status = :released"
                ),
                ConditionExpression=(
                    "attribute_exists(request_id) "
                    "AND #status = :allocated"
                ),
                ExpressionAttributeNames={
                    "#status": "Status"
                },
                ExpressionAttributeValues={
                    ":released": "RELEASED",
                    ":allocated": "ALLOCATED"
                }
            )

            history_table.put_item(
                Item={
                    "history_id":
                        "HIST-RELEASE-" +
                        resource_id + "-" +
                        datetime.now(
                            timezone.utc
                        ).strftime(
                            "%Y%m%d%H%M%S%f"
                        ),
                    "resource_id":
                        resource_id,
                    "resource_type":
                        resource.get("Type", ""),
                    "location":
                        resource.get("Location", ""),
                    "previous_status":
                        "ALLOCATED",
                    "new_status":
                        "AVAILABLE",
                    "changed_at":
                        released_at,
                    "reason":
                        "RESOURCE_RELEASED",
                    "allocation_id":
                        allocation_id,
                    "request_id":
                        request_id
                }
            )

            # Publish release event for downstream notifications.
            try:
                eventbridge.put_events(
                    Entries=[
                        {
                            "Source":
                                "emergency.resource.allocation",
                            "DetailType":
                                "ResourceReleased",
                            "Detail":
                                json.dumps(
                                    {
                                        "resource_id":
                                            resource_id,
                                        "allocation_id":
                                            allocation_id,
                                        "request_id":
                                            request_id,
                                        "resource_type":
                                            resource.get(
                                                "Type",
                                                ""
                                            ),
                                        "location":
                                            resource.get(
                                                "Location",
                                                ""
                                            ),
                                        "status":
                                            "RELEASED"
                                    }
                                ),
                                "EventBusName":
                                    "default"
                        }
                    ]
                )
            except Exception as event_error:
                print(
                    "Release event error:",
                    event_error
                )

            return response(
                200,
                {
                    "message":
                        "Resource released successfully",
                    "resource": {
                        "resource_id":
                            resource_id,
                        "Available":
                            True
                    },
                    "allocation": {
                        "allocation_id":
                            allocation_id,
                        "status":
                            "RELEASED"
                    },
                    "request": {
                        "request_id":
                            request_id,
                        "status":
                            "RELEASED"
                    }
                }
            )

        except json.JSONDecodeError:
            return response(
                400,
                {"message": "Invalid JSON body"}
            )

        except Exception as error:
            print(
                "Resource release error:",
                error
            )

            return response(
                500,
                {
                    "message":
                    "Failed to release resource"
                }
            )
    # ---------------------------------------------------------
    # GET RESOURCE STATUS HISTORY
    # ---------------------------------------------------------
    if method == "GET" and path.endswith("/history"):

        result = history_table.scan()

        histories = result.get(
            "Items",
            []
        )

        query = (
            event.get("queryStringParameters")
            or {}
        )

        resource_id = query.get(
            "resource_id"
        )

        if resource_id:
            histories = [
                item
                for item in histories
                if item.get("resource_id")
                == resource_id
            ]

        histories.sort(
            key=lambda item:
                item.get("changed_at", ""),
            reverse=True
        )

        return response(
            200,
            histories
        )

    # ---------------------------------------------------------
    # GET RESOURCES
    # ---------------------------------------------------------
    if method == "GET":

        result = resources_table.scan()

        resources = result.get(
            "Items",
            []
        )

        return response(
            200,
            resources
        )

    # ---------------------------------------------------------
    # REGISTER RESOURCE
    # ---------------------------------------------------------
    if method == "POST":

        try:
            body = parse_body(event)

            resource_id = body.get(
                "resource_id"
            )

            resource_type = body.get(
                "Type"
            )

            location = body.get(
                "Location"
            )

            if (
                not resource_id
                or not resource_type
                or not location
            ):
                return response(
                    400,
                    {
                        "message":
                        "resource_id, Type and Location are required"
                    }
                )

            available = body.get(
                "Available",
                True
            )

            if isinstance(
                available,
                str
            ):
                available = (
                    available.lower()
                    == "true"
                )

            item = {
                "resource_id": resource_id,
                "Type": resource_type,
                "Location": location,
                "Available": available
            }

            resources_table.put_item(
                Item=item,
                ConditionExpression=(
                    "attribute_not_exists(resource_id)"
                )
            )

            return response(
                201,
                {
                    "message":
                    "Resource registered successfully",
                    "resource": item
                }
            )

        except json.JSONDecodeError:
            return response(
                400,
                {"message": "Invalid JSON body"}
            )

        except (
            resources_table
            .meta
            .client
            .exceptions
            .ConditionalCheckFailedException
        ):
            return response(
                409,
                {"message": "Resource ID already exists"}
            )

        except Exception as error:
            print(
                "Resource registration error:",
                error
            )

            return response(
                500,
                {
                    "message":
                    "Failed to register resource"
                }
            )

    # ---------------------------------------------------------
    # METHOD NOT ALLOWED
    # ---------------------------------------------------------
    return response(
        405,
        {"message": "Method not allowed"}
    )
