"""v1.5.1 U P2-8: the plot-apply popup's rows fit, and "Changed since this run
by #N ..." spans its row.

Every row needed a horizontal scrollbar (the column floors 140 + 110 + 180 px
plus the button summed past the popup) and the history note fell into the
path column, wrapped into a strip four words wide. Pinned on a REAL page with
the SHIPPED app.js building the rows (tests/v151_plot_apply_rows_selfcheck.cjs).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.test_cz_plan_page import _client

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_the_popup_rows_fit_and_the_history_note_spans(tmp_path):
    c = _client(tmp_path)
    page = c.get("/qubits").get_data(as_text=True)
    assert 'id="plot-apply-popup"' in page
    fx = tmp_path / "fx.json"
    fx.write_text(json.dumps({"page": page, "active": c.get("/chip/active-token").get_json()}), encoding="utf-8")
    p = subprocess.run(["node", str(ROOT / "tests" / "v151_plot_apply_rows_selfcheck.cjs"), str(fx)],
                       capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT), timeout=120)
    if p.returncode == 2 and "jsdom not installed" in (p.stderr or ""):
        pytest.skip("jsdom not installed")
    assert p.returncode == 0, f"stdout:\n{p.stdout}\nstderr:\n{p.stderr}"
