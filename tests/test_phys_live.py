"""docs/238 -- the Live-Edit dBm sub-line follows an FSP edit, a Json-tree /
VS Code edit that reached the chip, and an Auto-Sync pull.

Server half: ``POST /bulk/phys`` answers what each amplitude's chain reads NOW
(the same ``amp_annotation`` the cold render uses), and the grid markup names
WHERE each amplitude's FSP lives. Client half: ``tests/phys_live_selfcheck.cjs``.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app

from tests.test_physical_units import _state, _write_chip

ROOT = Path(__file__).resolve().parents[1]
X180 = "qubits.qA1.xy.operations.x180.amplitude"
READOUT = "qubits.qA1.resonator.operations.readout.amplitude"
FLUX = "qubits.qA1.z.operations.const.amplitude"
FSP_XY = "ports.mw_outputs.con1.1.2.full_scale_power_dbm"


@pytest.fixture
def env(tmp_path):
    live = tmp_path / "chip"
    _write_chip(live, _state())
    app = create_app(instance_path=str(tmp_path / "inst"))
    app.config["TESTING"] = True
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    return {"client": c, "live": live}


def _phys(c, paths, chip=None):
    body = {"paths": paths}
    if chip is not None:
        body["chip"] = chip
    return c.post("/bulk/phys", data=json.dumps(body), content_type="application/json")


def _edit(c, path, value):
    # an FSP edit must say whether amps follow (the docs/FSP-compensation
    # contract); `solo` keeps the amplitudes, which is the case under test
    r = c.post("/field/edit", data={"dot_path": path, "value": str(value),
                                    "fsp_ack": "solo"})
    assert r.status_code == 200, r.data[:400]


def test_it_answers_each_amplitude_now(env):
    r = _phys(env["client"], [X180, READOUT, FLUX, "qubits.qA1.f_01"])
    d = r.get_json()
    assert r.status_code == 200 and d["ok"]
    assert d["phys"][X180]["text"] == "-20.0 dBm"
    assert d["phys"][X180]["fsp_path"] == FSP_XY
    assert d["phys"][FLUX]["kind"] == "lf"
    # only amplitude leaves are answered -- nothing else is a power
    assert "qubits.qA1.f_01" not in d["phys"]


def test_an_fsp_edit_moves_the_answer(env):
    c = env["client"]
    _edit(c, FSP_XY, -10)
    d = _phys(c, [X180, READOUT]).get_json()
    assert d["phys"][X180]["fsp"] == -10.0
    assert d["phys"][X180]["text"] == "-30.0 dBm"
    # the readout port was not touched
    assert d["phys"][READOUT]["text"] == "-20.0 dBm"


def test_an_amplitude_edit_elsewhere_moves_the_answer(env):
    c = env["client"]
    _edit(c, "qubits.qA1.xy.operations.x180_DragCosine.amplitude", 0.01)
    assert _phys(c, [X180]).get_json()["phys"][X180]["text"] == "-40.0 dBm"


def test_a_zero_amplitude_is_blank_not_minus_infinity(env):
    c = env["client"]
    _edit(c, "qubits.qA1.xy.operations.x180_DragCosine.amplitude", 0)
    assert _phys(c, [X180]).get_json()["phys"][X180] is None


def test_a_different_chip_is_refused(env):
    assert _phys(env["client"], [X180], chip="someone-else").status_code == 409


def test_bad_body_is_a_400(env):
    r = env["client"].post("/bulk/phys", data=json.dumps({"paths": "x"}),
                           content_type="application/json")
    assert r.status_code == 400


def test_the_grid_names_where_each_fsp_lives(env):
    html = env["client"].get("/bulk").get_data(as_text=True)
    assert f'data-phys-fsp-path="{FSP_XY}"' in html


def test_the_page_matches_the_route(env):
    """The cold render and the refresh are ONE computation: after an FSP edit
    a fresh page shows exactly the dBm the route answers."""
    c = env["client"]
    _edit(c, FSP_XY, 4)
    html = c.get("/bulk").get_data(as_text=True)
    want = _phys(c, [X180]).get_json()["phys"][X180]
    assert f'data-phys-fsp="{want["fsp"]}"' in html
    assert want["text"] in html


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_phys_live_selfcheck():
    r = subprocess.run(["node", str(ROOT / "tests" / "phys_live_selfcheck.cjs")],
                       capture_output=True, text=True, encoding="utf-8",
                       cwd=str(ROOT), timeout=180)
    if "Cannot find module 'jsdom'" in (r.stderr or ""):
        pytest.skip("jsdom not installed")
    out = r.stdout + r.stderr
    assert r.returncode == 0 and "FAIL" not in out, out[-3000:]
