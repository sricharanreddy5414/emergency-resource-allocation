import json

ALLOWED_ORIGIN = "https://main.d3enpe7opotop5.amplifyapp.com"
MAX_ORGANIZATION_NAME_LENGTH = 100
ALLOWED_METHODS = "GET,POST,OPTIONS"


def api_response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": ALLOWED_ORIGIN,
            "Access-Control-Allow-Headers": "Content-Type,Authorization",
            "Access-Control-Allow-Methods": ALLOWED_METHODS,
        },
        "body": json.dumps(body),
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
    raw = event.get("body") if isinstance(event, dict) else None

    if raw is None or raw == "":
        return {}

    if isinstance(raw, dict):
        return raw

    if event.get("isBase64Encoded"):
        import base64

        raw = base64.b64decode(raw).decode("utf-8")

    parsed = json.loads(raw)

    if not isinstance(parsed, dict):
        raise ValueError("JSON body must be an object")

    return parsed
