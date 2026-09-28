import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "shared"))
sys.path.insert(0, str(ROOT / "src" / "organization"))

import handler
from billing.models import access_when_subscription_missing
from test_organization import (
    CLAIMS_SUB,
    MemberStore,
    OrganizationStore,
    SubscriptionStore,
    conditional_error,
    event,
    response_body,
    use_stores,
)


UTC = timezone.utc


class Clock:
    current = datetime(2026, 9, 28, 0, 0, tzinfo=UTC)

    @classmethod
    def now(cls, tz=None):
        return cls.current


def create(monkeypatch, body, subscriptions=None):
    Clock.current = datetime(2026, 9, 28, 0, 0, tzinfo=UTC)
    organizations = OrganizationStore()
    members = MemberStore()
    subscriptions = use_stores(monkeypatch, organizations, members, subscriptions)
    monkeypatch.setattr(handler, "audit_table", lambda: None)
    monkeypatch.setattr(handler, "datetime", Clock)
    result = handler.lambda_handler(event(body=body), None)
    return result, organizations, members, subscriptions


def test_new_organization_receives_one_trial(monkeypatch):
    result, organizations, _members, subscriptions = create(
        monkeypatch,
        {"name": "City Hospital", "client_request_id": "request-1234"},
    )
    organization = next(iter(organizations.items.values()))
    subscription = next(iter(subscriptions.items.values()))
    start = datetime.fromisoformat(subscription["trial_start"])
    end = datetime.fromisoformat(subscription["trial_end"])
    body = response_body(result)

    assert result["statusCode"] == 201
    assert subscription["subscription_status"] == "TRIALING"
    assert subscription["plan_id"] == "FREE_TRIAL"
    assert subscription["billing_interval"] == "none"
    assert subscription["trial_start"] == organization["created_at"]
    assert end - start == timedelta(days=15)
    assert subscription["provider"] == ""
    assert subscription["provider_customer_id"] == ""
    assert "provider_subscription_id" not in subscription
    assert subscription["current_period_start"] == ""
    assert subscription["current_period_end"] == ""
    assert subscription["cancel_at_period_end"] is False
    assert subscription["cancelled_at"] == ""
    assert subscription["created_at"] == organization["created_at"]
    assert len(subscriptions.items) == 1
    assert len(subscriptions.puts) == 1
    assert "provider" not in json.dumps(body)
    assert "trial_start" not in json.dumps(body)
    assert body["organization"]["created_at"] == organization["created_at"]


def test_replay_keeps_the_same_organization_and_trial(monkeypatch):
    body = {"name": "City Hospital", "client_request_id": "request-1234"}
    first, organizations, members, subscriptions = create(monkeypatch, body)
    original = next(iter(subscriptions.items.values()))
    Clock.current = datetime(2026, 10, 20, 0, 0, tzinfo=UTC)

    replay = handler.lambda_handler(
        event(body={"name": "Renamed", "client_request_id": "request-1234"}),
        None,
    )
    replay_body = response_body(replay)
    current = next(iter(subscriptions.items.values()))

    assert response_body(first)["organization"]["organization_id"] == replay_body["organization"]["organization_id"]
    assert replay["statusCode"] == 200
    assert current["trial_start"] == original["trial_start"]
    assert current["trial_end"] == original["trial_end"]
    assert len(subscriptions.items) == 1
    assert len(subscriptions.puts) == 1
    assert len(members.puts) == 1
    assert len(organizations.items) == 1


def test_existing_subscription_is_not_overwritten(monkeypatch):
    organization_id = handler.organization_id_for_request(CLAIMS_SUB, "request-1234")
    organizations = OrganizationStore()
    members = MemberStore()
    subscriptions = SubscriptionStore()
    organizations.items[organization_id] = {
        "organization_id": organization_id,
        "name": "City Hospital",
        "owner_sub": CLAIMS_SUB,
        "status": "ACTIVE",
        "created_at": "2026-09-01T00:00:00+00:00",
    }
    members.rows.append(
        {
            "organization_id": organization_id,
            "user_sub": CLAIMS_SUB,
            "role": "OWNER",
            "status": "ACTIVE",
            "created_at": "2026-09-01T00:00:00+00:00",
        }
    )
    subscriptions.items[organization_id] = {
        "organization_id": organization_id,
        "subscription_status": "TRIALING",
        "plan_id": "FREE_TRIAL",
        "trial_start": "2026-09-01T00:00:00+00:00",
        "trial_end": "2026-09-16T00:00:00+00:00",
    }
    use_stores(monkeypatch, organizations, members, subscriptions)
    monkeypatch.setattr(handler, "audit_table", lambda: None)
    monkeypatch.setattr(handler, "datetime", Clock)
    Clock.current = datetime(2026, 12, 1, 0, 0, tzinfo=UTC)

    result = handler.lambda_handler(
        event(body={"name": "City Hospital", "client_request_id": "request-1234"}),
        None,
    )

    assert result["statusCode"] == 200
    assert subscriptions.items[organization_id]["trial_start"] == "2026-09-01T00:00:00+00:00"
    assert subscriptions.items[organization_id]["trial_end"] == "2026-09-16T00:00:00+00:00"
    assert subscriptions.puts == []


def test_different_request_creates_a_separate_trial(monkeypatch):
    _first, _organizations, _members, subscriptions = create(
        monkeypatch,
        {"name": "One", "client_request_id": "request-1111"},
    )
    second = handler.lambda_handler(
        event(body={"name": "Two", "client_request_id": "request-2222"}),
        None,
    )

    assert second["statusCode"] == 201
    assert len(subscriptions.items) == 2
    assert len({item["trial_start"] for item in subscriptions.items.values()}) == 1


def test_existing_organizations_are_not_backfilled(monkeypatch):
    organizations = OrganizationStore()
    organizations.items["ORG-PILOT"] = {
        "organization_id": "ORG-PILOT",
        "name": "Pilot",
        "owner_sub": "pilot-owner",
        "status": "ACTIVE",
        "created_at": "2026-01-01T00:00:00+00:00",
    }
    members = MemberStore()
    subscriptions = use_stores(monkeypatch, organizations, members)
    monkeypatch.setattr(handler, "audit_table", lambda: None)
    monkeypatch.setattr(handler, "datetime", Clock)

    result = handler.lambda_handler(
        event(body={"name": "New Org", "client_request_id": "request-1234"}),
        None,
    )

    assert result["statusCode"] == 201
    assert "ORG-PILOT" not in subscriptions.items
    assert access_when_subscription_missing() == "grandfathered"


def test_membership_failure_does_not_write_a_trial(monkeypatch):
    organizations = OrganizationStore()
    members = MemberStore()
    subscriptions = use_stores(monkeypatch, organizations, members)

    def fail_membership(**kwargs):
        raise ClientError(
            {"Error": {"Code": "InternalServerError", "Message": "unavailable"}},
            "PutItem",
        )

    members.put_item = fail_membership
    monkeypatch.setattr(handler, "audit_table", lambda: None)
    result = handler.lambda_handler(
        event(body={"name": "City Hospital", "client_request_id": "request-1234"}),
        None,
    )

    assert result["statusCode"] == 500
    assert response_body(result)["message"] == "Unable to create organization"
    assert subscriptions.items == {}


def test_subscription_failure_is_not_reported_as_success(monkeypatch):
    organizations = OrganizationStore()
    members = MemberStore()

    class FailingSubscriptions:
        def __init__(self):
            self.items = {}

        def put_item(self, Item, ConditionExpression=None):
            raise ClientError(
                {"Error": {"Code": "ProvisionedThroughputExceededException", "Message": "busy"}},
                "PutItem",
            )

        def get_item(self, Key):
            return {}

    subscriptions = FailingSubscriptions()
    use_stores(monkeypatch, organizations, members, subscriptions)
    monkeypatch.setattr(handler, "audit_table", lambda: None)
    result = handler.lambda_handler(
        event(body={"name": "City Hospital", "client_request_id": "request-1234"}),
        None,
    )

    assert result["statusCode"] == 500
    assert response_body(result)["message"] == "Unable to create organization"
    assert "ProvisionedThroughputExceededException" not in result["body"]
    assert subscriptions.items == {}


def test_lost_subscription_row_is_not_reported_as_success(monkeypatch):
    organizations = OrganizationStore()
    members = MemberStore()

    class LostSubscriptions:
        def put_item(self, Item, ConditionExpression=None):
            raise conditional_error()

        def get_item(self, Key):
            return {}

    use_stores(monkeypatch, organizations, members, LostSubscriptions())
    monkeypatch.setattr(handler, "audit_table", lambda: None)
    result = handler.lambda_handler(
        event(body={"name": "City Hospital", "client_request_id": "request-1234"}),
        None,
    )

    assert result["statusCode"] == 500
    assert response_body(result)["message"] == "Unable to create organization"
