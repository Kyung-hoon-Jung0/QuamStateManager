"""v1.5.1 U P2-20: Instrument Wiring -- each input names its own port, and the
colours have a legend.

The MW-FEM readout input 2 sits on the last row (where the front panel has
it) and, beside the output row numbers, read as "IN 8"; nothing said what the
colours mean. Inputs now carry "in N" and a legend lists the roles drawn.
Pinned on the REAL /instrument fragment in
tests/v151_wiring_legend_selfcheck.cjs.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app
from tests.cr_fixtures import make_cz_reference, write_folder

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_inputs_are_numbered_and_the_colours_explained(tmp_path):
    state, wiring = make_cz_reference()
    folder = write_folder(tmp_path / "chip", state, wiring)
    c = create_app(testing=True, instance_path=str(tmp_path / "_i")).test_client()
    assert c.post("/load", data={"folder": str(folder)}).status_code in (200, 302)
    page = c.get("/instrument", headers={"HX-Request": "true"}).get_data(as_text=True)
    assert "renderInstrumentWiring('instrument-diagram'" in page
    fx = tmp_path / "fx.json"
    fx.write_text(json.dumps({"page": page}), encoding="utf-8")
    p = subprocess.run(["node", str(ROOT / "tests" / "v151_wiring_legend_selfcheck.cjs"), str(fx)],
                       capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT), timeout=120)
    if p.returncode == 2 and "jsdom not installed" in (p.stderr or ""):
        pytest.skip("jsdom not installed")
    assert p.returncode == 0, f"stdout:\n{p.stdout}\nstderr:\n{p.stderr}"
