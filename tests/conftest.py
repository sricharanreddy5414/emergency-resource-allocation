"""Missing subscription rows stay grandfathered for existing handler tests."""

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


class MissingSubscriptions:
    def get_item(self, Key):
        return {}

    def put_item(self, *args, **kwargs):
        raise AssertionError("authorization must not create a subscription")


@pytest.fixture(autouse=True)
def grandfather_missing_subscription(monkeypatch):
    monkeypatch.setattr(access, "subscriptions_table", lambda: MissingSubscriptions())
