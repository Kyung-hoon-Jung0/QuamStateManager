"""R3B MINOR 6: Enter (or Down) from the Live State Edit grid search goes into
the first matching cell WITHOUT selecting its text, and on a virtualized grid
waits for that cell to arrive.

The browser side runs in ``tests/v151_grid_search_enter_selfcheck.cjs`` on the
REAL /bulk fragment of a chip large enough for the server to ship the far
columns cold, and the REAL /bulk/cells answer for them.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app

ROOT = Path(__file__).resolve().parent.parent
HX = {"HX-Request": "true"}


def _qubit(i: int) -> dict:
    calib = {f"p{j:03d}": round(0.001 * j + i, 6) for j in range(120)}
    calib["zeta_gain"] = 1.5 + i
    return {"id": f"q{i}", "f_01": 5.0e9 + i * 1e7, "calib": calib}


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_enter_from_the_grid_search_selects_nothing_and_waits_for_a_cold_cell(tmp_path):
    chip = tmp_path / "chip"
    chip.mkdir()
    names = [f"q{i}" for i in range(1, 13)]
    (chip / "state.json").write_text(json.dumps({
        "qubits": {n: _qubit(i) for i, n in enumerate(names, 1)}, "qubit_pairs": {},
        "active_qubit_names": names}), encoding="utf-8")
    (chip / "wiring.json").write_text("{}", encoding="utf-8")
    c = create_app(testing=True, instance_path=str(tmp_path / "_i")).test_client()
    assert c.post("/load", data={"folder": str(chip)}).status_code < 400
    page = c.get("/bulk?vw=400", headers=HX).get_data(as_text=True)
    cold_key = "dyn__calib_zeta_gain"
    td = re.search(r'<td class="bulk-td[^"]*"\s+data-col-key="' + cold_key + '"', page)
    assert td and "bulk-td-cold" in td.group(0), "the column is not server-cold: the pin would be vacuous"
    cold_keys = sorted(set(re.findall(r'<td class="bulk-td[^"]*bulk-td-cold[^"]*"\s+data-col-key="([^"]+)"', page)))
    cells = c.get("/bulk/cells", query_string={"cols": ",".join(cold_keys)}).get_json()
    assert cells["ok"] and cold_key in cells["cells"], cells.get("error")
    fx = tmp_path / "fx.json"
    fx.write_text(json.dumps({
        "page": page, "cells": cells, "cold_key": cold_key,
        "cold_query": "zeta_gain", "cold_path": "qubits.q1.calib.zeta_gain",
        "hot_query": "q2", "hot_path": "qubits.q2.f_01"}), encoding="utf-8")
    p = subprocess.run(["node", str(ROOT / "tests" / "v151_grid_search_enter_selfcheck.cjs"), str(fx)],
                       capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT), timeout=240)
    if p.returncode == 2 and "jsdom not installed" in (p.stderr or ""):
        pytest.skip("jsdom not installed")
    assert p.returncode == 0, f"stdout:\n{p.stdout}\nstderr:\n{p.stderr}"
