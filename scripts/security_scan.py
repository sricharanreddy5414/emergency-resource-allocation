"""Fail if tracked project files contain credential-like material.

This is not a universal secret scanner. It looks for accidental commits of
common key shapes and credential assignments. A match is reported as a file,
line, and detector name. The matched value is never printed.

Scanned roots: src, frontend, scripts, tests, .github, infra, plus the
requirements files and a committed .aws or .env file at the repository root.

Skipped directories: .git, __pycache__, .pytest_cache, .venv, venv,
node_modules, dist, build, coverage.

Skipped files: binaries (a NUL byte in the first kilobyte), files larger
than 1 MB, and suffixes .pyc, .zip, .png, .jpg, .jpeg, .gif, .webp, .ico,
.woff, .woff2, .pdf.

.env.example, .env.sample, and .env.template are scanned for values but are
not rejected for the filename alone. A placeholder such as
RAZORPAY_KEY_ID=<your-key-id> is not a match. The AWS documentation key ids
AKIAIOSFODNN7EXAMPLE and ASIAIOSFODNN7EXAMPLE are not matches.
"""

import re
import sys
from pathlib import Path

from lambda_manifest import ROOT


SCAN_ROOTS = ["src", "frontend", "scripts", "tests", ".github", "infra"]
SKIP_DIRS = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
    "coverage",
}
SKIP_SUFFIXES = {
    ".pyc",
    ".zip",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".woff",
    ".woff2",
    ".pdf",
}
ENV_EXAMPLES = {".env.example", ".env.sample", ".env.template"}
MAX_BYTES = 1_000_000
AWS_DOC_KEY_IDS = {"AKIAIOSFODNN7EXAMPLE", "ASIAIOSFODNN7EXAMPLE"}


class Finding:
    def __init__(self, path, line, category):
        self.path = path
        self.line = line
        self.category = category


class Detector:
    def __init__(self, name, pattern, value_group=0, examples=()):
        self.name = name
        self.pattern = re.compile(pattern)
        self.value_group = value_group
        self.examples = frozenset(examples)

    def matches(self, line):
        for match in self.pattern.finditer(line):
            value = match.group(self.value_group) if self.value_group else match.group(0)
            if value in self.examples:
                continue
            if self.value_group and _low_confidence(value):
                continue
            return True
        return False


def _assignment(names, minimum):
    joined = "|".join(names)
    return (
        rf"(?i)(?<![A-Za-z0-9_])(?:{joined})(?![A-Za-z0-9_])"
        rf"['\"]?\s*[:=]\s*['\"]?([A-Za-z0-9+_=-]{{{minimum},}})"
    )


DETECTORS = [
    Detector("aws-access-key", r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b", examples=AWS_DOC_KEY_IDS),
    Detector(
        "aws-secret-key",
        r"(?i)(?<![A-Za-z0-9_])aws_secret_access_key(?![A-Za-z0-9_])['\"]?\s*[:=]\s*['\"]?([A-Za-z0-9/+=]{40})\b",
        value_group=1,
    ),
    Detector(
        "private-key",
        r"-----BEGIN (?:RSA |OPENSSH |EC |DSA |ENCRYPTED |PGP )?PRIVATE KEY(?: BLOCK)?-----",
    ),
    Detector(
        "jwt",
        r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b",
    ),
    Detector("razorpay-key-id", r"\brzp_(?:test|live)_[A-Za-z0-9]{14}\b"),
    Detector(
        "razorpay-key-secret",
        _assignment(["key_secret", "razorpay_key_secret", "razorpay_secret"], 24),
        value_group=1,
    ),
    Detector(
        "razorpay-webhook-secret",
        _assignment(["webhook_secret", "razorpay_webhook_secret"], 24),
        value_group=1,
    ),
    Detector(
        "api-key",
        _assignment(["api_key", "api-key", "apikey", "x-api-key"], 20),
        value_group=1,
    ),
    Detector(
        "bearer-token",
        r"(?i)(?<![A-Za-z0-9_])(?:authorization\s*[:=]\s*['\"]?bearer|bearer)\s+"
        r"((?:eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,})|[A-Za-z0-9_\-=]{32,})",
        value_group=1,
    ),
    Detector("password-assignment", _assignment(["password", "passwd"], 16), value_group=1),
    Detector(
        "secret-assignment",
        _assignment(["secret", "client_secret"], 24),
        value_group=1,
    ),
    Detector(
        "token-assignment",
        _assignment(["access_token", "refresh_token", "id_token", "auth_token"], 24),
        value_group=1,
    ),
    Detector(
        "provider-token",
        r"\b(?:ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{22,}|xox[baprs]-[A-Za-z0-9-]{10,}|AIza[0-9A-Za-z\-_]{35}|(?:sk|rk)_(?:live|test)_[0-9A-Za-z]{24,})\b",
    ),
]


def _placeholder(value):
    text = str(value or "").strip().strip("'\"")
    if not text or "<" in text or ">" in text or " " in text:
        return True
    lowered = text.lower()
    if lowered in {
        "changeme",
        "placeholder",
        "example",
        "your-key-id",
        "your_key_id",
        "none",
        "null",
        "redacted",
        "dummy",
        "password",
        "secret",
        "token",
        "public",
        "write",
        "read",
    }:
        return True
    if re.search(
        r"(?i)(synthetic|example|placeholder|changeme|dummy|redacted|your[-_]key|must-not|not-a-)",
        text,
    ):
        return True
    return False


def _low_confidence(value):
    """Assignment values need mixed credential shape, not code or prose."""
    if _placeholder(value):
        return True
    if not re.search(r"[A-Za-z]", value) or not re.search(r"\d", value):
        return True
    common = max(value.count(char) for char in set(value))
    return common / len(value) > 0.6


def format_finding(finding):
    return (
        f"secret detected: file={finding.path} line={finding.line} "
        f"type={finding.category} value=<redacted>"
    )


def scan_text(relative, text):
    findings = []
    for number, line in enumerate(str(text).splitlines(), start=1):
        for detector in DETECTORS:
            if detector.matches(line):
                findings.append(Finding(str(relative), number, detector.name))
    return findings


def _env_file(name):
    if name in ENV_EXAMPLES:
        return False
    return name == ".env" or (name.startswith(".env.") and not name.endswith((".example", ".sample", ".template")))


def path_category(path):
    name = path.name
    if _env_file(name):
        return "env-file"
    if path.suffix.lower() == ".pem":
        return "private-key-file"
    if ".aws" in path.parts and name in {"credentials", "config"}:
        return "aws-credentials-file"
    return ""


def _skipped_dir(path):
    return any(part in SKIP_DIRS for part in path.parts)


def _binary(path):
    try:
        chunk = path.read_bytes()[:1024]
    except OSError:
        return True
    return b"\0" in chunk


def scan_file(path, root, max_bytes=MAX_BYTES):
    relative = path.relative_to(root).as_posix()
    findings = []
    category = path_category(path)
    if category:
        findings.append(Finding(relative, 1, category))
    if path.suffix.lower() in SKIP_SUFFIXES:
        return findings
    try:
        if path.stat().st_size > max_bytes:
            return findings
    except OSError:
        return findings
    if _binary(path):
        return findings
    text = path.read_text(encoding="utf-8", errors="ignore")
    findings.extend(scan_text(relative, text))
    return findings


def iter_files(root):
    root = Path(root)
    for relative in SCAN_ROOTS:
        folder = root / relative
        if not folder.exists():
            continue
        for path in folder.rglob("*"):
            if not path.is_file() or _skipped_dir(path):
                continue
            yield path
    for name in ("requirements.txt", "requirements-ci.txt"):
        path = root / name
        if path.is_file():
            yield path
    for path in root.glob(".env*"):
        if path.is_file():
            yield path
    aws_dir = root / ".aws"
    if aws_dir.is_dir():
        for name in ("credentials", "config"):
            path = aws_dir / name
            if path.is_file():
                yield path


def scan_tree(root, max_bytes=MAX_BYTES):
    findings = []
    for path in iter_files(root):
        findings.extend(scan_file(path, root, max_bytes=max_bytes))
    return findings


def main():
    findings = scan_tree(ROOT)
    if findings:
        print("\n".join(format_finding(item) for item in findings))
        return 1
    print("security scan passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
