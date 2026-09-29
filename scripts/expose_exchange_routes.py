"""Idempotent API Gateway exposure script for Resource Exchange routes.

Repository-only helper for a later reviewed deploy. Phase 5C does NOT run this
against AWS and does NOT create the ResourceExchanges table.

Routes (Cognito authorizer y0hzhr; OPTIONS Authorization NONE):

  POST/GET/OPTIONS  /exchange/requests
  GET/OPTIONS       /exchange/requests/{exchange_request_id}
  POST/GET/OPTIONS  /exchange/requests/{exchange_request_id}/offers
  GET/OPTIONS       /exchange/requests/{exchange_request_id}/offers/{offer_id}
  GET/OPTIONS       /exchange/offers

Integration target: erap-exchange:live
"""

from __future__ import annotations

import json
import sys

from aws_cli import aws
from lambda_manifest import ACCOUNT, ALIAS, API_ID, API_STAGE, REGION, ROOT

AUTHORIZER_ID = "y0hzhr"
FUNCTION = "erap-exchange"
LIVE_URI = (
    f"arn:aws:apigateway:{REGION}:lambda:path/2015-03-31/functions/"
    f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:{FUNCTION}:{ALIAS}/invocations"
)


def main():
    print(
        json.dumps(
            {
                "status": "not_applied",
                "note": (
                    "Phase 5C ships this script for a later reviewed exposure. "
                    "Do not run until ResourceExchanges exists and erap-exchange is deployed."
                ),
                "api_id": API_ID,
                "stage": API_STAGE,
                "function": FUNCTION,
                "authorizer_id": AUTHORIZER_ID,
                "uri": LIVE_URI,
                "paths": [
                    "/exchange/requests",
                    "/exchange/requests/{exchange_request_id}",
                    "/exchange/requests/{exchange_request_id}/offers",
                    "/exchange/requests/{exchange_request_id}/offers/{offer_id}",
                    "/exchange/offers",
                ],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    # Guard: refuse live mutation unless explicitly forced after review.
    if "--apply" in sys.argv:
        print("Refusing --apply in Phase 5C. Table creation and API exposure are deferred.", file=sys.stderr)
        raise SystemExit(2)

    raise SystemExit(main())
