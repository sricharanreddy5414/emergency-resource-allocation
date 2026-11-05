import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def node_executable():
    from shutil import which

    return which("node")


def test_request_lifecycle_ui():
    node = node_executable()
    assert node, "Node is required"
    result = subprocess.run(
        [node, str(ROOT / "tests" / "request_lifecycle_ui.mjs")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
