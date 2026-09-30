"""Read-only schedule and alias check. Not used by the GitHub deploy role.

The deploy role cannot read EventBridge. Run this with operator credentials.
"""

import sys

from aws_cli import aws
from lambda_manifest import REGION
from recovery_expectations import AUTO_RELEASE, SCHEDULES, alias_functions


def main():
    for name, expected in SCHEDULES.items():
        item = aws(["scheduler", "get-schedule", "--name", name], region=REGION)
        target = ((item.get("Target") or {}).get("Arn")) or ""
        if item.get("State") != "ENABLED" or item.get("ScheduleExpression") != expected["expression"]:
            raise SystemExit(f"schedule changed {name}")
        if not target.endswith(expected["target_suffix"]):
            raise SystemExit(f"schedule target changed {name}")
        print(f"ok schedule {name}")
    rule = aws(["events", "describe-rule", "--name", AUTO_RELEASE["name"]], region=REGION)
    if rule.get("State") != "ENABLED" or rule.get("ScheduleExpression") != AUTO_RELEASE["expression"]:
        raise SystemExit("auto-release rule changed")
    targets = aws(["events", "list-targets-by-rule", "--rule", AUTO_RELEASE["name"]], region=REGION)
    arns = [item.get("Arn") or "" for item in targets.get("Targets") or []]
    if not any(arn.endswith(AUTO_RELEASE["target_suffix"]) for arn in arns):
        raise SystemExit("auto-release target changed")
    print("ok auto-release")
    for name in alias_functions():
        alias = aws(["lambda", "get-alias", "--function-name", name, "--name", "live"], region=REGION)
        if not str(alias.get("FunctionVersion") or "").isdigit():
            raise SystemExit(f"{name} live alias missing")
    print(f"ok aliases {len(alias_functions())}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
