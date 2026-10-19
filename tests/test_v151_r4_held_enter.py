"""v1.5.1 R4 A2: holding Enter on an armed two-press button never confirms.

R3 M6 counted a confirm once 400 ms had passed since arming. A held Enter on
a focused button clicks it again on every key repeat -- about every 33 ms
after about 500 ms on Windows -- so the first repeat after 400 ms confirmed
"Keep mine -- overwrite live", "Discard my edits", the merge with picks and
the top bar's armed Apply. Now the arming press must be let go first, and
key repeats on an armed button do nothing.

Pinned on the REAL sync panel (/state/review: one with an unapplied edit,
one with a same-field collision) and the REAL tray (a saved working state:
the top bar's armed Apply), each button run through its own onclick, under
jsdom (tests/v151_r4_held_enter_selfcheck.cjs).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app

ROOT = Path(__file__).resolve().parent.parent
_WIRING = {"network": {"host": "127.0.0.1", "cluster_name": "C1"}}
_STATE = {"qubits": {"q1": {"id": "q1", "T1": 1e-5}}, "qubit_pairs": {},
          "active_qubit_names": ["q1"]}


def _chip(folder: Path) -> Path:
    folder.mkdir(parents=True)
    (folder / "state.json").write_text(json.dumps(_STATE), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")
    return folder


def _client(tmp_path, name):
    routes._quam_cache.clear()
    app = create_app(testing=True, instance_path=str(tmp_path / f"_inst_{name}"))
    return app.test_client()


def test_a_held_enter_never_confirms_a_two_press_button(tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    # one unapplied edit: Keep mine and Discard my edits
    live = _chip(tmp_path / "a" / "quam_state")
    c = _client(tmp_path, "a")
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    assert c.post("/field/edit", data={"dot_path": "qubits.q1.T1", "value": "2e-05"}).get_json()["ok"]
    mine = c.get("/state/review").get_data(as_text=True)
    assert "sp-keep" in mine and "sp-discard-all" in mine
    # the lab wrote the same field: the merge arms once a side is picked
    st = json.loads((live / "state.json").read_text(encoding="utf-8"))
    st["qubits"]["q1"]["T1"] = 3e-5
    (live / "state.json").write_text(json.dumps(st), encoding="utf-8")
    t = time.time() + 2
    os.utime(live / "state.json", (t, t))
    collide = c.get("/state/review").get_data(as_text=True)
    assert "sp-merge" in collide and "data-pick-path" in collide, collide[:2000]
    # a saved working state: the top bar's two-press Apply
    live2 = _chip(tmp_path / "b" / "quam_state")
    c2 = _client(tmp_path, "b")
    assert c2.post("/load", data={"folder": str(live2)}).status_code in (200, 302)
    assert c2.post("/field/edit", data={"dot_path": "qubits.q1.T1", "value": "2e-05"}).get_json()["ok"]
    assert c2.post("/save").status_code == 200
    tray = c2.get("/state/tray").get_data(as_text=True)
    assert "btn-apply-live sync-arm" in tray, tray[:2000]
    fx = tmp_path / "fixture.json"
    fx.write_text(json.dumps({"mine": mine, "collide": collide, "tray": tray}), encoding="utf-8")
    r = subprocess.run([node, str(ROOT / "tests" / "v151_r4_held_enter_selfcheck.cjs"), str(fx)],
                       capture_output=True, text=True, encoding="utf-8", timeout=180, cwd=str(ROOT))
    if r.returncode == 2:
        pytest.skip("jsdom not installed")
    assert r.returncode == 0 and "DONE" in r.stdout, r.stdout[-3000:] + r.stderr[-3000:]
