import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [
    str(ROOT / "src" / "shared"),
    str(ROOT / "src" / "organization"),
]

import access
import identity
import migration
import transitions
from access import AccessError
from migration import MigrationError


def load_module(name, relative):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


allocation_service = load_module("allocation_service_phase4c", "src/allocation/service.py")
resource_handler = load_module("resource_handler_phase4c", "src/resource/handler.py")
locations_handler = load_module("locations_handler_phase4c", "src/locations/handler.py")
auto_release = load_module("auto_release_phase4c", "src/auto_release/handler.py")
migrate_script = load_module("migrate_tenant_scope_phase4c", "scripts/migrate_tenant_scope.py")

ORG_A = "ORG-A"
ORG_B = "ORG-B"
USER = "user-a"


def event(method="POST", body=None, organization_id=ORG_A, path="/allocate", subject=USER):
    payload = {
        "httpMethod": method,
        "path": path,
        "pathParameters": {},
        "queryStringParameters": {"organization_id": organization_id},
        "requestContext": {"authorizer": {"claims": {"sub": subject}}},
    }

    if body is not None:
        payload["body"] = json.dumps(body)

    return payload


def memberships(*pairs):
    return [
        {
            "organization_id": organization_id,
            "name": organization_id,
            "role": role,
            "status": "ACTIVE",
        }
        for organization_id, role in pairs
    ]


def use_memberships(monkeypatch, records):
    monkeypatch.setattr(access, "list_memberships", lambda *args, **kwargs: records)


class Store:
    def __init__(self, items=None):
        self.items = list(items or [])
        self.updates = []
        self.puts = []
        self.scans = 0

    def query(self, **kwargs):
        return {"Items": list(self.items)}

    def get_item(self, Key):
        for item in self.items:
            if all(item.get(key) == value for key, value in Key.items()):
                return {"Item": dict(item)}

        return {}

    def put_item(self, Item, ConditionExpression=None):
        self.puts.append(Item)
        self.items.append(Item)

    def update_item(self, **kwargs):
        self.updates.append(kwargs)

    def scan(self, **kwargs):
        self.scans += 1
        raise AssertionError("scan")


def base_records():
    return {
        "resources": [
            {
                "resource_id": "R1",
                "organization_id": "",
                "location_id": "",
            }
        ],
        "requests": [
            {
                "request_id": "Q1",
                "organization_id": "",
                "location_id": "",
            }
        ],
        "allocations": [
            {
                "allocation_id": "A1",
                "resource_id": "R1",
                "request_id": "Q1",
            }
        ],
        "history": [
            {
                "history_id": "H1",
                "resource_id": "R1",
            }
        ],
        "organizations": [{"organization_id": ORG_A, "status": "ACTIVE"}],
        "locations": [
            {
                "organization_id": ORG_A,
                "location_id": "LOC-A",
                "status": "ACTIVE",
            }
        ],
    }


def plan_for(records):
    return validate_mapping(records)


def validate_mapping(records, resource_org=ORG_A, request_org=ORG_A, location_id="LOC-A"):
    return migration.validate_plan(
        {
            "resources": [
                {
                    "resource_id": "R1",
                    "organization_id": resource_org,
                    "location_id": location_id,
                }
            ],
            "requests": [
                {
                    "request_id": "Q1",
                    "organization_id": request_org,
                    "location_id": location_id,
                }
            ],
        },
        records["resources"],
        records["requests"],
        records["allocations"],
        records["history"],
        records["organizations"],
        records["locations"],
    )


def test_logical_identity_keeps_existing_ids():
    assert identity.logical_identity(ORG_A, "R1") == {
        "organization_id": ORG_A,
        "entity_id": "R1",
    }


def test_dry_run_does_not_mutate():
    records = base_records()
    before = json.dumps(records)
    writes = validate_mapping(records)

    assert writes
    assert json.dumps(records) == before


def test_invalid_organization_is_rejected():
    records = base_records()
    records["organizations"] = []

    with pytest.raises(MigrationError, match="invalid organization"):
        validate_mapping(records)


def test_invalid_location_is_rejected():
    records = base_records()
    records["locations"] = []

    with pytest.raises(MigrationError, match="invalid location"):
        validate_mapping(records)


def test_cross_organization_mapping_is_rejected():
    records = base_records()
    records["locations"].append(
        {
            "organization_id": ORG_B,
            "location_id": "LOC-B",
            "status": "ACTIVE",
        }
    )
    records["organizations"].append({"organization_id": ORG_B, "status": "ACTIVE"})

    with pytest.raises(MigrationError, match="invalid location"):
        validate_mapping(records, location_id="LOC-B")


def test_inconsistent_allocation_mapping_is_rejected():
    records = base_records()
    records["organizations"].append({"organization_id": ORG_B, "status": "ACTIVE"})
    records["locations"].append(
        {
            "organization_id": ORG_B,
            "location_id": "LOC-A",
            "status": "ACTIVE",
        }
    )

    with pytest.raises(MigrationError, match="organization mismatch"):
        validate_mapping(records, request_org=ORG_B)


def test_valid_mapping_is_accepted_and_idempotent():
    records = base_records()
    writes = validate_mapping(records)
    stored = {
        "resource": {"R1": records["resources"][0]},
        "request": {"Q1": records["requests"][0]},
        "allocation": {"A1": records["allocations"][0]},
        "history": {"H1": records["history"][0]},
    }
    migration.apply_writes(stored, writes)
    migration.apply_writes(stored, writes)

    assert stored["resource"]["R1"]["organization_id"] == ORG_A
    assert migration.validate_plan(
        {
            "resources": [
                {
                    "resource_id": "R1",
                    "organization_id": ORG_A,
                    "location_id": "LOC-A",
                }
            ],
            "requests": [
                {
                    "request_id": "Q1",
                    "organization_id": ORG_A,
                    "location_id": "LOC-A",
                }
            ],
        },
        records["resources"],
        records["requests"],
        records["allocations"],
        records["history"],
        records["organizations"],
        records["locations"],
    ) == []


def test_apply_without_mapping_refuses(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["migrate_tenant_scope.py", "--mode", "apply"])

    assert migrate_script.main() == 2


def test_membership_allows_only_the_member_organization(monkeypatch):
    use_memberships(monkeypatch, memberships((ORG_A, "OPERATOR")))
    user_sub, membership = access.authorize(event(), {"organization_id": ORG_A, "role": "OWNER"})

    assert user_sub == USER
    assert membership["role"] == "OPERATOR"

    with pytest.raises(AccessError) as denied:
        access.authorize(event(organization_id=ORG_B), {"organization_id": ORG_B, "role": "OWNER"})

    assert denied.value.status_code == 403


def test_user_without_membership_is_denied(monkeypatch):
    use_memberships(monkeypatch, [])

    with pytest.raises(AccessError) as denied:
        access.authorize(event(), {})

    assert denied.value.status_code == 403


def test_suspended_organization_is_denied(monkeypatch):
    use_memberships(
        monkeypatch,
        [
            {
                "organization_id": ORG_A,
                "role": "OWNER",
                "status": "SUSPENDED",
            }
        ],
    )

    with pytest.raises(AccessError) as denied:
        access.authorize(event(), {})

    assert denied.value.status_code == 403


def test_member_cannot_operate(monkeypatch):
    use_memberships(monkeypatch, memberships((ORG_A, "MEMBER")))

    with pytest.raises(AccessError) as denied:
        access.authorize(event(), {}, allowed_roles=access.OPERATE_ROLES)

    assert denied.value.status_code == 403


def test_invalid_request_transition():
    assert transitions.can_allocate_request("PENDING")
    assert not transitions.can_allocate_request("RELEASED")
    assert not transitions.can_allocate_request("ALLOCATED")
    assert transitions.can_release_allocation("ALLOCATED")
    assert not transitions.can_release_allocation("RELEASED")
    assert not transitions.can_allocate_resource(False)


def test_duplicate_allocation_is_rejected(monkeypatch):
    use_memberships(monkeypatch, memberships((ORG_A, "OPERATOR")))
    locations = Store(
        [
            {
                "organization_id": ORG_A,
                "location_id": "LOC-A",
                "name": "North",
                "status": "ACTIVE",
            }
        ]
    )
    requests = Store(
        [
            {
                "request_id": "Q1",
                "organization_id": ORG_A,
                "location_id": "LOC-A",
                "Status": "PENDING",
                "ResourceType": "ICU_BED",
                "Priority": 1,
            }
        ]
    )
    resources = Store(
        [
            {
                "resource_id": "R1",
                "organization_id": ORG_A,
                "location_id": "LOC-A",
                "Type": "ICU_BED",
                "Available": True,
            }
        ]
    )

    class ExistingAllocation(Store):
        def put_item(self, Item, ConditionExpression=None):
            raise ClientError(
                {"Error": {"Code": "ConditionalCheckFailedException", "Message": "exists"}},
                "PutItem",
            )

    monkeypatch.setattr(allocation_service, "locations_table", lambda: locations)
    monkeypatch.setattr(allocation_service, "requests_table", lambda: requests)
    monkeypatch.setattr(allocation_service, "resources_table", lambda: resources)
    monkeypatch.setattr(allocation_service, "allocations_table", lambda: ExistingAllocation())
    monkeypatch.setattr(allocation_service, "history_table", lambda: Store())
    monkeypatch.setattr(allocation_service, "audit_table", lambda: None)

    result = allocation_service.lambda_handler(
        event(
            body={
                "request_id": "Q1",
                "resource_type": "ICU_BED",
                "location_id": "LOC-A",
                "organization_id": ORG_A,
                "priority": 1,
            }
        ),
        None,
    )
    body = json.loads(result["body"])

    assert result["statusCode"] == 409
    assert "ORG-B" not in result["body"]
    assert "Traceback" not in result["body"]
    assert body["message"] == "Request is not eligible for allocation"
    assert resources.updates


def test_completed_request_cannot_be_allocated(monkeypatch):
    use_memberships(monkeypatch, memberships((ORG_A, "OPERATOR")))
    monkeypatch.setattr(
        allocation_service,
        "locations_table",
        lambda: Store(
            [
                {
                    "organization_id": ORG_A,
                    "location_id": "LOC-A",
                    "status": "ACTIVE",
                    "name": "North",
                }
            ]
        ),
    )
    monkeypatch.setattr(
        allocation_service,
        "requests_table",
        lambda: Store(
            [
                {
                    "request_id": "Q1",
                    "organization_id": ORG_A,
                    "location_id": "LOC-A",
                    "Status": "RELEASED",
                }
            ]
        ),
    )
    monkeypatch.setattr(allocation_service, "resources_table", lambda: Store())
    monkeypatch.setattr(allocation_service, "allocations_table", lambda: Store())
    monkeypatch.setattr(allocation_service, "audit_table", lambda: None)

    result = allocation_service.lambda_handler(
        event(
            body={
                "request_id": "Q1",
                "resource_type": "ICU_BED",
                "location_id": "LOC-A",
                "priority": 1,
            }
        ),
        None,
    )

    assert result["statusCode"] == 409


def test_location_with_records_cannot_be_deactivated(monkeypatch):
    use_memberships(monkeypatch, memberships((ORG_A, "ADMIN")))
    location = {
        "organization_id": ORG_A,
        "location_id": "LOC-A",
        "name": "North",
        "status": "ACTIVE",
    }
    locations = Store([location])
    monkeypatch.setattr(locations_handler, "locations_table", lambda: locations)
    monkeypatch.setattr(
        locations_handler,
        "resources_table",
        lambda: Store([{"organization_id": ORG_A, "location_id": "LOC-A", "resource_id": "R1"}]),
    )
    monkeypatch.setattr(locations_handler, "requests_table", lambda: Store())
    monkeypatch.setattr(locations_handler, "allocations_table", lambda: Store())
    monkeypatch.setattr(locations_handler, "audit_table", lambda: None)
    payload = event(method="DELETE", organization_id=ORG_A, path="/locations/LOC-A")
    payload["pathParameters"] = {"location_id": "LOC-A"}

    result = locations_handler.lambda_handler(payload, None)

    assert result["statusCode"] == 409
    assert location["status"] == "ACTIVE"


def test_empty_location_can_be_deactivated(monkeypatch):
    use_memberships(monkeypatch, memberships((ORG_A, "ADMIN")))
    location = {
        "organization_id": ORG_A,
        "location_id": "LOC-A",
        "name": "North",
        "status": "ACTIVE",
    }
    locations = Store([location])
    monkeypatch.setattr(locations_handler, "locations_table", lambda: locations)
    monkeypatch.setattr(locations_handler, "resources_table", lambda: Store())
    monkeypatch.setattr(locations_handler, "requests_table", lambda: Store())
    monkeypatch.setattr(locations_handler, "allocations_table", lambda: Store())
    monkeypatch.setattr(locations_handler, "audit_table", lambda: None)
    payload = event(method="DELETE", path="/locations/LOC-A")
    payload["pathParameters"] = {"location_id": "LOC-A"}

    result = locations_handler.lambda_handler(payload, None)

    assert result["statusCode"] == 200
    assert locations.puts[-1]["status"] == "INACTIVE"


def test_auto_release_filters_and_is_idempotent():
    now = datetime(2026, 9, 27, tzinfo=timezone.utc)
    allocation = {
        "allocation_id": "A1",
        "request_id": "Q1",
        "resource_id": "R1",
        "organization_id": ORG_A,
        "location_id": "LOC-A",
        "status": "ALLOCATED",
        "allocated_at": (now - timedelta(hours=2)).isoformat(),
    }
    resource = {
        "resource_id": "R1",
        "organization_id": ORG_A,
        "Available": False,
    }
    request = {
        "request_id": "Q1",
        "organization_id": ORG_A,
        "Status": "ALLOCATED",
    }

    class Allocations(Store):
        def query(self, **kwargs):
            self.queries = getattr(self, "queries", [])
            self.queries.append(kwargs)

            if kwargs.get("IndexName") == "AllocationStatusIndex":
                return {
                    "Items": [
                        item
                        for item in self.items
                        if item.get("status") == "ALLOCATED"
                    ]
                }

            return {"Items": list(self.items)}

        def update_item(self, **kwargs):
            self.updates.append(kwargs)
            condition = kwargs["ExpressionAttributeValues"]
            current = self.items[0]

            if current["status"] != condition[":allocated"]:
                raise ClientError(
                    {"Error": {"Code": "ConditionalCheckFailedException", "Message": "no"}},
                    "UpdateItem",
                )

            current["status"] = "RELEASED"

    class Resources(Store):
        def update_item(self, **kwargs):
            self.updates.append(kwargs)
            self.items[0]["Available"] = True

    allocations = Allocations([allocation])
    resources = Resources([resource])
    requests = Store([request])
    store = {
        "allocations": allocations,
        "resources": resources,
        "requests": requests,
        "history": Store(),
        "audit": None,
    }
    first = auto_release.lambda_handler({}, None, store=store, now=now)
    second = auto_release.lambda_handler({}, None, store=store, now=now)

    assert json.loads(first["body"])["released_count"] == 1
    assert json.loads(second["body"])["released_count"] == 0
    assert allocations.scans == 0
    assert allocations.queries[0]["IndexName"] == "AllocationStatusIndex"
    assert resources.items[0]["Available"] is True


def test_auto_release_skips_other_tenants_and_other_holders():
    allocation = {
        "allocation_id": "A1",
        "request_id": "Q1",
        "resource_id": "R1",
        "organization_id": ORG_A,
        "status": "ALLOCATED",
    }
    other = {
        "allocation_id": "A2",
        "resource_id": "R1",
        "organization_id": ORG_A,
        "status": "ALLOCATED",
    }
    unscoped = auto_release.plan_release(
        {"allocation_id": "LEGACY", "status": "ALLOCATED", "resource_id": "R1", "request_id": "Q1"},
        {"resource_id": "R1"},
        {"request_id": "Q1"},
        [],
    )
    mismatch = auto_release.plan_release(
        allocation,
        {"resource_id": "R1", "organization_id": ORG_B},
        {"request_id": "Q1", "organization_id": ORG_A},
        [],
    )
    shared = auto_release.plan_release(
        allocation,
        {"resource_id": "R1", "organization_id": ORG_A},
        {"request_id": "Q1", "organization_id": ORG_A},
        [allocation, other],
    )

    assert unscoped["reason"] == "missing organization"
    assert mismatch["reason"] == "resource mismatch"
    assert shared["free_resource"] is False


def test_cross_tenant_error_does_not_leak(monkeypatch):
    use_memberships(monkeypatch, memberships((ORG_A, "MEMBER")))
    resources = Store(
        [
            {
                "resource_id": "R9",
                "organization_id": ORG_B,
                "location_id": "LOC-B",
                "Location": "Secret Hospital",
            }
        ]
    )
    monkeypatch.setattr(resource_handler, "resources_table", lambda: resources)
    monkeypatch.setattr(resource_handler, "history_table", lambda: Store())
    monkeypatch.setattr(resource_handler, "audit_table", lambda: None)

    payload = event(method="GET", path="/allocate/resources/history", organization_id=ORG_A)
    payload["queryStringParameters"]["resource_id"] = "R9"

    result = resource_handler.lambda_handler(payload, None)

    assert result["statusCode"] == 404
    assert "Secret Hospital" not in result["body"]
    assert ORG_B not in result["body"]
    assert "Traceback" not in result["body"]
