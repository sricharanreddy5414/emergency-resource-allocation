"""Render a GitHub Actions summary from the local deploy and smoke files."""

import json
import sys
from pathlib import Path

from lambda_manifest import ALIAS, API_BASE, ROOT


def load(name):
    path = ROOT / "dist" / name
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    deploy = load("deploy-summary.json") or {}
    smoke = load("smoke-summary.json") or {}
    functions = deploy.get("functions") or []
    previous = [item.get("previous_version") or "none" for item in functions]
    lines = [
        "## ERAP deployment",
        "",
        f"- Environment: {deploy.get('environment', 'not recorded')}",
        f"- Commit: `{deploy.get('commit', 'not recorded')}`",
        f"- Backend: {len(functions)} Lambda functions published",
        f"- Alias: `{deploy.get('alias', ALIAS)}`",
        f"- API: {API_BASE}",
        "- API Gateway configuration: unchanged" if deploy else "- Backend deployment: not recorded",
        f"- Frontend: Amplify {smoke.get('amplify', 'not recorded')}",
        f"- Smoke test: {len(smoke.get('checks') or [])} API checks, frontend {((smoke.get('frontend') or {}).get('status', 'not recorded'))}",
        f"- Result: {'recorded' if deploy and smoke else 'incomplete'}",
        f"- Rollback target: previous alias versions {', '.join(previous) if previous else 'none'}",
        "",
    ]
    for item in functions:
        lines.append(
            f"- `{item['name']}` version {item['version']}, previous {item.get('previous_version') or 'none'}"
        )
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
