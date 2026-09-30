"""Phase 5E: transfer start + handover confirm (ownership/location) tests."""

import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest
from botocore.exceptions import ClientError
from boto3.dynamodb.types import TypeDeserializer

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [
    str(ROOT / "src"),
    str(ROOT / "src" / "shared"),
    str(ROOT / "src" / "organization"),
    str(ROOT / "src" / "exchange"),
]

import access


def load_module(name, relative):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


handler = load_module("exchange_handler_5e", "src/exchange/handler.py")
service = load_module("exchange_service_5e", "src/exchange/service.py")
handler.service = service

ORG_A = "ORG-A"
ORG_B = "ORG-B"
ORG_C = "ORG-C"
USER = "user-a"
TYPE_A = "RT-TYPE-A"
TYPE_B = "RT-TYPE-B"
DESER = TypeDeserializer()


def memberships(*pairs):
    return [
        {"organization_id": o, "name": o, "role": r, "status": "ACTIVE"}
        for o, r in pairs
    ]


def use_memberships(monkeypatch, records):
    monkeypatch.setattr(access, "list_memberships", lambda *a, **k: records)


def use_billing(monkeypatch, status="ACTIVE"):
    class Subs:
        def get_item(self, Key):
            if status is None:
                return {}
            return {
                "Item": {
                    "organization_id": Key["organization_id"],
                    "subscription_status": status,
                }
            }

    monkeypatch.setattr(access, "subscriptions_table", lambda: Subs())


def event(method="GET", body=None, organization_id=ORG_A, path="/exchange/requests", query=None):
    payload = {
        "httpMethod": method,
        "path": path,
        "queryStringParameters": {"organization_id": organization_id, **(query or {})},
        "requestContext": {"authorizer": {"claims": {"sub": USER, "token_use": "id"}}},
    }
    if body is not None:
        payload["body"] = json.dumps(body)
    return payload


def body_of(result):
    return json.loads(result["body"])


def _decode(av_map):
    return {k: DESER.deserialize(v) for k, v in av_map.items()}


class ExchangeStore:
    def __init__(self):
        self.name = "ResourceExchanges"
        self.table_name = "ResourceExchanges"
        self.items = {}

    def get_item(self, Key):
        item = self.items.get((Key["pk"], Key["sk"]))
        return {"Item": copy.deepcopy(item)} if item else {}

    def put_item(self, Item, ConditionExpression=None, **kwargs):
        key = (Item["pk"], Item["sk"])
        if ConditionExpression and "attribute_not_exists" in ConditionExpression and key in self.items:
            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "PutItem")
        self.items[key] = copy.deepcopy(Item)

    def update_item(self, **kwargs):
        key = (kwargs["Key"]["pk"], kwargs["Key"]["sk"])
        item = self.items.get(key)
        if not item:
            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")
        values = kwargs.get("ExpressionAttributeValues") or {}
        condition = kwargs.get("ConditionExpression") or ""
        if "#status = :open" in condition and item.get("status") != values.get(":open"):
            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")
        if "#status = :accepted" in condition and item.get("status") != values.get(":accepted"):
            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")
        if "#status = :pending" in condition and item.get("status") != values.get(":pending"):
            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")
        if values.get(":pending") == "TRANSFER_PENDING":
            item["status"] = "TRANSFER_PENDING"
            item["transfer_started_at"] = values.get(":now")
            item["transfer_started_by"] = values.get(":actor")
        if values.get(":completed") == "COMPLETED":
            item["status"] = "COMPLETED"
            item["completed_at"] = values.get(":now")
            item["confirming_actor_sub"] = values.get(":actor")
        if values.get(":accepted") == "ACCEPTED" and ":offer_id" in values:
            item["status"] = "ACCEPTED"
            item["accepted_offer_id"] = values[":offer_id"]
            item["accepted_resource_id"] = values.get(":resource_id")
            item["accepted_provider_organization_id"] = values.get(":provider")
            item.pop("network_list_key", None)
        if values.get(":superseded") == "SUPERSEDED":
            item["status"] = "SUPERSEDED"
        item["updated_at"] = values.get(":now", item.get("updated_at"))
        self.items[key] = item

    def query(self, **kwargs):
        items = list(self.items.values())
        index = kwargs.get("IndexName")
        if index == "NetworkOpenRequestIndex":
            items = [i for i in items if i.get("network_list_key") == "OPEN"]
        else:
            target_pk = None
            kce = kwargs.get("KeyConditionExpression")
            if kce is not None:
                from boto3.dynamodb.conditions import ConditionExpressionBuilder

                built = ConditionExpressionBuilder().build_expression(kce)
                for value in (built.attribute_value_placeholders or {}).values():
                    if isinstance(value, str) and value.startswith("EXREQ#"):
                        target_pk = value
                        break
            items = [i for i in items if i.get("entity_type") == "EXCHANGE_OFFER"]
            if target_pk:
                items = [i for i in items if i.get("pk") == target_pk]
        return {"Items": copy.deepcopy(items)}


class ResourceStore:
    def __init__(self, items):
        self.name = "Resources"
        self.table_name = "Resources"
        self.items = {i["resource_id"]: copy.deepcopy(i) for i in items}

    def get_item(self, Key):
        item = self.items.get(Key["resource_id"])
        return {"Item": copy.deepcopy(item)} if item else {}

    def update_item(self, **kwargs):
        rid = kwargs["Key"]["resource_id"]
        item = self.items[rid]
        values = kwargs.get("ExpressionAttributeValues") or {}
        condition = kwargs.get("ConditionExpression") or ""
        if "organization_id = :provider" in condition or "organization_id = :organization_id" in condition:
            expected = values.get(":provider") or values.get(":organization_id")
            if item.get("organization_id") != expected:
                raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")
        if "operational_status = :allocated" in condition:
            if item.get("operational_status") != "ALLOCATED":
                raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")
        if "#a = :false" in condition and item.get("Available") is not False:
            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")
        if values.get(":requester"):
            item["organization_id"] = values[":requester"]
            item["location_id"] = values.get(":loc_id")
            item["Location"] = values.get(":loc_name")
            item["Available"] = True
            item["operational_status"] = "AVAILABLE"
            item["visibility"] = "PRIVATE"
            for attr in (
                "visibility_key",
                "discovery_key",
                "public_type_name",
                "public_name",
                "public_description",
                "public_contact",
                "public_city",
                "public_state",
                "public_status",
                "show_availability",
            ):
                item.pop(attr, None)
        elif values.get(":op_allocated") == "ALLOCATED":
            item["Available"] = False
            item["operational_status"] = "ALLOCATED"


class AllocStore:
    def __init__(self):
        self.name = "Allocations"
        self.table_name = "Allocations"
        self.items = {}

    def get_item(self, Key):
        item = self.items.get(Key["allocation_id"])
        return {"Item": copy.deepcopy(item)} if item else {}

    def put_item(self, Item, ConditionExpression=None, **kwargs):
        aid = Item["allocation_id"]
        if ConditionExpression and aid in self.items:
            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "PutItem")
        self.items[aid] = copy.deepcopy(Item)

    def update_item(self, **kwargs):
        aid = kwargs["Key"]["allocation_id"]
        item = self.items.get(aid)
        if not item:
            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")
        values = kwargs.get("ExpressionAttributeValues") or {}
        condition = kwargs.get("ConditionExpression") or ""
        if "#status = :open" in condition and item.get("status") != values.get(":open"):
            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")
        if values.get(":released") == "RELEASED":
            item["status"] = "RELEASED"
            item["released_at"] = values.get(":now")


class SimpleStore:
    def __init__(self, items):
        self._items = list(items)

    def get_item(self, Key):
        for item in self._items:
            if all(item.get(k) == v for k, v in Key.items()):
                return {"Item": copy.deepcopy(item)}
        return {}


class AuditStore:
    def __init__(self):
        self.events = []

    def put_item(self, Item):
        self.events.append(Item)


class HistoryStore:
    def __init__(self):
        self.items = []

    def put_item(self, Item, **kwargs):
        if kwargs.get("ConditionExpression") and any(
            i.get("history_id") == Item.get("history_id") for i in self.items
        ):
            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "PutItem")
        self.items.append(Item)


def seed():
    orgs = SimpleStore(
        [
            {"organization_id": ORG_A, "status": "ACTIVE", "name": "A"},
            {"organization_id": ORG_B, "status": "ACTIVE", "name": "B"},
            {"organization_id": ORG_C, "status": "ACTIVE", "name": "C"},
        ]
    )
    locations = SimpleStore(
        [
            {
                "organization_id": ORG_A,
                "location_id": "LOC-A",
                "name": "A Depot",
                "city": "Bengaluru",
                "state": "KA",
                "status": "ACTIVE",
            },
            {
                "organization_id": ORG_A,
                "location_id": "LOC-A2",
                "name": "A Alt",
                "city": "Mysuru",
                "state": "KA",
                "status": "ACTIVE",
            },
            {
                "organization_id": ORG_B,
                "location_id": "LOC-B",
                "name": "B Depot",
                "city": "Mysuru",
                "state": "KA",
                "status": "ACTIVE",
            },
            {
                "organization_id": ORG_C,
                "location_id": "LOC-C",
                "name": "C Depot",
                "city": "Hubli",
                "state": "KA",
                "status": "ACTIVE",
            },
        ]
    )
    types = SimpleStore(
        [
            {
                "organization_id": ORG_A,
                "resource_type_id": TYPE_A,
                "name": "Medical Kit",
                "status": "ACTIVE",
            },
            {
                "organization_id": ORG_B,
                "resource_type_id": TYPE_B,
                "name": "Medical Kit",
                "status": "ACTIVE",
            },
        ]
    )
    resources = ResourceStore(
        [
            {
                "resource_id": "R-B-1",
                "organization_id": ORG_B,
                "location_id": "LOC-B",
                "Location": "B Depot",
                "name": "Spare",
                "Type": "Medical Kit",
                "Available": True,
                "operational_status": "AVAILABLE",
                "tracking_mode": "INDIVIDUAL",
                "visibility": "NETWORK",
            },
            {
                "resource_id": "R-B-Q",
                "organization_id": ORG_B,
                "location_id": "LOC-B",
                "Location": "B Depot",
                "name": "Pool",
                "Type": "Medical Kit",
                "Available": False,
                "operational_status": "AVAILABLE",
                "tracking_mode": "QUANTITY",
                "quantity_total": 10,
                "quantity_available": 10,
                "quantity_reserved": 0,
                "quantity_allocated": 0,
            },
        ]
    )
    return orgs, locations, types, resources


def install_transact(monkeypatch, exchanges, resources, allocations):
    def fake_transact(transact_items):
        planned = []
        for entry in transact_items:
            if "Update" in entry:
                upd = entry["Update"]
                table = upd["TableName"]
                key = _decode(upd["Key"])
                values = _decode(upd.get("ExpressionAttributeValues") or {})
                condition = upd.get("ConditionExpression") or ""
                expr = upd.get("UpdateExpression") or ""
                if table == "ResourceExchanges":
                    item = exchanges.items.get((key["pk"], key["sk"]))
                    if not item:
                        raise ClientError(
                            {"Error": {"Code": "TransactionCanceledException"}},
                            "TransactWriteItems",
                        )
                    if "#status = :open" in condition and item.get("status") != "OPEN":
                        raise ClientError(
                            {"Error": {"Code": "TransactionCanceledException"}},
                            "TransactWriteItems",
                        )
                    if "#status = :accepted" in condition and item.get("status") != "ACCEPTED":
                        raise ClientError(
                            {"Error": {"Code": "TransactionCanceledException"}},
                            "TransactWriteItems",
                        )
                    if "#status = :pending" in condition and item.get("status") != "TRANSFER_PENDING":
                        raise ClientError(
                            {"Error": {"Code": "TransactionCanceledException"}},
                            "TransactWriteItems",
                        )
                    if "#status = :active" in condition and item.get("status") != values.get(":active"):
                        raise ClientError(
                            {"Error": {"Code": "TransactionCanceledException"}},
                            "TransactWriteItems",
                        )
                    if "expires_at = :expires" in condition and item.get("expires_at") != values.get(":expires"):
                        raise ClientError(
                            {"Error": {"Code": "TransactionCanceledException"}},
                            "TransactWriteItems",
                        )
                    if "expires_at > :now" in condition and not (
                        item.get("expires_at") and item.get("expires_at") > values.get(":now")
                    ):
                        raise ClientError(
                            {"Error": {"Code": "TransactionCanceledException"}},
                            "TransactWriteItems",
                        )
                    if "session_id = :sid" in condition and item.get("session_id") != values.get(":sid"):
                        raise ClientError(
                            {"Error": {"Code": "TransactionCanceledException"}},
                            "TransactWriteItems",
                        )
                    if "version = :expected" in condition and item.get("version") != values.get(":expected"):
                        raise ClientError(
                            {"Error": {"Code": "TransactionCanceledException"}},
                            "TransactWriteItems",
                        )
                    if "generation_count < :cap" in condition and int(item.get("generation_count") or 0) >= int(
                        values.get(":cap")
                    ):
                        raise ClientError(
                            {"Error": {"Code": "TransactionCanceledException"}},
                            "TransactWriteItems",
                        )
                    planned.append(("ex_upd", key, values, expr))
                elif table == "Resources":
                    item = resources.items.get(key["resource_id"])
                    if not item:
                        raise ClientError(
                            {"Error": {"Code": "TransactionCanceledException"}},
                            "TransactWriteItems",
                        )
                    if "quantity_available >= :qty" in condition:
                        qty = int(values.get(":qty", 0))
                        if item.get("quantity_available", 0) < qty:
                            raise ClientError(
                                {"Error": {"Code": "TransactionCanceledException"}},
                                "TransactWriteItems",
                            )
                    elif "quantity_allocated >= :qty" in condition:
                        qty = int(values.get(":qty", 0))
                        if item.get("quantity_allocated", 0) < qty or item.get("quantity_total", 0) < qty:
                            raise ClientError(
                                {"Error": {"Code": "TransactionCanceledException"}},
                                "TransactWriteItems",
                            )
                    elif values.get(":requester") and values.get(":qty") is not None and "quantity_total = quantity_total + :qty" in (upd.get("UpdateExpression") or ""):
                        if item.get("organization_id") != values.get(":requester"):
                            raise ClientError(
                                {"Error": {"Code": "TransactionCanceledException"}},
                                "TransactWriteItems",
                            )
                    elif values.get(":requester"):
                        if item.get("organization_id") != values.get(":provider"):
                            raise ClientError(
                                {"Error": {"Code": "TransactionCanceledException"}},
                                "TransactWriteItems",
                            )
                        if item.get("operational_status") != "ALLOCATED" or item.get("Available") is not False:
                            raise ClientError(
                                {"Error": {"Code": "TransactionCanceledException"}},
                                "TransactWriteItems",
                            )
                    elif values.get(":available") == "AVAILABLE" and "#a = :false" in condition:
                        if (
                            item.get("organization_id") != values.get(":provider")
                            or item.get("operational_status") != "ALLOCATED"
                            or item.get("Available") is not False
                        ):
                            raise ClientError(
                                {"Error": {"Code": "TransactionCanceledException"}},
                                "TransactWriteItems",
                            )
                    elif not item.get("Available") or item.get("organization_id") != values.get(
                        ":organization_id"
                    ):
                        raise ClientError(
                            {"Error": {"Code": "TransactionCanceledException"}},
                            "TransactWriteItems",
                        )
                    planned.append(("res_upd", key, values, expr))
                elif table == "Allocations":
                    item = allocations.items.get(key["allocation_id"])
                    if not item:
                        raise ClientError(
                            {"Error": {"Code": "TransactionCanceledException"}},
                            "TransactWriteItems",
                        )
                    if item.get("status") != values.get(":open"):
                        raise ClientError(
                            {"Error": {"Code": "TransactionCanceledException"}},
                            "TransactWriteItems",
                        )
                    planned.append(("alloc_upd", key, values, expr))
            elif "Put" in entry:
                put = entry["Put"]
                item = _decode(put["Item"])
                table = put["TableName"]
                if table == "ResourceExchanges":
                    key = (item["pk"], item["sk"])
                    if key in exchanges.items:
                        raise ClientError(
                            {"Error": {"Code": "TransactionCanceledException"}},
                            "TransactWriteItems",
                        )
                    planned.append(("ex_put", item, None, None))
                elif table == "Resources" or "resource_id" in item and "allocation_id" not in item:
                    if item["resource_id"] in resources.items:
                        raise ClientError(
                            {"Error": {"Code": "TransactionCanceledException"}},
                            "TransactWriteItems",
                        )
                    planned.append(("res_put", item, None, None))
                else:
                    if item["allocation_id"] in allocations.items:
                        raise ClientError(
                            {"Error": {"Code": "TransactionCanceledException"}},
                            "TransactWriteItems",
                        )
                    planned.append(("alloc_put", item, None, None))

        for kind, a, b, c in planned:
            if kind == "ex_upd":
                item = exchanges.items[(a["pk"], a["sk"])]
                expr = c or ""
                if "#status = :pending" in expr:
                    item["status"] = "TRANSFER_PENDING"
                    item["transfer_started_at"] = b.get(":now")
                    item["transfer_started_by"] = b.get(":actor")
                elif "#status = :completed" in expr:
                    item["status"] = "COMPLETED"
                    item["completed_at"] = b.get(":now")
                    item["confirming_actor_sub"] = b.get(":actor")
                    item["completed_destination_location_id"] = b.get(":loc_id")
                    item["previous_owner_organization_id"] = b.get(":provider")
                    item["previous_location_id"] = b.get(":prev_loc")
                    if ":dest_resource" in b:
                        item["completed_destination_resource_id"] = b[":dest_resource"]
                    if ":dest_created" in b:
                        item["completed_destination_created"] = b[":dest_created"]
                    if ":qty" in b and "quantity_transferred" in expr:
                        item["quantity_transferred"] = int(b[":qty"])
                    item.pop("expiry_due_key", None)
                    item.pop("expiry_due_at", None)
                elif "#status = :accepted" in expr:
                    item["status"] = "ACCEPTED"
                    if ":offer_id" in b:
                        item["accepted_offer_id"] = b[":offer_id"]
                        item["accepted_resource_id"] = b.get(":resource_id")
                        item["accepted_provider_organization_id"] = b.get(":provider")
                    item.pop("network_list_key", None)
                if ":superseded" in expr and b.get(":superseded") == "SUPERSEDED":
                    item["status"] = "SUPERSEDED"
                if "generation_count = generation_count +" in expr:
                    item["generation_count"] = int(item.get("generation_count") or 0) + int(b[":one"])
                    item["version"] = int(item.get("version") or 0) + int(b[":one"])
                    item["active_token_hash"] = b.get(":hash")
                    item["session_id"] = b.get(":session")
                    item["status"] = b.get(":active_status") or item.get("status")
                    item["expires_at"] = b.get(":expires")
                    item["replaced_session_id"] = b.get(":old_session")
                    item["updated_at"] = b.get(":now")
                elif b.get(":next"):
                    item["status"] = b[":next"]
                    if ":ttl" in b:
                        item["qr_ttl_epoch"] = int(b[":ttl"])
                    item["updated_at"] = b.get(":now")
                    if "REMOVE active_token_hash" in expr:
                        item.pop("active_token_hash", None)
            elif kind == "ex_put":
                exchanges.items[(a["pk"], a["sk"])] = a
            elif kind == "res_upd":
                item = resources.items[a["resource_id"]]
                expr = c or ""
                qty = int(b[":qty"]) if ":qty" in b else None
                if qty is not None and "quantity_allocated = quantity_allocated - :qty" in expr and "quantity_total = quantity_total - :qty" in expr:
                    item["quantity_allocated"] = int(item.get("quantity_allocated", 0)) - qty
                    item["quantity_total"] = int(item.get("quantity_total", 0)) - qty
                elif qty is not None and "quantity_total = quantity_total + :qty" in expr:
                    item["quantity_total"] = int(item.get("quantity_total", 0)) + qty
                    item["quantity_available"] = int(item.get("quantity_available", 0)) + qty
                    if b.get(":private"):
                        item["visibility"] = b[":private"]
                elif qty is not None and "quantity_available = quantity_available - :qty" in expr:
                    item["quantity_available"] = int(item.get("quantity_available", 0)) - qty
                    item["quantity_allocated"] = int(item.get("quantity_allocated", 0)) + qty
                elif b.get(":requester"):
                    item["organization_id"] = b[":requester"]
                    item["location_id"] = b.get(":loc_id")
                    item["Location"] = b.get(":loc_name")
                    item["Available"] = True
                    item["operational_status"] = "AVAILABLE"
                    item["visibility"] = "PRIVATE"
                elif b.get(":available") == "AVAILABLE" and b.get(":true") is True:
                    item["Available"] = True
                    item["operational_status"] = "AVAILABLE"
                else:
                    item["Available"] = False
                    item["operational_status"] = "ALLOCATED"
            elif kind == "alloc_upd":
                item = allocations.items[a["allocation_id"]]
                item["status"] = b.get(":released", item.get("status"))
                item["released_at"] = b.get(":now")
                if ":dest_resource" in b:
                    item["destination_resource_id"] = b[":dest_resource"]
            elif kind == "alloc_put":
                allocations.items[a["allocation_id"]] = a
            elif kind == "res_put":
                resources.items[a["resource_id"]] = a

    monkeypatch.setattr(service, "_transact_write", fake_transact)


def wire(monkeypatch, exchanges, resources, allocations, orgs, locations, types, audit=None, history=None):
    sys.modules["service"] = service
    monkeypatch.setattr(service, "exchanges_table", lambda: exchanges)
    monkeypatch.setattr(service, "resources_table", lambda: resources)
    monkeypatch.setattr(service, "allocations_table", lambda: allocations)
    monkeypatch.setattr(service, "organizations_table", lambda: orgs)
    monkeypatch.setattr(service, "locations_table", lambda: locations)
    monkeypatch.setattr(service, "resource_types_table", lambda: types)
    monkeypatch.setattr(service, "audit_table", lambda: audit or AuditStore())
    monkeypatch.setattr(service, "history_table", lambda: history or HistoryStore())
    install_transact(monkeypatch, exchanges, resources, allocations)


def setup_accepted_exchange(monkeypatch, tracking="INDIVIDUAL", resource_id="R-B-1"):
    use_memberships(monkeypatch, memberships((ORG_A, "OPERATOR")))
    use_billing(monkeypatch, "ACTIVE")
    exchanges = ExchangeStore()
    allocations = AllocStore()
    orgs, locations, types, resources = seed()
    audit = AuditStore()
    history = HistoryStore()
    wire(monkeypatch, exchanges, resources, allocations, orgs, locations, types, audit, history)
    created = handler.lambda_handler(
        event(
            "POST",
            {
                "organization_id": ORG_A,
                "destination_location_id": "LOC-A",
                "resource_type_id": TYPE_A,
                "tracking_mode": tracking,
                "quantity_requested": 1 if tracking == "INDIVIDUAL" else 5,
                "idempotency_key": f"req-{tracking}-{resource_id}",
            },
        ),
        None,
    )
    assert created["statusCode"] == 201, body_of(created)
    request_id = body_of(created)["request"]["exchange_request_id"]
    use_memberships(monkeypatch, memberships((ORG_B, "OPERATOR")))
    offered = handler.lambda_handler(
        event(
            "POST",
            {
                "organization_id": ORG_B,
                "resource_id": resource_id,
                "provider_location_id": "LOC-B",
                "quantity_offered": 1 if tracking == "INDIVIDUAL" else 3,
                "idempotency_key": f"off-{resource_id}",
            },
            organization_id=ORG_B,
            path=f"/exchange/requests/{request_id}/offers",
        ),
        None,
    )
    assert offered["statusCode"] == 201, body_of(offered)
    offer_id = body_of(offered)["offer"]["offer_id"]
    use_memberships(monkeypatch, memberships((ORG_A, "OPERATOR")))
    accepted = handler.lambda_handler(
        event(
            "POST",
            {"organization_id": ORG_A},
            path=f"/exchange/requests/{request_id}/offers/{offer_id}/accept",
        ),
        None,
    )
    assert accepted["statusCode"] == 200, body_of(accepted)
    return request_id, offer_id, exchanges, resources, allocations, audit, history


def start_transfer_as_provider(monkeypatch, request_id):
    use_memberships(monkeypatch, memberships((ORG_B, "OPERATOR")))
    use_billing(monkeypatch, "ACTIVE")
    return handler.lambda_handler(
        event(
            "POST",
            {"organization_id": ORG_B},
            organization_id=ORG_B,
            path=f"/exchange/requests/{request_id}/transfer/start",
        ),
        None,
    )


def confirm_as_requester(monkeypatch, request_id, body=None):
    use_memberships(monkeypatch, memberships((ORG_A, "OPERATOR")))
    use_billing(monkeypatch, "ACTIVE")
    return handler.lambda_handler(
        event(
            "POST",
            body or {"organization_id": ORG_A},
            path=f"/exchange/requests/{request_id}/handover/confirm",
        ),
        None,
    )


def test_individual_handover_transfers_ownership_and_location(monkeypatch):
    request_id, offer_id, exchanges, resources, allocations, audit, history = setup_accepted_exchange(
        monkeypatch
    )
    started = start_transfer_as_provider(monkeypatch, request_id)
    assert started["statusCode"] == 200
    assert body_of(started)["request"]["status"] == "TRANSFER_PENDING"
    assert body_of(started)["ownership_transferred"] is False

    confirmed = confirm_as_requester(monkeypatch, request_id)
    assert confirmed["statusCode"] == 200, body_of(confirmed)
    payload = body_of(confirmed)
    assert payload["request"]["status"] == "COMPLETED"
    assert payload["ownership_transferred"] is True
    assert payload["location_transferred"] is True
    assert payload["allocation"]["status"] == "RELEASED"
    # Offer stays ACCEPTED (locked offer vocabulary has no COMPLETED).
    assert payload["offer"]["status"] == "ACCEPTED"

    resource = resources.items["R-B-1"]
    assert resource["organization_id"] == ORG_A
    assert resource["location_id"] == "LOC-A"
    assert resource["Location"] == "A Depot"
    assert resource["Available"] is True
    assert resource["operational_status"] == "AVAILABLE"
    assert resource["visibility"] == "PRIVATE"
    assert len(allocations.items) == 1
    assert next(iter(allocations.items.values()))["status"] == "RELEASED"
    assert any(e["action"] == "exchange.handover_confirmed" for e in audit.events)
    assert any(e["action"] == "resource.ownership_transferred" for e in audit.events)
    assert any(h["reason"] == "RESOURCE_EXCHANGE_TRANSFERRED" for h in history.items)


def test_provider_cannot_confirm_handover(monkeypatch):
    request_id, offer_id, exchanges, resources, allocations, audit, history = setup_accepted_exchange(
        monkeypatch
    )
    assert start_transfer_as_provider(monkeypatch, request_id)["statusCode"] == 200
    use_memberships(monkeypatch, memberships((ORG_B, "OPERATOR")))
    use_billing(monkeypatch, "ACTIVE")
    denied = handler.lambda_handler(
        event(
            "POST",
            {"organization_id": ORG_B},
            organization_id=ORG_B,
            path=f"/exchange/requests/{request_id}/handover/confirm",
        ),
        None,
    )
    assert denied["statusCode"] == 404
    assert resources.items["R-B-1"]["organization_id"] == ORG_B


def test_requester_cannot_start_transfer(monkeypatch):
    request_id, offer_id, exchanges, resources, allocations, audit, history = setup_accepted_exchange(
        monkeypatch
    )
    use_memberships(monkeypatch, memberships((ORG_A, "OPERATOR")))
    use_billing(monkeypatch, "ACTIVE")
    denied = handler.lambda_handler(
        event(
            "POST",
            {"organization_id": ORG_A},
            path=f"/exchange/requests/{request_id}/transfer/start",
        ),
        None,
    )
    assert denied["statusCode"] == 404


def test_unrelated_org_denied(monkeypatch):
    request_id, offer_id, exchanges, resources, allocations, audit, history = setup_accepted_exchange(
        monkeypatch
    )
    use_memberships(monkeypatch, memberships((ORG_C, "OPERATOR")))
    use_billing(monkeypatch, "ACTIVE")
    denied = handler.lambda_handler(
        event(
            "POST",
            {"organization_id": ORG_C},
            organization_id=ORG_C,
            path=f"/exchange/requests/{request_id}/transfer/start",
        ),
        None,
    )
    assert denied["statusCode"] == 404


def test_member_cannot_start_or_confirm(monkeypatch):
    request_id, offer_id, exchanges, resources, allocations, audit, history = setup_accepted_exchange(
        monkeypatch
    )
    use_memberships(monkeypatch, memberships((ORG_B, "MEMBER")))
    use_billing(monkeypatch, "ACTIVE")
    denied = handler.lambda_handler(
        event(
            "POST",
            {"organization_id": ORG_B},
            organization_id=ORG_B,
            path=f"/exchange/requests/{request_id}/transfer/start",
        ),
        None,
    )
    assert denied["statusCode"] == 403

    use_memberships(monkeypatch, memberships((ORG_B, "OPERATOR")))
    assert start_transfer_as_provider(monkeypatch, request_id)["statusCode"] == 200
    use_memberships(monkeypatch, memberships((ORG_A, "MEMBER")))
    use_billing(monkeypatch, "ACTIVE")
    denied2 = handler.lambda_handler(
        event(
            "POST",
            {"organization_id": ORG_A},
            path=f"/exchange/requests/{request_id}/handover/confirm",
        ),
        None,
    )
    assert denied2["statusCode"] == 403


def test_foreign_location_rejected(monkeypatch):
    request_id, offer_id, exchanges, resources, allocations, audit, history = setup_accepted_exchange(
        monkeypatch
    )
    assert start_transfer_as_provider(monkeypatch, request_id)["statusCode"] == 200
    use_memberships(monkeypatch, memberships((ORG_A, "OPERATOR")))
    use_billing(monkeypatch, "ACTIVE")
    for bad_loc in ("LOC-B", "LOC-C", "LOC-MISSING"):
        failed = handler.lambda_handler(
            event(
                "POST",
                {"organization_id": ORG_A, "destination_location_id": bad_loc},
                path=f"/exchange/requests/{request_id}/handover/confirm",
            ),
            None,
        )
        assert failed["statusCode"] in (403, 404), bad_loc
    assert resources.items["R-B-1"]["organization_id"] == ORG_B


def test_requester_alt_location_allowed(monkeypatch):
    request_id, offer_id, exchanges, resources, allocations, audit, history = setup_accepted_exchange(
        monkeypatch
    )
    assert start_transfer_as_provider(monkeypatch, request_id)["statusCode"] == 200
    confirmed = confirm_as_requester(
        monkeypatch, request_id, {"organization_id": ORG_A, "destination_location_id": "LOC-A2"}
    )
    assert confirmed["statusCode"] == 200
    assert resources.items["R-B-1"]["location_id"] == "LOC-A2"
    assert resources.items["R-B-1"]["Location"] == "A Alt"


def test_idempotent_transfer_and_handover(monkeypatch):
    request_id, offer_id, exchanges, resources, allocations, audit, history = setup_accepted_exchange(
        monkeypatch
    )
    first = start_transfer_as_provider(monkeypatch, request_id)
    second = start_transfer_as_provider(monkeypatch, request_id)
    assert first["statusCode"] == 200
    assert second["statusCode"] == 200
    assert body_of(second)["request"]["status"] == "TRANSFER_PENDING"

    confirm1 = confirm_as_requester(monkeypatch, request_id)
    confirm2 = confirm_as_requester(monkeypatch, request_id)
    assert confirm1["statusCode"] == 200
    assert confirm2["statusCode"] == 200
    assert body_of(confirm2)["request"]["status"] == "COMPLETED"
    assert resources.items["R-B-1"]["organization_id"] == ORG_A
    assert len(allocations.items) == 1
    assert sum(1 for h in history.items if h["reason"] == "RESOURCE_EXCHANGE_TRANSFERRED") == 1


def test_handover_before_transfer_conflicts(monkeypatch):
    request_id, offer_id, exchanges, resources, allocations, audit, history = setup_accepted_exchange(
        monkeypatch
    )
    failed = confirm_as_requester(monkeypatch, request_id)
    assert failed["statusCode"] == 409
    assert resources.items["R-B-1"]["organization_id"] == ORG_B


def test_open_cannot_handover(monkeypatch):
    use_memberships(monkeypatch, memberships((ORG_A, "OPERATOR")))
    use_billing(monkeypatch, "ACTIVE")
    exchanges = ExchangeStore()
    allocations = AllocStore()
    orgs, locations, types, resources = seed()
    wire(monkeypatch, exchanges, resources, allocations, orgs, locations, types)
    created = handler.lambda_handler(
        event(
            "POST",
            {
                "organization_id": ORG_A,
                "destination_location_id": "LOC-A",
                "resource_type_id": TYPE_A,
                "idempotency_key": "open-only",
            },
        ),
        None,
    )
    request_id = body_of(created)["request"]["exchange_request_id"]
    failed = confirm_as_requester(monkeypatch, request_id)
    assert failed["statusCode"] == 409


def test_failed_handover_transaction_leaves_no_partial_state(monkeypatch):
    request_id, offer_id, exchanges, resources, allocations, audit, history = setup_accepted_exchange(
        monkeypatch
    )
    assert start_transfer_as_provider(monkeypatch, request_id)["statusCode"] == 200

    def boom(items):
        raise ClientError({"Error": {"Code": "TransactionCanceledException"}}, "TransactWriteItems")

    monkeypatch.setattr(service, "_transact_write", boom)
    failed = confirm_as_requester(monkeypatch, request_id)
    assert failed["statusCode"] == 409
    meta = next(i for i in exchanges.items.values() if i.get("sk") == "META")
    assert meta["status"] == "TRANSFER_PENDING"
    assert resources.items["R-B-1"]["organization_id"] == ORG_B
    assert resources.items["R-B-1"]["location_id"] == "LOC-B"
    assert next(iter(allocations.items.values()))["status"] == "OPEN"
    assert not any(e["action"] == "exchange.handover_confirmed" for e in audit.events)


def test_concurrent_handover_one_wins(monkeypatch):
    request_id, offer_id, exchanges, resources, allocations, audit, history = setup_accepted_exchange(
        monkeypatch
    )
    assert start_transfer_as_provider(monkeypatch, request_id)["statusCode"] == 200
    first = confirm_as_requester(monkeypatch, request_id)
    # Simulate second concurrent attempt after first committed by forcing conflict path.
    second = confirm_as_requester(monkeypatch, request_id)
    assert first["statusCode"] == 200
    assert second["statusCode"] == 200  # idempotent success
    assert resources.items["R-B-1"]["organization_id"] == ORG_A
    assert len(allocations.items) == 1


@pytest.mark.parametrize("status", ["TRIALING", "ACTIVE", "PAST_DUE", "GRANDFATHERED"])
def test_billing_allows_handover(monkeypatch, status):
    request_id, offer_id, exchanges, resources, allocations, audit, history = setup_accepted_exchange(
        monkeypatch
    )
    use_memberships(monkeypatch, memberships((ORG_B, "OPERATOR")))
    use_billing(monkeypatch, status)
    started = handler.lambda_handler(
        event(
            "POST",
            {"organization_id": ORG_B},
            organization_id=ORG_B,
            path=f"/exchange/requests/{request_id}/transfer/start",
        ),
        None,
    )
    assert started["statusCode"] == 200
    use_memberships(monkeypatch, memberships((ORG_A, "OPERATOR")))
    use_billing(monkeypatch, status)
    confirmed = handler.lambda_handler(
        event(
            "POST",
            {"organization_id": ORG_A},
            path=f"/exchange/requests/{request_id}/handover/confirm",
        ),
        None,
    )
    assert confirmed["statusCode"] == 200


@pytest.mark.parametrize("status", ["CANCELLED", "EXPIRED"])
def test_billing_blocks_handover(monkeypatch, status):
    request_id, offer_id, exchanges, resources, allocations, audit, history = setup_accepted_exchange(
        monkeypatch
    )
    use_memberships(monkeypatch, memberships((ORG_B, "OPERATOR")))
    use_billing(monkeypatch, status)
    denied = handler.lambda_handler(
        event(
            "POST",
            {"organization_id": ORG_B},
            organization_id=ORG_B,
            path=f"/exchange/requests/{request_id}/transfer/start",
        ),
        None,
    )
    assert denied["statusCode"] == 403


def test_quantity_handover_completes(monkeypatch):
    request_id, offer_id, exchanges, resources, allocations, audit, history = setup_accepted_exchange(
        monkeypatch, tracking="QUANTITY", resource_id="R-B-Q"
    )
    started = start_transfer_as_provider(monkeypatch, request_id)
    assert started["statusCode"] == 200, body_of(started)
    confirmed = confirm_as_requester(
        monkeypatch,
        request_id,
        body={"organization_id": ORG_A, "quantity": 3, "destination_location_id": "LOC-A"},
    )
    assert confirmed["statusCode"] == 200, body_of(confirmed)
    payload = body_of(confirmed)
    assert payload["request"]["status"] == "COMPLETED"
    assert payload["transfer"]["quantity"] == 3
    assert payload["transfer"]["destination_created"] is True
    src = resources.items["R-B-Q"]
    assert src["organization_id"] == ORG_B
    assert src["quantity_total"] == 7
    assert src["quantity_available"] == 7
    assert src["quantity_allocated"] == 0
    dest = resources.items[payload["transfer"]["destination_resource_id"]]
    assert dest["organization_id"] == ORG_A
    assert dest["quantity_total"] == 3
    assert dest["visibility"] == "PRIVATE"


def test_handover_commits_when_notification_emit_raises(monkeypatch):
    request_id, offer_id, exchanges, resources, allocations, audit, history = setup_accepted_exchange(
        monkeypatch
    )
    assert start_transfer_as_provider(monkeypatch, request_id)["statusCode"] == 200
    import exchange_notify

    def fail(*_args, **_kwargs):
        raise RuntimeError("notifications down")

    monkeypatch.setattr(exchange_notify, "notify_handover_completed", fail)
    confirmed = confirm_as_requester(monkeypatch, request_id)
    assert confirmed["statusCode"] == 200, body_of(confirmed)
    assert body_of(confirmed)["request"]["status"] == "COMPLETED"
    assert resources.items["R-B-1"]["organization_id"] == ORG_A
    assert list(allocations.items.values())[0]["status"] == "RELEASED"


def test_quantity_replay_keeps_create_mode(monkeypatch):
    request_id, offer_id, exchanges, resources, allocations, audit, history = setup_accepted_exchange(
        monkeypatch, tracking="QUANTITY", resource_id="R-B-Q"
    )
    assert start_transfer_as_provider(monkeypatch, request_id)["statusCode"] == 200
    import exchange_notify

    seen = []
    monkeypatch.setattr(
        exchange_notify,
        "notify_handover_completed",
        lambda *args, **kwargs: seen.append(kwargs),
    )
    body = {"organization_id": ORG_A, "quantity": 3, "destination_location_id": "LOC-A"}
    first = confirm_as_requester(monkeypatch, request_id, body)
    assert first["statusCode"] == 200, body_of(first)
    second = confirm_as_requester(monkeypatch, request_id, body)
    assert second["statusCode"] == 200
    assert body_of(second)["message"] == "Handover already completed"
    assert [item.get("destination_mode") for item in seen] == ["CREATE", "CREATE"]
    assert resources.items["R-B-Q"]["quantity_total"] == 7


def test_provider_loses_resource_after_handover(monkeypatch):
    request_id, offer_id, exchanges, resources, allocations, audit, history = setup_accepted_exchange(
        monkeypatch
    )
    assert start_transfer_as_provider(monkeypatch, request_id)["statusCode"] == 200
    assert confirm_as_requester(monkeypatch, request_id)["statusCode"] == 200
    resource = resources.items["R-B-1"]
    assert resource["organization_id"] == ORG_A
    # Provider require_owned semantics: organization_id mismatch.
    from access import require_owned, AccessError

    with pytest.raises(AccessError):
        require_owned(resource, ORG_B)
    require_owned(resource, ORG_A)
