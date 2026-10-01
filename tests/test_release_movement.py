"""Dashboard movement must show a release at its release time."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")


def test_released_movement_uses_release_time_not_allocation_time():
    block = APP.split("allocations.forEach(item => {", 1)[1].split("if (!events.length)", 1)[0]
    released_line = next(line for line in block.splitlines() if "released_at" in line)
    open_line = next(line for line in block.splitlines() if "allocated_at" in line)

    assert "item.released_at" in released_line
    assert "allocated_at" not in released_line
    assert "item.allocated_at" in open_line
    assert 'released ? "Resource released" : "Allocation completed"' in block
