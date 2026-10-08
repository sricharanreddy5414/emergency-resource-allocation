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
    "api_views.py": "src/shared/api_views.py",
    "resource_state.py": "src/shared/resource_state.py",
    "everyday_operations.py": "src/shared/everyday_operations.py",
    "lifecycle_operations.py": "src/shared/lifecycle_operations.py",
    "visibility.py": "src/shared/visibility.py",
    "exchange_state.py": "src/shared/exchange_state.py",
    "exchange_model.py": "src/shared/exchange_model.py",
    "matching.py": "src/shared/matching.py",
    "observability.py": "src/shared/observability.py",
    "billing/__init__.py": "src/billing/__init__.py",
    "billing/entitlements.py": "src/billing/entitlements.py",
}

PACKAGES = {
    "get-resources": {
        **SHARED,
        "emergency_release.py": "src/shared/emergency_release.py",
        "resource_page_token.py": "src/shared/resource_page_token.py",
        "lambda_function.py": "src/resource/handler.py",
    },
    "create-request": {**SHARED, "lambda_function.py": "src/request/handler.py"},
    "emergency-resource-allocation": {**SHARED, "lambda_function.py": "src/allocation/service.py"},
    "emergency-resource-auto-release": {
        "audit.py": "src/shared/audit.py",
        "observability.py": "src/shared/observability.py",
        "emergency_release.py": "src/shared/emergency_release.py",
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
    "ResourceExchanges",
    "Notifications",
    "OrganizationSubscriptions",
    "BillingEvents",
]


# Exchange stays outside PACKAGES and outside deploy_backend.py.
# set_live_version.py --version still moves only PACKAGES.
# Billing is published by deploy_backend.py but stays out of PACKAGES so one
# shared version number cannot move the billing aliases.
EXCHANGE_PACKAGES = {
    "erap-exchange": {
        **SHARED,
        "notifications.py": "src/shared/notifications.py",
        "exchange_notify.py": "src/shared/exchange_notify.py",
        "handler.py": "src/exchange/handler.py",
        "service.py": "src/exchange/service.py",
        "lifecycle.py": "src/exchange/lifecycle.py",
        "quantity_handover.py": "src/exchange/quantity_handover.py",
        "handover_qr.py": "src/shared/handover_qr.py",
        "network_page_token.py": "src/exchange/network_page_token.py",
    },
    "erap-exchange-expiry": {
        **SHARED,
        "notifications.py": "src/shared/notifications.py",
        "exchange_notify.py": "src/shared/exchange_notify.py",
        "handler.py": "src/exchange/expiry_handler.py",
        "service.py": "src/exchange/service.py",
        "lifecycle.py": "src/exchange/lifecycle.py",
        "quantity_handover.py": "src/exchange/quantity_handover.py",
        "handover_qr.py": "src/shared/handover_qr.py",
    },
}

NOTIFICATION_PACKAGES = {
    "erap-notifications": {
        "access.py": "src/shared/access.py",
        "attributes.py": "src/shared/attributes.py",
        "common.py": "src/organization/common.py",
        "membership.py": "src/organization/membership.py",
        "pages.py": "src/shared/pages.py",
        "observability.py": "src/shared/observability.py",
        "notifications.py": "src/shared/notifications.py",
        "billing/__init__.py": "src/billing/__init__.py",
        "billing/entitlements.py": "src/billing/entitlements.py",
        "service.py": "src/notification_api/service.py",
        "handler.py": "src/notification_api/handler.py",
    },
}

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
    "erap-billing-expiry": {
        "observability.py": "src/shared/observability.py",
        "billing/__init__.py": "src/billing/__init__.py",
        "billing/errors.py": "src/billing/errors.py",
        "billing/models.py": "src/billing/models.py",
        "billing/plans.py": "src/billing/plans.py",
        "billing/transitions.py": "src/billing/transitions.py",
        "billing/expiry.py": "src/billing/expiry.py",
        "handler.py": "src/billing/expiry_handler.py",
    },
}


# Reservation expiry stays outside PACKAGES and outside deploy_backend.py.
# The general release must not publish this worker.
RESERVATION_EXPIRY_PACKAGES = {
    "erap-reservation-expiry": {
        "handler.py": "src/reservation_expiry_handler.py",
        "reservation_expiry.py": "src/shared/reservation_expiry.py",
        "resource_state.py": "src/shared/resource_state.py",
        "notifications.py": "src/shared/notifications.py",
        "observability.py": "src/shared/observability.py",
        "audit.py": "src/shared/audit.py",
    },
}


def package_map():
    mapping = dict(PACKAGES)
    mapping.update(BILLING_PACKAGES)
    mapping.update(EXCHANGE_PACKAGES)
    mapping.update(NOTIFICATION_PACKAGES)
    mapping.update(RESERVATION_EXPIRY_PACKAGES)
    return mapping


def function_names():
    return list(PACKAGES)
