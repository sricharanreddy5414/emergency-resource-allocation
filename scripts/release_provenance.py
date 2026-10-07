"""Prove a production commit is on main before alias live moves.

The check uses the local git history. It does not contact AWS or GitHub.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path


SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")


class ProvenanceError(SystemExit):
    pass


def normalize_sha(sha):
    text = str(sha or "").strip().lower()
    if not SHA_PATTERN.fullmatch(text):
        raise ProvenanceError("malformed SHA")
    return text


def _git(repo, args):
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        capture_output=True,
    )


def require_commit_on_main(sha, repo, main_ref="main"):
    """Reject a SHA that is malformed, missing, or not contained in main."""
    commit = normalize_sha(sha)
    root = Path(repo)
    exists = _git(root, ["cat-file", "-e", f"{commit}^{{commit}}"])
    if exists.returncode != 0:
        raise ProvenanceError("nonexistent SHA")
    ancestor = _git(root, ["merge-base", "--is-ancestor", commit, main_ref])
    if ancestor.returncode != 0:
        raise ProvenanceError("SHA is not on main")
    return commit


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--sha", required=True)
    parser.add_argument("--repo", default=".")
    parser.add_argument("--main-ref", default="origin/main")
    args = parser.parse_args(argv)
    commit = require_commit_on_main(args.sha, args.repo, args.main_ref)
    print(commit)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ProvenanceError as error:
        print(error, file=sys.stderr)
        sys.exit(1)
