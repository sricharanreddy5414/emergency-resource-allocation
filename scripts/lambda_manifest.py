"""Lambda zip layout used by the existing ERAP functions.

The archive names match the handlers already configured in AWS.
This module does not call AWS.
"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REGION = "eu-north-1"
ACCOUNT = "481838970142"
API_ID = "4c6dni17l3"
API_STAGE = "dev"
API_BASE = f"https://{API_ID}.execute-api.{REGION}.amazonaws.com/{API_STAGE}"
AMPLIFY_APP_ID = "d3enpe7opotop5"
AMPLIFY_REGION = "us-east-1"
AMPLIFY_BRANCH = "main"
AMPLIFY_URL = "https://main.d3enpe7opotop5.amplifyapp.com"
ALLOWED_ORIGIN = AMPLIFY_URL
ALIAS = "live"
REPO = "sricharanreddy5414/emergency-resource-allocation"
DEPLOY_ROLE = "ERAP-GitHub-Deploy"
PRODUCTION_ROLE = "ERAP-GitHub-Production"

SHARED = {
    "access.py": "src/shared/access.py",
    "attributes.py": "src/shared/attributes.py",
    "audit.py": "src/shared/audit.py",
    "common.py": "src/organization/common.py",
    "membership.py": "src/organization/membership.py",
    "pages.py": "src/shared/pages.py",
    "visibility.py": "src/shared/visibility.py",
    "matching.py": "src/shared/matching.py",
    "observability.py": "src/shared/observability.py",
    "billing/__init__.py": "src/billing/__init__.py",
    "billing/entitlements.py": "src/billing/entitlements.py",
}

PACKAGES = {
    "get-resources": {**SHARED, "lambda_function.py": "src/resource/handler.py"},
    "create-request": {**SHARED, "lambda_function.py": "src/request/handler.py"},
    "emergency-resource-allocation": {**SHARED, "lambda_function.py": "src/allocation/service.py"},
    "emergency-resource-auto-release": {
        "audit.py": "src/shared/audit.py",
        "observability.py": "src/shared/observability.py",
        "lambda_function.py": "src/auto_release/handler.py",
    },
    "erap-catalog": {**SHARED, "handler.py": "src/catalog/handler.py"},
    "erap-public-resources": {**SHARED, "handler.py": "src/public/handler.py"},
    "erap-locations": {**SHARED, "handler.py": "src/locations/handler.py"},
    "erap-create-organization": {
        "common.py": "src/organization/common.py",
        "membership.py": "src/organization/membership.py",
        "access.py": "src/shared/access.py",
        "member_admin.py": "src/organization/member_admin.py",
        "audit.py": "src/shared/audit.py",
        "observability.py": "src/shared/observability.py",
        "subscriptions.py": "src/organization/subscriptions.py",
        "billing/__init__.py": "src/billing/__init__.py",
        "billing/errors.py": "src/billing/errors.py",
        "billing/models.py": "src/billing/models.py",
        "billing/plans.py": "src/billing/plans.py",
        "billing/transitions.py": "src/billing/transitions.py",
        "billing/entitlements.py": "src/billing/entitlements.py",
        "handler.py": "src/organization/handler.py",
    },
    "erap-get-organization": {
        "common.py": "src/organization/common.py",
        "membership.py": "src/organization/membership.py",
        "access.py": "src/shared/access.py",
        "member_admin.py": "src/organization/member_admin.py",
        "observability.py": "src/shared/observability.py",
        "billing/__init__.py": "src/billing/__init__.py",
        "billing/entitlements.py": "src/billing/entitlements.py",
        "get_handler.py": "src/organization/get_handler.py",
    },
}

TABLES = [
    "Organizations",
    "OrganizationMembers",
    "Locations",
    "Resources",
    "EmergencyRequests",
    "Allocations",
    "ResourceStatusHistory",
    "ResourceTypes",
    "RequestTypes",
    "AuditEvents",
]


# Packaged for a later deploy. Not in PACKAGES, so the existing nine functions
# stay the only ones the deploy, alias, and route scripts touch.
BILLING_PACKAGES = {
    "erap-billing": {
        "common.py": "src/organization/common.py",
        "membership.py": "src/organization/membership.py",
        "access.py": "src/shared/access.py",
        "observability.py": "src/shared/observability.py",
        "billing/__init__.py": "src/billing/__init__.py",
        "billing/errors.py": "src/billing/errors.py",
        "billing/entitlements.py": "src/billing/entitlements.py",
        "billing/models.py": "src/billing/models.py",
        "billing/plans.py": "src/billing/plans.py",
        "billing/transitions.py": "src/billing/transitions.py",
        "billing/checkout.py": "src/billing/checkout.py",
        "billing/summary.py": "src/billing/summary.py",
        "billing/cancel.py": "src/billing/cancel.py",
        "billing/events.py": "src/billing/events.py",
        "billing/provider/__init__.py": "src/billing/provider/__init__.py",
        "billing/provider/razorpay.py": "src/billing/provider/razorpay.py",
        "handler.py": "src/billing/api_handler.py",
    },
    "erap-billing-webhook": {
        "common.py": "src/organization/common.py",
        "observability.py": "src/shared/observability.py",
        "billing/__init__.py": "src/billing/__init__.py",
        "billing/errors.py": "src/billing/errors.py",
        "billing/models.py": "src/billing/models.py",
        "billing/plans.py": "src/billing/plans.py",
        "billing/transitions.py": "src/billing/transitions.py",
        "billing/webhook.py": "src/billing/webhook.py",
        "billing/provider/__init__.py": "src/billing/provider/__init__.py",
        "billing/provider/razorpay.py": "src/billing/provider/razorpay.py",
        "handler.py": "src/billing/webhook_handler.py",
    },
}


def package_map():
    mapping = dict(PACKAGES)
    mapping.update(BILLING_PACKAGES)
    return mapping


def function_names():
    return list(PACKAGES)
