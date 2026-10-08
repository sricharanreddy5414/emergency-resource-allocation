"""Expire individual reservations that have reached their server-written due time.

The worker reads one bounded page of ReservationDueIndex. It does not scan
Resources and it does not query each organization. Quantity pools are not
indexed and are not expired here.
"""

from datetime import datetime, timezone

from botocore.exceptions import ClientError

from audit import build_audit_event, record_audit
from notifications import EVENT_RESERVATION_EXPIRED, emit_notification_event
from resource_state import RESERVATION_DUE_INDEX, RESERVATION_DUE_KEY, RESERVATION_HELD_ATTRIBUTES

BATCH_LIMIT = 25
SYSTEM_ACTOR = "system"


def _now_text(now):
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)
    return clock.astimezone(timezone.utc).isoformat()


def _conditional_failure(error):
    return error.response["Error"]["Code"] == "ConditionalCheckFailedException"


def query_due_reservations(table, now_text, limit=BATCH_LIMIT):
    """One indexed page of reservations whose due time is at or before now."""
    from boto3.dynamodb.conditions import Key

    return table.query(
        IndexName=RESERVATION_DUE_INDEX,
        KeyConditionExpression=(
            Key("reservation_due_key").eq(RESERVATION_DUE_KEY)
            & Key("reservation_expires_at").lte(now_text)
        ),
        Limit=limit,
    )


def _clear_replaced_due_key(table, item):
    """Drop only the due-index attributes when this reservation is no longer current."""
    try:
        table.update_item(
            Key={"resource_id": item["resource_id"]},
            UpdateExpression="REMOVE reservation_expires_at, reservation_due_key",
            ConditionExpression=(
                "organization_id = :organization_id AND reservation_expires_at = :expires "
                "AND (operational_status <> :reserved OR reserved_by <> :actor "
                "OR attribute_not_exists(reserved_by) OR reserved_at <> :reserved_at "
                "OR attribute_not_exists(reserved_at))"
            ),
            ExpressionAttributeValues={
                ":organization_id": item["organization_id"],
                ":expires": item["reservation_expires_at"],
                ":reserved": "RESERVED",
                ":actor": item["reserved_by"],
                ":reserved_at": item["reserved_at"],
            },
        )
    except ClientError as error:
        if not _conditional_failure(error):
            raise


def expire_reservation(tables, item, now_text):
    """Release one due reservation, or skip it when the row has changed."""
    resource_id = str(item.get("resource_id") or "").strip()
    organization_id = str(item.get("organization_id") or "").strip()
    reserved_by = str(item.get("reserved_by") or "").strip()
    reserved_at = str(item.get("reserved_at") or "").strip()
    expires = str(item.get("reservation_expires_at") or "").strip()
    if not resource_id or not organization_id or not reserved_by or not reserved_at or not expires:
        return "skipped"
    table = tables["resources"]
    if str(item.get("tracking_mode") or "INDIVIDUAL").upper() == "QUANTITY":
        try:
            table.update_item(
                Key={"resource_id": resource_id},
                UpdateExpression="REMOVE reservation_expires_at, reservation_due_key",
                ConditionExpression=(
                    "organization_id = :organization_id AND tracking_mode = :quantity "
                    "AND reservation_expires_at = :expires"
                ),
                ExpressionAttributeValues={
                    ":organization_id": organization_id,
                    ":quantity": "QUANTITY",
                    ":expires": expires,
                },
            )
        except ClientError as error:
            if not _conditional_failure(error):
                raise
        return "skipped"

    try:
        table.update_item(
            Key={"resource_id": resource_id},
            UpdateExpression=(
                "SET operational_status = :available, Available = :true "
                "REMOVE " + ", ".join(RESERVATION_HELD_ATTRIBUTES)
            ),
            ConditionExpression=(
                "organization_id = :organization_id AND operational_status = :reserved "
                "AND reserved_by = :actor AND reserved_at = :reserved_at "
                "AND reservation_expires_at = :expires AND reservation_expires_at <= :now"
            ),
            ExpressionAttributeValues={
                ":available": "AVAILABLE",
                ":true": True,
                ":organization_id": organization_id,
                ":reserved": "RESERVED",
                ":actor": reserved_by,
                ":reserved_at": reserved_at,
                ":expires": expires,
                ":now": now_text,
            },
        )
    except ClientError as error:
        if not _conditional_failure(error):
            raise
        _clear_replaced_due_key(table, item)
        return "skipped"

    stamp = now_text.replace(":", "").replace("-", "").replace("+", "")
    try:
        tables["history"].put_item(
            Item={
                "history_id": f"HIST-RESERVATION-EXPIRY-{resource_id}-{stamp}",
                "resource_id": resource_id,
                "organization_id": organization_id,
                "location_id": item.get("location_id") or "",
                "resource_type": item.get("Type") or "",
                "location": item.get("Location") or "",
                "previous_status": "RESERVED",
                "new_status": "AVAILABLE",
                "reason": "RESERVATION_EXPIRED",
                "changed_at": now_text,
                "actor_sub": SYSTEM_ACTOR,
            }
        )
        record_audit(
            tables.get("audit"),
            build_audit_event(
                organization_id,
                SYSTEM_ACTOR,
                "SYSTEM",
                "resource.reservation_expired",
                "resource",
                resource_id,
                location_id=item.get("location_id") or "",
            ),
        )
    except Exception:
        from observability import log_event

        log_event(
            "ERROR",
            "reservation-expiry",
            "expire_reservation",
            "audit_failed",
            organization_id=organization_id,
            resource_id=resource_id,
        )

    emit_notification_event(
        event_code=EVENT_RESERVATION_EXPIRED,
        subject_id=f"{resource_id}|{reserved_at}",
        recipient_organization_id=organization_id,
        actor_sub=SYSTEM_ACTOR,
        source_organization_id=organization_id,
        payload={
            "resource_id": resource_id,
            "resource_name": item.get("name") or item.get("Type") or "",
        },
        table=tables.get("notifications"),
        members=tables.get("members"),
        organizations=tables.get("organizations"),
    )
    return "expired"


def run_reservation_expiry(now=None, tables=None, limit=BATCH_LIMIT):
    """Expire at most one indexed page. A later run continues with whatever is still due."""
    from observability import begin_request, log_event

    correlation = begin_request({"requestContext": {"requestId": "reservation-expiry"}})
    now_text = _now_text(now)
    if tables is None:
        import boto3

        dynamodb = boto3.resource("dynamodb")
        tables = {
            "resources": dynamodb.Table("Resources"),
            "history": dynamodb.Table("ResourceStatusHistory"),
            "audit": dynamodb.Table("AuditEvents"),
        }
    result = query_due_reservations(tables["resources"], now_text, limit)
    counts = {"expired": 0, "skipped": 0}
    for item in result.get("Items") or []:
        outcome = expire_reservation(tables, item, now_text)
        counts[outcome] = counts.get(outcome, 0) + 1
    log_event(
        "INFO",
        "reservation-expiry",
        "run_reservation_expiry",
        "summary",
        correlation_id=correlation,
        items_considered=sum(counts.values()),
        items_expired=counts["expired"],
        items_skipped=counts["skipped"],
        more_due=bool(result.get("LastEvaluatedKey")),
    )
    return counts
