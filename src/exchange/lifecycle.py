"""Resource Exchange lifecycle recovery (Phase 7A).

Cancel / reject / withdraw / expire + EXCHANGE hold release.
Does not transfer ownership. Does not touch EMERGENCY or EVERYDAY allocations.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from botocore.exceptions import ClientError

from access import AccessError
from audit import build_audit_event, record_audit
from exchange_model import (
    DEFAULT_HANDOVER_TTL_HOURS,
    DEFAULT_REQUEST_TTL_DAYS,
    EXCHANGE_WRITE_ROLES,
    EXPIRY_DUE_PARTITION,
    GSI_EXPIRY_DUE_AT,
    GSI_EXPIRY_DUE_KEY,
    INDEX_EXPIRY_DUE,
    assert_provider_organization,
    assert_requester_organization,
    exchange_allocation_id,
    meta_pk,
    meta_sk,
    offer_sk,
)
from exchange_state import (
    ALLOCATION_TYPE_EXCHANGE,
    EXCHANGE_ALLOCATION_STATUS_OPEN,
    EXCHANGE_ALLOCATION_STATUS_RELEASED,
)
from resource_state import normalize_tracking_mode


def _svc():
    """Late import to avoid circular import at module load."""
    import service

    return service


def default_request_expires_at(now=None):
    clock = now or datetime.now(timezone.utc)
    return (clock + timedelta(days=DEFAULT_REQUEST_TTL_DAYS)).isoformat()


def default_handover_expires_at(now=None):
    clock = now or datetime.now(timezone.utc)
    return (clock + timedelta(hours=DEFAULT_HANDOVER_TTL_HOURS)).isoformat()


def parse_utc(value):
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def is_past_due(due_at, now=None):
    due = parse_utc(due_at)
    if due is None:
        return False
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)
    return due <= clock.astimezone(timezone.utc)


def _table_name(table, fallback):
    return getattr(table, "table_name", None) or getattr(table, "name", fallback)


def _cancel_open_offers(request_id, now, actor_sub, *, terminal="CANCELLED"):
    service = _svc()
    cancelled = 0
    for offer in service._list_open_offers(request_id):
        oid = offer.get("offer_id")
        try:
            service.exchanges_table().update_item(
                Key={"pk": meta_pk(request_id), "sk": offer_sk(oid)},
                UpdateExpression=(
                    "SET #status = :terminal, updated_at = :now, updated_by = :actor "
                    f"REMOVE {GSI_EXPIRY_DUE_KEY}, {GSI_EXPIRY_DUE_AT}"
                ),
                ConditionExpression="#status = :open",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":terminal": terminal,
                    ":open": "OPEN",
                    ":now": now,
                    ":actor": actor_sub,
                },
            )
            cancelled += 1
        except ClientError as error:
            if error.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
    return cancelled


def _hold_release_transact_items(
    *,
    resources_name,
    allocations_name,
    resource_id,
    provider_org,
    mode,
    quantity,
    allocation_id,
    now,
    actor_sub,
):
    service = _svc()
    items = []

    if mode == "INDIVIDUAL":
        items.append(
            {
                "Update": {
                    "TableName": resources_name,
                    "Key": service._serialize_map({"resource_id": resource_id}),
                    "UpdateExpression": (
                        "SET #a = :true, operational_status = :available, updated_at = :now"
                    ),
                    "ConditionExpression": (
                        "organization_id = :provider AND operational_status = :allocated "
                        "AND #a = :false"
                    ),
                    "ExpressionAttributeNames": {"#a": "Available"},
                    "ExpressionAttributeValues": service._serialize_map(
                        {
                            ":true": True,
                            ":false": False,
                            ":available": "AVAILABLE",
                            ":allocated": "ALLOCATED",
                            ":provider": provider_org,
                            ":now": now,
                        }
                    ),
                }
            }
        )
    else:
        items.append(
            {
                "Update": {
                    "TableName": resources_name,
                    "Key": service._serialize_map({"resource_id": resource_id}),
                    "UpdateExpression": (
                        "SET quantity_available = quantity_available + :qty, "
                        "quantity_allocated = quantity_allocated - :qty, updated_at = :now"
                    ),
                    "ConditionExpression": (
                        "organization_id = :provider AND tracking_mode = :quantity "
                        "AND quantity_allocated >= :qty"
                    ),
                    "ExpressionAttributeValues": service._serialize_map(
                        {
                            ":qty": quantity,
                            ":provider": provider_org,
                            ":quantity": "QUANTITY",
                            ":now": now,
                        }
                    ),
                }
            }
        )

    items.append(
        {
            "Update": {
                "TableName": allocations_name,
                "Key": service._serialize_map({"allocation_id": allocation_id}),
                "UpdateExpression": (
                    "SET #status = :released, released_at = :now, updated_at = :now, "
                    "released_by = :actor"
                ),
                "ConditionExpression": (
                    "attribute_exists(allocation_id) AND #status = :open "
                    "AND allocation_type = :exchange"
                ),
                "ExpressionAttributeNames": {"#status": "status"},
                "ExpressionAttributeValues": service._serialize_map(
                    {
                        ":released": EXCHANGE_ALLOCATION_STATUS_RELEASED,
                        ":open": EXCHANGE_ALLOCATION_STATUS_OPEN,
                        ":exchange": ALLOCATION_TYPE_EXCHANGE,
                        ":now": now,
                        ":actor": actor_sub,
                    }
                ),
            }
        }
    )
    return items


def _write_hold_release_history(
    *,
    resource_id,
    provider_org,
    resource,
    mode,
    quantity,
    allocation_id,
    request_id,
    offer_id,
    now,
    reason="RESOURCE_EXCHANGE_HOLD_RELEASED",
):
    service = _svc()
    history = {
        "history_id": "HIST-EXCHANGE-REL-" + offer_id + "-" + resource_id,
        "resource_id": resource_id,
        "organization_id": provider_org,
        "location_id": (resource or {}).get("location_id") or "",
        "resource_type": (resource or {}).get("Type") or "",
        "location": (resource or {}).get("Location") or "",
        "previous_status": "ALLOCATED" if mode == "INDIVIDUAL" else "AVAILABLE",
        "new_status": "AVAILABLE",
        "changed_at": now,
        "reason": reason,
        "allocation_id": allocation_id,
        "exchange_request_id": request_id,
        "offer_id": offer_id,
    }
    if mode == "QUANTITY":
        history["quantity"] = quantity
        history["history_id"] = "HIST-EXCHANGE-QTY-REL-" + offer_id + "-" + resource_id
    try:
        service.history_table().put_item(
            Item=history,
            ConditionExpression="attribute_not_exists(history_id)",
        )
    except ClientError:
        pass


def _release_already_done(allocation):
    return (
        allocation
        and allocation.get("allocation_type") == ALLOCATION_TYPE_EXCHANGE
        and str(allocation.get("status", "")).upper() == EXCHANGE_ALLOCATION_STATUS_RELEASED
    )


def cancel_exchange_request(exchange_request_id, organization_id, actor_sub, actor_role, membership):
    """Requester cancel for OPEN / ACCEPTED / TRANSFER_PENDING."""
    service = _svc()
    if actor_role not in EXCHANGE_WRITE_ROLES:
        raise AccessError(403, "You are not allowed to perform this action")

    service._require_active_organization(organization_id)
    meta = service._get_meta(exchange_request_id)
    assert_requester_organization(membership, meta)
    status = str(meta.get("status", "")).upper()
    request_id = meta["exchange_request_id"]
    now = service._now_iso()

    if status == "CANCELLED":
        return {
            "message": "Exchange request already cancelled",
            "request": service.requester_view(meta),
            "ownership_transferred": False,
        }

    if status in {"COMPLETED", "EXPIRED"}:
        raise service.ExchangeOperationError(409, "Exchange request cannot be cancelled")

    if status == "OPEN":
        exchanges_name = _table_name(service.exchanges_table(), "ResourceExchanges")
        try:
            service._transact_write(
                [
                    {
                        "Update": {
                            "TableName": exchanges_name,
                            "Key": service._serialize_map(
                                {"pk": meta_pk(request_id), "sk": meta_sk()}
                            ),
                            "UpdateExpression": (
                                "SET #status = :cancelled, updated_at = :now, updated_by = :actor "
                                "REMOVE network_list_key, "
                                f"{GSI_EXPIRY_DUE_KEY}, {GSI_EXPIRY_DUE_AT}"
                            ),
                            "ConditionExpression": "#status = :open",
                            "ExpressionAttributeNames": {"#status": "status"},
                            "ExpressionAttributeValues": service._serialize_map(
                                {
                                    ":cancelled": "CANCELLED",
                                    ":open": "OPEN",
                                    ":now": now,
                                    ":actor": actor_sub,
                                }
                            ),
                        }
                    }
                ]
            )
        except ClientError as error:
            code = error.response["Error"]["Code"]
            if code in {"TransactionCanceledException", "ConditionalCheckFailedException"}:
                latest = service._get_meta(request_id)
                if str(latest.get("status", "")).upper() == "CANCELLED":
                    return {
                        "message": "Exchange request already cancelled",
                        "request": service.requester_view(latest),
                        "ownership_transferred": False,
                    }
                raise service.ExchangeOperationError(409, "Exchange state conflict") from error
            raise

        _cancel_open_offers(request_id, now, actor_sub, terminal="CANCELLED")
        record_audit(
            service.audit_table(),
            build_audit_event(
                organization_id,
                actor_sub,
                actor_role,
                "exchange.request_cancelled",
                "exchange_request",
                request_id,
                location_id=meta.get("destination_location_id") or "",
                metadata={"previous_status": "OPEN"},
            ),
        )
        return {
            "message": "Exchange request cancelled",
            "request": service.requester_view(service._get_meta(request_id)),
            "ownership_transferred": False,
        }

    if status not in {"ACCEPTED", "TRANSFER_PENDING"}:
        raise service.ExchangeOperationError(409, "Exchange request cannot be cancelled")

    return _cancel_with_hold_release(
        meta,
        organization_id,
        actor_sub,
        actor_role,
        expected_status=status,
        terminal_request="CANCELLED",
        terminal_offer="CANCELLED",
        audit_action="exchange.request_cancelled",
        message="Exchange request cancelled",
        idempotent_message="Exchange request already cancelled",
    )


def _cancel_with_hold_release(
    meta,
    organization_id,
    actor_sub,
    actor_role,
    *,
    expected_status,
    terminal_request,
    terminal_offer,
    audit_action,
    message,
    idempotent_message,
    history_reason="RESOURCE_EXCHANGE_HOLD_RELEASED",
):
    service = _svc()
    request_id = meta["exchange_request_id"]
    offer, allocation = service._accepted_context(meta)
    oid = offer["offer_id"]
    allocation_id = allocation["allocation_id"]
    provider_org = str(
        meta.get("accepted_provider_organization_id")
        or offer.get("provider_organization_id")
        or ""
    ).strip()
    resource_id = str(meta.get("accepted_resource_id") or offer.get("resource_id") or "").strip()
    resource = service.resources_table().get_item(Key={"resource_id": resource_id}).get("Item")
    mode = normalize_tracking_mode(
        (resource or {}).get("tracking_mode") or meta.get("tracking_mode")
    )
    quantity = int(offer.get("quantity_offered") or allocation.get("quantity") or 1)
    now = service._now_iso()

    if str(meta.get("status", "")).upper() == terminal_request and _release_already_done(allocation):
        return {
            "message": idempotent_message,
            "request": service.requester_view(meta),
            "offer": service.requester_offer_view(offer),
            "allocation": service._allocation_view(allocation),
            "ownership_transferred": False,
        }

    if (
        allocation.get("allocation_type") != ALLOCATION_TYPE_EXCHANGE
        or str(allocation.get("status", "")).upper() not in {
            EXCHANGE_ALLOCATION_STATUS_OPEN,
            EXCHANGE_ALLOCATION_STATUS_RELEASED,
        }
    ):
        raise service.ExchangeOperationError(409, "Exchange allocation is not open")

    exchanges_name = _table_name(service.exchanges_table(), "ResourceExchanges")
    resources_name = _table_name(service.resources_table(), "Resources")
    allocations_name = _table_name(service.allocations_table(), "Allocations")

    transact_items = [
        {
            "Update": {
                "TableName": exchanges_name,
                "Key": service._serialize_map({"pk": meta_pk(request_id), "sk": meta_sk()}),
                "UpdateExpression": (
                    "SET #status = :terminal, updated_at = :now, updated_by = :actor "
                    "REMOVE network_list_key, "
                    f"{GSI_EXPIRY_DUE_KEY}, {GSI_EXPIRY_DUE_AT}"
                ),
                "ConditionExpression": "#status = :expected",
                "ExpressionAttributeNames": {"#status": "status"},
                "ExpressionAttributeValues": service._serialize_map(
                    {
                        ":terminal": terminal_request,
                        ":expected": expected_status,
                        ":now": now,
                        ":actor": actor_sub,
                    }
                ),
            }
        },
        {
            "Update": {
                "TableName": exchanges_name,
                "Key": service._serialize_map({"pk": meta_pk(request_id), "sk": offer_sk(oid)}),
                "UpdateExpression": (
                    "SET #status = :terminal, updated_at = :now, updated_by = :actor "
                    f"REMOVE {GSI_EXPIRY_DUE_KEY}, {GSI_EXPIRY_DUE_AT}"
                ),
                "ConditionExpression": "#status = :accepted",
                "ExpressionAttributeNames": {"#status": "status"},
                "ExpressionAttributeValues": service._serialize_map(
                    {
                        ":terminal": terminal_offer,
                        ":accepted": "ACCEPTED",
                        ":now": now,
                        ":actor": actor_sub,
                    }
                ),
            }
        },
    ]

    if str(allocation.get("status", "")).upper() == EXCHANGE_ALLOCATION_STATUS_OPEN:
        if not resource or resource.get("organization_id") != provider_org:
            raise service.ExchangeOperationError(409, "Offered resource is no longer eligible")
        transact_items.extend(
            _hold_release_transact_items(
                resources_name=resources_name,
                allocations_name=allocations_name,
                resource_id=resource_id,
                provider_org=provider_org,
                mode=mode,
                quantity=quantity,
                allocation_id=allocation_id,
                now=now,
                actor_sub=actor_sub,
            )
        )

    try:
        service._transact_write(transact_items)
    except ClientError as error:
        code = error.response["Error"]["Code"]
        if code in {"TransactionCanceledException", "ConditionalCheckFailedException"}:
            latest = service._get_meta(request_id)
            if str(latest.get("status", "")).upper() == terminal_request:
                offer_row, allocation_row = service._accepted_context(latest)
                return {
                    "message": idempotent_message,
                    "request": service.requester_view(latest),
                    "offer": service.requester_offer_view(offer_row),
                    "allocation": service._allocation_view(allocation_row),
                    "ownership_transferred": False,
                }
            raise service.ExchangeOperationError(409, "Exchange state conflict") from error
        raise

    if str(allocation.get("status", "")).upper() == EXCHANGE_ALLOCATION_STATUS_OPEN:
        _write_hold_release_history(
            resource_id=resource_id,
            provider_org=provider_org,
            resource=resource,
            mode=mode,
            quantity=quantity,
            allocation_id=allocation_id,
            request_id=request_id,
            offer_id=oid,
            now=now,
            reason=history_reason,
        )
        record_audit(
            service.audit_table(),
            build_audit_event(
                provider_org,
                actor_sub,
                actor_role,
                "resource.exchange_hold_released",
                "allocation",
                allocation_id,
                location_id=(resource or {}).get("location_id") or "",
                metadata={
                    "exchange_request_id": request_id,
                    "offer_id": oid,
                    "resource_id": resource_id,
                    "reason": history_reason,
                },
            ),
        )

    record_audit(
        service.audit_table(),
        build_audit_event(
            organization_id,
            actor_sub,
            actor_role,
            audit_action,
            "exchange_request",
            request_id,
            location_id=meta.get("destination_location_id") or "",
            metadata={
                "previous_status": expected_status,
                "offer_id": oid,
                "resource_id": resource_id,
                "ownership_transferred": False,
            },
        ),
    )

    latest = service._get_meta(request_id)
    offer_row, allocation_row = service._accepted_context(latest)
    return {
        "message": message,
        "request": service.requester_view(latest),
        "offer": service.requester_offer_view(offer_row),
        "allocation": service._allocation_view(allocation_row),
        "ownership_transferred": False,
    }


def reject_offer(exchange_request_id, offer_id, organization_id, actor_sub, actor_role, membership):
    """Requester rejects an OPEN offer on an OPEN request. No hold."""
    service = _svc()
    if actor_role not in EXCHANGE_WRITE_ROLES:
        raise AccessError(403, "You are not allowed to perform this action")

    service._require_active_organization(organization_id)
    meta = service._get_meta(exchange_request_id)
    assert_requester_organization(membership, meta)

    if str(meta.get("status", "")).upper() != "OPEN":
        raise service.ExchangeOperationError(409, "Exchange request is not open")

    offer = service._get_offer(exchange_request_id, offer_id)
    status = str(offer.get("status", "")).upper()
    request_id = meta["exchange_request_id"]
    oid = offer["offer_id"]
    now = service._now_iso()

    if status == "REJECTED":
        return {
            "message": "Offer already rejected",
            "offer": service.requester_offer_view(offer),
            "request": service.requester_view(meta),
        }

    if status != "OPEN":
        raise service.ExchangeOperationError(409, "Offer is not open")

    if offer.get("provider_organization_id") == organization_id:
        raise service.ExchangeOperationError(403, "Cannot reject your own organization's offer")

    try:
        service.exchanges_table().update_item(
            Key={"pk": meta_pk(request_id), "sk": offer_sk(oid)},
            UpdateExpression=(
                "SET #status = :rejected, updated_at = :now, updated_by = :actor "
                f"REMOVE {GSI_EXPIRY_DUE_KEY}, {GSI_EXPIRY_DUE_AT}"
            ),
            ConditionExpression="#status = :open",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":rejected": "REJECTED",
                ":open": "OPEN",
                ":now": now,
                ":actor": actor_sub,
            },
        )
    except ClientError as error:
        if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
            latest = service._get_offer(request_id, oid)
            if str(latest.get("status", "")).upper() == "REJECTED":
                return {
                    "message": "Offer already rejected",
                    "offer": service.requester_offer_view(latest),
                    "request": service.requester_view(service._get_meta(request_id)),
                }
            raise service.ExchangeOperationError(409, "Offer state conflict") from error
        raise

    record_audit(
        service.audit_table(),
        build_audit_event(
            organization_id,
            actor_sub,
            actor_role,
            "exchange.offer_rejected",
            "exchange_offer",
            oid,
            location_id=meta.get("destination_location_id") or "",
            metadata={
                "exchange_request_id": request_id,
                "provider_organization_id": offer.get("provider_organization_id"),
            },
        ),
    )
    return {
        "message": "Offer rejected",
        "offer": service.requester_offer_view(service._get_offer(request_id, oid)),
        "request": service.requester_view(service._get_meta(request_id)),
    }


def withdraw_offer(exchange_request_id, offer_id, organization_id, actor_sub, actor_role, membership):
    """Provider withdraws an OPEN offer. Never undoes an accepted hold."""
    service = _svc()
    if actor_role not in EXCHANGE_WRITE_ROLES:
        raise AccessError(403, "You are not allowed to perform this action")

    service._require_active_organization(organization_id)
    meta = service._get_meta(exchange_request_id)
    offer = service._get_offer(exchange_request_id, offer_id)
    assert_provider_organization(membership, offer)

    status = str(offer.get("status", "")).upper()
    request_id = meta["exchange_request_id"]
    oid = offer["offer_id"]
    now = service._now_iso()

    if status == "WITHDRAWN":
        return {
            "message": "Offer already withdrawn",
            "offer": service.provider_offer_view(offer),
            "request": service.requester_view(meta),
        }

    if status == "ACCEPTED":
        raise service.ExchangeOperationError(
            409, "Accepted offers cannot be withdrawn; cancel the exchange request instead"
        )

    if status != "OPEN":
        raise service.ExchangeOperationError(409, "Offer is not open")

    try:
        service.exchanges_table().update_item(
            Key={"pk": meta_pk(request_id), "sk": offer_sk(oid)},
            UpdateExpression=(
                "SET #status = :withdrawn, updated_at = :now, updated_by = :actor "
                f"REMOVE {GSI_EXPIRY_DUE_KEY}, {GSI_EXPIRY_DUE_AT}"
            ),
            ConditionExpression="#status = :open",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":withdrawn": "WITHDRAWN",
                ":open": "OPEN",
                ":now": now,
                ":actor": actor_sub,
            },
        )
    except ClientError as error:
        if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
            latest = service._get_offer(request_id, oid)
            latest_status = str(latest.get("status", "")).upper()
            if latest_status == "WITHDRAWN":
                return {
                    "message": "Offer already withdrawn",
                    "offer": service.provider_offer_view(latest),
                    "request": service.requester_view(service._get_meta(request_id)),
                }
            if latest_status == "ACCEPTED":
                raise service.ExchangeOperationError(
                    409, "Accepted offers cannot be withdrawn; cancel the exchange request instead"
                ) from error
            raise service.ExchangeOperationError(409, "Offer state conflict") from error
        raise

    record_audit(
        service.audit_table(),
        build_audit_event(
            organization_id,
            actor_sub,
            actor_role,
            "exchange.offer_withdrawn",
            "exchange_offer",
            oid,
            location_id=offer.get("source_location_id") or "",
            metadata={
                "exchange_request_id": request_id,
                "resource_id": offer.get("resource_id"),
            },
        ),
    )
    return {
        "message": "Offer withdrawn",
        "offer": service.provider_offer_view(service._get_offer(request_id, oid)),
        "request": service.requester_view(service._get_meta(request_id)),
    }


def expire_open_request(meta, *, now=None, actor_sub="system:exchange-expiry", actor_role="SYSTEM"):
    service = _svc()
    request_id = meta["exchange_request_id"]
    clock = now or service._now_iso()
    status = str(meta.get("status", "")).upper()

    if status == "EXPIRED":
        return {"outcome": "idempotent", "request_id": request_id}

    if status != "OPEN":
        return {"outcome": "skipped", "request_id": request_id}

    if not is_past_due(meta.get("expires_at"), parse_utc(clock)):
        return {"outcome": "skipped", "request_id": request_id}

    exchanges_name = _table_name(service.exchanges_table(), "ResourceExchanges")
    try:
        service._transact_write(
            [
                {
                    "Update": {
                        "TableName": exchanges_name,
                        "Key": service._serialize_map({"pk": meta_pk(request_id), "sk": meta_sk()}),
                        "UpdateExpression": (
                            "SET #status = :expired, updated_at = :now, updated_by = :actor "
                            "REMOVE network_list_key, "
                            f"{GSI_EXPIRY_DUE_KEY}, {GSI_EXPIRY_DUE_AT}"
                        ),
                        "ConditionExpression": "#status = :open",
                        "ExpressionAttributeNames": {"#status": "status"},
                        "ExpressionAttributeValues": service._serialize_map(
                            {
                                ":expired": "EXPIRED",
                                ":open": "OPEN",
                                ":now": clock,
                                ":actor": actor_sub,
                            }
                        ),
                    }
                }
            ]
        )
    except ClientError as error:
        code = error.response["Error"]["Code"]
        if code in {"TransactionCanceledException", "ConditionalCheckFailedException"}:
            latest = service._get_meta(request_id)
            if str(latest.get("status", "")).upper() == "EXPIRED":
                return {"outcome": "idempotent", "request_id": request_id}
            return {"outcome": "conflict", "request_id": request_id}
        raise

    _cancel_open_offers(request_id, clock, actor_sub, terminal="EXPIRED")
    record_audit(
        service.audit_table(),
        build_audit_event(
            meta.get("requester_organization_id"),
            actor_sub,
            actor_role,
            "exchange.expired",
            "exchange_request",
            request_id,
            location_id=meta.get("destination_location_id") or "",
            metadata={"previous_status": "OPEN"},
        ),
    )
    return {"outcome": "expired", "request_id": request_id}


def expire_held_request(meta, *, now=None, actor_sub="system:exchange-expiry", actor_role="SYSTEM"):
    service = _svc()
    status = str(meta.get("status", "")).upper()
    request_id = meta["exchange_request_id"]
    clock = now or service._now_iso()

    if status == "EXPIRED":
        return {"outcome": "idempotent", "request_id": request_id}

    if status not in {"ACCEPTED", "TRANSFER_PENDING"}:
        return {"outcome": "skipped", "request_id": request_id}

    if not is_past_due(meta.get("handover_expires_at"), parse_utc(clock)):
        return {"outcome": "skipped", "request_id": request_id}

    try:
        _cancel_with_hold_release(
            meta,
            meta.get("requester_organization_id"),
            actor_sub,
            actor_role,
            expected_status=status,
            terminal_request="EXPIRED",
            terminal_offer="EXPIRED",
            audit_action="exchange.expired",
            message="Exchange request expired",
            idempotent_message="Exchange request already expired",
            history_reason="RESOURCE_EXCHANGE_HOLD_RELEASED",
        )
        return {"outcome": "expired", "request_id": request_id}
    except service.ExchangeOperationError:
        latest = service._get_meta(request_id)
        if str(latest.get("status", "")).upper() == "EXPIRED":
            return {"outcome": "idempotent", "request_id": request_id}
        return {"outcome": "conflict", "request_id": request_id}


def expire_open_offer(offer, *, now=None, actor_sub="system:exchange-expiry", actor_role="SYSTEM"):
    service = _svc()
    request_id = offer["exchange_request_id"]
    oid = offer["offer_id"]
    clock = now or service._now_iso()
    status = str(offer.get("status", "")).upper()

    if status == "EXPIRED":
        return {"outcome": "idempotent", "offer_id": oid}

    if status != "OPEN":
        return {"outcome": "skipped", "offer_id": oid}

    if not is_past_due(offer.get("expires_at"), parse_utc(clock)):
        return {"outcome": "skipped", "offer_id": oid}

    meta = service._get_meta(request_id)
    if str(meta.get("status", "")).upper() != "OPEN":
        return {"outcome": "skipped", "offer_id": oid}

    try:
        service.exchanges_table().update_item(
            Key={"pk": meta_pk(request_id), "sk": offer_sk(oid)},
            UpdateExpression=(
                "SET #status = :expired, updated_at = :now, updated_by = :actor "
                f"REMOVE {GSI_EXPIRY_DUE_KEY}, {GSI_EXPIRY_DUE_AT}"
            ),
            ConditionExpression="#status = :open",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":expired": "EXPIRED",
                ":open": "OPEN",
                ":now": clock,
                ":actor": actor_sub,
            },
        )
    except ClientError as error:
        if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
            latest = service._get_offer(request_id, oid)
            if str(latest.get("status", "")).upper() == "EXPIRED":
                return {"outcome": "idempotent", "offer_id": oid}
            return {"outcome": "conflict", "offer_id": oid}
        raise

    record_audit(
        service.audit_table(),
        build_audit_event(
            offer.get("provider_organization_id"),
            actor_sub,
            actor_role,
            "exchange.expired",
            "exchange_offer",
            oid,
            location_id=offer.get("source_location_id") or "",
            metadata={"exchange_request_id": request_id, "previous_status": "OPEN"},
        ),
    )
    return {"outcome": "expired", "offer_id": oid}


def apply_lazy_expiry(meta_or_none, *, now=None):
    """Expire a META row on the read/write path when due. Returns refreshed META or None."""
    if not meta_or_none:
        return meta_or_none
    service = _svc()
    status = str(meta_or_none.get("status", "")).upper()
    clock = now or service._now_iso()

    if status == "OPEN" and is_past_due(meta_or_none.get("expires_at"), parse_utc(clock)):
        expire_open_request(meta_or_none, now=clock)
        return service._get_meta(meta_or_none["exchange_request_id"], apply_expiry=False)

    if status in {"ACCEPTED", "TRANSFER_PENDING"} and is_past_due(
        meta_or_none.get("handover_expires_at"), parse_utc(clock)
    ):
        expire_held_request(meta_or_none, now=clock)
        return service._get_meta(meta_or_none["exchange_request_id"], apply_expiry=False)

    return meta_or_none


def query_due_exchange_items(now_text):
    """Query ExpiryDueIndex for all items with expiry_due_at <= now (outage-safe)."""
    service = _svc()
    from boto3.dynamodb.conditions import Key

    items = []
    start_key = None
    while True:
        kwargs = {
            "IndexName": INDEX_EXPIRY_DUE,
            "KeyConditionExpression": (
                Key(GSI_EXPIRY_DUE_KEY).eq(EXPIRY_DUE_PARTITION)
                & Key(GSI_EXPIRY_DUE_AT).lte(now_text)
            ),
        }
        if start_key:
            kwargs["ExclusiveStartKey"] = start_key
        result = service.exchanges_table().query(**kwargs)
        items.extend(result.get("Items") or [])
        start_key = result.get("LastEvaluatedKey")
        if not start_key:
            return items


def run_expiry(now=None, invocation_id=""):
    service = _svc()
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)
    clock = clock.astimezone(timezone.utc)
    now_text = clock.isoformat()
    counts = {"expired": 0, "idempotent": 0, "skipped": 0, "conflict": 0}

    for item in query_due_exchange_items(now_text):
        entity = str(item.get("entity_type") or "").upper()
        if entity == "EXCHANGE_REQUEST" or item.get("sk") == "META":
            status = str(item.get("status", "")).upper()
            if status == "OPEN":
                outcome = expire_open_request(item, now=now_text)
            else:
                outcome = expire_held_request(item, now=now_text)
        elif entity == "EXCHANGE_OFFER" or str(item.get("sk", "")).startswith("OFFER#"):
            outcome = expire_open_offer(item, now=now_text)
        else:
            outcome = {"outcome": "skipped"}
        counts[outcome.get("outcome", "skipped")] = counts.get(outcome.get("outcome", "skipped"), 0) + 1

    print(
        json_log(
            invocation_id,
            now_text,
            counts,
        )
    )
    return counts


def json_log(invocation_id, now_text, counts):
    import json

    return json.dumps(
        {
            "exchange_expiry": True,
            "invocation_id": invocation_id,
            "now": now_text,
            "counts": counts,
        },
        default=str,
    )
