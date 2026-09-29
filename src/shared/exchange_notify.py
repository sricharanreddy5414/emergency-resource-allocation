"""Post-commit Exchange → notification bridges (Phase 8B).

Call only after successful Exchange transitions. Never raises.
"""

from notifications import (
    EVENT_HANDOVER_COMPLETED,
    EVENT_OFFER_ACCEPTED,
    EVENT_OFFER_RECEIVED,
    EVENT_OFFER_REJECTED,
    EVENT_OFFER_SUPERSEDED,
    EVENT_OFFER_WITHDRAWN,
    EVENT_REQUEST_CANCELLED,
    EVENT_REQUEST_EXPIRED,
    EVENT_TRANSFER_STARTED,
    emit_for_orgs,
    emit_notification_event,
)


def _payload(meta=None, offer=None, **extra):
    meta = meta or {}
    offer = offer or {}
    data = {
        "exchange_request_id": meta.get("exchange_request_id") or extra.get("exchange_request_id") or "",
        "offer_id": (offer.get("offer_id") if offer else None) or extra.get("offer_id") or "",
        "resource_type_name": meta.get("resource_type_name") or "",
        "quantity": extra.get("quantity"),
        "destination_mode": extra.get("destination_mode") or "",
        "destination_resource_id": extra.get("destination_resource_id") or "",
        "tracking_mode": extra.get("tracking_mode")
        or meta.get("tracking_mode")
        or "",
        "href_kind": "exchange_request",
    }
    if data["quantity"] is None and offer.get("quantity_offered") is not None:
        data["quantity"] = offer.get("quantity_offered")
    return {key: value for key, value in data.items() if value not in (None, "")}


def notify_offer_received(meta, offer, actor_sub):
    requester = meta.get("requester_organization_id")
    emit_notification_event(
        event_code=EVENT_OFFER_RECEIVED,
        subject_id=meta.get("exchange_request_id"),
        recipient_organization_id=requester,
        actor_sub=actor_sub,
        source_organization_id=offer.get("provider_organization_id") or "",
        payload=_payload(meta, offer),
    )


def notify_offer_accepted(meta, offer, actor_sub):
    requester = meta.get("requester_organization_id")
    provider = offer.get("provider_organization_id") or meta.get(
        "accepted_provider_organization_id"
    )
    emit_for_orgs(
        event_code=EVENT_OFFER_ACCEPTED,
        subject_id=offer.get("offer_id"),
        recipient_organization_ids=[requester, provider],
        actor_sub=actor_sub,
        source_organization_id=requester or "",
        payload=_payload(meta, offer),
    )


def notify_offer_rejected(meta, offer, actor_sub):
    emit_notification_event(
        event_code=EVENT_OFFER_REJECTED,
        subject_id=offer.get("offer_id"),
        recipient_organization_id=offer.get("provider_organization_id"),
        actor_sub=actor_sub,
        source_organization_id=meta.get("requester_organization_id") or "",
        payload=_payload(meta, offer),
    )


def notify_offer_withdrawn(meta, offer, actor_sub):
    emit_notification_event(
        event_code=EVENT_OFFER_WITHDRAWN,
        subject_id=offer.get("offer_id"),
        recipient_organization_id=meta.get("requester_organization_id"),
        actor_sub=actor_sub,
        source_organization_id=offer.get("provider_organization_id") or "",
        payload=_payload(meta, offer),
    )


def notify_offer_superseded(meta, offer, actor_sub):
    emit_notification_event(
        event_code=EVENT_OFFER_SUPERSEDED,
        subject_id=offer.get("offer_id"),
        recipient_organization_id=offer.get("provider_organization_id"),
        actor_sub=actor_sub,
        source_organization_id=meta.get("requester_organization_id") or "",
        payload=_payload(meta, offer),
    )


def notify_transfer_started(meta, offer, actor_sub):
    requester = meta.get("requester_organization_id")
    provider = offer.get("provider_organization_id") or meta.get(
        "accepted_provider_organization_id"
    )
    emit_for_orgs(
        event_code=EVENT_TRANSFER_STARTED,
        subject_id=meta.get("exchange_request_id"),
        recipient_organization_ids=[requester, provider],
        actor_sub=actor_sub,
        source_organization_id=provider or "",
        payload=_payload(meta, offer),
    )


def notify_handover_completed(meta, offer, actor_sub, **extra):
    requester = meta.get("requester_organization_id")
    provider = offer.get("provider_organization_id") or meta.get(
        "accepted_provider_organization_id"
    )
    emit_for_orgs(
        event_code=EVENT_HANDOVER_COMPLETED,
        subject_id=meta.get("exchange_request_id"),
        recipient_organization_ids=[requester, provider],
        actor_sub=actor_sub,
        source_organization_id=requester or "",
        payload=_payload(meta, offer, **extra),
    )


def notify_request_cancelled(meta, actor_sub, *, previous_status=""):
    """Notify counterparty organization(s). OPEN cancel has no provider counterparty."""
    requester = meta.get("requester_organization_id")
    provider = meta.get("accepted_provider_organization_id")
    status = str(previous_status or meta.get("status") or "").upper()
    targets = []
    if status in {"ACCEPTED", "TRANSFER_PENDING"} and provider:
        # Actor is requester → notify provider. If somehow provider cancelled, notify requester.
        if actor_sub and provider:
            targets.append(provider)
        if requester and requester != provider:
            # Only add requester when they are not the sole actor-side and counterparty exists;
            # architecture: counterparty. Requester cancel → provider only.
            pass
        targets = [provider]
    elif status == "OPEN":
        # No counterparty for OPEN cancel.
        targets = []
    else:
        if provider:
            targets = [provider]
    for org in targets:
        emit_notification_event(
            event_code=EVENT_REQUEST_CANCELLED,
            subject_id=meta.get("exchange_request_id"),
            recipient_organization_id=org,
            actor_sub=actor_sub,
            source_organization_id=requester or "",
            payload=_payload(meta),
        )


def notify_request_expired(meta, actor_sub, *, previous_status=""):
    requester = meta.get("requester_organization_id")
    provider = meta.get("accepted_provider_organization_id")
    status = str(previous_status or "").upper()
    if status == "OPEN" or (not provider and status not in {"ACCEPTED", "TRANSFER_PENDING"}):
        targets = [requester]
    else:
        targets = [requester, provider]
    emit_for_orgs(
        event_code=EVENT_REQUEST_EXPIRED,
        subject_id=meta.get("exchange_request_id"),
        recipient_organization_ids=targets,
        actor_sub=actor_sub,
        source_organization_id=requester or "",
        payload=_payload(meta),
    )
