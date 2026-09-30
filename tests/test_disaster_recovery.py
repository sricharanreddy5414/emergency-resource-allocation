"""Recovery expectations stay aligned with the repository. No AWS calls."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from lambda_manifest import TABLES
from recovery_expectations import (
    AUTO_RELEASE,
    CLASSIFICATION,
    RESTORE_WARNING,
    SCHEDULES,
    alias_functions,
)


def test_every_production_table_is_classified():
    assert set(CLASSIFICATION) == set(TABLES)
    assert CLASSIFICATION["Notifications"] == "IMPORTANT"
    assert CLASSIFICATION["ResourceExchanges"] == "CRITICAL"
    assert CLASSIFICATION["OrganizationMembers"] == "CRITICAL"


def test_schedules_match_infra_json():
    exchange = json.loads((ROOT / "infra" / "exchange-expiry.json").read_text(encoding="utf-8"))
    billing = json.loads((ROOT / "infra" / "billing-expiry.json").read_text(encoding="utf-8"))
    assert exchange["schedule"]["schedule_expression"] == SCHEDULES["erap-exchange-expiry-hourly"]["expression"]
    assert billing["schedule"]["schedule_expression"] == SCHEDULES["erap-billing-expiry-daily"]["expression"]
    assert AUTO_RELEASE["expression"] == "rate(5 minutes)"
    assert AUTO_RELEASE["target_suffix"].endswith(":live")


def test_packages_have_a_live_alias_expectation():
    names = alias_functions()
    assert "erap-exchange" in names
    assert "erap-billing-webhook" in names
    assert "emergency-resource-auto-release" in names
    assert len(names) == len(set(names))


def test_runbook_forbids_overwrite_and_covers_required_sections():
    text = (ROOT / "docs" / "disaster-recovery.md").read_text(encoding="utf-8")
    assert RESTORE_WARNING in text
    for heading in (
        "Scope",
        "Systems covered",
        "Data classification",
        "PITR status",
        "Backup strategy",
        "Restore procedure",
        "Temporary restore procedure",
        "Production restore warning",
        "Tenant isolation precautions",
        "Exchange recovery considerations",
        "Billing recovery considerations",
        "Notification recovery considerations",
        "Lambda recovery",
        "API Gateway recovery",
        "Cognito recovery",
        "EventBridge recovery",
        "IAM recovery",
        "CloudWatch recovery",
        "RPO",
        "RTO",
        "Validation checklist",
        "Rollback/abort conditions",
        "Pilot safety rules",
        "Incident evidence to capture",
        "Known limitations",
    ):
        assert heading in text
    assert "ORG-D13B30D99127" in text


def test_deploy_role_can_describe_every_table_and_cannot_restore():
    policy = json.loads((ROOT / "infra" / "github-deploy-policy.json").read_text(encoding="utf-8"))
    described = set()
    actions = set()
    for statement in policy["Statement"]:
        action = statement.get("Action")
        values = {action} if isinstance(action, str) else set(action or [])
        actions.update(values)
        if "dynamodb:DescribeTable" in values:
            for resource in statement.get("Resource") or []:
                described.add(resource.rsplit("/", 1)[-1])
    assert set(TABLES) <= described
    assert "dynamodb:RestoreTableToPointInTime" not in actions
    assert "AdministratorAccess" not in actions


def test_runtime_infra_does_not_grant_restore():
    infra = ROOT / "infra"
    for path in infra.glob("*.json"):
        text = path.read_text(encoding="utf-8")
        assert "RestoreTableToPointInTime" not in text
        assert "AdministratorAccess" not in text
