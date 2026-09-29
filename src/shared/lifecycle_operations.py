"""Advanced resource lifecycle: maintenance, damage, retire, assign, in-use."""

from datetime import datetime, timezone

from botocore.exceptions import ClientError

from audit import build_audit_event, record_audit
from resource_state import (
    ResourceStateError,
    available_for_status,
    effective_operational_status,
    normalize_condition,
    normalize_tracking_mode,
    quantity_snapshot,
    validate_transition,
)


class LifecycleOperationError(Exception):
    def __init__(self, status_code, message):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def _now():
    return datetime.now(timezone.utc).isoformat()


def _history_id(prefix, resource_id):
    return prefix + resource_id + "-" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")


def _resource_id(body):
    resource_id = str((body or {}).get("resource_id") or "").strip()
    if not resource_id:
        raise LifecycleOperationError(400, "resource_id is required")
    return resource_id


def _notes(body, limit=300):
    notes = str((body or {}).get("notes") or (body or {}).get("reason") or "").strip()
    if len(notes) > limit:
        raise LifecycleOperationError(400, "Notes are too long")
    return notes


def _conflict_from_client(error):
    if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
        raise LifecycleOperationError(409, "Resource state conflict") from error
    raise error


def _write_history(history_table, **fields):
    history_table.put_item(Item=fields)


def _active_allocations(tables, organization_id, resource_id):
    allocations = tables.get("allocations")
    if allocations is None:
        return [], []

    items = []
    if hasattr(allocations, "query"):
        from access import query_by_organization

        items = query_by_organization(allocations, organization_id)
    elif hasattr(allocations, "allocation_items"):
        items = list(allocations.allocation_items.values())
    elif hasattr(allocations, "items"):
        raw = allocations.items
        items = list(raw.values()) if isinstance(raw, dict) else list(raw)

    emergency = []
    everyday = []
    for item in items:
        if item.get("resource_id") != resource_id:
            continue
        if item.get("organization_id") != organization_id:
            continue
        status = str(item.get("status", "")).upper()
        allocation_type = str(item.get("allocation_type", "")).upper()
        if status == "ALLOCATED":
            emergency.append(item)
        elif status == "OPEN" and allocation_type in {"EVERYDAY", "EXCHANGE"}:
            # EXCHANGE OPEN holds block lifecycle/everyday like everyday OPEN holds.
            everyday.append(item)
    return emergency, everyday


def _require_no_active_holds(tables, organization_id, resource_id, *, allow_everyday=False):
    emergency, everyday = _active_allocations(tables, organization_id, resource_id)
    if emergency:
        raise LifecycleOperationError(409, "Resource has an active emergency allocation")
    if everyday and not allow_everyday:
        # Message covers everyday and exchange OPEN holds.
        raise LifecycleOperationError(409, "Resource has an open allocation hold")


def _require_quantity_pool_idle(resource):
    if normalize_tracking_mode(resource.get("tracking_mode")) != "QUANTITY":
        return
    snapshot = quantity_snapshot(resource)
    if snapshot["quantity_reserved"] > 0 or snapshot["quantity_allocated"] > 0:
        raise LifecycleOperationError(409, "Quantity pool still has reserved or allocated stock")


def _condition_values(current_status, organization_id):
    values = {
        ":organization_id": organization_id,
        ":current": current_status,
    }
    if current_status == "AVAILABLE":
        values[":true"] = True
    return values


def _status_condition(current_status):
    if current_status == "AVAILABLE":
        return (
            "organization_id = :organization_id AND Available = :true AND "
            "(attribute_not_exists(operational_status) OR operational_status = :current)"
        )
    return "organization_id = :organization_id AND operational_status = :current"


def _transition_resource(
    resource,
    organization_id,
    actor_sub,
    actor_role,
    tables,
    *,
    target_status,
    history_reason,
    audit_action,
    notes="",
    condition=None,
    extra_set=None,
    extra_remove=None,
):
    current = effective_operational_status(resource)
    try:
        validate_transition(current, target_status)
    except ResourceStateError as error:
        raise LifecycleOperationError(409, str(error)) from error

    _require_quantity_pool_idle(resource)

    now = _now()
    resource_id = resource["resource_id"]
    available = available_for_status(target_status)
    values = _condition_values(current, organization_id)
    values[":target"] = target_status
    values[":available"] = available
    values[":now"] = now

    parts = ["operational_status = :target", "Available = :available", "updated_at = :now"]
    remove = list(extra_remove or [])
    names = {}

    if notes:
        parts.append("lifecycle_notes = :notes")
        values[":notes"] = notes

    if condition is not None:
        parts.append("#condition = :condition")
        names["#condition"] = "condition"
        values[":condition"] = condition

    for key, value in (extra_set or {}).items():
        token = f":x_{key}"
        parts.append(f"{key} = {token}")
        values[token] = value

    if target_status == "AVAILABLE":
        for name in ("reserved_by", "reserved_at"):
            if name not in remove:
                remove.append(name)

    update = "SET " + ", ".join(parts)
    if remove:
        update += " REMOVE " + ", ".join(remove)

    update_kwargs = {
        "Key": {"resource_id": resource_id},
        "UpdateExpression": update,
        "ConditionExpression": _status_condition(current),
        "ExpressionAttributeValues": values,
    }
    if names:
        update_kwargs["ExpressionAttributeNames"] = names

    try:
        tables["resources"].update_item(**update_kwargs)
    except ClientError as error:
        _conflict_from_client(error)

    if resource.get("visibility") == "PUBLIC" and resource.get("show_availability") is True:
        try:
            tables["resources"].update_item(
                Key={"resource_id": resource_id},
                UpdateExpression="SET public_status = :public_status",
                ConditionExpression="organization_id = :organization_id AND visibility = :public",
                ExpressionAttributeValues={
                    ":public_status": "AVAILABLE" if available else "UNAVAILABLE",
                    ":organization_id": organization_id,
                    ":public": "PUBLIC",
                },
            )
        except ClientError:
            pass

    _write_history(
        tables["history"],
        history_id=_history_id(f"HIST-{history_reason}-", resource_id),
        resource_id=resource_id,
        organization_id=organization_id,
        location_id=resource.get("location_id", ""),
        resource_type=resource.get("Type", ""),
        location=resource.get("Location", ""),
        previous_status=current,
        new_status=target_status,
        changed_at=now,
        reason=history_reason,
        notes=notes,
    )
    record_audit(
        tables["audit"],
        build_audit_event(
            organization_id,
            actor_sub,
            actor_role,
            audit_action,
            "resource",
            resource_id,
            location_id=resource.get("location_id", ""),
            metadata={"from": current, "to": target_status, "notes": notes},
        ),
    )
    return {
        "message": "Resource updated",
        "resource_id": resource_id,
        "operational_status": target_status,
        "Available": available,
    }


def start_maintenance(body, organization_id, actor_sub, actor_role, resource, tables):
    _require_no_active_holds(tables, organization_id, resource["resource_id"])
    return _transition_resource(
        resource,
        organization_id,
        actor_sub,
        actor_role,
        tables,
        target_status="MAINTENANCE",
        history_reason="RESOURCE_MAINTENANCE_STARTED",
        audit_action="resource.maintenance_start",
        notes=_notes(body),
    )


def complete_maintenance(body, organization_id, actor_sub, actor_role, resource, tables):
    condition = None
    if "condition" in (body or {}):
        condition = normalize_condition(body.get("condition"))
    return _transition_resource(
        resource,
        organization_id,
        actor_sub,
        actor_role,
        tables,
        target_status="AVAILABLE",
        history_reason="RESOURCE_MAINTENANCE_COMPLETED",
        audit_action="resource.maintenance_complete",
        notes=_notes(body),
        condition=condition,
    )


def mark_damaged(body, organization_id, actor_sub, actor_role, resource, tables):
    _require_no_active_holds(tables, organization_id, resource["resource_id"])
    return _transition_resource(
        resource,
        organization_id,
        actor_sub,
        actor_role,
        tables,
        target_status="DAMAGED",
        history_reason="RESOURCE_DAMAGED",
        audit_action="resource.damage",
        notes=_notes(body),
    )


def recover_damage(body, organization_id, actor_sub, actor_role, resource, tables):
    target = str((body or {}).get("target_status") or "AVAILABLE").strip().upper()
    if target not in {"AVAILABLE", "MAINTENANCE"}:
        raise LifecycleOperationError(400, "Damage recovery target is invalid")
    condition = None
    if "condition" in (body or {}):
        condition = normalize_condition(body.get("condition"))
    reason = "RESOURCE_DAMAGE_RECOVERED" if target == "AVAILABLE" else "RESOURCE_MAINTENANCE_STARTED"
    action = "resource.damage_recover" if target == "AVAILABLE" else "resource.maintenance_start"
    return _transition_resource(
        resource,
        organization_id,
        actor_sub,
        actor_role,
        tables,
        target_status=target,
        history_reason=reason,
        audit_action=action,
        notes=_notes(body),
        condition=condition,
    )


def retire_resource(body, organization_id, actor_sub, actor_role, resource, tables):
    _require_no_active_holds(tables, organization_id, resource["resource_id"])
    _require_quantity_pool_idle(resource)
    return _transition_resource(
        resource,
        organization_id,
        actor_sub,
        actor_role,
        tables,
        target_status="RETIRED",
        history_reason="RESOURCE_RETIRED",
        audit_action="resource.retire",
        notes=_notes(body),
        extra_remove=["visibility_key", "discovery_key", "public_status"]
        if resource.get("visibility") == "PUBLIC"
        else None,
    )


def mark_in_use(body, organization_id, actor_sub, actor_role, resource, tables):
    return _transition_resource(
        resource,
        organization_id,
        actor_sub,
        actor_role,
        tables,
        target_status="IN_USE",
        history_reason="RESOURCE_IN_USE",
        audit_action="resource.in_use",
        notes=_notes(body),
    )


def return_to_available(body, organization_id, actor_sub, actor_role, resource, tables):
    """IN_USE → AVAILABLE. Closes an open everyday allocation when present."""
    current = effective_operational_status(resource)
    if current != "IN_USE":
        raise LifecycleOperationError(409, "Resource is not in use")
    emergency, everyday = _active_allocations(tables, organization_id, resource["resource_id"])
    if emergency:
        raise LifecycleOperationError(409, "Resource has an active emergency allocation")

    result = _transition_resource(
        resource,
        organization_id,
        actor_sub,
        actor_role,
        tables,
        target_status="AVAILABLE",
        history_reason="RESOURCE_RETURNED_TO_AVAILABLE",
        audit_action="resource.in_use_return",
        notes=_notes(body),
    )

    for allocation in everyday:
        allocation_id = str(allocation.get("allocation_id") or "").strip()
        if not allocation_id:
            continue
        try:
            tables["allocations"].update_item(
                Key={"allocation_id": allocation_id},
                UpdateExpression=(
                    "SET #status = :returned, returned_at = :now, returned_by = :actor, updated_at = :now"
                ),
                ConditionExpression="#status = :open AND organization_id = :organization_id",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":returned": "RETURNED",
                    ":open": "OPEN",
                    ":now": _now(),
                    ":actor": actor_sub,
                    ":organization_id": organization_id,
                },
            )
        except ClientError:
            pass

    return result


def assign_resource(body, organization_id, actor_sub, actor_role, resource, tables):
    current = effective_operational_status(resource)
    if current == "RETIRED":
        raise LifecycleOperationError(409, "Retired resources cannot be assigned")

    assigned_to = str((body or {}).get("assigned_to") or "").strip()
    department = str((body or {}).get("department") or "").strip()
    team = str((body or {}).get("responsible_team") or "").strip()

    if not assigned_to and not department and not team:
        raise LifecycleOperationError(400, "Assignment target is required")

    for value, label in ((assigned_to, "assigned_to"), (department, "department"), (team, "responsible_team")):
        if len(value) > 80:
            raise LifecycleOperationError(400, f"{label} is too long")

    now = _now()
    resource_id = resource["resource_id"]
    parts = ["updated_at = :now"]
    values = {":organization_id": organization_id, ":now": now}
    if assigned_to:
        parts.append("assigned_to = :assigned_to")
        values[":assigned_to"] = assigned_to
    if department:
        parts.append("department = :department")
        values[":department"] = department
    if team:
        parts.append("responsible_team = :team")
        values[":team"] = team

    try:
        tables["resources"].update_item(
            Key={"resource_id": resource_id},
            UpdateExpression="SET " + ", ".join(parts),
            ConditionExpression="organization_id = :organization_id AND (attribute_not_exists(operational_status) OR operational_status <> :retired)",
            ExpressionAttributeValues={**values, ":retired": "RETIRED"},
        )
    except ClientError as error:
        _conflict_from_client(error)

    record_audit(
        tables["audit"],
        build_audit_event(
            organization_id,
            actor_sub,
            actor_role,
            "resource.assign",
            "resource",
            resource_id,
            location_id=resource.get("location_id", ""),
            metadata={
                "assigned_to": assigned_to,
                "department": department,
                "responsible_team": team,
            },
        ),
    )
    return {
        "message": "Resource assigned",
        "resource_id": resource_id,
        "assigned_to": assigned_to or resource.get("assigned_to", ""),
        "department": department or resource.get("department", ""),
        "responsible_team": team or resource.get("responsible_team", ""),
    }


def unassign_resource(body, organization_id, actor_sub, actor_role, resource, tables):
    current = effective_operational_status(resource)
    if current == "RETIRED":
        raise LifecycleOperationError(409, "Retired resources cannot be unassigned")

    body = body or {}
    remove = []
    if body.get("clear_all") is True:
        remove = ["assigned_to", "department", "responsible_team"]
    else:
        if body.get("assigned_to") is not False:
            remove.append("assigned_to")
        if body.get("department") is True:
            remove.append("department")
        if body.get("responsible_team") is True:
            remove.append("responsible_team")

    if not remove:
        remove = ["assigned_to"]

    now = _now()
    resource_id = resource["resource_id"]
    try:
        tables["resources"].update_item(
            Key={"resource_id": resource_id},
            UpdateExpression="SET updated_at = :now REMOVE " + ", ".join(remove),
            ConditionExpression="organization_id = :organization_id AND (attribute_not_exists(operational_status) OR operational_status <> :retired)",
            ExpressionAttributeValues={
                ":organization_id": organization_id,
                ":now": now,
                ":retired": "RETIRED",
            },
        )
    except ClientError as error:
        _conflict_from_client(error)

    record_audit(
        tables["audit"],
        build_audit_event(
            organization_id,
            actor_sub,
            actor_role,
            "resource.unassign",
            "resource",
            resource_id,
            location_id=resource.get("location_id", ""),
            metadata={"cleared": remove},
        ),
    )
    return {"message": "Resource unassigned", "resource_id": resource_id, "cleared": remove}


def dispatch_lifecycle(path, body, organization_id, actor_sub, actor_role, load_resource, tables):
    resource = load_resource(_resource_id(body))

    if path.endswith("/maintenance/complete"):
        return complete_maintenance(body, organization_id, actor_sub, actor_role, resource, tables)
    if path.endswith("/maintenance"):
        return start_maintenance(body, organization_id, actor_sub, actor_role, resource, tables)
    if path.endswith("/damage/recover"):
        return recover_damage(body, organization_id, actor_sub, actor_role, resource, tables)
    if path.endswith("/damage"):
        return mark_damaged(body, organization_id, actor_sub, actor_role, resource, tables)
    if path.endswith("/retire"):
        return retire_resource(body, organization_id, actor_sub, actor_role, resource, tables)
    if path.endswith("/in-use/return"):
        return return_to_available(body, organization_id, actor_sub, actor_role, resource, tables)
    if path.endswith("/in-use"):
        return mark_in_use(body, organization_id, actor_sub, actor_role, resource, tables)
    if path.endswith("/unassign"):
        return unassign_resource(body, organization_id, actor_sub, actor_role, resource, tables)
    if path.endswith("/assign"):
        return assign_resource(body, organization_id, actor_sub, actor_role, resource, tables)

    raise LifecycleOperationError(404, "Not found")
