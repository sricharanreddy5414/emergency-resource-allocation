"""Fail if source, workflows, or infra files contain credentials."""

import re
import sys
from pathlib import Path

from lambda_manifest import ROOT


SCAN_ROOTS = ["src", "frontend", "scripts", "tests", ".github", "infra"]
SKIP_DIRS = {"__pycache__", ".git", ".venv", "venv", "dist", ".pytest_cache"}
PATTERNS = [
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"ASIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----"),
    re.compile(r"aws_secret_access_key\s*=\s*['\"][A-Za-z0-9/+=]{16,}"),
    re.compile(r"AWS_SECRET_ACCESS_KEY\s*[:=]\s*['\"]?[A-Za-z0-9/+=]{16,}"),
]


def iter_files():
    for relative in SCAN_ROOTS:
        folder = ROOT / relative
        if not folder.exists():
            continue
        for path in folder.rglob("*"):
            if not path.is_file():
                continue
            if any(part in SKIP_DIRS for part in path.parts):
                continue
            if path.suffix.lower() in {".pyc", ".zip", ".png", ".jpg"}:
                continue
            yield path
    for name in ("requirements.txt", "requirements-ci.txt"):
        path = ROOT / name
        if path.is_file():
            yield path


def main():
    failures = []
    for path in iter_files():
        if path.name.startswith(".env") or path.suffix == ".pem":
            failures.append(f"{path.relative_to(ROOT)} is a secret file")
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pattern in PATTERNS:
            if pattern.search(text):
                failures.append(f"{path.relative_to(ROOT)} matched {pattern.pattern}")
                break
    if failures:
        print("\n".join(failures))
        return 1
    print("security scan passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
