import json

from observability import error_body, load_object, log_result

ALLOWED_ORIGIN = "https://main.d3enpe7opotop5.amplifyapp.com"
MAX_ORGANIZATION_NAME_LENGTH = 100
ALLOWED_METHODS = "GET,POST,OPTIONS"


def api_response(status_code, body):
    payload = error_body(status_code, body)
    if status_code >= 400 and isinstance(payload, dict):
        log_result(status_code, error_code=payload.get("error", {}).get("code", ""))
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": ALLOWED_ORIGIN,
            "Access-Control-Allow-Headers": "Content-Type,Authorization",
            "Access-Control-Allow-Methods": ALLOWED_METHODS,
        },
        "body": json.dumps(payload),
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
