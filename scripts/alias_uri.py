"""Pure helpers for sending API Gateway traffic to the live alias."""


def live_invocation_uri(uri, function_names):
    if not isinstance(uri, str) or "/invocations" not in uri or ":live/invocations" in uri:
        return ""
    for name in function_names:
        needle = f"function:{name}/invocations"
        if needle in uri:
            return uri.replace(needle, f"function:{name}:live/invocations", 1)
    return ""
