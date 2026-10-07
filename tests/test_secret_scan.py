"""Synthetic secret-scanner coverage. Values are built at runtime so this file stays clean."""

import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import security_scan


ALPHABET = "Ab3Cd5Ef7Gh9Jk2Lm4Np6Qr8St1Uv0WxYz"


def mixed(length):
    return "".join(ALPHABET[index % len(ALPHABET)] for index in range(length))


def samples():
    return {
        "aws-access-key": "AKIA" + "FAKEKEYEXAMPLE12",
        "aws-secret-key": "aws_secret_access_key=" + mixed(40),
        "private-key": "-----BEGIN " + "RSA PRIVATE KEY-----",
        "jwt": "eyJhbGciOiJIUzI1NiJ9." + "eyJzdWIiOiI0MjAifQ." + "c2lnbmF0dXJlMTIzNDU2",
        "razorpay-key-id": "rzp_live_" + mixed(14),
        "razorpay-key-secret": "key_secret=" + mixed(24),
        "razorpay-webhook-secret": "webhook_secret=" + mixed(24),
        "api-key": "api_key=" + mixed(24),
        "bearer-token": "Authorization: Bearer " + mixed(32),
        "password-assignment": "password=" + mixed(16),
        "secret-assignment": "client_secret=" + mixed(24),
        "token-assignment": "access_token=" + mixed(24),
        "provider-token": "ghp_" + mixed(36),
    }


def test_every_detector_has_a_synthetic_sample_and_fires():
    provided = samples()
    assert set(provided) == {detector.name for detector in security_scan.DETECTORS}
    for name, text in provided.items():
        found = security_scan.scan_text("fixture.txt", text)
        assert any(item.category == name for item in found), name
        rendered = "\n".join(security_scan.format_finding(item) for item in found)
        assert text not in rendered
        assert "value=<redacted>" in rendered
        assert f"type={name}" in rendered


@pytest.mark.parametrize("header", ["", "RSA ", "EC ", "OPENSSH ", "DSA ", "ENCRYPTED "])
def test_private_key_headers_are_detected(header):
    text = "-----BEGIN " + header + "PRIVATE KEY-----"
    found = security_scan.scan_text("key.pem", text)
    assert any(item.category == "private-key" for item in found)


def test_pgp_private_key_block_is_detected():
    text = "-----BEGIN " + "PGP PRIVATE KEY BLOCK-----"
    found = security_scan.scan_text("key.asc", text)
    assert any(item.category == "private-key" for item in found)


def test_provider_shapes_are_detected():
    shapes = [
        "xoxb-" + mixed(12),
        "sk_live_" + mixed(24),
        "rk_test_" + mixed(24),
    ]
    for text in shapes:
        found = security_scan.scan_text("provider.txt", text)
        assert any(item.category == "provider-token" for item in found)
        assert text not in security_scan.format_finding(found[0])


def test_configuration_files_detect_assignments(tmp_path):
    secret = mixed(24)
    source = tmp_path / "src"
    source.mkdir()
    (source / "settings.json").write_text('{"api_key": "' + secret + '"}\n', encoding="utf-8")
    (source / "settings.yaml").write_text("webhook_secret: " + secret + "\n", encoding="utf-8")
    (source / "settings.toml").write_text('key_secret = "' + secret + '"\n', encoding="utf-8")
    found = security_scan.scan_tree(tmp_path)
    categories = {item.category for item in found}
    assert "api-key" in categories
    assert "razorpay-webhook-secret" in categories
    assert "razorpay-key-secret" in categories
    rendered = "\n".join(security_scan.format_finding(item) for item in found)
    assert secret not in rendered


def test_env_and_aws_credential_files_are_detected(tmp_path):
    secret = mixed(24)
    (tmp_path / ".env").write_text("API_KEY=" + secret + "\n", encoding="utf-8")
    example = tmp_path / ".env.example"
    example.write_text("RAZORPAY_KEY_ID=<your-key-id>\n", encoding="utf-8")
    aws_dir = tmp_path / ".aws"
    aws_dir.mkdir()
    (aws_dir / "credentials").write_text(
        "[default]\naws_secret_access_key=" + mixed(40) + "\n",
        encoding="utf-8",
    )
    (aws_dir / "config").write_text("[default]\nregion=eu-north-1\n", encoding="utf-8")
    found = security_scan.scan_tree(tmp_path)
    categories = {item.category for item in found}
    assert "env-file" in categories
    assert "api-key" in categories
    assert "aws-credentials-file" in categories
    assert "aws-secret-key" in categories
    rendered = "\n".join(security_scan.format_finding(item) for item in found)
    assert secret not in rendered
    assert "your-key-id" not in {item.category for item in found}
    example_hits = [item for item in found if item.path.endswith(".env.example")]
    assert example_hits == []


def test_scanner_exit_hides_the_value(tmp_path, monkeypatch):
    secret = mixed(24)
    source = tmp_path / "src"
    source.mkdir()
    (source / "config.json").write_text('{"api_key": "' + secret + '"}\n', encoding="utf-8")
    monkeypatch.setattr(security_scan, "ROOT", tmp_path)
    output = io.StringIO()
    with redirect_stdout(output):
        code = security_scan.main()
    rendered = output.getvalue()
    assert code == 1
    assert secret not in rendered
    assert "type=api-key" in rendered
    assert "value=<redacted>" in rendered


def test_skips_vendor_binary_and_oversized_files(tmp_path):
    secret_line = "api_key=" + mixed(24)
    vendor = tmp_path / "src" / "node_modules" / "pkg.js"
    vendor.parent.mkdir(parents=True)
    vendor.write_text(secret_line, encoding="utf-8")
    git_file = tmp_path / "src" / ".git" / "config"
    git_file.parent.mkdir()
    git_file.write_text(secret_line, encoding="utf-8")
    binary = tmp_path / "src" / "blob.bin"
    binary.write_bytes(b"\0api_key=" + mixed(24).encode("ascii"))
    oversized = tmp_path / "src" / "large.txt"
    oversized.write_text(secret_line, encoding="utf-8")
    assert security_scan.scan_tree(tmp_path, max_bytes=8) == []


@pytest.mark.parametrize(
    "text",
    [
        "d63a0c45eb1ae207bbfd64b07df595f9d84f3551",
        "123e4567-e89b-12d3-a456-426614174000",
        "ORG-EXAMPLE0000001",
        "RES-1001",
        "Q-1001",
        "12",
        "https://example.com/resources",
        "RAZORPAY_KEY_ID=<your-key-id>",
        "rzp_test_public",
        "rzp_live_",
        "a" * 64,
        "plan_ExamplePlan0001",
        "AKIAIOSFODNN7EXAMPLE",
        "ASIAIOSFODNN7EXAMPLE",
        'password="secret-pass"',
        'webhook_secret="whsec"',
        'key_secret="test-secret-value"',
        "arn:aws:secretsmanager:eu-north-1:481838970142:secret:erap/billing/razorpay/test",
    ],
)
def test_ordinary_identifiers_are_not_secrets(text):
    assert security_scan.scan_text("notes.md", text) == []


def test_secret_scan_runs_before_deployment_eligibility():
    for name in ("ci.yml", "deploy-backend.yml", "release.yml", "rollback.yml"):
        text = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
        assert "python scripts/security_scan.py" in text
