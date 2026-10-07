"""Handler tests receive an explicit grandfathered subscription.

Production authorization denies a genuinely missing row. Tests that cover
that denial replace this fixture with an empty subscription table.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [
    str(ROOT / "src"),
    str(ROOT / "src" / "shared"),
    str(ROOT / "src" / "organization"),
]

import access


class KnownSubscription:
    def get_item(self, Key):
        return {
            "Item": {
                "organization_id": Key["organization_id"],
                "subscription_status": "GRANDFATHERED",
            }
        }

    def put_item(self, *args, **kwargs):
        raise AssertionError("authorization must not create a subscription")


@pytest.fixture(autouse=True)
def grandfather_known_subscription(monkeypatch):
    monkeypatch.setattr(access, "subscriptions_table", lambda: KnownSubscription())
