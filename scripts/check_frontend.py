"""Syntax-check the static frontend. There is no frontend build step."""

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from lambda_manifest import ROOT


def node_executable():
    found = shutil.which("node")
    if found:
        return found
    portable = Path(r"C:\Users\sri charan reddy\AppData\Local\Temp\erap-node\node-v22.20.0-win-x64\node.exe")
    if portable.is_file():
        return str(portable)
    raise SystemExit("node was not found")


def main():
    node = node_executable()
    app = ROOT / "frontend" / "app.js"
    index = ROOT / "frontend" / "index.html"
    style = ROOT / "frontend" / "style.css"
    for path in (app, index, style):
        if not path.is_file():
            raise SystemExit(f"Missing frontend file {path.name}")
    subprocess.run([node, "--check", str(app)], check=True)
    for extra in ("qr-code.js", "qr-handover.js"):
        subprocess.run([node, "--check", str(ROOT / "frontend" / extra)], check=True)
    html = index.read_text(encoding="utf-8")
    if "app.js" not in html or "style.css" not in html:
        raise SystemExit("index.html does not reference app.js and style.css")
    if "qr-code.js" not in html or "qr-handover.js" not in html:
        raise SystemExit("index.html does not reference the QR handover scripts")
    if 'id="exchange"' not in html or 'data-section="exchange"' not in html:
        raise SystemExit("Exchange destination missing from index.html")
    js = app.read_text(encoding="utf-8")
    for marker in (
        "EXCHANGE_API_URL",
        "loadExchangeWorkspace",
        "initializeExchange",
        "handover/confirm",
        "transfer/start",
    ):
        if marker not in js:
            raise SystemExit(f"Exchange frontend marker missing: {marker}")
    scripts = re.findall(r"<script>(.*?)</script>", html, flags=re.DOTALL)
    if not scripts:
        raise SystemExit("index.html has no inline script to check")
    with tempfile.TemporaryDirectory() as temporary:
        for number, body in enumerate(scripts, start=1):
            path = Path(temporary) / f"inline-{number}.js"
            path.write_text(body, encoding="utf-8")
            subprocess.run([node, "--check", str(path)], check=True)
    print("frontend syntax passed")
    print("NO_FRONTEND_BUILD")
    return 0


if __name__ == "__main__":
    sys.exit(main())
