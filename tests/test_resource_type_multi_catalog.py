"""Organizations can persist multiple independently named resource types."""

from pathlib import Path

from test_phase5 import (
    ORG_A,
    ORG_B,
    Store,
    USER,
    body_of,
    catalog,
    event,
    memberships,
    use_memberships,
)


def test_create_multiple_custom_resource_types(monkeypatch):
    use_memberships(monkeypatch, memberships((ORG_A, "ADMIN")))
    table = Store(
        [
            {
                "organization_id": ORG_A,
                "resource_type_id": "RT-EE",
                "name": "Emergency equipment",
                "status": "ACTIVE",
                "attributes_schema": {"fields": []},
            }
        ]
    )
    monkeypatch.setattr(catalog, "table_for", lambda kind: table)
    monkeypatch.setattr(catalog, "audit_table", lambda: Store())
    monkeypatch.setattr(
        catalog,
        "query_by_organization",
        lambda *args, **kwargs: list(table.items),
    )

    first = catalog.lambda_handler(
        event("POST", {"name": "Medical supplies", "organization_id": ORG_A}, path="/resource-types"),
        None,
    )
    second = catalog.lambda_handler(
        event("POST", {"name": "Vehicles", "organization_id": ORG_A}, path="/resource-types"),
        None,
    )

    assert first["statusCode"] == 201
    assert second["statusCode"] == 201
    assert body_of(first)["type"]["resource_type_id"] != body_of(second)["type"]["resource_type_id"]
    assert body_of(first)["type"]["name"] == "Medical supplies"
    assert body_of(second)["type"]["name"] == "Vehicles"

    listed = catalog.lambda_handler(event("GET", path="/resource-types"), None)
    names = {item["name"] for item in body_of(listed)["types"] if item.get("status") == "ACTIVE"}
    assert names == {"Emergency equipment", "Medical supplies", "Vehicles"}
    ids = {item["resource_type_id"] for item in body_of(listed)["types"]}
    assert "RT-EE" in ids


def test_duplicate_active_name_is_rejected(monkeypatch):
    use_memberships(monkeypatch, memberships((ORG_A, "ADMIN")))
    table = Store(
        [
            {
                "organization_id": ORG_A,
                "resource_type_id": "RT-EE",
                "name": "Emergency equipment",
                "status": "ACTIVE",
                "attributes_schema": {"fields": []},
            }
        ]
    )
    monkeypatch.setattr(catalog, "table_for", lambda kind: table)
    monkeypatch.setattr(catalog, "audit_table", lambda: Store())
    monkeypatch.setattr(
        catalog,
        "query_by_organization",
        lambda *args, **kwargs: list(table.items),
    )

    duplicate = catalog.lambda_handler(
        event("POST", {"name": "Emergency equipment", "organization_id": ORG_A}, path="/resource-types"),
        None,
    )

    assert duplicate["statusCode"] == 409
    assert len([item for item in table.items if item.get("name") == "Emergency equipment"]) == 1


def test_resource_types_are_organization_scoped(monkeypatch):
    use_memberships(monkeypatch, memberships((ORG_A, "ADMIN")))
    table = Store(
        [
            {
                "organization_id": ORG_B,
                "resource_type_id": "RT-B1",
                "name": "Shelter",
                "status": "ACTIVE",
                "attributes_schema": {"fields": []},
            }
        ]
    )
    monkeypatch.setattr(catalog, "table_for", lambda kind: table)
    monkeypatch.setattr(catalog, "audit_table", lambda: Store())
    monkeypatch.setattr(
        catalog,
        "query_by_organization",
        lambda store, organization_id, index_name=None: [
            item for item in store.items if item.get("organization_id") == organization_id
        ],
    )

    created = catalog.lambda_handler(
        event("POST", {"name": "Generators", "organization_id": ORG_A}, path="/resource-types"),
        None,
    )
    assert created["statusCode"] == 201
    assert body_of(created)["type"]["organization_id"] == ORG_A
    assert body_of(created)["type"]["name"] == "Generators"

    listed = catalog.lambda_handler(event("GET", path="/resource-types"), None)
    names = {item["name"] for item in body_of(listed)["types"]}
    assert "Generators" in names
    assert "Shelter" not in names


def test_custom_type_names_are_not_limited_to_emergency_equipment():
    spec = catalog.COLLECTIONS["resource-types"]
    record = catalog.build_record({"name": "Fire extinguishers"}, ORG_A, USER, spec)
    assert record["name"] == "Fire extinguishers"
    assert record["resource_type_id"].startswith("RT-")
    assert record["status"] == "ACTIVE"


def test_frontend_keeps_existing_emergency_equipment_selectable():
    app = Path(__file__).resolve().parents[1].joinpath("frontend", "app.js").read_text(encoding="utf-8")
    register = app[app.index("async function registerResource(") : app.index("async function registerResource(") + 1200]
    assert "resource_type_id: type" in register
    fill = app[app.index("function fillCatalogSelects(") : app.index("function showSelectedTypeAttributes(")]
    assert "activeTypes.map" in fill
    assert "Ambulance" not in fill
    assert "Emergency equipment" not in fill
    assert "Medical supplies" not in fill
