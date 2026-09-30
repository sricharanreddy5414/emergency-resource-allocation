"""Authenticated notification inbox API (Phase 8B)."""

from __future__ import annotations

import os

from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError

from access import AccessError
from notifications import (
    NOTIFICATION_ROLES,
    UNREAD_COUNT_CAP,
    inbox_pk,
    notifications_table,
    parse_notification_id,
    public_notification_view,
    unread_key_for,
)
from pages import decode_token, encode_token


class NotificationOperationError(Exception):
    def __init__(self, status_code, message):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def _now_iso():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _table(table=None):
    return table or notifications_table()


def _require_notification_role(membership):
    role = str((membership or {}).get("role") or "").upper()
    if role not in NOTIFICATION_ROLES:
        raise AccessError(403, "You are not allowed to perform this action")


def list_notifications(
    organization_id,
    user_sub,
    membership,
    *,
    limit=20,
    page_token=None,
    unread_only=False,
    table=None,
):
    _require_notification_role(membership)
    org_id = str(organization_id or "").strip()
    sub = str(user_sub or "").strip()
    if membership.get("organization_id") != org_id:
        raise AccessError(403, "Organization access denied")

    try:
        size = int(limit)
    except (TypeError, ValueError):
        raise NotificationOperationError(400, "limit must be an integer") from None
    if size < 1 or size > 50:
        raise NotificationOperationError(400, "limit must be between 1 and 50")

    exclusive = None
    if page_token:
        exclusive = decode_token(
            page_token, ["pk", "sk"] if not unread_only else ["unread_key", "created_at", "pk", "sk"]
        )
        expected_pk = inbox_pk(org_id, sub)
        if exclusive.get("pk") != expected_pk or not str(exclusive.get("sk") or "").startswith("AT#"):
            raise NotificationOperationError(400, "Invalid page token")
        if unread_only and exclusive.get("unread_key") != unread_key_for(org_id, sub):
            raise NotificationOperationError(400, "Invalid page token")

    store = _table(table)
    if unread_only:
        query = {
            "IndexName": "UnreadByUserIndex",
            "KeyConditionExpression": Key("unread_key").eq(unread_key_for(org_id, sub)),
            "ScanIndexForward": False,
            "Limit": size,
        }
        if exclusive:
            query["ExclusiveStartKey"] = exclusive
        result = store.query(**query)
        items = list(result.get("Items") or [])
        next_key = result.get("LastEvaluatedKey")
        token_keys = ["unread_key", "created_at", "pk", "sk"]
    else:
        query = {
            "KeyConditionExpression": Key("pk").eq(inbox_pk(org_id, sub))
            & Key("sk").begins_with("AT#"),
            "ScanIndexForward": False,
            "Limit": size,
        }
        if exclusive:
            query["ExclusiveStartKey"] = exclusive
        result = store.query(**query)
        items = [
            item
            for item in (result.get("Items") or [])
            if item.get("entity_type") == "NOTIFICATION"
        ]
        next_key = result.get("LastEvaluatedKey")
        token_keys = ["pk", "sk"]

    return {
        "organization_id": org_id,
        "notifications": [public_notification_view(item) for item in items],
        "next_page_token": encode_token(
            {key: next_key[key] for key in token_keys} if next_key else None
        ),
    }


def unread_count(organization_id, user_sub, membership, *, table=None, cap=UNREAD_COUNT_CAP):
    _require_notification_role(membership)
    org_id = str(organization_id or "").strip()
    sub = str(user_sub or "").strip()
    if membership.get("organization_id") != org_id:
        raise AccessError(403, "Organization access denied")

    store = _table(table)
    counted = 0
    start_key = None
    while counted <= cap:
        query = {
            "IndexName": "UnreadByUserIndex",
            "KeyConditionExpression": Key("unread_key").eq(unread_key_for(org_id, sub)),
            "Select": "COUNT",
            "Limit": min(50, cap + 1 - counted),
        }
        if start_key:
            query["ExclusiveStartKey"] = start_key
        result = store.query(**query)
        counted += int(result.get("Count") or 0)
        start_key = result.get("LastEvaluatedKey")
        if not start_key:
            break
        if counted > cap:
            break

    return {
        "organization_id": org_id,
        "unread_count": min(counted, cap),
        "capped": counted > cap,
    }


def _find_inbox_item(store, organization_id, user_sub, event_id):
    start_key = None
    while True:
        query = {
            "KeyConditionExpression": Key("pk").eq(inbox_pk(organization_id, user_sub))
            & Key("sk").begins_with("AT#"),
            "FilterExpression": Attr("event_id").eq(event_id),
            "Limit": 50,
        }
        if start_key:
            query["ExclusiveStartKey"] = start_key
        result = store.query(**query)
        for item in result.get("Items") or []:
            if item.get("entity_type") == "NOTIFICATION" and item.get("event_id") == event_id:
                return item
        start_key = result.get("LastEvaluatedKey")
        if not start_key:
            return None


def mark_read(organization_id, user_sub, membership, notification_id, *, table=None):
    _require_notification_role(membership)
    org_id = str(organization_id or "").strip()
    sub = str(user_sub or "").strip()
    if membership.get("organization_id") != org_id:
        raise AccessError(403, "Organization access denied")

    parsed = parse_notification_id(notification_id)
    if (
        not parsed
        or parsed["organization_id"] != org_id
        or parsed["user_sub"] != sub
    ):
        raise NotificationOperationError(404, "Notification not found")

    store = _table(table)
    item = _find_inbox_item(store, org_id, sub, parsed["event_id"])
    if not item:
        raise NotificationOperationError(404, "Notification not found")

    if item.get("read_at"):
        return {"message": "Notification already read", "notification": public_notification_view(item)}

    now = _now_iso()
    try:
        store.update_item(
            Key={"pk": item["pk"], "sk": item["sk"]},
            UpdateExpression="SET read_at = :now REMOVE unread_key",
            ConditionExpression="attribute_exists(pk) AND (attribute_not_exists(read_at) OR read_at = :empty)",
            ExpressionAttributeValues={":now": now, ":empty": ""},
        )
    except ClientError as error:
        if error.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
        latest = store.get_item(Key={"pk": item["pk"], "sk": item["sk"]}).get("Item") or item
        return {
            "message": "Notification already read",
            "notification": public_notification_view(latest),
        }

    latest = store.get_item(Key={"pk": item["pk"], "sk": item["sk"]}).get("Item") or {
        **item,
        "read_at": now,
        "unread_key": None,
    }
    return {"message": "Notification marked read", "notification": public_notification_view(latest)}


def mark_all_read(organization_id, user_sub, membership, *, table=None, max_items=500):
    """Mark unread inbox rows read via UnreadByUserIndex pages. No Scan."""
    _require_notification_role(membership)
    org_id = str(organization_id or "").strip()
    sub = str(user_sub or "").strip()
    if membership.get("organization_id") != org_id:
        raise AccessError(403, "Organization access denied")

    store = _table(table)
    now = _now_iso()
    marked = 0
    start_key = None
    pages = 0
    truncated = False
    while marked < max_items and pages < 20:
        pages += 1
        query = {
            "IndexName": "UnreadByUserIndex",
            "KeyConditionExpression": Key("unread_key").eq(unread_key_for(org_id, sub)),
            "Limit": 25,
        }
        if start_key:
            query["ExclusiveStartKey"] = start_key
        result = store.query(**query)
        items = list(result.get("Items") or [])
        next_key = result.get("LastEvaluatedKey")
        if next_key and next_key == start_key:
            truncated = True
            break
        if not items:
            start_key = next_key
            if not start_key:
                break
            continue
        for item in items:
            if marked >= max_items:
                truncated = True
                break
            if item.get("organization_id") != org_id or item.get("user_sub") != sub:
                continue
            try:
                store.update_item(
                    Key={"pk": item["pk"], "sk": item["sk"]},
                    UpdateExpression="SET read_at = :now REMOVE unread_key",
                    ConditionExpression="attribute_exists(unread_key)",
                    ExpressionAttributeValues={":now": now},
                )
                marked += 1
            except ClientError as error:
                if error.response["Error"]["Code"] != "ConditionalCheckFailedException":
                    raise
        if truncated:
            break
        start_key = next_key
        if not start_key:
            break
    else:
        if start_key:
            truncated = True

    return {
        "organization_id": org_id,
        "marked_read": marked,
        "truncated": truncated,
    }
