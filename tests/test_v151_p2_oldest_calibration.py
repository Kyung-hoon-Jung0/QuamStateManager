"""v1.5.1 U P2-18: Chip Status Health -- the "oldest calibration" tile and the
"Needs attention" chip name the same thing.

The tile spans qubits and pairs (core/query.py's summary); the chip looked at
qubits only, so a chip whose oldest value sat on a pair read "2 days ago"
in the tile and "q1 today -- oldest calibration" in the chip. Pinned on the
REAL /topology health fragment in tests/v151_oldest_calibration_selfcheck.cjs.
"""

from __future__ import annotations

import datetime as dt
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_the_tile_and_the_chip_agree(tmp_path):
    now = dt.datetime.now(dt.timezone.utc)
    iso = lambda d: d.isoformat()  # noqa: E731
    state = {
        "qubits": {"q1": {"id": "q1", "grid_location": "0,0", "T1": 3e-5,
                          "T1_updated_at": iso(now - dt.timedelta(hours=1))},
                   "q2": {"id": "q2", "grid_location": "1,0", "T1": 2e-5,
                          "T1_updated_at": iso(now - dt.timedelta(hours=2))}},
        "qubit_pairs": {"q1-2": {"id": "q1-2", "qubit_control": "#/qubits/q1", "qubit_target": "#/qubits/q2",
                                 "macros": {"cz": {"fidelity": {"InterleavedRB": 0.99,
                                                                "updated_at": iso(now - dt.timedelta(days=3))}}}}},
        "active_qubit_names": ["q1", "q2"]}
    chip = tmp_path / "chip"
    chip.mkdir()
    (chip / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (chip / "wiring.json").write_text(json.dumps({"wiring": {}, "network": {"host": "1.1.1.1"}}), encoding="utf-8")
    c = create_app(testing=True, instance_path=str(tmp_path / "_i")).test_client()
    assert c.post("/load", data={"folder": str(chip)}).status_code in (200, 302)
    page = c.get("/topology?view=health", headers={"HX-Request": "true"}).get_data(as_text=True)
    assert "ChipStatus.mount(" in page and 'id="topo-health-worst"' in page
    fx = tmp_path / "fx.json"
    fx.write_text(json.dumps({"page": page, "expect_id": "q1-2", "expect_kind": "p"}), encoding="utf-8")
    p = subprocess.run(["node", str(ROOT / "tests" / "v151_oldest_calibration_selfcheck.cjs"), str(fx)],
                       capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT), timeout=120)
    if p.returncode == 2 and "jsdom not installed" in (p.stderr or ""):
        pytest.skip("jsdom not installed")
    assert p.returncode == 0, f"stdout:\n{p.stdout}\nstderr:\n{p.stderr}"
