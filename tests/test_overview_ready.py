"""Overview readiness follows the resource operational status."""

import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def node_executable():
    found = shutil.which("node")
    if found:
        return found
    portable = Path(r"C:\Users\sri charan reddy\AppData\Local\Temp\erap-node\node-v22.20.0-win-x64\node.exe")
    if portable.is_file():
        return str(portable)
    return None


def test_cancelled_lifecycle_note_does_not_submit():
    source = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
    start = source.index("async function lifecycleResourceAction")
    end = source.index("async function assignResourceAction", start)
    body = source[start:end]
    assert "if (notes === null)" in body
    assert body.index("if (notes === null)") < body.index("postEverydayResource")


def test_overview_ready_count_follows_operational_status():
    node = node_executable()
    assert node, "Node is required to execute the overview readiness test"
    result = subprocess.run(
        [node, str(ROOT / "tests" / "overview_ready_ui.mjs")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
