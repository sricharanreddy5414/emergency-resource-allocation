"""Small AWS CLI wrapper. It does not print command output."""

import json
import subprocess
import time


def aws(args, region="eu-north-1", retries=10):
    command = ["aws", *args, "--region", region, "--output", "json"]
    last_error = ""
    for attempt in range(retries):
        result = subprocess.run(command, text=True, capture_output=True)
        if result.returncode == 0:
            text = result.stdout.strip()
            return json.loads(text) if text else None
        last_error = (result.stderr or result.stdout or "").strip()
        if "ResourceConflictException" in last_error or "TooManyRequestsException" in last_error:
            time.sleep(min(3 * (attempt + 1), 15))
            continue
        raise SystemExit(last_error[:500])
    raise SystemExit(last_error[:500] or "AWS CLI failed")
