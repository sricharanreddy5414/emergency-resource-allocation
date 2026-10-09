"""Post-commit emergency request and allocation inbox events.

Call only after the business write has succeeded. Notification failures
must not undo that write.
"""

from notifications import (
    EVENT_EMERGENCY_ALLOCATION_CREATED,
    EVENT_EMERGENCY_ALLOCATION_RELEASED,
    EVENT_EMERGENCY_REQUEST_CANCELLED,
    EVENT_EMERGENCY_REQUEST_CREATED,
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
