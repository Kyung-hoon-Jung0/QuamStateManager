"""An undo pressed while an Apply/Auto-Sync push is still writing the chip
undoes the NEWEST action, never an older one.

The push saves the working copy first (the change log is cleared) and journals
the outgoing edits only once the live write is decided. An undo that arrived
in between saw an empty log, walked the journal, and undid the PREVIOUS
applied action -- leaving the newest one in effect (a pair pointing at a qubit
that the undo had just removed). The undo now waits for the in-flight push.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from quam_state_manager.core import working_copy
from quam_state_manager.web.app import create_app

_WIRING = {"network": {"host": "1.1.1.1", "cluster_name": "C1"}}


@pytest.fixture
def env(tmp_path):
    live = tmp_path / "chips" / "live"
    live.mkdir(parents=True)
    state = {"qubits": {"qA1": {"id": "qA1", "f_01": 5.0e9,
                                "z": {"joint_offset": 0.08}}},
             "qubit_pairs": {}, "active_qubit_names": ["qA1"]}
    (live / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (live / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    return {"app": app, "client": c, "live": live}


def _edit(c, path, value):
    r = c.post("/field/edit-batch", json={
        "updates": [{"dot_path": path, "value": str(value)}], "expect_chip": ""})
    assert r.status_code == 200 and r.get_json()["ok"], r.data


def _live(env):
    q = json.loads((env["live"] / "state.json").read_text(encoding="utf-8"))["qubits"]["qA1"]
    return q["z"]["joint_offset"], q["f_01"]


def test_an_undo_during_a_push_undoes_the_pushed_action(env, monkeypatch):
    c = env["client"]
    _edit(c, "qubits.qA1.z.joint_offset", 0.1)          # action 1
    assert c.post("/state/apply-to-live").status_code == 200
    _edit(c, "qubits.qA1.f_01", 5.5e9)                   # action 2, the newest

    in_write = threading.Event()
    release = threading.Event()
    real = working_copy.apply_to_live

    def slow_apply(*a, **k):
        in_write.set()
        assert release.wait(10)
        return real(*a, **k)

    monkeypatch.setattr(working_copy, "apply_to_live", slow_apply)
    out = {}

    def push():
        with env["app"].test_client() as c2:
            out["push"] = c2.post("/state/apply-to-live").status_code

    t = threading.Thread(target=push)
    t.start()
    assert in_write.wait(10), "the push never reached the live write"

    def undo():
        with env["app"].test_client() as c3:
            out["undo"] = c3.post("/undo").status_code

    u = threading.Thread(target=undo)
    u.start()
    u.join(0.5)
    release.set()
    t.join(15)
    u.join(15)
    assert out.get("push") == 200
    assert out.get("undo") == 200
    off, f01 = _live(env)
    # the undo removed action 2 (f_01) and left action 1 in effect
    assert f01 == 5.0e9, f"newest action not undone (f_01={f01})"
    assert off == 0.1, f"an OLDER action was undone instead (joint_offset={off})"
