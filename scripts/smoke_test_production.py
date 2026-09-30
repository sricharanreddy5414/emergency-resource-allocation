"""Read-only production smoke. No writes, no tokens printed, no pilot access."""

import sys
import urllib.error
import urllib.request

from lambda_manifest import ALLOWED_ORIGIN, API_BASE

FORBIDDEN_ORG = "ORG-D13B30D99127"


def assert_safe_url(url):
    if FORBIDDEN_ORG in url:
        raise SystemExit("refusing pilot organization")


def request(method, url):
    assert_safe_url(url)
    call = urllib.request.Request(url, method=method, headers={"Origin": ALLOWED_ORIGIN})
    try:
        with urllib.request.urlopen(call, timeout=20) as response:
            body = response.read(2000).decode()
            return response.status, response.headers.get("Access-Control-Allow-Origin", ""), body
    except urllib.error.HTTPError as error:
        body = error.read(2000).decode()
        return error.code, error.headers.get("Access-Control-Allow-Origin", ""), body


def main():
    public_url = API_BASE + "/public/resources"
    code, origin, body = request("GET", public_url)
    if code != 200 or origin != ALLOWED_ORIGIN or origin == "*":
        raise SystemExit(f"public resources failed {code}")
    if "organization_id" in body or FORBIDDEN_ORG in body:
        raise SystemExit("public resources exposed a tenant")
    print(f"ok public resources {code}")
    denied, denied_origin, _body = request("GET", API_BASE + "/organization")
    if denied not in (401, 403) or denied_origin == "*":
        raise SystemExit(f"unauthenticated organization read returned {denied}")
    print(f"ok unauthenticated organization {denied}")
    print("skipped authenticated reads")
    return 0


if __name__ == "__main__":
    sys.exit(main())
