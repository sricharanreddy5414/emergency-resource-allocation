"""In-app notification emit + storage helpers (Phase 8B).

Post-commit best-effort fan-out. Never called inside Exchange TransactWrite.
Failures are logged and must not raise to Exchange callers.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

from botocore.exceptions import ClientError

from observability import current_request_id

NOTIFICATION_ROLES = {"OWNER", "ADMIN", "OPERATOR"}
FANOUT_CAP = 50
TTL_DAYS = 90
UNREAD_COUNT_CAP = 99
SCHEMA_VERSION = 1

EVENT_OFFER_RECEIVED = "exchange.offer.received"
EVENT_OFFER_ACCEPTED = "exchange.offer.accepted"
EVENT_OFFER_REJECTED = "exchange.offer.rejected"
EVENT_OFFER_WITHDRAWN = "exchange.offer.withdrawn"
EVENT_OFFER_SUPERSEDED = "exchange.offer.superseded"
EVENT_TRANSFER_STARTED = "exchange.transfer.started"
EVENT_HANDOVER_COMPLETED = "exchange.handover.completed"
EVENT_REQUEST_CANCELLED = "exchange.request.cancelled"
EVENT_REQUEST_EXPIRED = "exchange.request.expired"
EVENT_RESERVATION_EXPIRED = "resource.reservation.expired"

COPY = {
    EVENT_OFFER_RECEIVED: (
        "New exchange offer",
        "An organization offered a resource for your exchange request.",
    ),
    EVENT_OFFER_ACCEPTED: (
        "Offer accepted",
        "An exchange offer was accepted and a hold was placed.",
    ),
    EVENT_OFFER_REJECTED: (
        "Offer rejected",
        "Your exchange offer was rejected.",
    ),
    EVENT_OFFER_WITHDRAWN: (
        "Offer withdrawn",
        "An offer on your exchange request was withdrawn.",
    ),
    EVENT_OFFER_SUPERSEDED: (
        "Offer superseded",
        "Another offer was accepted; your open offer was superseded.",
    ),
    EVENT_TRANSFER_STARTED: (
        "Transfer started",
        "Resource transfer has started. Handover confirmation is pending.",
    ),
    EVENT_HANDOVER_COMPLETED: (
        "Handover completed",
        "Exchange handover completed and ownership transfer finished.",
    ),
    EVENT_REQUEST_CANCELLED: (
        "Exchange cancelled",
        "An exchange request was cancelled.",
    ),
    EVENT_REQUEST_EXPIRED: (
        "Exchange expired",
        "An exchange request expired.",
    ),
    EVENT_RESERVATION_EXPIRED: (
        "Reservation expired",
        "A reserved resource is available again.",
    ),
}


def notifications_table():
    import boto3

    return boto3.resource("dynamodb").Table(
        os.environ.get("NOTIFICATIONS_TABLE", "Notifications")
    )


def members_table():
    import boto3

    return boto3.resource("dynamodb").Table(
        os.environ.get("ORGANIZATION_MEMBERS_TABLE", "OrganizationMembers")
    )


def organizations_table():
    import boto3

    return boto3.resource("dynamodb").Table(
        os.environ.get("ORGANIZATIONS_TABLE", "Organizations")
    )


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def ttl_epoch(created_at=None, days=TTL_DAYS):
    if created_at:
        try:
            base = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
        except ValueError:
            base = datetime.now(timezone.utc)
    else:
        base = datetime.now(timezone.utc)
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    return int((base + timedelta(days=days)).timestamp())


def event_id_for(event_code, subject_id, recipient_organization_id):
    return (
        f"{event_code}#{str(subject_id or '').strip()}"
        f"#{str(recipient_organization_id or '').strip()}"
    )


def inbox_notification_id(event_id, user_sub):
    return f"{event_id}#{str(user_sub or '').strip()}"


def inbox_pk(organization_id, user_sub):
    return f"INBOX#{organization_id}#{user_sub}"


def inbox_sk(created_at, event_id):
    return f"AT#{created_at}#{event_id}"


def event_pk(event_id):
    return f"EVENT#{event_id}"


def unread_key_for(organization_id, user_sub):
    return inbox_pk(organization_id, user_sub)


def parse_notification_id(notification_id):
    text = str(notification_id or "").strip()
    parts = text.split("#")
    if len(parts) != 4:
        return None
    event_code, subject_id, organization_id, user_sub = parts
    if not event_code or not subject_id or not organization_id or not user_sub:
        return None
    if not organization_id.startswith("ORG-"):
        return None
    event_id = f"{event_code}#{subject_id}#{organization_id}"
    return {
        "notification_id": text,
        "event_id": event_id,
        "event_code": event_code,
        "subject_id": subject_id,
        "organization_id": organization_id,
        "user_sub": user_sub,
    }


def _organization_active(organization_id, organizations=None):
    org_id = str(organization_id or "").strip()
    if not org_id:
        return False
    table = organizations or organizations_table()
    item = table.get_item(Key={"organization_id": org_id}).get("Item") or {}
    return str(item.get("status") or "").upper() == "ACTIVE"


def resolve_recipients(organization_id, actor_sub, *, members=None, organizations=None):
    """ACTIVE OWNER/ADMIN/OPERATOR in org, excluding actor. No Scan."""
    org_id = str(organization_id or "").strip()
    if not org_id:
        return []
    if not _organization_active(org_id, organizations=organizations):
        return []

    table = members or members_table()
    from boto3.dynamodb.conditions import Key

    items = []
    start_key = None
    while True:
        query = {"KeyConditionExpression": Key("organization_id").eq(org_id)}
        if start_key:
            query["ExclusiveStartKey"] = start_key
        result = table.query(**query)
        items.extend(result.get("Items") or [])
        start_key = result.get("LastEvaluatedKey")
        if not start_key:
            break

    owners_admins = []
    operators = []
    actor = str(actor_sub or "").strip()
    for row in items:
        user_sub = str(row.get("user_sub") or "").strip()
        if not user_sub or user_sub.startswith("invite-"):
            continue
        if user_sub == actor:
            continue
        if str(row.get("status") or "ACTIVE").upper() != "ACTIVE":
            continue
        role = str(row.get("role") or "").upper()
        if role not in NOTIFICATION_ROLES:
            continue
        if role in {"OWNER", "ADMIN"}:
            owners_admins.append(user_sub)
        else:
            operators.append(user_sub)

    ordered = owners_admins + operators
    # Stable unique preserve order
    seen = set()
    unique = []
    for user_sub in ordered:
        if user_sub in seen:
            continue
        seen.add(user_sub)
        unique.append(user_sub)
    return unique[:FANOUT_CAP]


def _copy_for(event_code, payload):
    title, body = COPY.get(event_code, ("Exchange update", "An exchange event occurred."))
    if event_code == EVENT_RESERVATION_EXPIRED:
        name = str((payload or {}).get("resource_name") or "").strip()
        if name:
            body = f"{name} is available again. Its reservation expired."
        return title, body
    type_name = str((payload or {}).get("resource_type_name") or "").strip()
    quantity = (payload or {}).get("quantity")
    destination_mode = str((payload or {}).get("destination_mode") or "").strip().upper()
    if event_code == EVENT_OFFER_RECEIVED and type_name:
        if quantity is not None:
            body = f"An organization offered {type_name} (qty {quantity})."
        else:
            body = f"An organization offered {type_name}."
    if event_code == EVENT_HANDOVER_COMPLETED and destination_mode == "CREATE":
        body = "Quantity handover completed and a new destination pool was created."
    elif event_code == EVENT_HANDOVER_COMPLETED and destination_mode == "MERGE":
        body = "Quantity handover completed and units merged into a destination pool."
    return title, body


def _identifier(value, prefix):
    text = str(value or "").strip()
    if text.startswith(prefix) and all(char not in text for char in ("/", "\\", " ", "#")):
        return text
    return ""


def _safe_payload(payload, event_code=""):
    raw = payload or {}
    if event_code == EVENT_RESERVATION_EXPIRED:
        out = {}
        resource_id = str(raw.get("resource_id") or "").strip()
        if resource_id and len(resource_id) <= 80 and "://" not in resource_id and "#" not in resource_id:
            out["resource_id"] = resource_id
        name = str(raw.get("resource_name") or "").strip()
        if name and "://" not in name and "#" not in name:
            out["resource_name"] = name[:80]
        out["href_kind"] = "resource"
        return out
    out = {}
    request_id = _identifier(raw.get("exchange_request_id"), "EXREQ-")
    offer_id = _identifier(raw.get("offer_id"), "EXOFF-")
    if request_id:
        out["exchange_request_id"] = request_id
    if offer_id:
        out["offer_id"] = offer_id
    type_name = str(raw.get("resource_type_name") or "").strip()
    if type_name and "://" not in type_name:
        out["resource_type_name"] = type_name[:80]
    quantity = raw.get("quantity")
    if isinstance(quantity, int) and not isinstance(quantity, bool):
        out["quantity"] = quantity
    destination_mode = str(raw.get("destination_mode") or "").strip().upper()
    if destination_mode in {"CREATE", "MERGE"}:
        out["destination_mode"] = destination_mode
    destination_resource_id = str(raw.get("destination_resource_id") or "").strip()
    if destination_resource_id and "://" not in destination_resource_id and "#" not in destination_resource_id:
        out["destination_resource_id"] = destination_resource_id[:80]
    tracking_mode = str(raw.get("tracking_mode") or "").strip().upper()
    if tracking_mode in {"INDIVIDUAL", "QUANTITY"}:
        out["tracking_mode"] = tracking_mode
    out["href_kind"] = "exchange_request"
    return out


def emit_notification_event(
    *,
    event_code,
    subject_id,
    recipient_organization_id,
    actor_sub,
    source_organization_id="",
    payload=None,
    table=None,
    members=None,
    organizations=None,
):
    """Persist EVENT + fan-out INBOX rows. Never raises to callers."""
    try:
        _emit_notification_event(
            event_code=event_code,
            subject_id=subject_id,
            recipient_organization_id=recipient_organization_id,
            actor_sub=actor_sub,
            source_organization_id=source_organization_id,
            payload=payload,
            table=table,
            members=members,
            organizations=organizations,
        )
    except Exception as error:
        error_code = ""
        if isinstance(error, ClientError):
            error_code = str((error.response.get("Error") or {}).get("Code") or "")
        from observability import log_event

        log_event(
            "ERROR",
            "notifications",
            "notification_emit",
            "failed",
            event="NOTIFICATION_EMIT_FAILED",
            event_code=event_code,
            subject_id=str(subject_id or ""),
            organization_id=str(recipient_organization_id or ""),
            request_id=current_request_id(),
            error=error.__class__.__name__,
            error_code=error_code,
        )


def _emit_notification_event(
    *,
    event_code,
    subject_id,
    recipient_organization_id,
    actor_sub,
    source_organization_id="",
    payload=None,
    table=None,
    members=None,
    organizations=None,
):
    org_id = str(recipient_organization_id or "").strip()
    subject = str(subject_id or "").strip()
    code = str(event_code or "").strip()
    if not org_id or not subject or not code:
        return

    table = table or notifications_table()
    eid = event_id_for(code, subject, org_id)
    created_at = now_iso()
    expires_at = ttl_epoch(created_at)
    safe_payload = _safe_payload(payload, code)
    title, body = _copy_for(code, safe_payload)
    request_id = current_request_id()

    event_item = {
        "pk": event_pk(eid),
        "sk": "META",
        "entity_type": "NOTIFICATION_EVENT",
        "event_id": eid,
        "event_code": code,
        "subject_id": subject,
        "organization_id": org_id,
        "source_organization_id": str(source_organization_id or "").strip(),
        "actor_sub": str(actor_sub or "").strip(),
        "created_at": created_at,
        "expires_at": expires_at,
        "request_id": request_id,
        "payload": safe_payload,
        "title": title,
        "body": body,
        "fanout_status": "PENDING",
        "schema_version": SCHEMA_VERSION,
    }

    created_new = False
    try:
        table.put_item(
            Item=event_item,
            ConditionExpression="attribute_not_exists(pk) AND attribute_not_exists(sk)",
        )
        created_new = True
    except ClientError as error:
        if error.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
        existing = table.get_item(Key={"pk": event_pk(eid), "sk": "META"}).get("Item") or {}
        if not existing:
            raise
        created_at = existing.get("created_at") or created_at
        expires_at = existing.get("expires_at") or ttl_epoch(created_at)
        title = existing.get("title") or title
        body = existing.get("body") or body
        safe_payload = existing.get("payload") or safe_payload
        if str(existing.get("fanout_status") or "").upper() == "COMPLETE":
            from observability import log_event

            log_event(
                "WARNING",
                "notifications",
                "notification_emit",
                "idempotent_complete",
                notification_event_id=eid,
                event_code=code,
                organization_id=org_id,
                request_id=request_id,
                recipient_count=0,
            )
            return

    recipients = resolve_recipients(
        org_id, actor_sub, members=members, organizations=organizations
    )
    written = 0
    for user_sub in recipients:
        inbox = {
            "pk": inbox_pk(org_id, user_sub),
            "sk": inbox_sk(created_at, eid),
            "entity_type": "NOTIFICATION",
            "notification_id": inbox_notification_id(eid, user_sub),
            "event_id": eid,
            "event_code": code,
            "organization_id": org_id,
            "user_sub": user_sub,
            "title": title,
            "body": body,
            "created_at": created_at,
            "read_at": "",
            "unread_key": unread_key_for(org_id, user_sub),
            "expires_at": expires_at,
            "payload": safe_payload,
            "href_kind": safe_payload.get("href_kind") or "exchange_request",
            "href_exchange_request_id": safe_payload.get("exchange_request_id") or "",
            "href_offer_id": safe_payload.get("offer_id") or "",
            "schema_version": SCHEMA_VERSION,
        }
        try:
            table.put_item(
                Item=inbox,
                ConditionExpression="attribute_not_exists(pk) AND attribute_not_exists(sk)",
            )
            written += 1
        except ClientError as error:
            if error.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise

    table.update_item(
        Key={"pk": event_pk(eid), "sk": "META"},
        UpdateExpression="SET fanout_status = :complete, recipient_count = :count",
        ExpressionAttributeValues={":complete": "COMPLETE", ":count": written},
    )

    from observability import log_event

    log_event(
        "INFO" if created_new else "WARNING",
        "notifications",
        "notification_emit",
        "created" if created_new else "idempotent_fanout",
        notification_event_id=eid,
        event_code=code,
        organization_id=org_id,
        request_id=request_id,
        recipient_count=written,
    )


def emit_for_orgs(
    *,
    event_code,
    subject_id,
    recipient_organization_ids,
    actor_sub,
    source_organization_id="",
    payload=None,
    table=None,
    members=None,
    organizations=None,
):
    seen = set()
    for org_id in recipient_organization_ids or []:
        org = str(org_id or "").strip()
        if not org or org in seen:
            continue
        seen.add(org)
        emit_notification_event(
            event_code=event_code,
            subject_id=subject_id,
            recipient_organization_id=org,
            actor_sub=actor_sub,
            source_organization_id=source_organization_id,
            payload=payload,
            table=table,
            members=members,
            organizations=organizations,
        )


def public_notification_view(item):
    read_at = item.get("read_at") or None
    if read_at == "":
        read_at = None
    payload = item.get("payload") or {}
    request_id = _identifier(
        item.get("href_exchange_request_id") or payload.get("exchange_request_id"),
        "EXREQ-",
    )
    offer_id = _identifier(item.get("href_offer_id") or payload.get("offer_id"), "EXOFF-")
    return {
        "notification_id": item.get("notification_id"),
        "event_code": item.get("event_code"),
        "title": item.get("title") or "",
        "body": item.get("body") or "",
        "created_at": item.get("created_at") or "",
        "read_at": read_at,
        "href": {
            "kind": "exchange_request",
            "exchange_request_id": request_id,
            "offer_id": offer_id,
        },
    }
