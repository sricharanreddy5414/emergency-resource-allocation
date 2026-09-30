"""Recovery controls that tests and verify_hardening.py both use.

This module does not call AWS.
"""

from lambda_manifest import package_map


# Whole-table restore copies every tenant. These labels describe loss impact.
CLASSIFICATION = {
    "Organizations": "CRITICAL",
    "OrganizationMembers": "CRITICAL",
    "Locations": "CRITICAL",
    "Resources": "CRITICAL",
    "EmergencyRequests": "CRITICAL",
    "Allocations": "CRITICAL",
    "ResourceExchanges": "CRITICAL",
    "OrganizationSubscriptions": "CRITICAL",
    "ResourceStatusHistory": "IMPORTANT",
    "AuditEvents": "IMPORTANT",
    "Notifications": "IMPORTANT",
    "BillingEvents": "IMPORTANT",
    "ResourceTypes": "IMPORTANT",
    "RequestTypes": "IMPORTANT",
}

SCHEDULES = {
    "erap-exchange-expiry-hourly": {
        "expression": "cron(15 * * * ? *)",
        "target_suffix": "function:erap-exchange-expiry:live",
    },
    "erap-billing-expiry-daily": {
        "expression": "cron(0 2 * * ? *)",
        "target_suffix": "function:erap-billing-expiry:live",
    },
}

AUTO_RELEASE = {
    "name": "EmergencyResourceAutoReleaseRule",
    "expression": "rate(5 minutes)",
    "target_suffix": "function:emergency-resource-auto-release:live",
}

RESTORE_WARNING = "NEVER restore directly over a production table during routine testing."


def alias_functions():
    return sorted(package_map())
