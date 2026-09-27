"""Logical tenant identity without changing existing primary keys.

Resource, request, and allocation ids stay globally unique.
The authoritative tenant identity is organization_id plus that existing id.
Every API lookup loads the record by its current key and then checks organization_id.
"""


def logical_identity(organization_id, entity_id):
    if not organization_id or not entity_id:
        return None

    return {
        "organization_id": str(organization_id),
        "entity_id": str(entity_id),
    }
