"""Signup country selector and phone normalization."""

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


def test_signup_phone_country_selector():
    node = node_executable()
    assert node, "Node is required to execute the signup phone tests"
    result = subprocess.run(
        [node, str(ROOT / "tests" / "signup_phone.mjs")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
