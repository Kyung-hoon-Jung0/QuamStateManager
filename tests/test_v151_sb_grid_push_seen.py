"""v1.5.1 SB-F4: the grid's "Apply to live now" pushed another window's edit.

Window A has a saved draft and types a cell; window B stages ``q4.T1``. A's
"Apply to live now" confirm named only A's cell (and "the saved, not-yet-
applied working state"); the grid's commit then refreshed A's tray from the
server, and the direct push declared nothing -- live ended up with B's q4.

The push now declares the paths its confirm showed (``seen_paths``); any
pending row outside them is unseen and refused by name (docs/120 gate), and
``ack_unseen`` is the one answer that writes it too. The client half is
pinned in tests/v151_tab_chip_selfcheck.cjs.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app

_WIRING = {"network": {"host": "127.0.0.1", "cluster_name": "C1"}}
JSON = {"Accept": "application/json"}


def _state():
    qs = {f"q{i}": {"id": f"q{i}", "T1": 1.0e-5 * i} for i in (1, 2, 3, 4)}
    qs["q2"]["T2"] = "#/qubits/q2/T1"          # a pointer leaf: edits land on q2.T1
    return {"qubits": qs, "qubit_pairs": {}, "active_qubit_names": sorted(qs)}


@pytest.fixture
def env(tmp_path):
    live = tmp_path / "chip" / "quam_state"
    live.mkdir(parents=True)
    (live / "state.json").write_text(json.dumps(_state()), encoding="utf-8")
    (live / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")
    routes._quam_cache.clear()
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    return {"app": app, "c": c, "live": live}


def _live(env, q, k="T1"):
    return json.loads((env["live"] / "state.json").read_text(encoding="utf-8"))["qubits"][q][k]


def _edit(c, path, value):
    r = c.post("/field/edit", data={"dot_path": path, "value": value})
    assert r.status_code == 200 and r.get_json()["ok"], r.data


def _scene(env):
    """A: saved draft (q3). B: stages q4. A: grid commit of q1."""
    c = env["c"]
    _edit(c, "qubits.q3.T1", "3.3e-05")
    assert c.post("/save").status_code == 200          # the saved draft
    _edit(c, "qubits.q4.T1", "4.4e-05")                # window B
    _edit(c, "qubits.q1.T1", "1.1e-05")                # window A's grid commit


class TestTheDirectPushDeclaresItsConfirm:
    def test_another_windows_edit_is_refused_by_name(self, env):
        _scene(env)
        r = env["c"].post("/state/apply-to-live", headers=JSON,
                          data={"seen_paths": json.dumps(["qubits.q1.T1"])})
        assert r.status_code == 409, r.get_data(as_text=True)[:300]
        body = r.get_json()
        assert body["status"] == "unseen_changes" and body["paths"] == ["qubits.q4.T1"], body
        assert _live(env, "q4") == 1.0e-5 * 4 and _live(env, "q1") == 1.0e-5, "nothing may be written"
        assert _live(env, "q3") == 1.0e-5 * 3

    def test_ack_unseen_writes_them_too(self, env):
        _scene(env)
        r = env["c"].post("/state/apply-to-live", headers=JSON,
                          data={"seen_paths": json.dumps(["qubits.q1.T1"]), "ack_unseen": "1"})
        assert r.status_code == 200, r.get_data(as_text=True)[:300]
        assert (_live(env, "q1"), _live(env, "q3"), _live(env, "q4")) == (1.1e-5, 3.3e-5, 4.4e-5)

    def test_a_confirm_that_showed_every_row_applies(self, env):
        _scene(env)
        r = env["c"].post("/state/apply-to-live",
                          data={"seen_paths": json.dumps(["qubits.q1.T1", "qubits.q4.T1"])})
        assert r.status_code == 200 and _live(env, "q4") == 4.4e-5

    def test_a_typed_pointer_cell_counts_as_its_target(self, env):
        """The grid knows the cell it typed; the tray records the pointer's
        target. The gate follows the edit door's own resolution."""
        c = env["c"]
        _edit(c, "qubits.q2.T2", "2.2e-05")            # lands on qubits.q2.T1
        assert [e.dot_path for e in env["app"].config["contexts"][
            env["app"].config["active_context"]]["store"].change_log] == ["qubits.q2.T1"]
        r = c.post("/state/apply-to-live", data={"seen_paths": json.dumps(["qubits.q2.T2"])})
        assert r.status_code == 200 and _live(env, "q2") == 2.2e-5

    def test_rows_the_same_press_staged_count_as_seen(self, env):
        """A press can stage more than the cell typed (an FSP edit's
        compensated amplitudes, one batch): its group is seen; another
        window's row still is not."""
        c = env["c"]
        _edit(c, "qubits.q4.T1", "4.4e-05")                       # window B
        r = c.post("/field/edit-batch", json={"updates": [
            {"dot_path": "qubits.q1.T1", "value": "1.1e-05"},
            {"dot_path": "qubits.q3.T1", "value": "3.3e-05"}]})    # one press, two rows
        assert r.status_code == 200 and r.get_json()["group_id"], r.data
        r = c.post("/state/apply-to-live", headers=JSON,
                   data={"seen_paths": json.dumps(["qubits.q1.T1"])})
        assert r.status_code == 409 and r.get_json()["paths"] == ["qubits.q4.T1"], r.get_json()

    def test_the_merge_press_is_held_to_the_same_set(self, env):
        c = env["c"]
        _edit(c, "qubits.q4.T1", "4.4e-05")            # window B
        _edit(c, "qubits.q1.T1", "1.1e-05")            # window A
        r = c.post("/state/sync", data={"mode": "apply", "seen_paths": json.dumps(["qubits.q1.T1"]),
                                        # the refreshed tray's own count/sig would pass
                                        "seen_changes": "2"})
        assert r.status_code == 409 and r.get_json()["paths"] == ["qubits.q4.T1"]
        assert _live(env, "q1") == 1.0e-5 and _live(env, "q4") == 1.0e-5 * 4
