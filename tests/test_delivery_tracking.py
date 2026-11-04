"""Local delivery tracking and physical handover. Does not call AWS."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [
    str(ROOT / "src"),
    str(ROOT / "src" / "shared"),
    str(ROOT / "src" / "organization"),
]

import access
import delivery_service


def load_module(name, relative):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


allocation = load_module("allocation_service_delivery", "src/allocation/service.py")

ORG = "ORG-A"
OTHER = "ORG-B"
USER = "operator-sub"


class Store:
    def __init__(self, items=None):
        self.items = [dict(item) for item in (items or [])]

    def query(self, **kwargs):
        del kwargs
        return {"Items": [dict(item) for item in self.items]}

    def get_item(self, Key):
        for item in self.items:
            if all(item.get(key) == value for key, value in Key.items()):
                return {"Item": dict(item)}
        return {}

    def put_item(self, Item, ConditionExpression=None, ExpressionAttributeValues=None, **kwargs):
        del kwargs
        values = ExpressionAttributeValues or {}
        if ConditionExpression and "attribute_not_exists(delivery_id)" in ConditionExpression:
            if any(row.get("delivery_id") == Item.get("delivery_id") for row in self.items):
                raise ClientError(
                    {"Error": {"Code": "ConditionalCheckFailedException"}},
                    "PutItem",
                )
        if ConditionExpression and "version = :version" in ConditionExpression:
            existing = self.get_item(Key={"delivery_id": Item.get("delivery_id")}).get("Item")
            if (
                not existing
                or existing.get("organization_id") != values.get(":org")
                or existing.get("version") != values.get(":version")
                or existing.get("status") != values.get(":status")
            ):
                raise ClientError(
                    {"Error": {"Code": "ConditionalCheckFailedException"}},
                    "PutItem",
                )
            self.items = [
                row for row in self.items if row.get("delivery_id") != Item.get("delivery_id")
            ]
        self.items.append(dict(Item))


def use_memberships(monkeypatch, role="OPERATOR", organization_id=ORG):
    monkeypatch.setattr(
        access,
        "list_memberships",
        lambda *args, **kwargs: [
            {
                "organization_id": organization_id,
                "name": organization_id,
                "role": role,
                "status": "ACTIVE",
            }
        ],
    )


def allocation_row(**extra):
    row = {
        "allocation_id": "ALLOC-REQ1",
        "request_id": "REQ1",
        "resource_id": "R1",
        "resource_type": "ICU_BED",
        "location": "North",
        "location_id": "LOC-A",
        "organization_id": ORG,
        "status": "ALLOCATED",
        "quantity": 10,
    }
    row.update(extra)
    return row


@pytest.fixture
def world(monkeypatch):
    deliveries = Store()
    allocations = Store([allocation_row()])
    audits = Store()
    monkeypatch.setattr(delivery_service, "deliveries_table", lambda: deliveries)
    monkeypatch.setattr(delivery_service, "allocations_table", lambda: allocations)
    monkeypatch.setattr(delivery_service, "audit_table", lambda: audits)
    monkeypatch.setattr(allocation, "locations_table", lambda: Store([
        {
            "organization_id": ORG,
            "location_id": "LOC-A",
            "name": "North",
            "status": "ACTIVE",
        }
    ]))
    use_memberships(monkeypatch)
    return {"deliveries": deliveries, "allocations": allocations, "audits": audits}


def call(method, path, body=None, query=None, organization_id=ORG):
    event = {
        "httpMethod": method,
        "path": path,
        "queryStringParameters": {"organization_id": organization_id, **(query or {})},
        "requestContext": {"authorizer": {"claims": {"sub": USER}}},
    }
    if body is not None:
        event["body"] = json.dumps(body)
    result = allocation.lambda_handler(event, None)
    return result["statusCode"], json.loads(result["body"])


def create(world):
    del world
    return call("POST", "/allocate/deliveries", {"allocation_id": "ALLOC-REQ1", "organization_id": ORG})


def delivery_id(payload):
    return payload["delivery"]["delivery_id"]


def test_create_delivery_from_allocation(world):
    status, body = create(world)
    assert status == 200
    assert body["delivery"]["status"] == "ALLOCATED"
    assert body["delivery"]["quantity_allocated"] == 10
    assert body["delivery"]["quantity_dispatched"] == 0
    assert body["delivery"]["allocation_id"] == "ALLOC-REQ1"
    assert world["allocations"].items[0]["status"] == "ALLOCATED"
    assert world["allocations"].items[0]["quantity"] == 10


def test_duplicate_active_delivery_rejected(world):
    first = create(world)
    assert first[0] == 200
    status, body = create(world)
    assert status == 409
    assert "active delivery" in body["message"]


def test_ineligible_allocation_rejected(world):
    world["allocations"].items[0]["status"] = "RELEASED"
    status, body = create(world)
    assert status == 409
    assert "not eligible" in body["message"]


def test_cross_organization_allocation_not_found(world, monkeypatch):
    world["allocations"].items[0]["organization_id"] = OTHER
    status, body = create(world)
    assert status == 404


def test_get_delivery_hides_other_organization(world):
    _, created = create(world)
    ident = delivery_id(created)
    status, body = call("GET", f"/allocate/deliveries/{ident}", query=None, organization_id=OTHER)
    assert status in {403, 404}


def test_member_cannot_create_delivery(world, monkeypatch):
    use_memberships(monkeypatch, role="MEMBER")
    status, body = create(world)
    assert status == 403


def test_invalid_status_transition(world):
    _, created = create(world)
    ident = delivery_id(created)
    status, body = call(
        "POST",
        f"/allocate/deliveries/{ident}/status",
        {"status": "DELIVERED", "organization_id": ORG},
    )
    assert status == 409
    assert "not allowed" in body["message"]


def _advance_to_dispatched(ident):
    call(
        "POST",
        f"/allocate/deliveries/{ident}/ready",
        {
            "organization_id": ORG,
            "dispatch_quantity": 8,
            "vehicle_number": "KA01AB1234",
            "source_location": "North warehouse",
            "destination_location": "Field site",
            "driver_name": "Asha",
        },
    )
    return call("POST", f"/allocate/deliveries/{ident}/dispatch", {"organization_id": ORG})


def test_transport_dispatch_in_transit_delivered(world):
    _, created = create(world)
    ident = delivery_id(created)
    status, body = call(
        "POST",
        f"/allocate/deliveries/{ident}/transport",
        {
            "organization_id": ORG,
            "vehicle_number": "KA01AB1234",
            "dispatch_quantity": 8,
            "source_location": "North warehouse",
            "destination_location": "Field site",
        },
    )
    assert status == 200
    assert body["delivery"]["vehicle_number"] == "KA01AB1234"
    assert body["delivery"]["quantity_allocated"] == 10
    status, body = _advance_to_dispatched(ident)
    assert status == 200
    assert body["delivery"]["status"] == "DISPATCHED"
    assert body["delivery"]["dispatch_at"]
    status, body = call(
        "POST",
        f"/allocate/deliveries/{ident}/status",
        {"status": "IN_TRANSIT", "organization_id": ORG},
    )
    assert status == 200
    assert body["delivery"]["status"] == "IN_TRANSIT"
    status, body = call(
        "POST",
        f"/allocate/deliveries/{ident}/status",
        {"status": "PARTIALLY_DELIVERED", "organization_id": ORG},
    )
    assert status == 200
    assert body["delivery"]["status"] == "PARTIALLY_DELIVERED"
    assert world["allocations"].items[0]["status"] == "ALLOCATED"


def test_dispatch_quantity_cannot_exceed_allocated(world):
    _, created = create(world)
    ident = delivery_id(created)
    status, body = call(
        "POST",
        f"/allocate/deliveries/{ident}/ready",
        {
            "organization_id": ORG,
            "dispatch_quantity": 11,
            "vehicle_number": "KA01AB1234",
            "source_location": "North",
            "destination_location": "South",
        },
    )
    assert status == 400


def test_receipt_quantity_rules_and_partial(world):
    _, created = create(world)
    ident = delivery_id(created)
    _advance_to_dispatched(ident)
    call(
        "POST",
        f"/allocate/deliveries/{ident}/status",
        {"status": "DELIVERED", "organization_id": ORG},
    )
    status, body = call(
        "POST",
        f"/allocate/deliveries/{ident}/receipt",
        {
            "organization_id": ORG,
            "receiver_name": "Kiran",
            "quantity_received": 9,
        },
    )
    assert status == 400
    status, body = call(
        "POST",
        f"/allocate/deliveries/{ident}/receipt",
        {
            "organization_id": ORG,
            "receiver_name": "Kiran",
            "receiver_organization": "Field team",
            "quantity_received": 6,
            "quantity_damaged": 1,
            "quantity_missing": 1,
        },
    )
    assert status == 200
    assert body["delivery"]["status"] == "PARTIALLY_RECEIVED"
    assert body["delivery"]["receipt_confirmed"] is True
    assert body["delivery"]["quantity_allocated"] == 10
    assert body["delivery"]["discrepancy"] is True
    status, body = call(
        "POST",
        f"/allocate/deliveries/{ident}/receipt",
        {
            "organization_id": ORG,
            "receiver_name": "Kiran",
            "quantity_received": 6,
            "quantity_damaged": 1,
            "quantity_missing": 1,
        },
    )
    assert status == 409


def test_duplicate_receipt_and_handover_verify(world):
    _, created = create(world)
    ident = delivery_id(created)
    call(
        "POST",
        f"/allocate/deliveries/{ident}/ready",
        {
            "organization_id": ORG,
            "dispatch_quantity": 10,
            "vehicle_number": "KA01AB1234",
            "source_location": "North",
            "destination_location": "South",
        },
    )
    call("POST", f"/allocate/deliveries/{ident}/dispatch", {"organization_id": ORG})
    call(
        "POST",
        f"/allocate/deliveries/{ident}/status",
        {"status": "DELIVERED", "organization_id": ORG},
    )
    status, body = call(
        "POST",
        f"/allocate/deliveries/{ident}/receipt",
        {
            "organization_id": ORG,
            "receiver_name": "Kiran",
            "quantity_received": 10,
            "quantity_damaged": 0,
            "quantity_missing": 0,
        },
    )
    assert status == 200
    assert body["delivery"]["status"] == "RECEIVED"
    status, body = call(
        "POST",
        f"/allocate/deliveries/{ident}/handover",
        {
            "organization_id": ORG,
            "handover_sender": "Depot desk",
            "handover_receiver": "Kiran",
            "quantity_handed_over": 10,
        },
    )
    assert status == 200
    assert body["delivery"]["status"] == "HANDOVER_PENDING"
    assert body["delivery"]["handover_verified"] is False
    status, body = call(
        "POST",
        f"/allocate/deliveries/{ident}/handover/verify",
        {"organization_id": ORG, "confirmation": "verified"},
    )
    assert status == 200
    assert body["delivery"]["status"] == "HANDOVER_COMPLETED"
    assert body["delivery"]["handover_verified"] is True
    assert world["allocations"].items[0]["status"] == "ALLOCATED"
    status, body = call(
        "POST",
        f"/allocate/deliveries/{ident}/handover/verify",
        {"organization_id": ORG, "confirmation": "verified"},
    )
    assert status == 409


def test_handover_before_receipt_rejected(world):
    _, created = create(world)
    ident = delivery_id(created)
    status, body = call(
        "POST",
        f"/allocate/deliveries/{ident}/handover",
        {
            "organization_id": ORG,
            "handover_sender": "Desk",
            "handover_receiver": "Kiran",
            "quantity_handed_over": 1,
        },
    )
    assert status == 409


def test_list_counts_from_records_and_history_appends(world):
    _, created = create(world)
    ident = delivery_id(created)
    _advance_to_dispatched(ident)
    status, body = call("GET", "/allocate/deliveries", query={"organization_id": ORG})
    assert status == 200
    assert body["counts"]["total"] == 1
    assert body["counts"]["DISPATCHED"] == 1
    assert body["record_source"] == "application_records"
    status, history = call("GET", f"/allocate/deliveries/{ident}/history")
    assert status == 200
    actions = [event["action"] for event in history["events"]]
    assert actions[0] == "delivery.created"
    assert "delivery.dispatched" in actions
    assert history["events"][0]["actor_sub"] == USER


def test_existing_allocation_get_still_works(world, monkeypatch):
    monkeypatch.setattr(allocation, "allocations_table", lambda: world["allocations"])
    status, body = call("GET", "/allocate/allocations", query={"organization_id": ORG, "location_id": "LOC-A"})
    assert status == 200
    assert body["allocations"][0]["allocation_id"] == "ALLOC-REQ1"


def test_cancelled_delivery_allows_new_record(world):
    _, created = create(world)
    ident = delivery_id(created)
    status, body = call(
        "POST",
        f"/allocate/deliveries/{ident}/status",
        {"status": "CANCELLED", "organization_id": ORG},
    )
    assert status == 200
    status, body = create(world)
    assert status == 200
    assert body["delivery"]["delivery_id"] != ident
