"""v1.5.1 (r6 P2-1): a plot-apply row's outcome gets its own line.

Once a row showed its outcome -- staged, written, or refused with the
server's reason -- the text sat in the row's auto-width button column, took
the width, and squeezed the field path and the PREVIOUS column to one
character per line. The outcome now spans the row under the values and
wraps there. Pinned on a REAL page with the SHIPPED app.js marking the row
(tests/v151_r6_plot_outcome_line_selfcheck.cjs).
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
def test_the_outcome_is_its_own_line_and_never_squeezes_the_values(tmp_path):
    c = _client(tmp_path)
    page = c.get("/qubits").get_data(as_text=True)
    assert 'id="plot-apply-popup"' in page
    fx = tmp_path / "fx.json"
    fx.write_text(json.dumps({"page": page, "active": c.get("/chip/active-token").get_json()}), encoding="utf-8")
    p = subprocess.run(["node", str(ROOT / "tests" / "v151_r6_plot_outcome_line_selfcheck.cjs"), str(fx)],
                       capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT), timeout=120)
    if p.returncode == 2 and "jsdom not installed" in (p.stderr or ""):
        pytest.skip("jsdom not installed")
    assert p.returncode == 0, f"stdout:\n{p.stdout}\nstderr:\n{p.stderr}"
