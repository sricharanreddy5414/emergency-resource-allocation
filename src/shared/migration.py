"""Controlled tenant migration. Ambiguous ownership is never applied."""


class MigrationError(Exception):
    def __init__(self, message):
        super().__init__(message)
        self.message = message


def _index(items, key_name):
    return {item.get(key_name): item for item in items if item.get(key_name)}


def discover(resources, requests, allocations, history):
    """Report records that cannot be owned automatically."""
    findings = []

    def flag(entity_type, entity_id, reason):
        findings.append(
            {
                "entity_type": entity_type,
                "entity_id": entity_id,
                "reason": reason,
            }
        )

    resource_ids = {item.get("resource_id") for item in resources}
    request_ids = {item.get("request_id") for item in requests}

    for item in resources:
        if not item.get("organization_id"):
            flag("resource", item.get("resource_id"), "missing organization_id")

    for item in requests:
        if not item.get("organization_id"):
            flag("request", item.get("request_id"), "missing organization_id")

    for item in allocations:
        if not item.get("organization_id"):
            flag("allocation", item.get("allocation_id"), "missing organization_id")

        if item.get("resource_id") not in resource_ids:
            flag("allocation", item.get("allocation_id"), "missing resource")

        if item.get("request_id") not in request_ids:
            flag("allocation", item.get("allocation_id"), "missing request")

    for item in history:
        if not item.get("organization_id"):
            flag("history", item.get("history_id"), "missing organization_id")

        if item.get("resource_id") not in resource_ids:
            flag("history", item.get("history_id"), "missing resource")

    return findings


def _same_scope(item, organization_id, location_id):
    return (
        item.get("organization_id") == organization_id
        and item.get("location_id") == location_id
    )


def validate_plan(plan, resources, requests, allocations, history, organizations, locations):
    """Return a write plan or raise MigrationError. This function does not write."""
    organizations_by_id = _index(organizations, "organization_id")
    locations_by_id = {
        (item.get("organization_id"), item.get("location_id")): item
        for item in locations
    }
    resources_by_id = _index(resources, "resource_id")
    requests_by_id = _index(requests, "request_id")
    resource_plan = {
        item["resource_id"]: item for item in plan.get("resources", [])
    }
    request_plan = {
        item["request_id"]: item for item in plan.get("requests", [])
    }
    errors = []

    def check_target(kind, entity_id, mapping):
        organization_id = mapping.get("organization_id")
        location_id = mapping.get("location_id")
        organization = organizations_by_id.get(organization_id)

        if not organization or organization.get("status", "ACTIVE") != "ACTIVE":
            errors.append(f"{kind} {entity_id}: invalid organization")
            return None

        location = locations_by_id.get((organization_id, location_id))

        if not location or location.get("organization_id") != organization_id:
            errors.append(f"{kind} {entity_id}: invalid location")
            return None

        if location.get("status", "ACTIVE") != "ACTIVE":
            errors.append(f"{kind} {entity_id}: invalid location")
            return None

        return organization_id, location_id

    writes = []

    for resource_id, mapping in resource_plan.items():
        resource = resources_by_id.get(resource_id)

        if not resource:
            errors.append(f"resource {resource_id}: missing resource")
            continue

        target = check_target("resource", resource_id, mapping)

        if not target:
            continue

        if resource.get("organization_id") and not _same_scope(resource, *target):
            errors.append(f"resource {resource_id}: organization mismatch")
            continue

        if not _same_scope(resource, *target):
            writes.append(("resource", resource_id, target[0], target[1]))

    for request_id, mapping in request_plan.items():
        request = requests_by_id.get(request_id)

        if not request:
            errors.append(f"request {request_id}: missing request")
            continue

        target = check_target("request", request_id, mapping)

        if not target:
            continue

        if request.get("organization_id") and not _same_scope(request, *target):
            errors.append(f"request {request_id}: organization mismatch")
            continue

        if not _same_scope(request, *target):
            writes.append(("request", request_id, target[0], target[1]))

    for allocation in allocations:
        resource_mapping = resource_plan.get(allocation.get("resource_id"))
        request_mapping = request_plan.get(allocation.get("request_id"))

        if not resource_mapping or not request_mapping:
            if not allocation.get("organization_id"):
                errors.append(
                    f"allocation {allocation.get('allocation_id')}: manual review"
                )
            continue

        if resource_mapping.get("organization_id") != request_mapping.get("organization_id"):
            errors.append(
                f"allocation {allocation.get('allocation_id')}: organization mismatch"
            )
            continue

        target = (
            resource_mapping["organization_id"],
            resource_mapping["location_id"],
        )

        if allocation.get("organization_id") and not _same_scope(allocation, *target):
            errors.append(
                f"allocation {allocation.get('allocation_id')}: organization mismatch"
            )
            continue

        if not _same_scope(allocation, *target):
            writes.append(("allocation", allocation.get("allocation_id"), target[0], target[1]))

    for record in history:
        resource = resources_by_id.get(record.get("resource_id"))
        mapping = resource_plan.get(record.get("resource_id"))

        if not resource or not mapping:
            if not record.get("organization_id"):
                errors.append(f"history {record.get('history_id')}: manual review")
            continue

        target = (mapping["organization_id"], mapping["location_id"])

        if record.get("organization_id") and not _same_scope(record, *target):
            errors.append(f"history {record.get('history_id')}: organization mismatch")
            continue

        if not _same_scope(record, *target):
            writes.append(("history", record.get("history_id"), target[0], target[1]))

    if errors:
        raise MigrationError("; ".join(errors))

    return writes


def apply_writes(records, writes):
    """Apply an already validated plan. Repeating the same writes is a no-op."""
    for kind, entity_id, organization_id, location_id in writes:
        item = records[kind][entity_id]
        current = item.get("organization_id")

        if current and current != organization_id:
            raise MigrationError(f"{kind} {entity_id}: organization mismatch")

        item["organization_id"] = organization_id
        item["location_id"] = location_id

    return records
