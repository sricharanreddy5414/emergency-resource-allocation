import json
from datetime import datetime, timezone, timedelta

import boto3
from botocore.exceptions import ClientError


dynamodb = boto3.resource("dynamodb")

resources_table = dynamodb.Table("Resources")
allocations_table = dynamodb.Table("Allocations")
requests_table = dynamodb.Table("EmergencyRequests")
history_table = dynamodb.Table("ResourceStatusHistory")

RELEASE_AFTER_MINUTES = 30


def response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*"
        },
        "body": json.dumps(body, default=str)
    }


def lambda_handler(event, context):
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=RELEASE_AFTER_MINUTES)

    print("===== AUTOMATIC RESOURCE RELEASE =====")
    print("Current time:", now.isoformat())
    print("Release cutoff:", cutoff.isoformat())

    allocation_response = allocations_table.scan()
    allocations = allocation_response.get("Items", [])

    while "LastEvaluatedKey" in allocation_response:
        allocation_response = allocations_table.scan(
            ExclusiveStartKey=allocation_response["LastEvaluatedKey"]
        )
        allocations.extend(
            allocation_response.get("Items", [])
        )

    released = []
    skipped = []

    for allocation in allocations:

        if str(
            allocation.get("status", "")
        ).upper() != "ALLOCATED":
            continue

        allocated_at = allocation.get("allocated_at")

        if not allocated_at:
            skipped.append({
                "allocation_id": allocation.get("allocation_id"),
                "reason": "Missing allocated_at"
            })
            continue

        try:
            allocated_time = datetime.fromisoformat(
                str(allocated_at).replace("Z", "+00:00")
            )

        except ValueError:
            skipped.append({
                "allocation_id": allocation.get("allocation_id"),
                "reason": "Invalid allocated_at"
            })
            continue

        if allocated_time > cutoff:
            continue

        allocation_id = allocation.get("allocation_id")
        resource_id = allocation.get("resource_id")
        request_id = allocation.get("request_id")

        print(
            "EXPIRING ALLOCATION:",
            allocation_id,
            resource_id,
            request_id
        )

        try:

            resources_table.update_item(
                Key={
                    "resource_id": resource_id
                },
                UpdateExpression="SET #a = :true",
                ConditionExpression="#a = :false",
                ExpressionAttributeNames={
                    "#a": "Available"
                },
                ExpressionAttributeValues={
                    ":true": True,
                    ":false": False
                }
            )

            allocations_table.update_item(
                Key={
                    "allocation_id": allocation_id
                },
                UpdateExpression=(
                    "SET #s = :status, "
                    "released_at = :released_at"
                ),
                ExpressionAttributeNames={
                    "#s": "status"
                },
                ExpressionAttributeValues={
                    ":status": "RELEASED",
                    ":released_at": now.isoformat()
                }
            )

            if request_id:

                requests_table.update_item(
                    Key={
                        "request_id": request_id
                    },
                    UpdateExpression="SET #s = :status",
                    ExpressionAttributeNames={
                        "#s": "Status"
                    },
                    ExpressionAttributeValues={
                        ":status": "RELEASED"
                    }
                )

            history_table.put_item(
                Item={
                    "history_id": (
                        "HIST-AUTO-RELEASE-"
                        + str(resource_id)
                        + "-"
                        + now.strftime(
                            "%Y%m%d%H%M%S%f"
                        )
                    ),
                    "resource_id": resource_id,
                    "resource_type": allocation.get(
                        "resource_type",
                        ""
                    ),
                    "location": allocation.get(
                        "location",
                        ""
                    ),
                    "previous_status": "ALLOCATED",
                    "new_status": "AVAILABLE",
                    "changed_at": now.isoformat(),
                    "reason": "AUTOMATIC_RESOURCE_RELEASE",
                    "request_id": request_id,
                    "allocation_id": allocation_id
                }
            )

            released.append({
                "allocation_id": allocation_id,
                "resource_id": resource_id,
                "request_id": request_id
            })

            print(
                "RESOURCE AUTOMATICALLY RELEASED:",
                resource_id
            )

        except ClientError as error:

            if (
                error.response["Error"]["Code"]
                == "ConditionalCheckFailedException"
            ):
                print(
                    "RESOURCE ALREADY AVAILABLE:",
                    resource_id
                )

                skipped.append({
                    "allocation_id": allocation_id,
                    "resource_id": resource_id,
                    "reason": "Resource already available"
                })

                continue

            print("DYNAMODB ERROR:", error)
            raise

    return response(
        200,
        {
            "message": "Automatic release process completed",
            "released_count": len(released),
            "released": released,
            "skipped": skipped
        }
    )