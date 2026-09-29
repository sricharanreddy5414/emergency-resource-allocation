"""Everyday resource state. Emergency matching still uses Available only."""

RESOURCE_OPERATIONAL_STATUSES = frozenset(
    {
        "AVAILABLE",
        "RESERVED",
        "ALLOCATED",
        "IN_USE",
        "MAINTENANCE",
        "DAMAGED",
        "RETIRED",
    }
)

TRACKING_MODES = frozenset({"INDIVIDUAL", "QUANTITY"})

QUANTITY_FIELD_NAMES = (
    "quantity_total",
    "quantity_available",
    "quantity_reserved",
    "quantity_allocated",
)


class ResourceStateError(ValueError):
    """Invalid resource state in a request or stored item."""


def available_flag(value):
    if isinstance(value, str):
        return value.lower() == "true"

    return value is True


def normalize_operational_status(value):
    """Validate an explicit operational_status string."""
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ResourceStateError("Operational status is invalid")

    status = str(value).strip().upper()

    if status not in RESOURCE_OPERATIONAL_STATUSES:
        raise ResourceStateError("Operational status is invalid")

    return status


def normalize_tracking_mode(value):
    if value is None or (isinstance(value, str) and not value.strip()):
        return "INDIVIDUAL"

    mode = str(value).strip().upper()

    if mode not in TRACKING_MODES:
        raise ResourceStateError("Tracking mode is invalid")

    return mode


def effective_operational_status(resource):
    """Read-time status. Does not mutate resource or write DynamoDB."""
    if not isinstance(resource, dict):
        raise ResourceStateError("Resource is invalid")

    stored = resource.get("operational_status")

    if stored is not None and str(stored).strip():
        return normalize_operational_status(stored)

    if available_flag(resource.get("Available")):
        return "AVAILABLE"

    return "ALLOCATED"


def _non_negative_int(value, label):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ResourceStateError(f"{label} is invalid")

    if value < 0:
        raise ResourceStateError(f"{label} is invalid")

    return value


def quantity_snapshot(resource):
    """Return quantity fields when tracking_mode is QUANTITY."""
    if normalize_tracking_mode(resource.get("tracking_mode")) != "QUANTITY":
        return None

    total = _non_negative_int(resource.get("quantity_total"), "Quantity total")
    available = _non_negative_int(resource.get("quantity_available"), "Quantity available")
    reserved = _non_negative_int(resource.get("quantity_reserved"), "Quantity reserved")
    allocated = _non_negative_int(resource.get("quantity_allocated"), "Quantity allocated")

    if available + reserved + allocated != total:
        raise ResourceStateError("Quantity totals are inconsistent")

    return {
        "quantity_total": total,
        "quantity_available": available,
        "quantity_reserved": reserved,
        "quantity_allocated": allocated,
    }


def validate_quantity_fields(resource):
    """Validate quantity fields on a resource-shaped dict."""
    quantity_snapshot(resource)


def initialize_new_resource_fields(body, *, available):
    """Defaults and validation for a new resource row. Side-effect free."""
    if not isinstance(body, dict):
        raise ResourceStateError("Resource is invalid")

    tracking_mode = normalize_tracking_mode(body.get("tracking_mode"))

    if body.get("operational_status") is None or not str(body.get("operational_status") or "").strip():
        operational_status = "AVAILABLE"
    else:
        operational_status = normalize_operational_status(body.get("operational_status"))

    fields = {
        "operational_status": operational_status,
        "tracking_mode": tracking_mode,
    }

    if tracking_mode == "INDIVIDUAL":
        for name in QUANTITY_FIELD_NAMES:
            if body.get(name) is not None:
                raise ResourceStateError("Quantity fields are not allowed for individual resources")

        return fields

    if body.get("quantity_total") is None:
        raise ResourceStateError("Quantity total is required")

    total = _non_negative_int(body.get("quantity_total"), "Quantity total")

    if total < 1:
        raise ResourceStateError("Quantity total is invalid")

    fields["Available"] = False
    fields.update(
        {
            "quantity_total": total,
            "quantity_available": total,
            "quantity_reserved": 0,
            "quantity_allocated": 0,
        }
    )
    validate_quantity_fields({**fields, "tracking_mode": "QUANTITY"})
    return fields


def emergency_matchable(resource):
    """True when the legacy emergency matcher may claim this resource."""
    if not isinstance(resource, dict):
        return False

    if not available_flag(resource.get("Available")):
        return False

    if normalize_tracking_mode(resource.get("tracking_mode")) == "QUANTITY":
        return False

    stored = resource.get("operational_status")

    if stored is None or not str(stored).strip():
        return True

    return str(stored).strip().upper() == "AVAILABLE"


EMERGENCY_CLAIM_CONDITION = (
    "#a = :true AND organization_id = :organization_id AND "
    "(attribute_not_exists(operational_status) OR operational_status = :op_available) AND "
    "(attribute_not_exists(tracking_mode) OR tracking_mode = :indiv)"
)


def lifecycle_fields_from_body(body):
    """Fields clients must not set through ordinary metadata update."""
    if not isinstance(body, dict):
        return set()

    blocked = {"Available", "operational_status", "tracking_mode", *QUANTITY_FIELD_NAMES}

    return {name for name in blocked if name in body}
