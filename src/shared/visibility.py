"""Explicit visibility projection. PUBLIC alone feeds PublicDiscoveryIndex."""

import re


VISIBILITIES = frozenset({"PRIVATE", "PUBLIC", "NETWORK"})

# Attributes written only for PUBLIC discovery. PRIVATE and NETWORK omit them.
PRIVATE_INDEX_ATTRIBUTES = (
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
)


def token(value):
    text = re.sub(r"[^A-Z0-9]+", "-", str(value or "").upper()).strip("-")
    return (text or "NONE")[:40]


def discovery_key(type_name, city, resource_id):
    return f"{token(type_name)}#{token(city)}#{resource_id}"


def normalize_visibility(value):
    visibility = str(value or "PRIVATE").strip().upper() or "PRIVATE"

    if visibility not in VISIBILITIES:
        raise ValueError("Visibility is invalid")

    return visibility


def omits_public_discovery(visibility):
    """NETWORK and PRIVATE must never populate PublicDiscoveryIndex."""
    return normalize_visibility(visibility) in {"PRIVATE", "NETWORK"}


def publication_fields(body, location, type_name, resource_id, available, operational_status=None):
    visibility = normalize_visibility((body or {}).get("visibility"))

    if visibility == "NETWORK":
        status = str(operational_status or "").strip().upper()

        if status == "RETIRED":
            raise ValueError("Retired resources cannot use NETWORK visibility")

        # NETWORK is exchange-eligible visibility only. Never set visibility_key.
        return {"visibility": "NETWORK"}

    if visibility == "PRIVATE":
        return {"visibility": "PRIVATE"}

    name = str(body.get("public_name") or body.get("name") or type_name).strip()
    description = str(body.get("public_description") or "").strip()
    contact = str(body.get("public_contact") or "").strip()

    if not name or len(name) > 80 or len(description) > 300 or len(contact) > 120:
        raise ValueError("Public resource details are invalid")

    city = str((location or {}).get("city") or "").strip()[:60]
    state = str((location or {}).get("state") or "").strip()[:60]
    show = body.get("show_availability") is True
    fields = {
        "visibility": "PUBLIC",
        "visibility_key": "PUBLIC",
        "discovery_key": discovery_key(type_name, city, resource_id),
        "public_type_name": type_name[:60],
        "public_name": name,
        "public_description": description,
        "public_contact": contact,
        "public_city": city,
        "public_state": state,
        "show_availability": show,
    }

    if show:
        fields["public_status"] = "AVAILABLE" if available else "UNAVAILABLE"

    return fields
