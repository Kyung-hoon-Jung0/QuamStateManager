"""Driver for tests/chrome_toggle_selfcheck.cjs — the ☰ chrome toggle
(customer feedback 2026-08-27): ONE press collapses the sidebar AND the top
bar; one press on the floating ☰ restores both. Runs the real app.js under
jsdom; skips (honestly) when node or jsdom is absent."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "chrome_toggle_selfcheck.cjs"


def _node() -> str | None:
    return shutil.which("node")


@pytest.mark.skipif(_node() is None, reason="node not available")
def test_chrome_toggle_selfcheck():
    probe = subprocess.run(
        [_node(), "-e", "require('jsdom')"],
        capture_output=True, text=True, cwd=str(_ROOT),
    )
    if probe.returncode != 0:
        pytest.skip("jsdom not installed for node")
    res = subprocess.run(
        [_node(), str(_SELFCHECK)],
        capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT),
    )
    assert res.returncode == 0, (res.stdout + "\n" + res.stderr)
    assert "ok - ONE press collapses the sidebar AND the top bar" in res.stdout


def test_one_press_collapses_both_source_pin():
    """Greppable without node: cycleChrome is a two-state toggle, not the
    old three-leg cycle."""
    js = (_ROOT / "quam_state_manager" / "web" / "static" / "app.js").read_text(encoding="utf-8")
    body = js.split("window.cycleChrome = function()", 1)[1].split("};", 1)[0]
    assert "collapseAll" in body
    assert "// 0 → 1" not in body and "// 1 → 2" not in body, "the three-leg cycle is gone"
