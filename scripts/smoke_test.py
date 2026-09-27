"""Non-destructive smoke checks. No accounts, organizations, or allocations are created."""

import json
import os
import sys
import time
import urllib.error
import urllib.request

from aws_cli import aws
from lambda_manifest import ALLOWED_ORIGIN, AMPLIFY_APP_ID, AMPLIFY_BRANCH, AMPLIFY_REGION, AMPLIFY_URL, API_BASE


def request(method, url):
    call = urllib.request.Request(url, method=method, headers={"Origin": ALLOWED_ORIGIN})
    try:
        with urllib.request.urlopen(call, timeout=20) as response:
            return response.status, response.headers.get("Access-Control-Allow-Origin", ""), response.read(2000).decode()
    except urllib.error.HTTPError as error:
        return error.code, error.headers.get("Access-Control-Allow-Origin", ""), error.read(2000).decode()


def expect(name, method, url, status, origin=None, absent=()):
    code, allow_origin, body = request(method, url)
    if code != status:
        raise SystemExit(f"{name} returned {code}, expected {status}")
    if origin is not None and allow_origin != origin:
        raise SystemExit(f"{name} CORS origin is {allow_origin or 'missing'}")
    if allow_origin == "*":
        raise SystemExit(f"{name} allows every origin")
    for token in absent:
        if token in body:
            raise SystemExit(f"{name} exposed {token}")
    print(f"ok {name} {code}")
    return {"name": name, "status": code, "ok": True}


def amplify_job(commit):
    if os.environ.get("SMOKE_WAIT_AMPLIFY") != "1" or not commit:
        return "not-checked"
    deadline = time.time() + int(os.environ.get("SMOKE_AMPLIFY_SECONDS", "360"))
    while time.time() < deadline:
        page = aws(
            [
                "amplify",
                "list-jobs",
                "--app-id",
                AMPLIFY_APP_ID,
                "--branch-name",
                AMPLIFY_BRANCH,
                "--max-items",
                "10",
            ],
            region=AMPLIFY_REGION,
        )
        for job in page.get("jobSummaries", []):
            if (job.get("commitId") or "").startswith(commit[:12]):
                status = job.get("status", "")
                if status == "SUCCEED":
                    return status
                if status in {"FAILED", "CANCELLED"}:
                    raise SystemExit(f"Amplify job {status}")
        time.sleep(20)
    return "still-running"


def main():
    checks = [
        expect(
            "public resources",
            "GET",
            API_BASE + "/public/resources",
            200,
            ALLOWED_ORIGIN,
            ("organization_id", "actor_sub"),
        ),
        expect("public page size", "GET", API_BASE + "/public/resources?limit=1000", 400, ALLOWED_ORIGIN),
        expect("public page token", "GET", API_BASE + "/public/resources?page_token=not-a-token", 400, ALLOWED_ORIGIN),
        expect("resources require auth", "GET", API_BASE + "/allocate/resources", 401, ALLOWED_ORIGIN),
        expect("types require auth", "GET", API_BASE + "/resource-types", 401, ALLOWED_ORIGIN),
        expect("requests require auth", "GET", API_BASE + "/requests", 401, ALLOWED_ORIGIN),
        expect("public options", "OPTIONS", API_BASE + "/public/resources", 200, ALLOWED_ORIGIN),
    ]
    page_code, _origin, _page = request("GET", AMPLIFY_URL)
    asset_code, _asset_origin, asset_body = request("GET", AMPLIFY_URL + "/app.js")
    style_code, _style_origin, _style = request("GET", AMPLIFY_URL + "/style.css")
    if page_code != 200 or asset_code != 200 or "API_URL" not in asset_body or style_code != 200:
        raise SystemExit(f"frontend returned page {page_code}, app.js {asset_code}, style.css {style_code}")
    print("ok frontend 200")
    commit = os.environ.get("GITHUB_SHA", "")
    amplify_status = amplify_job(commit)
    print(f"amplify {amplify_status}")
    summary = {"checks": checks, "frontend": {"status": page_code, "app_js": asset_code, "ok": True}, "amplify": amplify_status}
    from lambda_manifest import ROOT

    folder = ROOT / "dist"
    folder.mkdir(exist_ok=True)
    (folder / "smoke-summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
