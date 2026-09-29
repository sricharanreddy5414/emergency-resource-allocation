import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "shared"))

from resource_state import (
    RESOURCE_OPERATIONAL_STATUSES,
    ResourceStateError,
    effective_operational_status,
    initialize_new_resource_fields,
    normalize_operational_status,
    normalize_tracking_mode,
    quantity_snapshot,
    validate_quantity_fields,
)


def test_operational_status_constants():
    assert "AVAILABLE" in RESOURCE_OPERATIONAL_STATUSES
    assert "RETIRED" in RESOURCE_OPERATIONAL_STATUSES
    assert "REGISTERED" not in RESOURCE_OPERATIONAL_STATUSES


def test_effective_status_from_available_true():
    assert effective_operational_status({"Available": True}) == "AVAILABLE"


def test_effective_status_from_available_false():
    assert effective_operational_status({"Available": False}) == "ALLOCATED"


def test_effective_status_uses_explicit_value():
    assert effective_operational_status({"Available": True, "operational_status": "MAINTENANCE"}) == "MAINTENANCE"


def test_effective_status_rejects_invalid():
    with pytest.raises(ResourceStateError):
        effective_operational_status({"Available": True, "operational_status": "INVALID"})


def test_normalize_operational_status():
    assert normalize_operational_status("maintenance") == "MAINTENANCE"


def test_normalize_operational_status_rejects_blank():
    with pytest.raises(ResourceStateError):
        normalize_operational_status("")


def test_tracking_mode_defaults_to_individual():
    assert normalize_tracking_mode(None) == "INDIVIDUAL"
    assert normalize_tracking_mode("") == "INDIVIDUAL"


def test_tracking_mode_quantity():
    assert normalize_tracking_mode("quantity") == "QUANTITY"


def test_tracking_mode_rejects_unknown():
    with pytest.raises(ResourceStateError):
        normalize_tracking_mode("BATCH")


def test_quantity_invariant():
    validate_quantity_fields(
        {
            "tracking_mode": "QUANTITY",
            "quantity_total": 10,
            "quantity_available": 7,
            "quantity_reserved": 2,
            "quantity_allocated": 1,
        }
    )


def test_quantity_invariant_rejects_mismatch():
    with pytest.raises(ResourceStateError):
        validate_quantity_fields(
            {
                "tracking_mode": "QUANTITY",
                "quantity_total": 10,
                "quantity_available": 7,
                "quantity_reserved": 2,
                "quantity_allocated": 2,
            }
        )


def test_quantity_rejects_negative():
    with pytest.raises(ResourceStateError):
        quantity_snapshot(
            {
                "tracking_mode": "QUANTITY",
                "quantity_total": 5,
                "quantity_available": -1,
                "quantity_reserved": 0,
                "quantity_allocated": 6,
            }
        )


def test_quantity_rejects_non_integer():
    with pytest.raises(ResourceStateError):
        quantity_snapshot(
            {
                "tracking_mode": "QUANTITY",
                "quantity_total": "10",
                "quantity_available": 10,
                "quantity_reserved": 0,
                "quantity_allocated": 0,
            }
        )


def test_new_individual_defaults():
    fields = initialize_new_resource_fields({}, available=True)
    assert fields == {"operational_status": "AVAILABLE", "tracking_mode": "INDIVIDUAL"}


def test_new_quantity_initialization():
    fields = initialize_new_resource_fields({"tracking_mode": "QUANTITY", "quantity_total": 50}, available=True)
    assert fields["tracking_mode"] == "QUANTITY"
    assert fields["operational_status"] == "AVAILABLE"
    assert fields["Available"] is False
    assert fields["quantity_total"] == 50
    assert fields["quantity_available"] == 50
    assert fields["quantity_reserved"] == 0
    assert fields["quantity_allocated"] == 0


def test_new_quantity_requires_total():
    with pytest.raises(ResourceStateError):
        initialize_new_resource_fields({"tracking_mode": "QUANTITY"}, available=True)


def test_individual_rejects_quantity_fields():
    with pytest.raises(ResourceStateError):
        initialize_new_resource_fields({"quantity_total": 5}, available=True)


def test_existing_row_compatibility_effective_status():
    legacy = {"resource_id": "R001", "Available": True}
    assert effective_operational_status(legacy) == "AVAILABLE"
    assert "operational_status" not in legacy
