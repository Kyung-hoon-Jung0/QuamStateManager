"""v1.5.1 SB-P2-2: an Auto-Sync pull keeps a redo the pull did not touch.

Undo an edit, let Auto-Sync take a live change of ANOTHER field, press redo:
"Nothing to redo -- the state changed after your undo". The pull rebuilt the
store and the seq handshake read it as a fork of the user's timeline. A pull
is not an edit of the user's: a redo whose paths the pull did not change now
survives it (the direct pull of a clean copy and the merge pull alike). When
the pull DID change a path the redo would write, the history is dropped and
the redo says why, naming the path. Pinned over the real routes.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app

_WIRING = {"network": {"host": "1.1.1.1"}, "wiring": {"qubits": {}}}


def _mk(tmp_path):
    live = tmp_path / "quam_state"
    live.mkdir(parents=True, exist_ok=True)
    (live / "state.json").write_text(json.dumps(
        {"qubits": {"q1": {"id": "q1", "f_01": 6.0e9, "T1": 1e-5},
                    "q2": {"id": "q2", "f_01": 5.0e9, "T1": 2e-5}},
         "qubit_pairs": {}, "active_qubit_names": ["q1", "q2"]}), encoding="utf-8")
    (live / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")
    routes._quam_cache.clear()
    app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
    c = app.test_client()
    c.post("/load", data={"folder": str(live)})
    c.post("/auto-sync/set", data={"pull": "1"})
    return app, c, live


def _ctx(app):
    return (app.config.get("contexts") or {}).get(app.config.get("active_context"))


def _lab_writes(app, live: Path, q: str, key: str, value, bump=1):
    d = json.loads((live / "state.json").read_text(encoding="utf-8"))
    d["qubits"][q][key] = value
    (live / "state.json").write_text(json.dumps(d), encoding="utf-8")
    t = time.time() + 2 * bump
    import os
    os.utime(live / "state.json", (t, t))
    _ctx(app)["live_diverged"] = True


def _msg(r):
    return json.loads(r.headers["HX-Trigger"])["cellsReverted"]["message"]


def test_a_pull_of_another_field_keeps_the_redo(tmp_path):
    app, c, live = _mk(tmp_path)
    assert c.post("/field/edit", data={"dot_path": "qubits.q1.T1", "value": "3e-05"}).get_json()["ok"]
    c.post("/undo")                                    # the change log is clean again
    _lab_writes(app, live, "q2", "f_01", 5.1e9)
    assert c.post("/auto-sync/pull").status_code == 200
    assert _ctx(app)["store"].merged["qubits"]["q2"]["f_01"] == 5.1e9, "fixture: the pull landed"
    m = _msg(c.post("/redo"))
    assert m.startswith("Redone") and "qubits.q1.T1" in m, m
    assert _ctx(app)["store"].merged["qubits"]["q1"]["T1"] == 3e-5


def test_a_merge_pull_of_another_field_keeps_the_redo(tmp_path):
    app, c, live = _mk(tmp_path)
    for v in ("3e-05", "4e-05"):
        assert c.post("/field/edit", data={"dot_path": "qubits.q1.T1", "value": v}).get_json()["ok"]
    c.post("/undo")                                    # one edit stays pending
    _lab_writes(app, live, "q2", "f_01", 5.2e9)
    r = c.post("/state/sync", data={"mode": "reapply"})   # the Auto-Sync merge press
    assert r.status_code == 200 and r.get_json()["status"] == "ok", r.data[:300]
    m = _msg(c.post("/redo"))
    assert m.startswith("Redone") and "qubits.q1.T1" in m, m
    assert _ctx(app)["store"].merged["qubits"]["q1"]["T1"] == 4e-5


def test_a_pull_of_the_same_field_drops_the_redo_and_says_why(tmp_path):
    app, c, live = _mk(tmp_path)
    assert c.post("/field/edit", data={"dot_path": "qubits.q1.T1", "value": "3e-05"}).get_json()["ok"]
    c.post("/undo")
    _lab_writes(app, live, "q1", "T1", 1.7e-5)
    assert c.post("/auto-sync/pull").status_code == 200
    m = _msg(c.post("/redo"))
    assert m.startswith("Nothing to redo") and "qubits.q1.T1" in m, m
    assert _ctx(app)["store"].merged["qubits"]["q1"]["T1"] == 1.7e-5, "the lab's value stands"
