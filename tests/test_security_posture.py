"""Repository security expectations. These tests do not call AWS."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_packaged_handlers_do_not_use_wildcard_cors():
    for path in (ROOT / "src").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert '"Access-Control-Allow-Origin": "*"' not in text
        assert "'Access-Control-Allow-Origin': '*'" not in text


def test_workflows_keep_contents_read():
    for path in (ROOT / ".github" / "workflows").glob("*.yml"):
        text = path.read_text(encoding="utf-8")
        assert "contents: write" not in text
        assert "contents: read" in text


def test_security_posture_doc_covers_the_required_topics():
    text = (ROOT / "docs" / "security-posture.md").read_text(encoding="utf-8")
    for heading in (
        "Authentication",
        "Authorization",
        "Tenant isolation",
        "IAM",
        "Secrets",
        "API Gateway",
        "CORS",
        "DynamoDB",
        "S3",
        "Lambda",
        "EventBridge",
        "CloudWatch",
        "GitHub Actions",
        "Dependencies",
        "Security tests",
        "Configuration drift",
        "Known limitations",
        "Deferred improvements",
    ):
        assert heading in text
    assert "ORG-D13B30D99127" not in text
