"""Location-aware matching inside one organization.

Typed requests use the request type's compatible resource types and required
attributes. Requests without a request type keep the original type-name match.
A resource from another organization is never selected.
"""


def _text(value):
    return str(value or "").strip()


def _available(resource):
    available = resource.get("Available")

    if isinstance(available, str):
        return available.lower() == "true"

    if "status" in resource and "Available" not in resource:
        return str(resource.get("status", "")).upper() == "AVAILABLE"

    return available is True


def _resource_type(record):
    return _text(
        record.get("Type")
        or record.get("ResourceType")
        or record.get("resource_type")
    )


def _config(request):
    config = request.get("matching_config") or {}

    if not isinstance(config, dict):
        return {}

    return config


def _compatible(request):
    values = _config(request).get("compatible_resource_type_ids") or []

    if not isinstance(values, list):
        return []

    return [str(value) for value in values if value]


def _requirements_met(resource, request):
    required = _config(request).get("required_attributes") or {}

    if not isinstance(required, dict):
        return False

    attributes = resource.get("attributes") or {}

    if not isinstance(attributes, dict):
        return False

    for key, rule in required.items():
        if not isinstance(rule, dict) or "minimum" not in rule:
            return False

        value = attributes.get(key)

        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False

        if value < rule["minimum"]:
            return False

    return True


def _type_matches(resource, request):
    compatible = _compatible(request)

    if compatible:
        return resource.get("resource_type_id") in compatible

    requested = _resource_type(request)
    return bool(requested) and _resource_type(resource) == requested


def explain_match(resource, request):
    """Explain a candidate. This does not grant a match by itself."""
    reasons = []

    if resource.get("organization_id") and resource.get("organization_id") == request.get("organization_id"):
        reasons.append("same organization")

    if _type_matches(resource, request):
        reasons.append("compatible resource type")

    if _available(resource):
        reasons.append("resource available")

    if resource.get("location_id") and resource.get("location_id") == request.get("location_id"):
        reasons.append("same location")
    elif resource.get("organization_id") == request.get("organization_id"):
        reasons.append("alternate location")

    if _requirements_met(resource, request):
        reasons.append("required attributes satisfied")

    return reasons


def choose_resource(resources, request):
    organization_id = request.get("organization_id")
    location_id = request.get("location_id")

    if not organization_id or not (_resource_type(request) or _compatible(request)):
        return None

    same_location = []
    same_organization = []
    prefer_same = _config(request).get("same_location_preferred", True) is not False

    for resource in resources:
        if resource.get("organization_id") != organization_id:
            continue

        if not _type_matches(resource, request):
            continue

        if not _available(resource):
            continue

        if not _requirements_met(resource, request):
            continue

        if resource.get("location_id") == location_id:
            same_location.append(resource)
        else:
            same_organization.append(resource)

    if prefer_same:
        pool = same_location or same_organization
    else:
        pool = same_location + same_organization

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
