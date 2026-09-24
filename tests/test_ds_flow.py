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


class TestDigestBandSaysWhatItCounted:
    """QA datasets-r2-18 (server half; the client twin is pinned in
    ``ds_flow_selfcheck.cjs``). The band read "all OK" (run status) beside a
    red "q3 x2" (per-qubit outcome), and the chip pasted an unscoped
    ``qubit:q3 outcome:fail`` that matched every date and any row holding q3
    where SOME target failed."""

    @staticmethod
    def _seed(root, run_id, date, qubits, outcomes, status="successful"):
        import json
        d = root / date
        d.mkdir(parents=True, exist_ok=True)
        run = d / f"#{run_id}_rabi_0{run_id % 10}0000"
        run.mkdir()
        (run / "node.json").write_text(json.dumps({
            "metadata": {"name": "rabi", "status": status,
                         "run_start": f"{date}T01:00:00", "run_end": f"{date}T01:00:01"},
            "data": {"parameters": {"model": {"qubits": qubits}},
                     "outcomes": outcomes},
            "id": run_id, "parents": [], "created_at": f"{date}T01:00:00",
        }), encoding="utf-8")
        (run / "data.json").write_text("{}", encoding="utf-8")

    def _band(self, tmp_path, runs):
        import re
        from quam_state_manager.web.app import create_app
        root = tmp_path / "data"
        for args in runs:
            self._seed(root, *args)
        c = create_app(testing=True, instance_path=str(tmp_path / "inst")).test_client()
        c.post("/workspace/add", data={"folder": str(root)})
        html = c.get("/datasets").get_data(as_text=True)
        m = re.search(r'<div class="ds-digest-band">(.*?)</div>', html, re.S)
        assert m, "no digest band rendered"
        return m.group(1)

    def test_failed_outcomes_are_not_all_ok_and_chips_are_day_scoped(self, tmp_path):
        band = self._band(tmp_path, [
            (4, "2026-09-24", ["q3", "q4"], {"q3": "failed", "q4": "successful"}),
            (3, "2026-09-24", ["q3"], {"q3": "failed"}),
            (2, "2026-09-24", ["q3", "q4"], {"q3": "successful", "q4": "failed"}),
            (1, "2026-09-23", ["q3"], {"q3": "failed"}),
        ])
        assert "all OK" not in band
        assert "no run errors" in band
        assert 'data-example="date:2026-09-24 outcome:q3=fail"' in band
        assert "q3&nbsp;×2" in band
        assert 'data-example="date:2026-09-24 outcome:q4=fail"' in band
        assert "qubit:q3 outcome:fail" not in band

    def test_failed_run_button_is_day_scoped(self, tmp_path):
        band = self._band(tmp_path, [
            (2, "2026-09-24", ["q1"], {"q1": "successful"}, "error"),
            (1, "2026-09-23", ["q1"], {"q1": "successful"}, "error"),
        ])
        assert 'data-example="date:2026-09-24 is:failed"' in band
        assert "1 failed" in band

    def test_all_ok_still_shown_when_nothing_failed(self, tmp_path):
        band = self._band(tmp_path, [
            (1, "2026-09-24", ["q1"], {"q1": "successful"}),
        ])
        assert "all OK" in band
        assert "ds-digest-qchip" not in band
