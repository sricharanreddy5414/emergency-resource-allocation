import json
import re
import uuid
from decimal import Decimal

from observability import error_body, load_object, log_result

ALLOWED_ORIGIN = "https://main.d3enpe7opotop5.amplifyapp.com"
MAX_ORGANIZATION_NAME_LENGTH = 100
ALLOWED_METHODS = "GET,POST,OPTIONS"
_DECIMAL_MARKER = "\u0000erap-decimal-"
_DECIMAL_LITERAL = re.compile(r"-?(?:0|[1-9]\d*)(?:\.\d+)?")


def _decimal_literal(value):
    """Return a JSON number token. Integral decimals stay integers."""
    if not isinstance(value, Decimal) or not value.is_finite():
        raise TypeError("Object of type Decimal is not JSON serializable")

    if value == value.to_integral_value():
        return str(int(value))

    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")

    if text in {"", "-"}:
        text = "0"

    if not _DECIMAL_LITERAL.fullmatch(text):
        raise TypeError("Object of type Decimal is not JSON serializable")

    return text


def dumps_json(payload):
    """Serialize an API body. DynamoDB Decimals become JSON numbers."""
    marker = f"{_DECIMAL_MARKER}{uuid.uuid4().hex}-"
    numbers = []

    def default(value):
        if isinstance(value, Decimal):
            numbers.append(_decimal_literal(value))
            return f"{marker}{len(numbers) - 1}"

        raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")

    encoded = json.dumps(payload, default=default)

    for index, literal in enumerate(numbers):
        encoded = encoded.replace(json.dumps(f"{marker}{index}"), literal, 1)

    return encoded


def api_response(status_code, body):
    payload = error_body(status_code, body)
    if isinstance(payload, dict):
        log_result(status_code, error_code=payload.get("error", {}).get("code", ""))
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": ALLOWED_ORIGIN,
            "Access-Control-Allow-Headers": "Content-Type,Authorization",
            "Access-Control-Allow-Methods": ALLOWED_METHODS,
        },
        "body": dumps_json(payload),
    }


def get_user_sub(event):
    """Return the Cognito subject. Never read user_sub from the client."""
    if not isinstance(event, dict):
        return None

    claims = (
        event.get("requestContext", {})
        .get("authorizer", {})
        .get("claims", {})
    )

    if not isinstance(claims, dict):
        return None

    subject = claims.get("sub")

    if not isinstance(subject, str):
        return None

    subject = subject.strip()

    return subject or None


def parse_json_body(event):
    event = event if isinstance(event, dict) else {}
    return load_object(event.get("body"), event.get("isBase64Encoded"))
