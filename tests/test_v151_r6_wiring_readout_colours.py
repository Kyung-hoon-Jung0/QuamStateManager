"""v1.5.1 (r6 P2-16): Instrument Wiring's "Readout out" and "Readout in" are
told apart at a glance, in both themes.

The legend (and the ports) used two near-identical oranges for them.
Readout in keeps the orange family as a dark burnt orange: the pair now
differs in lightness, it stands out on both theme backgrounds, the white
port label reads on it, and the Wiring table's role badge uses the same
colour with readable text. Pinned on the REAL /instrument fragment drawn by
the SHIPPED app.js, and the shipped stylesheets
(tests/v151_r6_wiring_readout_colours_selfcheck.cjs).
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
def test_readout_out_and_in_are_told_apart(tmp_path):
    state, wiring = make_cz_reference()
    folder = write_folder(tmp_path / "chip", state, wiring)
    c = create_app(testing=True, instance_path=str(tmp_path / "_i")).test_client()
    assert c.post("/load", data={"folder": str(folder)}).status_code in (200, 302)
    page = c.get("/instrument", headers={"HX-Request": "true"}).get_data(as_text=True)
    assert "renderInstrumentWiring('instrument-diagram'" in page
    fx = tmp_path / "fx.json"
    fx.write_text(json.dumps({"page": page}), encoding="utf-8")
    p = subprocess.run(["node", str(ROOT / "tests" / "v151_r6_wiring_readout_colours_selfcheck.cjs"), str(fx)],
                       capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT), timeout=120)
    if p.returncode == 2 and "jsdom not installed" in (p.stderr or ""):
        pytest.skip("jsdom not installed")
    assert p.returncode == 0, f"stdout:\n{p.stdout}\nstderr:\n{p.stderr}"
