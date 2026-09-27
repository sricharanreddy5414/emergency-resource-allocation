"""Keep Lambda logs for 30 days.

Thirty days covers an incident review after a deploy. The logs do not contain
tokens, and this platform has one environment, so a longer default is not useful.
"""

import sys

from aws_cli import aws
from lambda_manifest import PACKAGES, REGION


def main():
    for name in PACKAGES:
        group = f"/aws/lambda/{name}"
        try:
            aws(
                ["logs", "put-retention-policy", "--log-group-name", group, "--retention-in-days", "30"],
                region=REGION,
            )
        except SystemExit as error:
            if "ResourceNotFoundException" not in str(error):
                raise
            aws(["logs", "create-log-group", "--log-group-name", group], region=REGION)
            aws(
                ["logs", "put-retention-policy", "--log-group-name", group, "--retention-in-days", "30"],
                region=REGION,
            )
            print(f"created {group}")
            continue
        print(f"retention 30 {group}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
