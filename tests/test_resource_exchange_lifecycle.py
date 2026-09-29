"""Phase 7A: exchange cancel / reject / withdraw / expiry / hold release."""

import copy
import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
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

ORG_A = "ORG-A"
ORG_B = "ORG-B"
ORG_C = "ORG-C"
USER = "user-a"
TYPE_A = "RT-TYPE-A"
TYPE_B = "RT-TYPE-B"
DESER = TypeDeserializer()


def load_module(name, relative):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


handler = load_module("exchange_handler_7a", "src/exchange/handler.py")
service = load_module("exchange_service_7a", "src/exchange/service.py")
lifecycle = load_module("exchange_lifecycle_7a", "src/exchange/lifecycle.py")
handler.service = service
# lifecycle late-binds via import service inside functions; point it at this service module
sys.modules["service"] = service
sys.modules["lifecycle"] = lifecycle


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


def event(method="GET", body=None, organization_id=ORG_A, path="/exchange/requests", query=None, role_sub=USER):
    payload = {
        "httpMethod": method,
        "path": path,
        "queryStringParameters": {"organization_id": organization_id, **(query or {})},
        "requestContext": {"authorizer": {"claims": {"sub": role_sub, "token_use": "id"}}},
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
        names = kwargs.get("ExpressionAttributeNames") or {}
        condition = kwargs.get("ConditionExpression") or ""
        expr = kwargs.get("UpdateExpression") or ""

        status_name = names.get("#status", "status")
        current = item.get(status_name) or item.get("status")

        if "#status = :open" in condition and current != values.get(":open"):
            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")
        if "#status = :accepted" in condition and current != values.get(":accepted"):
            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")
        if "#status = :expected" in condition and current != values.get(":expected"):
            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")
        if "#status = :pending" in condition and current != values.get(":pending"):
            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")

        for token in (":cancelled", ":rejected", ":withdrawn", ":expired", ":accepted", ":superseded", ":terminal", ":pending", ":completed"):
            if token in values and "#status =" in expr.replace(" ", ""):
                # Prefer explicit status assignments from SET #status = :x
                pass
        if "SET #status = :cancelled" in expr or values.get(":cancelled") == "CANCELLED" and "#status = :cancelled" in expr:
            item["status"] = "CANCELLED"
        if "SET #status = :rejected" in expr or (":rejected" in values and "#status = :rejected" in expr):
            item["status"] = "REJECTED"
        if "SET #status = :withdrawn" in expr or (":withdrawn" in values and "#status = :withdrawn" in expr):
            item["status"] = "WITHDRAWN"
        if "SET #status = :expired" in expr or (":expired" in values and "#status = :expired" in expr):
            item["status"] = "EXPIRED"
        if "SET #status = :accepted" in expr or (values.get(":accepted") == "ACCEPTED" and "#status = :accepted" in expr):
            item["status"] = "ACCEPTED"
        if values.get(":superseded") == "SUPERSEDED":
            item["status"] = "SUPERSEDED"
        if values.get(":terminal") in {"CANCELLED", "EXPIRED"} and "#status = :terminal" in expr:
            item["status"] = values[":terminal"]
        if values.get(":pending") == "TRANSFER_PENDING" and "#status = :pending" in expr:
            item["status"] = "TRANSFER_PENDING"
        if values.get(":completed") == "COMPLETED" and "#status = :completed" in expr:
            item["status"] = "COMPLETED"
            if ":loc_id" in values and "completed_destination_location_id" in expr:
                item["completed_destination_location_id"] = values[":loc_id"]
            if ":dest_resource" in values and "completed_destination_resource_id" in expr:
                item["completed_destination_resource_id"] = values[":dest_resource"]
            if ":qty" in values and "quantity_transferred" in expr:
                item["quantity_transferred"] = values[":qty"]
            if ":provider" in values and "previous_owner_organization_id" in expr:
                item["previous_owner_organization_id"] = values[":provider"]
            if ":prev_loc" in values and "previous_location_id" in expr:
                item["previous_location_id"] = values[":prev_loc"]
            if ":now" in values:
                item["completed_at"] = values[":now"]
                item["updated_at"] = values[":now"]
            if ":actor" in values:
                item["confirming_actor_sub"] = values[":actor"]
                item["updated_by"] = values[":actor"]
            if "REMOVE expiry_due_key" in expr:
                item.pop("expiry_due_key", None)
                item.pop("expiry_due_at", None)

        for field in (
            "accepted_offer_id",
            "accepted_resource_id",
            "accepted_provider_organization_id",
            "handover_expires_at",
            "expiry_due_key",
            "expiry_due_at",
            "updated_at",
            "updated_by",
            "transfer_started_at",
            "transfer_started_by",
        ):
            token = ":" + field.replace("accepted_", "").replace("handover_", "handover_").split("_")[0]
        # Apply known value keys used in UpdateExpressions
        mapping = {
            ":offer_id": "accepted_offer_id",
            ":resource_id": "accepted_resource_id",
            ":provider": "accepted_provider_organization_id",
            ":handover_due": "handover_expires_at",
            ":due_key": "expiry_due_key",
            ":now": "updated_at",
            ":actor": "updated_by",
        }
        for token, attr in mapping.items():
            if token in values and token.replace(":", "") in expr.replace(attr, "") or token in expr:
                if token in values and (
                    attr in expr or token in expr or f"{attr} = {token}" in expr.replace(" ", "")
                ):
                    if attr == "handover_expires_at" and ":handover_due" in values:
                        item["handover_expires_at"] = values[":handover_due"]
                        if ":due_key" in values:
                            item["expiry_due_key"] = values[":due_key"]
                            item["expiry_due_at"] = values[":handover_due"]
                    elif attr == "accepted_offer_id" and ":offer_id" in values and "accepted_offer_id" in expr:
                        item["accepted_offer_id"] = values[":offer_id"]
                    elif attr == "accepted_resource_id" and ":resource_id" in values and "accepted_resource_id" in expr:
                        item["accepted_resource_id"] = values[":resource_id"]
                    elif attr == "accepted_provider_organization_id" and ":provider" in values and "accepted_provider_organization_id" in expr:
                        item["accepted_provider_organization_id"] = values[":provider"]
                    elif attr == "updated_at" and ":now" in values:
                        item["updated_at"] = values[":now"]
                    elif attr == "updated_by" and ":actor" in values:
                        item["updated_by"] = values[":actor"]

        if "accepted_offer_id = :offer_id" in expr:
            item["accepted_offer_id"] = values.get(":offer_id", item.get("accepted_offer_id"))
        if "accepted_resource_id = :resource_id" in expr:
            item["accepted_resource_id"] = values.get(":resource_id", item.get("accepted_resource_id"))
        if "accepted_provider_organization_id = :provider" in expr:
            item["accepted_provider_organization_id"] = values.get(
                ":provider", item.get("accepted_provider_organization_id")
            )
        if "handover_expires_at = :handover_due" in expr:
            item["handover_expires_at"] = values.get(":handover_due")
            item["expiry_due_key"] = values.get(":due_key", "DUE")
            item["expiry_due_at"] = values.get(":handover_due")

        if "REMOVE network_list_key" in expr:
            item.pop("network_list_key", None)
        if "REMOVE expiry_due_key" in expr or "REMOVE network_list_key, expiry_due_key" in expr.replace(" ", ""):
            item.pop("expiry_due_key", None)
            item.pop("expiry_due_at", None)
        if "expiry_due_key, expiry_due_at" in expr and "REMOVE" in expr:
            item.pop("expiry_due_key", None)
            item.pop("expiry_due_at", None)

        self.items[key] = item

    def query(self, **kwargs):
        items = list(self.items.values())
        index = kwargs.get("IndexName")
        if index == "NetworkOpenRequestIndex":
            items = [i for i in items if i.get("network_list_key") == "OPEN"]
        elif index == "ExpiryDueIndex":
            items = [i for i in items if i.get("expiry_due_key") == "DUE"]
            due_limit = None
            kce = kwargs.get("KeyConditionExpression")
            if kce is not None:
                from boto3.dynamodb.conditions import ConditionExpressionBuilder

                built = ConditionExpressionBuilder().build_expression(kce)
                for value in (built.attribute_value_placeholders or {}).values():
                    if isinstance(value, str) and "T" in value:
                        due_limit = value
            if due_limit:
                items = [i for i in items if str(i.get("expiry_due_at") or "") <= due_limit]
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
        if ":qty" in values:
            values = dict(values)
            values[":qty"] = int(values[":qty"])
        if "Available = :true" in condition or "#a = :true" in condition:
            if not item.get("Available"):
                raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")
        if "operational_status = :allocated" in condition or "operational_status = :allocated" in condition.replace(" ", ""):
            if str(item.get("operational_status", "")).upper() != "ALLOCATED":
                raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")
            if item.get("Available") is not False and "#a = :false" in condition:
                raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")
        if "quantity_available >= :qty" in condition:
            if item.get("quantity_available", 0) < values.get(":qty", 0):
                raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")
            item["quantity_available"] -= values[":qty"]
            item["quantity_allocated"] = item.get("quantity_allocated", 0) + values[":qty"]
            return
        expr = kwargs.get("UpdateExpression") or ""
        if "quantity_allocated >= :qty" in condition:
            if item.get("quantity_allocated", 0) < values.get(":qty", 0):
                raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")
            # Phase 7C complete: allocated-=n AND total-=n (available unchanged).
            if "quantity_total = quantity_total - :qty" in expr:
                if item.get("quantity_total", 0) < values.get(":qty", 0):
                    raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")
                if "organization_id = :provider" in condition and item.get("organization_id") != values.get(":provider"):
                    raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")
                item["quantity_allocated"] -= values[":qty"]
                item["quantity_total"] -= values[":qty"]
                return
            # Hold release: allocated → available
            item["quantity_allocated"] -= values[":qty"]
            item["quantity_available"] = item.get("quantity_available", 0) + values[":qty"]
            return
        if "quantity_total = quantity_total + :qty" in expr:
            if "organization_id = :requester" in condition and item.get("organization_id") != values.get(":requester"):
                raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")
            if "location_id = :loc_id" in condition and item.get("location_id") != values.get(":loc_id"):
                raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")
            if "operational_status = :available" in condition and str(item.get("operational_status", "")).upper() != "AVAILABLE":
                raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")
            item["quantity_total"] = item.get("quantity_total", 0) + values[":qty"]
            item["quantity_available"] = item.get("quantity_available", 0) + values[":qty"]
            if values.get(":private"):
                item["visibility"] = values[":private"]
            return
        if values.get(":op_allocated") == "ALLOCATED" or values.get(":allocated") == "ALLOCATED":
            if values.get(":false") is False or values.get(":op_allocated") == "ALLOCATED":
                item["Available"] = False
                item["operational_status"] = "ALLOCATED"
        if values.get(":available") == "AVAILABLE" and values.get(":true") is True:
            item["Available"] = True
            item["operational_status"] = "AVAILABLE"

    def put_item(self, Item, ConditionExpression=None, **kwargs):
        rid = Item["resource_id"]
        if ConditionExpression and "attribute_not_exists" in ConditionExpression and rid in self.items:
            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "PutItem")
        self.items[rid] = copy.deepcopy(Item)


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
        if "allocation_type = :exchange" in condition and item.get("allocation_type") != values.get(":exchange"):
            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")
        if values.get(":released") == "RELEASED":
            item["status"] = "RELEASED"
            item["released_at"] = values.get(":now")
            if ":dest_resource" in values:
                item["destination_resource_id"] = values[":dest_resource"]
            if ":loc_id" in values and "destination_location_id" in (kwargs.get("UpdateExpression") or ""):
                item["destination_location_id"] = values[":loc_id"]


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
                "name": "A",
                "city": "Bengaluru",
                "state": "KA",
                "status": "ACTIVE",
            },
            {
                "organization_id": ORG_B,
                "location_id": "LOC-B",
                "name": "B",
                "city": "Mysuru",
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
                "Location": "B",
                "name": "Spare",
                "Type": "Medical Kit",
                "Available": True,
                "operational_status": "AVAILABLE",
                "tracking_mode": "INDIVIDUAL",
            }
        ]
    )
    return orgs, locations, types, resources, ExchangeStore(), AllocStore(), AuditStore(), HistoryStore()


def install(monkeypatch, orgs, locations, types, resources, exchanges, allocs, audits, history):
    sys.modules["service"] = service
    monkeypatch.setattr(service, "organizations_table", lambda: orgs)
    monkeypatch.setattr(service, "locations_table", lambda: locations)
    monkeypatch.setattr(service, "resource_types_table", lambda: types)
    monkeypatch.setattr(service, "resources_table", lambda: resources)
    monkeypatch.setattr(service, "exchanges_table", lambda: exchanges)
    monkeypatch.setattr(service, "allocations_table", lambda: allocs)
    monkeypatch.setattr(service, "audit_table", lambda: audits)
    monkeypatch.setattr(service, "history_table", lambda: history)

    def fake_transact(items):
        for entry in items:
            if "Update" in entry:
                upd = entry["Update"]
                key = _decode(upd["Key"])
                values = _decode(upd.get("ExpressionAttributeValues") or {})
                names = upd.get("ExpressionAttributeNames") or {}
                table = upd["TableName"]
                store = {
                    "ResourceExchanges": exchanges,
                    "Resources": resources,
                    "Allocations": allocs,
                }[table]
                store.update_item(
                    Key=key,
                    UpdateExpression=upd.get("UpdateExpression"),
                    ConditionExpression=upd.get("ConditionExpression"),
                    ExpressionAttributeValues=values,
                    ExpressionAttributeNames=names,
                )
            elif "Put" in entry:
                put = entry["Put"]
                item = _decode(put["Item"])
                table = put["TableName"]
                if table == "Allocations":
                    allocs.put_item(item, ConditionExpression=put.get("ConditionExpression"))
                elif table == "Resources":
                    resources.put_item(item, ConditionExpression=put.get("ConditionExpression"))
                else:
                    exchanges.put_item(item, ConditionExpression=put.get("ConditionExpression"))

    monkeypatch.setattr(service, "_transact_write", fake_transact)


def create_open_pair(monkeypatch):
    use_memberships(monkeypatch, memberships((ORG_A, "OWNER"), (ORG_B, "OWNER")))
    use_billing(monkeypatch, "ACTIVE")
    orgs, locations, types, resources, exchanges, allocs, audits, history = seed()
    install(monkeypatch, orgs, locations, types, resources, exchanges, allocs, audits, history)

    created = handler.lambda_handler(
        event(
            "POST",
            {
                "destination_location_id": "LOC-A",
                "resource_type_id": TYPE_A,
                "tracking_mode": "INDIVIDUAL",
                "quantity_requested": 1,
                "visibility": "NETWORK",
                "idempotency_key": "req-1",
            },
            ORG_A,
            "/exchange/requests",
        ),
        None,
    )
    assert created["statusCode"] == 201
    request_id = body_of(created)["request"]["exchange_request_id"]

    offered = handler.lambda_handler(
        event(
            "POST",
            {"resource_id": "R-B-1", "provider_location_id": "LOC-B", "quantity_offered": 1, "idempotency_key": "off-1"},
            ORG_B,
            f"/exchange/requests/{request_id}/offers",
        ),
        None,
    )
    assert offered["statusCode"] == 201
    offer_id = body_of(offered)["offer"]["offer_id"]
    return request_id, offer_id, resources, exchanges, allocs, audits


def test_cancel_open_request(monkeypatch):
    request_id, offer_id, resources, exchanges, allocs, audits = create_open_pair(monkeypatch)
    result = handler.lambda_handler(
        event("POST", {}, ORG_A, f"/exchange/requests/{request_id}/cancel"),
        None,
    )
    assert result["statusCode"] == 200
    assert body_of(result)["request"]["status"] == "CANCELLED"
    offer = exchanges.get_item(
        {"pk": f"EXREQ#{request_id}", "sk": f"OFFER#{offer_id}"}
    )["Item"]
    assert offer["status"] == "CANCELLED"
    assert resources.items["R-B-1"]["Available"] is True
    assert not allocs.items
    again = handler.lambda_handler(
        event("POST", {}, ORG_A, f"/exchange/requests/{request_id}/cancel"),
        None,
    )
    assert again["statusCode"] == 200


def test_reject_and_withdraw(monkeypatch):
    request_id, offer_id, resources, exchanges, allocs, audits = create_open_pair(monkeypatch)

    denied = handler.lambda_handler(
        event("POST", {}, ORG_B, f"/exchange/requests/{request_id}/offers/{offer_id}/reject"),
        None,
    )
    assert denied["statusCode"] in {403, 404}

    rejected = handler.lambda_handler(
        event("POST", {}, ORG_A, f"/exchange/requests/{request_id}/offers/{offer_id}/reject"),
        None,
    )
    assert rejected["statusCode"] == 200
    assert body_of(rejected)["offer"]["status"] == "REJECTED"
    assert resources.items["R-B-1"]["Available"] is True

    request_id2, offer_id2, resources, exchanges, allocs, audits = create_open_pair(monkeypatch)
    withdrawn = handler.lambda_handler(
        event("POST", {}, ORG_B, f"/exchange/requests/{request_id2}/offers/{offer_id2}/withdraw"),
        None,
    )
    assert withdrawn["statusCode"] == 200
    assert body_of(withdrawn)["offer"]["status"] == "WITHDRAWN"


def test_accept_then_cancel_releases_hold(monkeypatch):
    request_id, offer_id, resources, exchanges, allocs, audits = create_open_pair(monkeypatch)
    accepted = handler.lambda_handler(
        event("POST", {}, ORG_A, f"/exchange/requests/{request_id}/offers/{offer_id}/accept"),
        None,
    )
    assert accepted["statusCode"] == 200
    assert resources.items["R-B-1"]["Available"] is False
    assert list(allocs.items.values())[0]["status"] == "OPEN"
    assert list(allocs.items.values())[0]["allocation_type"] == "EXCHANGE"

    cancelled = handler.lambda_handler(
        event("POST", {}, ORG_A, f"/exchange/requests/{request_id}/cancel"),
        None,
    )
    assert cancelled["statusCode"] == 200
    assert body_of(cancelled)["request"]["status"] == "CANCELLED"
    assert resources.items["R-B-1"]["Available"] is True
    assert resources.items["R-B-1"]["operational_status"] == "AVAILABLE"
    assert list(allocs.items.values())[0]["status"] == "RELEASED"
    assert list(allocs.items.values())[0]["allocation_type"] == "EXCHANGE"


def test_transfer_pending_cancel(monkeypatch):
    request_id, offer_id, resources, exchanges, allocs, audits = create_open_pair(monkeypatch)
    assert handler.lambda_handler(
        event("POST", {}, ORG_A, f"/exchange/requests/{request_id}/offers/{offer_id}/accept"),
        None,
    )["statusCode"] == 200
    assert handler.lambda_handler(
        event("POST", {}, ORG_B, f"/exchange/requests/{request_id}/transfer/start"),
        None,
    )["statusCode"] == 200
    cancelled = handler.lambda_handler(
        event("POST", {}, ORG_A, f"/exchange/requests/{request_id}/cancel"),
        None,
    )
    assert cancelled["statusCode"] == 200
    assert body_of(cancelled)["request"]["status"] == "CANCELLED"
    assert resources.items["R-B-1"]["organization_id"] == ORG_B
    assert resources.items["R-B-1"]["Available"] is True


def test_completed_cannot_cancel(monkeypatch):
    request_id, offer_id, resources, exchanges, allocs, audits = create_open_pair(monkeypatch)
    assert handler.lambda_handler(
        event("POST", {}, ORG_A, f"/exchange/requests/{request_id}/offers/{offer_id}/accept"),
        None,
    )["statusCode"] == 200
    assert handler.lambda_handler(
        event("POST", {}, ORG_B, f"/exchange/requests/{request_id}/transfer/start"),
        None,
    )["statusCode"] == 200
    completed = handler.lambda_handler(
        event(
            "POST",
            {"destination_location_id": "LOC-A"},
            ORG_A,
            f"/exchange/requests/{request_id}/handover/confirm",
        ),
        None,
    )
    assert completed["statusCode"] == 200
    blocked = handler.lambda_handler(
        event("POST", {}, ORG_A, f"/exchange/requests/{request_id}/cancel"),
        None,
    )
    assert blocked["statusCode"] == 409


def test_withdraw_accepted_rejected(monkeypatch):
    request_id, offer_id, resources, exchanges, allocs, audits = create_open_pair(monkeypatch)
    assert handler.lambda_handler(
        event("POST", {}, ORG_A, f"/exchange/requests/{request_id}/offers/{offer_id}/accept"),
        None,
    )["statusCode"] == 200
    blocked = handler.lambda_handler(
        event("POST", {}, ORG_B, f"/exchange/requests/{request_id}/offers/{offer_id}/withdraw"),
        None,
    )
    assert blocked["statusCode"] == 409
    assert resources.items["R-B-1"]["Available"] is False


def test_billing_blocks_cancel(monkeypatch):
    request_id, offer_id, resources, exchanges, allocs, audits = create_open_pair(monkeypatch)
    use_billing(monkeypatch, "EXPIRED")
    blocked = handler.lambda_handler(
        event("POST", {}, ORG_A, f"/exchange/requests/{request_id}/cancel"),
        None,
    )
    assert blocked["statusCode"] == 403
    assert body_of(blocked).get("error", {}).get("code") == "BILLING_REQUIRED"


def test_member_cannot_cancel(monkeypatch):
    request_id, offer_id, resources, exchanges, allocs, audits = create_open_pair(monkeypatch)
    use_memberships(monkeypatch, memberships((ORG_A, "MEMBER"), (ORG_B, "OWNER")))
    blocked = handler.lambda_handler(
        event("POST", {}, ORG_A, f"/exchange/requests/{request_id}/cancel", role_sub="member-1"),
        None,
    )
    assert blocked["statusCode"] == 403


def test_unrelated_tenant_cannot_cancel(monkeypatch):
    request_id, offer_id, resources, exchanges, allocs, audits = create_open_pair(monkeypatch)
    use_memberships(monkeypatch, memberships((ORG_C, "OWNER"), (ORG_A, "OWNER"), (ORG_B, "OWNER")))
    blocked = handler.lambda_handler(
        event("POST", {}, ORG_C, f"/exchange/requests/{request_id}/cancel"),
        None,
    )
    assert blocked["statusCode"] in {403, 404}


def test_open_request_expiry(monkeypatch):
    request_id, offer_id, resources, exchanges, allocs, audits = create_open_pair(monkeypatch)
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    meta_key = (f"EXREQ#{request_id}", "META")
    exchanges.items[meta_key]["expires_at"] = past
    exchanges.items[meta_key]["expiry_due_key"] = "DUE"
    exchanges.items[meta_key]["expiry_due_at"] = past
    offer_key = (f"EXREQ#{request_id}", f"OFFER#{offer_id}")
    exchanges.items[offer_key]["expires_at"] = past
    exchanges.items[offer_key]["expiry_due_key"] = "DUE"
    exchanges.items[offer_key]["expiry_due_at"] = past

    counts = lifecycle.run_expiry(datetime.now(timezone.utc), "test")
    assert counts["expired"] >= 1
    assert exchanges.items[meta_key]["status"] == "EXPIRED"
    assert exchanges.items[offer_key]["status"] == "EXPIRED"
    assert resources.items["R-B-1"]["Available"] is True


def test_accepted_hold_expiry_releases(monkeypatch):
    request_id, offer_id, resources, exchanges, allocs, audits = create_open_pair(monkeypatch)
    assert handler.lambda_handler(
        event("POST", {}, ORG_A, f"/exchange/requests/{request_id}/offers/{offer_id}/accept"),
        None,
    )["statusCode"] == 200
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    meta_key = (f"EXREQ#{request_id}", "META")
    exchanges.items[meta_key]["handover_expires_at"] = past
    exchanges.items[meta_key]["expiry_due_key"] = "DUE"
    exchanges.items[meta_key]["expiry_due_at"] = past

    counts = lifecycle.run_expiry(datetime.now(timezone.utc), "test")
    assert counts["expired"] >= 1
    assert exchanges.items[meta_key]["status"] == "EXPIRED"
    assert resources.items["R-B-1"]["Available"] is True
    assert list(allocs.items.values())[0]["status"] == "RELEASED"


def test_frontend_has_lifecycle_actions():
    script = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
    assert "Cancel Request" in script
    assert "Reject Offer" in script
    assert "Withdraw Offer" in script
    assert "/cancel" in script
    assert "/reject" in script
    assert "/withdraw" in script


def test_expiry_index_declared():
    import exchange_model

    assert exchange_model.INDEX_EXPIRY_DUE == "ExpiryDueIndex"
    infra = json.loads((ROOT / "infra" / "resource-exchanges-table.json").read_text(encoding="utf-8"))
    names = [g["IndexName"] for g in infra["tables"][0]["GlobalSecondaryIndexes"]]
    assert "ExpiryDueIndex" in names
