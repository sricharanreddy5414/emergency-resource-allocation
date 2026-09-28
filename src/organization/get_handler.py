from common import api_response, get_user_sub
from observability import begin_request
from membership import list_memberships


def lambda_handler(event, context):
    begin_request(event)
    if event.get("httpMethod") == "OPTIONS":
        return api_response(200, {"message": "OK"})

    if event.get("httpMethod") != "GET":
        return api_response(405, {"message": "Method not allowed"})

    user_sub = get_user_sub(event)

    if not user_sub:
        return api_response(401, {"message": "Authentication required"})

    from member_admin import handle_member_read, pending_invitations

    query = event.get("queryStringParameters") or {}

    if not isinstance(query, dict):
        query = {}

    if str(query.get("view") or "") == "members":
        return handle_member_read(event)

    try:
        organizations = list_memberships(user_sub)
        invitations = pending_invitations(event)
    except Exception as error:
        print(
            "Organization lookup failed:",
            error.__class__.__name__,
        )
        return api_response(500, {"message": "Unable to load organizations"})

    payload = {"organizations": organizations}

    if invitations:
        payload["pending_invitations"] = invitations

    return api_response(200, payload)
