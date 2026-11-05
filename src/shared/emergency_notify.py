"""Post-commit emergency request and allocation inbox events.

Call only after the business write has succeeded. Notification failures
must not undo that write.
"""

from notifications import (
    EVENT_EMERGENCY_ALLOCATION_CREATED,
    EVENT_EMERGENCY_ALLOCATION_PARTIAL,
    EVENT_EMERGENCY_ALLOCATION_RELEASED,
    EVENT_EMERGENCY_DELIVERY_DISPATCHED,
    EVENT_EMERGENCY_DELIVERY_RECEIVED,
    EVENT_EMERGENCY_REQUEST_APPROVED,
    EVENT_EMERGENCY_REQUEST_CANCELLED,
    EVENT_EMERGENCY_REQUEST_COMPLETED,
    EVENT_EMERGENCY_REQUEST_CREATED,
    EVENT_EMERGENCY_REQUEST_REJECTED,
    emit_notification_event,
)


def _payload(**fields):
    return {key: value for key, value in fields.items() if value not in (None, "")}


def _emit(event_code, subject_id, organization_id, actor_sub, payload, table=None, members=None, organizations=None):
    emit_notification_event(
        event_code=event_code,
        subject_id=subject_id,
        recipient_organization_id=organization_id,
        actor_sub=actor_sub,
        source_organization_id=organization_id,
        payload=payload,
        table=table,
        members=members,
        organizations=organizations,
    )


def notify_request_created(
    *,
    organization_id,
    actor_sub,
    request_id,
    resource_type="",
    location="",
    table=None,
    members=None,
    organizations=None,
):
    _emit(
        EVENT_EMERGENCY_REQUEST_CREATED,
        request_id,
        organization_id,
        actor_sub,
        _payload(request_id=request_id, resource_type=resource_type, location=location),
        table=table,
        members=members,
        organizations=organizations,
    )


def notify_request_cancelled(
    *,
    organization_id,
    actor_sub,
    request_id,
    resource_type="",
    location="",
    table=None,
    members=None,
    organizations=None,
):
    _emit(
        EVENT_EMERGENCY_REQUEST_CANCELLED,
        request_id,
        organization_id,
        actor_sub,
        _payload(request_id=request_id, resource_type=resource_type, location=location),
        table=table,
        members=members,
        organizations=organizations,
    )


def _request_notice(event_code):
    def notify(
        *,
        organization_id,
        actor_sub,
        request_id,
        resource_type="",
        location="",
        table=None,
        members=None,
        organizations=None,
    ):
        _emit(
            event_code,
            request_id,
            organization_id,
            actor_sub,
            _payload(request_id=request_id, resource_type=resource_type, location=location),
            table=table,
            members=members,
            organizations=organizations,
        )

    return notify


notify_request_approved = _request_notice(EVENT_EMERGENCY_REQUEST_APPROVED)
notify_request_rejected = _request_notice(EVENT_EMERGENCY_REQUEST_REJECTED)
notify_request_completed = _request_notice(EVENT_EMERGENCY_REQUEST_COMPLETED)


def notify_delivery_event(
    event_code,
    *,
    organization_id,
    actor_sub,
    request_id,
    allocation_id="",
    resource_id="",
    table=None,
    members=None,
    organizations=None,
):
    _emit(
        event_code,
        allocation_id or request_id,
        organization_id,
        actor_sub,
        _payload(request_id=request_id, allocation_id=allocation_id, resource_id=resource_id),
        table=table,
        members=members,
        organizations=organizations,
    )


def notify_delivery_dispatched(**kwargs):
    notify_delivery_event(EVENT_EMERGENCY_DELIVERY_DISPATCHED, **kwargs)


def notify_delivery_received(**kwargs):
    notify_delivery_event(EVENT_EMERGENCY_DELIVERY_RECEIVED, **kwargs)


def notify_allocation_created(
    *,
    organization_id,
    actor_sub,
    request_id,
    allocation_id,
    resource_id="",
    resource_type="",
    location="",
    table=None,
    members=None,
    organizations=None,
):
    _emit(
        EVENT_EMERGENCY_ALLOCATION_CREATED,
        allocation_id,
        organization_id,
        actor_sub,
        _payload(
            request_id=request_id,
            allocation_id=allocation_id,
            resource_id=resource_id,
            resource_type=resource_type,
            location=location,
        ),
        table=table,
        members=members,
        organizations=organizations,
    )


def notify_allocation_partial(
    *,
    organization_id,
    actor_sub,
    request_id,
    allocation_id,
    resource_id="",
    resource_type="",
    location="",
    table=None,
    members=None,
    organizations=None,
):
    _emit(
        EVENT_EMERGENCY_ALLOCATION_PARTIAL,
        allocation_id,
        organization_id,
        actor_sub,
        _payload(
            request_id=request_id,
            allocation_id=allocation_id,
            resource_id=resource_id,
            resource_type=resource_type,
            location=location,
        ),
        table=table,
        members=members,
        organizations=organizations,
    )


def notify_allocation_released(
    *,
    organization_id,
    actor_sub,
    request_id,
    allocation_id,
    resource_id="",
    resource_type="",
    location="",
    table=None,
    members=None,
    organizations=None,
):
    _emit(
        EVENT_EMERGENCY_ALLOCATION_RELEASED,
        allocation_id,
        organization_id,
        actor_sub,
        _payload(
            request_id=request_id,
            allocation_id=allocation_id,
            resource_id=resource_id,
            resource_type=resource_type,
            location=location,
        ),
        table=table,
        members=members,
        organizations=organizations,
    )
