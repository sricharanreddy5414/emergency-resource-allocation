"""Small attribute schemas for organization-defined types."""

import json
import re

from access import AccessError


MAX_FIELDS = 12
MAX_KEY_LENGTH = 32
MAX_STRING_LENGTH = 200
MAX_LABEL_LENGTH = 60
MAX_SCHEMA_BYTES = 4000
MAX_ATTRIBUTES_BYTES = 4000
KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
FIELD_TYPES = {"string", "number", "boolean"}


def _size(value):
    return len(json.dumps(value, separators=(",", ":"), default=str).encode("utf-8"))


def validate_schema(schema):
    if schema is None:
        return {"fields": []}

    if not isinstance(schema, dict):
        raise AccessError(400, "Attribute schema is invalid")

    fields = schema.get("fields", [])

    if not isinstance(fields, list) or len(fields) > MAX_FIELDS:
        raise AccessError(400, "Attribute schema is invalid")

    normalized = []
    seen = set()

    for field in fields:
        if not isinstance(field, dict):
            raise AccessError(400, "Attribute schema is invalid")

        key = str(field.get("key") or "").strip()
        field_type = str(field.get("type") or "").strip()
        label = str(field.get("label") or key).strip()

        if not KEY_PATTERN.match(key) or field_type not in FIELD_TYPES:
            raise AccessError(400, "Attribute schema is invalid")

        if key in seen or len(label) > MAX_LABEL_LENGTH:
            raise AccessError(400, "Attribute schema is invalid")

        seen.add(key)
        item = {
            "key": key,
            "type": field_type,
            "required": bool(field.get("required")),
            "label": label,
        }

        if field_type == "number":
            if "minimum" in field:
                item["minimum"] = _number(field.get("minimum"))
            if "maximum" in field:
                item["maximum"] = _number(field.get("maximum"))

        if field_type == "string":
            item["max_length"] = min(int(field.get("max_length") or MAX_STRING_LENGTH), MAX_STRING_LENGTH)

        normalized.append(item)

    result = {"fields": normalized}

    if _size(result) > MAX_SCHEMA_BYTES:
        raise AccessError(400, "Attribute schema is too large")

    return result


def validate_attributes(values, schema):
    schema = validate_schema(schema)
    values = values or {}

    if not isinstance(values, dict):
        raise AccessError(400, "Attributes are invalid")

    if _size(values) > MAX_ATTRIBUTES_BYTES:
        raise AccessError(400, "Attributes are too large")

    allowed = {field["key"]: field for field in schema["fields"]}
    normalized = {}

    for key in values:
        if key not in allowed:
            raise AccessError(400, "Attributes are invalid")

    for key, field in allowed.items():
        if key not in values:
            if field["required"]:
                raise AccessError(400, "Attributes are invalid")
            continue

        normalized[key] = _coerce(values[key], field)

    return normalized


def validate_matching_config(config, schema):
    if config is None:
        return {"compatible_resource_type_ids": [], "required_attributes": {}, "same_location_preferred": True}

    if not isinstance(config, dict):
        raise AccessError(400, "Matching configuration is invalid")

    identifiers = config.get("compatible_resource_type_ids") or []

    if not isinstance(identifiers, list) or len(identifiers) > 20:
        raise AccessError(400, "Matching configuration is invalid")

    clean_ids = []

    for value in identifiers:
        text = str(value or "").strip()

        if not re.fullmatch(r"RT-[A-F0-9]{12}", text):
            raise AccessError(400, "Matching configuration is invalid")

        clean_ids.append(text)

    required = config.get("required_attributes") or {}

    if not isinstance(required, dict) or len(required) > MAX_FIELDS:
        raise AccessError(400, "Matching configuration is invalid")

    clean_required = {}

    for key, rule in required.items():
        if not KEY_PATTERN.match(str(key)) or not isinstance(rule, dict):
            raise AccessError(400, "Matching configuration is invalid")

        if "minimum" not in rule:
            raise AccessError(400, "Matching configuration is invalid")

        clean_required[key] = {"minimum": _number(rule.get("minimum"))}

    preferred = config.get("same_location_preferred", True)

    if not isinstance(preferred, bool):
        raise AccessError(400, "Matching configuration is invalid")

    result = {
        "compatible_resource_type_ids": clean_ids,
        "required_attributes": clean_required,
        "same_location_preferred": preferred,
    }

    if _size(result) > MAX_SCHEMA_BYTES:
        raise AccessError(400, "Matching configuration is invalid")

    return result


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AccessError(400, "Attributes are invalid")

    if isinstance(value, float) and value.is_integer():
        return int(value)

    return value


def _coerce(value, field):
    field_type = field["type"]

    if field_type == "string":
        if not isinstance(value, str):
            raise AccessError(400, "Attributes are invalid")

        text = value.strip()
        limit = field.get("max_length", MAX_STRING_LENGTH)

        if len(text) > limit:
            raise AccessError(400, "Attributes are invalid")

        return text

    if field_type == "number":
        number = _number(value)

        if "minimum" in field and number < field["minimum"]:
            raise AccessError(400, "Attributes are invalid")

        if "maximum" in field and number > field["maximum"]:
            raise AccessError(400, "Attributes are invalid")

        return number

    if field_type == "boolean":
        if not isinstance(value, bool):
            raise AccessError(400, "Attributes are invalid")

        return value

    raise AccessError(400, "Attributes are invalid")
