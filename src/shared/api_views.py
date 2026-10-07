"""Explicit response fields for emergency requests and allocations.

Stored items may contain implementation attributes. API responses copy only
the names in these tuples. Authorization happens before this projection.
"""

REQUEST_FIELDS = (
    "request_id",
    "ResourceType",
    "resource_type",
    "Location",
    "location",
    "location_id",
    "organization_id",
    "Priority",
    "priority",
    "Status",
    "status",
    "CreatedAt",
    "created_at",
    "request_type_id",
    "attributes",
    "matching_config",
)

ALLOCATION_FIELDS = (
    "allocation_id",
    "allocation_type",
    "request_id",
    "resource_id",
    "resource_type",
    "location",
    "location_id",
    "organization_id",
    "priority",
    "Priority",
    "quantity",
    "purpose",
    "status",
    "Status",
    "allocated_at",
    "released_at",
    "created_at",
    "updated_at",
)


def _view(item, fields):
    if not isinstance(item, dict):
        return {}
    projected = {}
    for name in fields:
        if name not in item:
            continue
        value = item[name]
        if isinstance(value, dict):
            value = dict(value)
        elif isinstance(value, list):
            value = list(value)
        projected[name] = value
    return projected


def request_view(item):
    return _view(item, REQUEST_FIELDS)


def allocation_view(item):
    return _view(item, ALLOCATION_FIELDS)
