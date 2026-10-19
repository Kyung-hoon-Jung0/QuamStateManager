"""R3 MINOR 10c: redo that exceeds the existing walk cap says why."""
from __future__ import annotations

import json
import os
import time

import pytest

from quam_state_manager.core import json_diff
from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app


@pytest.mark.parametrize("field", ["T1", "f_01"])
def test_redo_after_a_large_chip_pull_says_that_this_change_is_unavailable(tmp_path, field):
    live = tmp_path / "chip"
    live.mkdir()
    state = {"qubits": {"q1": {"id": "q1", "T1": 1e-5, "f_01": 5e9}},
             "qubit_pairs": {}, "active_qubit_names": ["q1"],
             "extras": {"samples": [0] * (json_diff.WALK_CAP + 1)}}
    (live / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (live / "wiring.json").write_text('{"network": {"host": "127.0.0.1"}}', encoding="utf-8")
    routes._quam_cache.clear()
    app = create_app(testing=True, instance_path=str(tmp_path / "inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    c.post("/auto-sync/set", data={"pull": "1"})
    assert c.post("/field/edit", data={"dot_path": "qubits.q1.T1", "value": "3e-05"}).get_json()["ok"]
    c.post("/undo")
    ctx = app.config["contexts"][app.config["active_context"]]
    assert ctx.get("redo_stack"), "fixture: a redo exists before the pull"
    state["qubits"]["q1"][field] = 2e-5 if field == "T1" else 5.1e9
    (live / "state.json").write_text(json.dumps(state), encoding="utf-8")
    stamp = time.time() + 2
    os.utime(live / "state.json", (stamp, stamp))
    ctx["live_diverged"] = True
    assert c.post("/auto-sync/pull").status_code == 200
    for _ in range(2):
        r = c.post("/redo")
        events = json.loads(r.headers.get("HX-Trigger", "{}"))
        message = events.get("cellsReverted", {}).get("message", "")
        assert "Redo is not available for this change on a chip this large" in message, message
        assert "200,000" in message and "\n" not in message
    assert ctx["store"].merged["qubits"]["q1"][field] == state["qubits"]["q1"][field]
    assert json_diff.WALK_CAP == 200_000
