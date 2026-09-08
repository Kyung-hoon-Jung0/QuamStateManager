"""docs/112 (#12) — datasets daily-flow driver.

Entirely client-side (j/k/Enter keyboard nav, the "↻ Newest" sort-reset
chip, digest-follows-filter): server routes and /datasets HTML are
untouched; the behavior is pinned by ``tests/ds_flow_selfcheck.cjs``
against the REAL dataset-virtual.js.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "ds_flow_selfcheck.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_ds_flow_client_selfcheck():
    proc = subprocess.run(
        ["node", str(_SELFCHECK)], capture_output=True, text=True,
        cwd=str(_ROOT), timeout=120)
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"


_ARROW_SELFCHECK = _ROOT / "tests" / "ds_arrow_nav_selfcheck.cjs"


class TestArrowKeysWalkTheLeftList:
    """Customer feedback 2026-09-08: click a run in the left list, then ↑ / ↓
    open the previous / next run (PgUp / PgDn step 10) -- data exploration by
    keyboard alone. The behaviour lives in app.js and is pinned by
    ``tests/ds_arrow_nav_selfcheck.cjs`` against the REAL file; the cheat
    sheet says so."""

    @pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
    def test_arrow_nav_client_selfcheck(self):
        proc = subprocess.run(
            ["node", str(_ARROW_SELFCHECK)], capture_output=True, text=True,
            cwd=str(_ROOT), timeout=120)
        if proc.returncode == 2 and "jsdom not installed" in (proc.stderr or ""):
            pytest.skip("jsdom not installed")
        assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"

    def test_the_cheat_sheet_teaches_the_arrows(self, tmp_path):
        from quam_state_manager.web.app import create_app
        c = create_app(testing=True, instance_path=str(tmp_path / "inst")).test_client()
        html = c.get("/help").get_data(as_text=True)
        assert "previous / next run from the left list" in html
        assert "<kbd>PgUp</kbd> / <kbd>PgDn</kbd> step 10" in html
