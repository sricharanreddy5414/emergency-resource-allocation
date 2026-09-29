"""Phase 5B: Resource Exchange foundation and NETWORK visibility."""

import copy
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [
    str(ROOT / "src"),
    str(ROOT / "src" / "shared"),
    str(ROOT / "src" / "organization"),
]


def load_module(name, relative):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


visibility = load_module("visibility_foundation", "src/shared/visibility.py")
exchange_state = load_module("exchange_state_foundation", "src/shared/exchange_state.py")
# exchange_model imports access; load after paths set
exchange_model = load_module("exchange_model_foundation", "src/shared/exchange_model.py")
public_api = load_module("public_foundation", "src/public/handler.py")
resource_state = load_module("resource_state_foundation", "src/shared/resource_state.py")


ORG_A = "ORG-A"
ORG_B = "ORG-B"
USER = "user-a"


def test_private_visibility_unchanged():
    fields = visibility.publication_fields(
        {"visibility": "PRIVATE"},
        {"city": "Bengaluru", "state": "KA"},
        "Kit",
        "R1",
        True,
    )
    assert fields == {"visibility": "PRIVATE"}
    assert "visibility_key" not in fields
    assert "discovery_key" not in fields


def test_public_visibility_unchanged():
    fields = visibility.publication_fields(
        {
            "visibility": "PUBLIC",
            "public_name": "Public Kit",
            "public_description": "desc",
            "show_availability": True,
        },
        {"city": "Bengaluru", "state": "KA"},
        "Kit",
        "R1",
        True,
    )
    assert fields["visibility"] == "PUBLIC"
    assert fields["visibility_key"] == "PUBLIC"
    assert fields["discovery_key"].startswith("KIT#BENGALURU#R1")
    assert fields["public_status"] == "AVAILABLE"


def test_network_visibility_accepted_without_public_index():
    fields = visibility.publication_fields(
        {"visibility": "NETWORK"},
        {"city": "Bengaluru", "state": "KA"},
        "Kit",
        "R1",
        True,
        operational_status="AVAILABLE",
    )
    assert fields == {"visibility": "NETWORK"}
    for name in visibility.PRIVATE_INDEX_ATTRIBUTES:
        assert name not in fields


def test_invalid_visibility_rejected():
    with pytest.raises(ValueError, match="Visibility is invalid"):
        visibility.publication_fields({"visibility": "MARKET"}, {}, "Kit", "R1", True)


def test_network_does_not_enter_public_discovery_view():
    item = {
        "visibility": "NETWORK",
        "visibility_key": "PUBLIC",  # hostile/stale key must still fail public_view
        "public_name": "Should hide",
    }
    assert public_api.public_view(item) is None

    clean = {"visibility": "NETWORK", "public_name": "Hidden"}
    assert public_api.public_view(clean) is None


def test_public_view_still_requires_public():
    assert public_api.public_view(
        {
            "visibility": "PUBLIC",
            "visibility_key": "PUBLIC",
            "public_name": "Shown",
            "public_type_name": "Kit",
        }
    )["name"] == "Shown"


def test_retired_cannot_become_network():
    with pytest.raises(ValueError, match="Retired resources cannot use NETWORK"):
        visibility.publication_fields(
            {"visibility": "NETWORK"},
            {},
            "Kit",
            "R1",
            False,
            operational_status="RETIRED",
        )


def test_omits_public_discovery_helper():
    assert visibility.omits_public_discovery("PRIVATE") is True
    assert visibility.omits_public_discovery("NETWORK") is True
    assert visibility.omits_public_discovery("PUBLIC") is False


def test_request_and_offer_state_constants():
    assert exchange_state.REQUEST_STATUSES == {
        "OPEN",
        "ACCEPTED",
        "TRANSFER_PENDING",
        "COMPLETED",
        "CANCELLED",
        "EXPIRED",
    }
    assert exchange_state.OFFER_STATUSES == {
        "OPEN",
        "ACCEPTED",
        "REJECTED",
        "WITHDRAWN",
        "SUPERSEDED",
        "EXPIRED",
        "CANCELLED",
    }
    assert exchange_state.normalize_request_status("open") == "OPEN"
    assert exchange_state.normalize_offer_status("superseded") == "SUPERSEDED"
    with pytest.raises(exchange_state.ExchangeStateError):
        exchange_state.normalize_request_status("DRAFT")
    with pytest.raises(exchange_state.ExchangeStateError):
        exchange_state.normalize_offer_status("PENDING")
    assert "EXCHANGE_HELD" not in resource_state.RESOURCE_OPERATIONAL_STATUSES


def test_meta_item_and_network_open_gsi_keys():
    meta = exchange_model.build_meta_item(
        exchange_request_id="EXREQ-TEST001",
        requester_organization_id=ORG_A,
        resource_type_id="RT-1",
        resource_type_name="Medical Kit",
        tracking_mode="INDIVIDUAL",
        destination_location_id="LOC-1",
        created_by=USER,
        destination_city="Bengaluru",
        created_at="2026-09-29T10:00:00+00:00",
    )
    assert meta["pk"] == "EXREQ#EXREQ-TEST001"
    assert meta["sk"] == "META"
    assert meta["status"] == "OPEN"
    assert meta["network_list_key"] == "OPEN"
    assert meta["requester_organization_id"] == ORG_A
    assert meta["created_at"] == "2026-09-29T10:00:00+00:00"

    accepted = exchange_model.apply_network_open_index(meta, "ACCEPTED")
    assert accepted["status"] == "ACCEPTED"
    assert "network_list_key" not in accepted


def test_empty_index_keys_omitted():
    cleaned = exchange_model.omit_blank_index_keys(
        {
            "pk": "EXREQ#EXREQ-1",
            "sk": "META",
            "network_list_key": "",
            "requester_organization_id": "ORG-A",
            "provider_organization_id": "  ",
            "created_at": "2026-09-29T10:00:00+00:00",
        }
    )
    assert "network_list_key" not in cleaned
    assert "provider_organization_id" not in cleaned
    assert cleaned["requester_organization_id"] == "ORG-A"


def test_offer_item_provider_gsi_keys():
    offer = exchange_model.build_offer_item(
        exchange_request_id="EXREQ-TEST001",
        offer_id="EXOFF-1",
        provider_organization_id=ORG_B,
        resource_id="R-NET-1",
        created_by=USER,
        quantity_offered=1,
        source_location_id="LOC-B",
        resource_snapshot={
            "name": "Spare Kit",
            "resource_type_name": "Medical Kit",
            "serial_number": "SECRET-SN",
            "asset_tag": "SECRET-TAG",
            "assigned_to": "person",
        },
        created_at="2026-09-29T11:00:00+00:00",
    )
    assert offer["pk"] == "EXREQ#EXREQ-TEST001"
    assert offer["sk"] == "OFFER#EXOFF-1"
    assert offer["provider_organization_id"] == ORG_B
    assert offer["created_at"] == "2026-09-29T11:00:00+00:00"
    assert "network_list_key" not in offer
    assert "serial_number" not in offer["resource_snapshot"]
    assert "asset_tag" not in offer["resource_snapshot"]
    assert offer["resource_snapshot"]["name"] == "Spare Kit"


def test_requester_and_provider_tenant_helpers():
    meta = exchange_model.build_meta_item(
        exchange_request_id="EXREQ-T",
        requester_organization_id=ORG_A,
        resource_type_id="RT",
        resource_type_name="Kit",
        tracking_mode="INDIVIDUAL",
        destination_location_id="LOC",
        created_by=USER,
    )
    membership_a = {"organization_id": ORG_A, "role": "OPERATOR"}
    membership_b = {"organization_id": ORG_B, "role": "OPERATOR"}
    exchange_model.assert_requester_organization(membership_a, meta)
    with pytest.raises(Exception) as error:
        exchange_model.assert_requester_organization(membership_b, meta)
    assert error.value.status_code == 404

    offer = exchange_model.build_offer_item(
        exchange_request_id="EXREQ-T",
        offer_id="EXOFF-T",
        provider_organization_id=ORG_B,
        resource_id="R1",
        created_by=USER,
    )
    exchange_model.assert_provider_organization(membership_b, offer)
    with pytest.raises(Exception) as err2:
        exchange_model.assert_provider_organization(membership_a, offer)
    assert err2.value.status_code == 404


def test_network_projection_hides_own_and_non_open():
    meta = exchange_model.build_meta_item(
        exchange_request_id="EXREQ-N",
        requester_organization_id=ORG_A,
        resource_type_id="RT",
        resource_type_name="Kit",
        tracking_mode="INDIVIDUAL",
        destination_location_id="LOC",
        created_by=USER,
        destination_city="Bengaluru",
    )
    assert exchange_model.network_request_projection(meta, viewer_organization_id=ORG_A) is None
    view = exchange_model.network_request_projection(meta, viewer_organization_id=ORG_B)
    assert view["exchange_request_id"] == "EXREQ-N"
    assert "requester_organization_id" not in view
    assert "destination_location_id" not in view

    closed = exchange_model.apply_network_open_index(meta, "CANCELLED")
    assert exchange_model.network_request_projection(closed, viewer_organization_id=ORG_B) is None


def test_idempotency_item_foundation():
    item = exchange_model.build_idempotency_item(
        organization_id=ORG_A,
        idempotency_key="client-1",
        operation="CREATE_REQUEST",
        result_ref={"exchange_request_id": "EXREQ-1"},
    )
    assert item["pk"] == "IDEM#ORG-A#client-1"
    assert item["sk"] == "CREATE_REQUEST"


def test_gsi_index_names_match_design():
    assert exchange_model.INDEX_NETWORK_OPEN == "NetworkOpenRequestIndex"
    assert exchange_model.INDEX_REQUESTER_ORG == "RequesterOrgIndex"
    assert exchange_model.INDEX_PROVIDER_OFFER == "ProviderOrgOfferIndex"
    assert exchange_model.INDEX_EXPIRY_DUE == "ExpiryDueIndex"
    assert exchange_model.TABLE_NAME == "ResourceExchanges"


def test_exchange_write_roles_do_not_elevate_member():
    assert "MEMBER" in exchange_model.EXCHANGE_READ_ROLES
    assert "MEMBER" not in exchange_model.EXCHANGE_WRITE_ROLES
    assert exchange_model.EXCHANGE_WRITE_ROLES == {"OPERATOR", "ADMIN", "OWNER"}


def test_infra_spec_lists_exchange_gsis():
    import json

    spec = json.loads((ROOT / "infra" / "resource-exchanges-table.json").read_text(encoding="utf-8"))
    assert spec["applied"] is True
    table = spec["tables"][0]
    assert table["TableName"] == "ResourceExchanges"
    names = [gsi["IndexName"] for gsi in table["GlobalSecondaryIndexes"]]
    assert names == [
        "NetworkOpenRequestIndex",
        "RequesterOrgIndex",
        "ProviderOrgOfferIndex",
        "ExpiryDueIndex",
    ]
    assert table["BillingMode"] == "PAY_PER_REQUEST"
    assert table["DeletionProtectionEnabled"] is True
    assert table["PointInTimeRecoverySpecification"]["PointInTimeRecoveryEnabled"] is True
    actions = set()
    for statement in (spec.get("runtime_iam") or {}).get("Statement") or []:
        action = statement.get("Action")
        if isinstance(action, str):
            actions.add(action)
        else:
            actions.update(action or [])
    assert "dynamodb:DeleteItem" not in actions
    assert "dynamodb:Scan" not in actions
