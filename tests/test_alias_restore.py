"""Failed-deploy restore must not overwrite a newer alias."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from set_live_version import from_summary, restore_target


def test_restore_only_when_this_deploy_still_owns_live():
    assert restore_target("61", "61", "60") == "60"


def test_restore_leaves_a_newer_deploy_in_place():
    assert restore_target("62", "61", "60") == ""


def test_restore_leaves_an_alias_that_is_already_previous():
    assert restore_target("60", "61", "60") == ""


def test_restore_rejects_a_missing_or_unpublished_version():
    assert restore_target("61", "61", "") == ""
    assert restore_target("61", "", "60") == ""
    assert restore_target("1", "1", "0") == ""


def test_from_summary_moves_only_owned_aliases(tmp_path):
    summary = tmp_path / "deploy-summary.json"
    summary.write_text(
        json.dumps(
            {
                "functions": [
                    {"name": "get-resources", "version": "61", "previous_version": "60"},
                    {"name": "create-request", "version": "55", "previous_version": "54"},
                ]
            }
        ),
        encoding="utf-8",
    )
    moved = []

    def read_version(name):
        return {"get-resources": "61", "create-request": "56"}[name]

    from_summary(path=summary, read_version=read_version, move=lambda name, version: moved.append((name, version)))
    assert moved == [("get-resources", "60")]
