"""Register Resource quantity fields stay wired to the existing quantity API."""

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


def test_register_resource_quantity_ui():
    node = node_executable()
    assert node, "Node is required to execute the register-resource quantity UI test"
    result = subprocess.run(
        [node, str(ROOT / "tests" / "register_resource_quantity_ui.mjs")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "register resource quantity ui passed" in result.stdout
