"""RM-12: individual reservations expire two hours after the server reserved them."""

import copy
import inspect
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import ensure_reservation_expiry
import everyday_operations as everyday
import lifecycle_operations as lifecycle
import reservation_expiry
from api_views import RESOURCE_FIELDS, resource_history_view
from lambda_manifest import BILLING_PACKAGES, PACKAGES, package_map
from resource_state import (
    RESERVATION_DUE_INDEX,
    RESERVATION_DUE_KEY,
    RESERVATION_DURATION_HOURS,
    RESERVATION_HELD_ATTRIBUTES,
    emergency_matchable,
    initialize_new_resource_fields,
    lifecycle_fields_from_body,
    reservation_due_values,
)

ORG = "ORG-A66B0A1E4F96"
OTHER = "ORG-17D0E2939B2D"
ACTOR = "operator-a"
OTHER_ACTOR = "operator-b"
WHEN = "2026-10-08T10:00:00+00:00"
DUE = "2026-10-08T12:00:00+00:00"
LATER = "2026-10-08T13:00:00+00:00"


def _failed():
    return ClientError(
        {"Error": {"Code": "ConditionalCheckFailedException", "Message": "conflict"}},
        "UpdateItem",
    )


def _decode(value):
    if isinstance(value, dict) and "S" in value:
        return value["S"]
    if isinstance(value, dict) and "N" in value:
        return int(value["N"])
    if isinstance(value, dict) and "BOOL" in value:
        return value["BOOL"]
    if isinstance(value, dict) and len(value) == 1:
        return next(iter(value.values()))
    return value


def _condition_literals(expression):
    found = []
    raw = expression.get_expression()
    for value in raw.get("values") or ():
        if hasattr(value, "get_expression"):
            found.extend(_condition_literals(value))
        else:
            found.append(value)
    return found


class Store:
    def __init__(self, items):
        rows = items if isinstance(items, list) else [items]
        self.items = {row["resource_id"]: copy.deepcopy(row) for row in rows}
        self.queries = []
        self.race = ""
        self.allocations = []
        self.meta = type("Meta", (), {"client": self})()

    def query(self, **kwargs):
        self.queries.append(kwargs)
        if kwargs.get("IndexName") != RESERVATION_DUE_INDEX:
            raise AssertionError("worker queried the wrong index")
        if kwargs.get("Limit") != reservation_expiry.BATCH_LIMIT:
            raise AssertionError("worker query is not bounded")
        literals = _condition_literals(kwargs["KeyConditionExpression"])
        if RESERVATION_DUE_KEY not in literals:
            raise AssertionError("worker query missed the sparse partition")
        now_text = next(value for value in literals if isinstance(value, str) and value != RESERVATION_DUE_KEY)
        due = [
            copy.deepcopy(item)
            for item in self.items.values()
            if item.get("reservation_due_key") == RESERVATION_DUE_KEY
            and str(item.get("reservation_expires_at") or "") <= now_text
        ]
        due.sort(key=lambda item: item["reservation_expires_at"])
        page = due[: kwargs["Limit"]]
        result = {"Items": page}
        if len(due) > kwargs["Limit"]:
            result["LastEvaluatedKey"] = {"resource_id": page[-1]["resource_id"]}
        return result

    def update_item(self, **kwargs):
        item = self.items[kwargs["Key"]["resource_id"]]
        values = kwargs.get("ExpressionAttributeValues") or {}
        condition = kwargs.get("ConditionExpression") or ""
        expression = kwargs.get("UpdateExpression") or ""
        if self.race and "reservation_expires_at <= :now" in condition:
            self._lose_reservation(item, self.race)
            self.race = ""
        self._check(item, condition, values)
        if expression.startswith("REMOVE"):
            for name in expression.split("REMOVE", 1)[1].split(","):
                item.pop(name.strip(), None)
            return
        if "operational_status = :reserved" in expression and "REMOVE" not in expression:
            item["operational_status"] = values[":reserved"]
            item["Available"] = values[":false"]
            item["reserved_by"] = values[":actor"]
            item["reserved_at"] = values[":now"]
            item["reservation_expires_at"] = values[":expires"]
            item["reservation_due_key"] = values[":due"]
            return
        if "operational_status = :allocated" in expression:
            item["operational_status"] = values[":allocated"]
            item["Available"] = values[":false"]
        elif "operational_status = :target" in expression:
            item["operational_status"] = values[":target"]
            item["Available"] = values[":available"]
            item["updated_at"] = values.get(":now")
        elif "operational_status = :available" in expression:
            item["operational_status"] = values[":available"]
            item["Available"] = values[":true"]
        elif "quantity_available" in expression:
            item["quantity_available"] = item["quantity_available"] - values[":qty"]
            item["quantity_reserved"] = item["quantity_reserved"] + values[":qty"]
        if "REMOVE" in expression:
            for name in expression.split("REMOVE", 1)[1].split(","):
                item.pop(name.strip(), None)

    def transact_write_items(self, TransactItems):
        for step in TransactItems:
            if "Update" in step:
                update = step["Update"]
                values = {key: _decode(value) for key, value in update["ExpressionAttributeValues"].items()}
                self.update_item(
                    Key={"resource_id": _decode(update["Key"]["resource_id"])},
                    UpdateExpression=update["UpdateExpression"],
                    ConditionExpression=update.get("ConditionExpression") or "",
                    ExpressionAttributeValues=values,
                )
            if "Put" in step:
                self.allocations.append({key: _decode(value) for key, value in step["Put"]["Item"].items()})

    def _lose_reservation(self, item, race):
        for name in RESERVATION_HELD_ATTRIBUTES:
            item.pop(name, None)
        if race == "allocate":
            item["operational_status"] = "ALLOCATED"
            item["Available"] = False
        else:
            item["operational_status"] = "AVAILABLE"
            item["Available"] = True

    def _check(self, item, condition, values):
        if "organization_id = :organization_id" in condition and item.get("organization_id") != values.get(":organization_id"):
            raise _failed()
        if "Available = :true" in condition and item.get("Available") is not True:
            raise _failed()
        if "OR operational_status = :available" in condition:
            stored = item.get("operational_status")
            if stored is not None and stored != "AVAILABLE":
                raise _failed()
        if "reservation_expires_at = :expires" in condition and item.get("reservation_expires_at") != values.get(":expires"):
            raise _failed()
        if "operational_status <> :reserved" in condition:
            same = (
                item.get("operational_status") == "RESERVED"
                and item.get("reserved_by") == values.get(":actor")
                and item.get("reserved_at") == values.get(":reserved_at")
            )
            if same:
                raise _failed()
            return
        if "operational_status = :reserved" in condition and item.get("operational_status") != values.get(":reserved", "RESERVED"):
            raise _failed()
        if "operational_status = :current" in condition and item.get("operational_status") != values.get(":current"):
            raise _failed()
        if "reserved_by = :actor" in condition and item.get("reserved_by") != values.get(":actor"):
            raise _failed()
        if "reserved_at = :reserved_at" in condition and item.get("reserved_at") != values.get(":reserved_at"):
            raise _failed()
        if "reservation_expires_at = :expires" in condition and item.get("reservation_expires_at") != values.get(":expires"):
            raise _failed()
        if "reservation_expires_at <= :now" in condition and str(item.get("reservation_expires_at")) > str(values.get(":now")):
            raise _failed()


class Rows:
    def __init__(self):
        self.rows = []

    def put_item(self, Item, ConditionExpression=None):
        del ConditionExpression
        self.rows.append(dict(Item))


class Notes:
    def __init__(self, fail=False):
        self.fail = fail
        self.items = {}

    def put_item(self, Item, ConditionExpression=None):
        if self.fail:
            raise RuntimeError("notification unavailable")
        key = (Item["pk"], Item["sk"])
        if ConditionExpression and key in self.items:
            raise _failed()
        self.items[key] = dict(Item)

    def get_item(self, Key):
        return {"Item": self.items.get((Key["pk"], Key["sk"]))}

    def update_item(self, **kwargs):
        item = self.items[(kwargs["Key"]["pk"], kwargs["Key"]["sk"])]
        item["fanout_status"] = "COMPLETE"


class Members:
    def __init__(self, rows):
        self.rows = rows
        self.queries = 0

    def query(self, **kwargs):
        del kwargs
        self.queries += 1
        return {"Items": self.rows}


class Organizations:
    def __init__(self, status):
        self.status = status

    def get_item(self, Key):
        return {"Item": {"organization_id": Key["organization_id"], "status": self.status}}


def individual(resource_id="R1", **extra):
    item = {
        "resource_id": resource_id,
        "organization_id": ORG,
        "name": "Trauma kit",
        "Available": True,
        "operational_status": "AVAILABLE",
        "tracking_mode": "INDIVIDUAL",
        "location_id": "LOC1",
        "Type": "Kit",
        "Location": "HQ",
    }
    item.update(extra)
    return item


def reserved_item(**extra):
    item = individual()
    item.update(
        {
            "Available": False,
            "operational_status": "RESERVED",
            "reserved_by": ACTOR,
            "reserved_at": WHEN,
            "reservation_expires_at": DUE,
            "reservation_due_key": RESERVATION_DUE_KEY,
        }
    )
    item.update(extra)
    return item


def tables_for(item, status="ACTIVE", fail_notification=False):
    store = item if isinstance(item, Store) else Store(item)
    return {
        "resources": store,
        "history": Rows(),
        "audit": Rows(),
        "notifications": Notes(fail=fail_notification),
        "members": Members(
            [
                {
                    "organization_id": ORG,
                    "user_sub": ACTOR,
                    "role": "OPERATOR",
                    "status": "ACTIVE",
                }
            ]
        ),
        "organizations": Organizations(status),
        "allocations": type("Alloc", (), {"allocation_items": {}})(),
    }


def reserve(tables, body=None):
    payload = {"resource_id": "R1", "reservation_expires_at": "1999-01-01T00:00:00+00:00", "reserved_by": OTHER_ACTOR, "reserved_at": "1999-01-01T00:00:00+00:00"}
    if body:
        payload.update(body)
    return everyday.reserve_individual(payload, ORG, ACTOR, "OPERATOR", tables["resources"].items["R1"], tables)


def run_at(tables, moment=DUE):
    clock = datetime.fromisoformat(moment)
    return reservation_expiry.run_reservation_expiry(clock, tables)


def test_server_writes_a_two_hour_due_time(monkeypatch):
    monkeypatch.setattr(everyday, "_now", lambda: WHEN)
    tables = tables_for(individual())
    reserve(tables)
    item = tables["resources"].items["R1"]
    assert RESERVATION_DURATION_HOURS == 2
    assert item["reserved_by"] == ACTOR
    assert item["reserved_at"] == WHEN
    assert item["reservation_due_key"] == RESERVATION_DUE_KEY
    expires = datetime.fromisoformat(item["reservation_expires_at"])
    reserved = datetime.fromisoformat(item["reserved_at"])
    assert expires - reserved == timedelta(hours=2)
    assert item["reservation_expires_at"] != "1999-01-01T00:00:00+00:00"


def test_client_cannot_set_reservation_fields():
    blocked = lifecycle_fields_from_body(
        {
            "reservation_expires_at": DUE,
            "reservation_due_key": RESERVATION_DUE_KEY,
            "reserved_by": OTHER_ACTOR,
            "reserved_at": WHEN,
        }
    )
    assert blocked == {"reservation_expires_at", "reservation_due_key", "reserved_by", "reserved_at"}
    created = initialize_new_resource_fields(
        {"tracking_mode": "INDIVIDUAL", "reservation_expires_at": DUE, "reserved_by": OTHER_ACTOR}
    )
    assert "reservation_expires_at" not in created
    assert "reserved_by" not in created


def test_due_reservation_becomes_available_and_clears_reservation_fields():
    tables = tables_for(reserved_item())
    assert run_at(tables) == {"expired": 1, "skipped": 0}
    item = tables["resources"].items["R1"]
    assert item["operational_status"] == "AVAILABLE"
    assert item["Available"] is True
    for name in RESERVATION_HELD_ATTRIBUTES:
        assert name not in item
    assert emergency_matchable(item) is True


def test_repeat_expiry_is_harmless():
    tables = tables_for(reserved_item())
    run_at(tables)
    assert run_at(tables) == {"expired": 0, "skipped": 0}
    assert tables["resources"].items["R1"]["operational_status"] == "AVAILABLE"
    assert len(tables["history"].rows) == 1
    assert len(tables["notifications"].items) >= 1


def test_manual_release_removes_the_due_key_before_expiry(monkeypatch):
    monkeypatch.setattr(everyday, "_now", lambda: WHEN)
    tables = tables_for(individual())
    reserve(tables)
    everyday.release_reservation({"resource_id": "R1"}, ORG, ACTOR, "OPERATOR", tables["resources"].items["R1"], tables)
    snapshot = reserved_item()
    assert reservation_expiry.expire_reservation(tables, snapshot, LATER) == "skipped"
    item = tables["resources"].items["R1"]
    assert item["operational_status"] == "AVAILABLE"
    for name in RESERVATION_HELD_ATTRIBUTES:
        assert name not in item
    assert [row for row in tables["history"].rows if row.get("reason") == "RESERVATION_EXPIRED"] == []


def test_allocation_removes_the_due_key_and_blocks_expiry(monkeypatch):
    monkeypatch.setattr(everyday, "_now", lambda: WHEN)
    tables = tables_for(individual())
    reserve(tables)
    snapshot = copy.deepcopy(tables["resources"].items["R1"])
    everyday.everyday_allocate_individual({}, ORG, ACTOR, "OPERATOR", tables["resources"].items["R1"], tables)
    assert reservation_expiry.expire_reservation(tables, snapshot, LATER) == "skipped"
    item = tables["resources"].items["R1"]
    assert item["operational_status"] == "ALLOCATED"
    for name in RESERVATION_HELD_ATTRIBUTES:
        assert name not in item


@pytest.mark.parametrize(
    ("operation", "status"),
    [
        (lifecycle.start_maintenance, "MAINTENANCE"),
        (lifecycle.mark_damaged, "DAMAGED"),
    ],
)
def test_maintenance_and_damage_close_the_reservation(operation, status):
    tables = tables_for(reserved_item())
    snapshot = copy.deepcopy(tables["resources"].items["R1"])
    operation({}, ORG, ACTOR, "OPERATOR", tables["resources"].items["R1"], tables)
    assert reservation_expiry.expire_reservation(tables, snapshot, LATER) == "skipped"
    item = tables["resources"].items["R1"]
    assert item["operational_status"] == status
    for name in RESERVATION_HELD_ATTRIBUTES:
        assert name not in item


def test_replacement_reservation_survives_the_old_due_item():
    tables = tables_for(
        reserved_item(
            reserved_by=OTHER_ACTOR,
            reserved_at=LATER,
            reservation_expires_at="2026-10-08T15:00:00+00:00",
        )
    )
    old = reserved_item()
    assert reservation_expiry.expire_reservation(tables, old, "2026-10-08T16:00:00+00:00") == "skipped"
    item = tables["resources"].items["R1"]
    assert item["operational_status"] == "RESERVED"
    assert item["reserved_by"] == OTHER_ACTOR
    assert item["reserved_at"] == LATER
    assert item["reservation_expires_at"] == "2026-10-08T15:00:00+00:00"
    assert item["reservation_due_key"] == RESERVATION_DUE_KEY


@pytest.mark.parametrize("race", ["allocate", "release"])
def test_concurrent_transition_wins_over_expiry(race):
    tables = tables_for(reserved_item())
    tables["resources"].race = race
    assert run_at(tables) == {"expired": 0, "skipped": 1}
    item = tables["resources"].items["R1"]
    assert item["operational_status"] == ("ALLOCATED" if race == "allocate" else "AVAILABLE")
    for name in RESERVATION_HELD_ATTRIBUTES:
        assert name not in item
    assert tables["history"].rows == []
    assert tables["notifications"].items == {}


def test_cross_tenant_due_item_does_not_change_the_row():
    tables = tables_for(reserved_item())
    foreign = reserved_item(organization_id=OTHER)
    assert reservation_expiry.expire_reservation(tables, foreign, LATER) == "skipped"
    item = tables["resources"].items["R1"]
    assert item["organization_id"] == ORG
    assert item["operational_status"] == "RESERVED"
    assert item["reserved_by"] == ACTOR
    assert tables["history"].rows == []


def test_inactive_organization_still_expires_without_fanout():
    tables = tables_for(reserved_item(), status="INACTIVE")
    assert run_at(tables)["expired"] == 1
    assert tables["resources"].items["R1"]["operational_status"] == "AVAILABLE"
    assert tables["members"].queries == 0
    inbox = [item for item in tables["notifications"].items.values() if item.get("entity_type") == "NOTIFICATION"]
    assert inbox == []


def test_human_reserve_route_still_authorizes_and_worker_does_not():
    handler = (ROOT / "src" / "resource" / "handler.py").read_text(encoding="utf-8")
    assert handler.index("authorize(") < handler.index('path.endswith("/reserve")')
    worker = (ROOT / "src" / "shared" / "reservation_expiry.py").read_text(encoding="utf-8")
    assert "billing" not in worker
    assert "authorize(" not in worker
    assert ".scan(" not in worker
    assert "OrganizationLocationIndex" not in worker
    entry = (ROOT / "src" / "reservation_expiry_handler.py").read_text(encoding="utf-8")
    assert "authorize(" not in entry
    assert ".scan(" not in entry


def test_quantity_reservation_does_not_gain_an_expiry():
    source = inspect.getsource(everyday.reserve_quantity)
    assert "reservation_expires_at" not in source
    assert "reservation_expires_at" in inspect.getsource(everyday.reserve_individual)
    pool = {
        "resource_id": "Q1",
        "organization_id": ORG,
        "tracking_mode": "QUANTITY",
        "operational_status": "AVAILABLE",
        "quantity_available": 5,
        "quantity_reserved": 1,
        "quantity_allocated": 0,
    }
    tables = tables_for(pool)
    everyday.reserve_quantity({"resource_id": "Q1", "quantity": 2}, ORG, ACTOR, "OPERATOR", pool, tables)
    item = tables["resources"].items["Q1"]
    assert item["quantity_available"] == 3
    assert item["quantity_reserved"] == 3
    for name in RESERVATION_HELD_ATTRIBUTES:
        assert name not in item
    poisoned = dict(pool)
    poisoned.update(
        {
            "tracking_mode": "QUANTITY",
            "reservation_due_key": RESERVATION_DUE_KEY,
            "reservation_expires_at": DUE,
            "reserved_by": ACTOR,
            "reserved_at": WHEN,
        }
    )
    tables["resources"].items["Q1"] = poisoned
    assert reservation_expiry.expire_reservation(tables, poisoned, LATER) == "skipped"
    assert tables["resources"].items["Q1"]["quantity_available"] == 5
    assert tables["resources"].items["Q1"]["quantity_reserved"] == 1
    assert "reservation_due_key" not in tables["resources"].items["Q1"]
    assert "reservation_expires_at" not in tables["resources"].items["Q1"]


def test_expiry_history_uses_the_system_actor_and_stays_off_the_api():
    tables = tables_for(reserved_item())
    run_at(tables)
    history = tables["history"].rows[0]
    assert history["actor_sub"] == "system"
    assert history["previous_status"] == "RESERVED"
    assert history["new_status"] == "AVAILABLE"
    assert history["reason"] == "RESERVATION_EXPIRED"
    assert resource_history_view(history).get("actor_sub") is None
    audit = tables["audit"].rows[0]
    assert audit["actor_sub"] == "system"
    assert audit["actor_role"] == "SYSTEM"
    assert audit["action"] == "resource.reservation_expired"
    for name in ("reservation_expires_at", "reservation_due_key", "reserved_by", "reserved_at"):
        assert name not in RESOURCE_FIELDS


def test_notification_follows_a_successful_expiry_only():
    tables = tables_for(reserved_item())
    run_at(tables)
    inbox = [item for item in tables["notifications"].items.values() if item.get("entity_type") == "NOTIFICATION"]
    assert len(inbox) == 1
    assert inbox[0]["title"] == "Reservation expired"
    assert "Trauma kit" in inbox[0]["body"]
    assert inbox[0]["user_sub"] == ACTOR
    assert inbox[0]["payload"]["href_kind"] == "resource"
    assert "reserved_by" not in inbox[0]["payload"]
    assert "#" not in inbox[0]["event_id"].split("#")[1]

    skipped = tables_for(reserved_item(operational_status="ALLOCATED", Available=False))
    for name in RESERVATION_HELD_ATTRIBUTES:
        skipped["resources"].items["R1"].pop(name, None)
    snapshot = reserved_item()
    assert reservation_expiry.expire_reservation(skipped, snapshot, LATER) == "skipped"
    assert skipped["notifications"].items == {}


def test_notification_failure_does_not_roll_back_expiry():
    tables = tables_for(reserved_item(), fail_notification=True)
    assert run_at(tables)["expired"] == 1
    assert tables["resources"].items["R1"]["operational_status"] == "AVAILABLE"
    assert "reserved_by" not in tables["resources"].items["R1"]


def test_worker_reads_one_bounded_index_page_and_continues_later():
    items = []
    for number in range(30):
        items.append(
            reserved_item(
                resource_id=f"R{number:02d}",
                reservation_expires_at=f"2026-10-08T11:{number:02d}:00+00:00",
            )
        )
    tables = tables_for(items)
    first = run_at(tables, "2026-10-08T12:00:00+00:00")
    assert first == {"expired": 25, "skipped": 0}
    assert len(tables["resources"].queries) == 1
    assert "ExclusiveStartKey" not in tables["resources"].queries[0]
    remaining = [
        item for item in tables["resources"].items.values() if item.get("reservation_due_key") == RESERVATION_DUE_KEY
    ]
    assert len(remaining) == 5
    second = run_at(tables, "2026-10-08T12:00:00+00:00")
    assert second == {"expired": 5, "skipped": 0}
    assert len(tables["resources"].queries) == 2


def test_row_without_a_due_key_is_not_selected():
    legacy = reserved_item()
    legacy.pop("reservation_due_key")
    legacy.pop("reservation_expires_at")
    tables = tables_for(legacy)
    assert run_at(tables) == {"expired": 0, "skipped": 0}
    assert tables["resources"].items["R1"]["operational_status"] == "RESERVED"
    assert len(tables["resources"].queries) == 1


def test_release_remains_owner_only():
    source = inspect.getsource(everyday.release_reservation)
    assert "reserved_by = :actor" in source
    assert "ADMIN" not in source
    tables = tables_for(reserved_item())
    with pytest.raises(everyday.EverydayOperationError) as error:
        everyday.release_reservation(
            {"resource_id": "R1", "reserved_by": ACTOR},
            ORG,
            OTHER_ACTOR,
            "OWNER",
            tables["resources"].items["R1"],
            tables,
        )
    assert error.value.status_code == 409
    assert tables["resources"].items["R1"]["reserved_by"] == ACTOR


def test_infrastructure_is_least_privilege_and_not_applied():
    spec = json.loads((ROOT / "infra" / "reservation-expiry.json").read_text(encoding="utf-8"))
    assert spec["applied"] is False
    assert spec["schedule"]["schedule_expression"] == "rate(5 minutes)"
    assert spec["schedule"]["name"] == "erap-reservation-expiry"
    assert spec["index"]["name"] == "ReservationDueIndex"
    actions = []
    resources = []
    for statement in spec["iam"]["Statement"] + spec["scheduler_iam"]["Statement"]:
        action = statement["Action"]
        resource = statement["Resource"]
        actions.extend(action if isinstance(action, list) else [action])
        resources.extend(resource if isinstance(resource, list) else [resource])
    assert "dynamodb:Scan" not in actions
    assert "dynamodb:*" not in actions
    assert "*" not in actions
    assert "*" not in resources
    assert "arn:aws:dynamodb:eu-north-1:481838970142:table/Resources/index/ReservationDueIndex" in resources
    assert "arn:aws:logs:eu-north-1:481838970142:log-group:/aws/lambda/erap-reservation-expiry:*" in resources
    policy = json.loads((ROOT / "infra" / "github-production-reservation-expiry-policy.json").read_text(encoding="utf-8"))
    assert policy["Statement"][0]["Resource"] == [
        "arn:aws:lambda:eu-north-1:481838970142:function:erap-reservation-expiry",
        "arn:aws:lambda:eu-north-1:481838970142:function:erap-reservation-expiry:*",
    ]
    assert "lambda:CreateAlias" in policy["Statement"][0]["Action"]
    assert "lambda:DeleteAlias" not in policy["Statement"][0]["Action"]
    assert "lambda:UpdateFunctionConfiguration" not in policy["Statement"][0]["Action"]
    assert "lambda:*" not in policy["Statement"][0]["Action"]
    backend = (ROOT / "scripts" / "deploy_backend.py").read_text(encoding="utf-8")
    assert "erap-reservation-expiry" not in backend
    assert "erap-reservation-expiry" not in PACKAGES
    assert "erap-reservation-expiry" not in BILLING_PACKAGES
    assert "erap-reservation-expiry" in package_map()


def test_ensure_script_does_not_call_aws_without_apply(capsys):
    assert ensure_reservation_expiry.main([]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["applied"] is False
    assert report["index"] == "ReservationDueIndex"
    source = inspect.getsource(ensure_reservation_expiry.main)
    assert "--apply" in source


def test_reservation_workflows_publish_only_the_worker():
    release = (ROOT / ".github" / "workflows" / "release-reservation-expiry.yml").read_text(encoding="utf-8")
    rollback = (ROOT / ".github" / "workflows" / "rollback-reservation-expiry.yml").read_text(encoding="utf-8")
    assert "workflow_dispatch" in release
    assert "environment: production" in release
    assert "push:" not in release
    assert "release_reservation_expiry.py" in release
    assert "ensure_reservation_expiry.py" not in release
    assert "get-resources" not in release
    assert "inputs.commit" not in rollback
    assert "update-function-code" not in rollback
    publisher = (ROOT / "scripts" / "release_reservation_expiry.py").read_text(encoding="utf-8")
    assert 'FUNCTION = "erap-reservation-expiry"' in publisher
    assert "update-function-configuration" not in publisher


def test_emergency_auto_release_duration_is_unchanged():
    source = (ROOT / "src" / "auto_release" / "handler.py").read_text(encoding="utf-8")
    assert "RELEASE_AFTER_MINUTES = 30" in source
    assert reservation_due_values(WHEN)["reservation_expires_at"] == DUE
