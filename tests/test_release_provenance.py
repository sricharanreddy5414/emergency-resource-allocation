"""Local git history proves whether a release SHA is on main."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from release_provenance import ProvenanceError, require_commit_on_main


def git(repo, *args):
    environment = os.environ.copy()
    environment.update(
        {
            "GIT_AUTHOR_NAME": "ERAP Test",
            "GIT_AUTHOR_EMAIL": "erap-test@example.com",
            "GIT_COMMITTER_NAME": "ERAP Test",
            "GIT_COMMITTER_EMAIL": "erap-test@example.com",
        }
    )
    subprocess.run(["git", *args], cwd=repo, check=True, env=environment, capture_output=True, text=True)


def repository(tmp_path):
    git(tmp_path, "init", "-b", "main")
    readme = tmp_path / "README"
    readme.write_text("one\n", encoding="utf-8")
    git(tmp_path, "add", "README")
    git(tmp_path, "commit", "-m", "one")
    main_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    git(tmp_path, "checkout", "-b", "side")
    readme.write_text("side\n", encoding="utf-8")
    git(tmp_path, "add", "README")
    git(tmp_path, "commit", "-m", "side")
    side_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    git(tmp_path, "checkout", "main")
    return main_sha, side_sha


def test_sha_on_main_is_allowed(tmp_path):
    main_sha, _side = repository(tmp_path)
    assert require_commit_on_main(main_sha, tmp_path, "main") == main_sha


def test_sha_not_on_main_is_rejected(tmp_path):
    _main, side_sha = repository(tmp_path)
    with pytest.raises(ProvenanceError, match="not on main"):
        require_commit_on_main(side_sha, tmp_path, "main")


def test_malformed_sha_is_rejected(tmp_path):
    repository(tmp_path)
    with pytest.raises(ProvenanceError, match="malformed"):
        require_commit_on_main("abc", tmp_path, "main")


def test_nonexistent_sha_is_rejected(tmp_path):
    repository(tmp_path)
    missing = "0123456789abcdef0123456789abcdef01234567"
    with pytest.raises(ProvenanceError, match="nonexistent"):
        require_commit_on_main(missing, tmp_path, "main")


def test_known_numeric_rollback_version_stays_separate_from_unrelated_commits(tmp_path):
    main_sha, side_sha = repository(tmp_path)
    from set_live_version import restore_target

    assert restore_target("8", "8", "7") == "7"
    assert require_commit_on_main(main_sha, tmp_path, "main") == main_sha
    with pytest.raises(ProvenanceError, match="not on main"):
        require_commit_on_main(side_sha, tmp_path, "main")
