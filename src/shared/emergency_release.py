"""Conditional emergency release of one resource, allocation, and request.

The three records commit in one TransactWriteItems call. History, audit, and
EventBridge stay after that commit: a logging failure must not undo a release,
and a retry that finds the allocation already released does not write them
again. public_status stays outside as well. DynamoDB accepts one update of an
item per transaction, and the availability update already uses the resource.
"""

import boto3
from boto3.dynamodb.types import TypeSerializer

RESOURCES_TABLE = "Resources"
ALLOCATIONS_TABLE = "Allocations"
REQUESTS_TABLE = "EmergencyRequests"

_serializer = TypeSerializer()


def _dynamodb_client():
    return boto3.client("dynamodb")


def _transact_write(transact_items):
    """Execute TransactWriteItems. Tests may replace this helper."""
    _dynamodb_client().transact_write_items(TransactItems=transact_items)


def _encoded(values):
    return {key: _serializer.serialize(value) for key, value in values.items()}


def emergency_release_items(
    *,
    resource_id,
    allocation_id,
    request_id,
    organization_id,
    released_at,
    free_resource,
):
    """Build the release writes. The resource write is omitted when another allocation still holds it."""
    items = []

    if free_resource:
        items.append(
            {
                "Update": {
                    "TableName": RESOURCES_TABLE,
                    "Key": _encoded({"resource_id": resource_id}),
                    "UpdateExpression": "SET Available = :available, operational_status = :op_available",
                    "ConditionExpression": (
                        "attribute_exists(resource_id) AND Available = :held "
                        "AND organization_id = :organization_id "
                        "AND (attribute_not_exists(operational_status) OR operational_status = :op_allocated)"
                    ),
                    "ExpressionAttributeValues": _encoded(
                        {
                            ":available": True,
                            ":held": False,
                            ":organization_id": organization_id,
                            ":op_available": "AVAILABLE",
                            ":op_allocated": "ALLOCATED",
                        }
                    ),
                }
            }
        )

    items.append(
        {
            "Update": {
                "TableName": ALLOCATIONS_TABLE,
                "Key": _encoded({"allocation_id": allocation_id}),
                "UpdateExpression": "SET #status = :released, released_at = :released_at",
                "ConditionExpression": (
                    "attribute_exists(allocation_id) AND #status = :allocated "
                    "AND organization_id = :organization_id"
                ),
                "ExpressionAttributeNames": {"#status": "status"},
                "ExpressionAttributeValues": _encoded(
                    {
                        ":released": "RELEASED",
                        ":allocated": "ALLOCATED",
                        ":released_at": released_at,
                        ":organization_id": organization_id,
                    }
                ),
            }
        }
    )
    items.append(
        {
            "Update": {
                "TableName": REQUESTS_TABLE,
                "Key": _encoded({"request_id": request_id}),
                "UpdateExpression": "SET #status = :released",
                "ConditionExpression": (
                    "attribute_exists(request_id) AND #status = :allocated "
                    "AND organization_id = :organization_id"
                ),
                "ExpressionAttributeNames": {"#status": "Status"},
                "ExpressionAttributeValues": _encoded(
                    {
                        ":released": "RELEASED",
                        ":allocated": "ALLOCATED",
                        ":organization_id": organization_id,
                    }
                ),
            }
        }
    )
    return items


def commit_emergency_release(
    *,
    resource_id,
    allocation_id,
    request_id,
    organization_id,
    released_at,
    free_resource,
):
    _transact_write(
        emergency_release_items(
            resource_id=resource_id,
            allocation_id=allocation_id,
            request_id=request_id,
            organization_id=organization_id,
            released_at=released_at,
            free_resource=free_resource,
        )
    )


def emergency_release_conflict(error, *, free_resource):
    """Map a cancelled transaction to an application reason. Item data is not included."""
    reasons = error.response.get("CancellationReasons") or []
    codes = [str(reason.get("Code") or "") if isinstance(reason, dict) else "" for reason in reasons]
    allocation_index = 1 if free_resource else 0
    request_index = allocation_index + 1

    if _failed(codes, allocation_index):
        return "already released"

    if free_resource and _failed(codes, 0):
        return "resource state changed"

    if _failed(codes, request_index):
        return "request state changed"

    return "state changed"


def _failed(codes, index):
    return index < len(codes) and codes[index] == "ConditionalCheckFailed"
