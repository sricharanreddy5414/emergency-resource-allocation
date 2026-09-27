"""Location-aware matching inside one organization.

Order:
1. Same resource type, available, same organization, same location.
2. Same resource type, available, same organization, any other location.
3. Never another organization.
"""


def _text(value):
    return str(value or "").strip()


def _available(resource):
    available = resource.get("Available")

    if isinstance(available, str):
        return available.lower() == "true"

    return available is True


def _resource_type(record):
    return _text(
        record.get("Type")
        or record.get("ResourceType")
        or record.get("resource_type")
    )


def choose_resource(resources, request):
    organization_id = request.get("organization_id")
    location_id = request.get("location_id")
    requested_type = _resource_type(request)

    if not organization_id or not requested_type:
        return None

    same_location = []
    same_organization = []

    for resource in resources:
        if resource.get("organization_id") != organization_id:
            continue

        if _resource_type(resource) != requested_type:
            continue

        if not _available(resource):
            continue

        if resource.get("location_id") == location_id:
            same_location.append(resource)
        else:
            same_organization.append(resource)

    pool = same_location or same_organization

    if not pool:
        return None

    return sorted(pool, key=lambda item: _text(item.get("resource_id")))[0]


def sort_requests_by_priority(requests):
    def priority(request):
        try:
            return int(request.get("Priority", request.get("priority", 999)))
        except (TypeError, ValueError):
            return 999

    return sorted(requests, key=priority)
