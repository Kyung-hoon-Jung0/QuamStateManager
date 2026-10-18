"""v1.5.1 U P2-24: the sync panel lists the user's own unapplied edits first.

With 31 live-changed rows, "Your unapplied edits" was the last group of the
panel, below the fold. Pinned on the real /state/review: the mine group
renders before the live chip's changes (fields that collide stay on top --
they need a pick), and the live group still renders whole.
"""
from __future__ import annotations

import json

import pytest

from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app

_WIRING = {"network": {"host": "127.0.0.1", "cluster_name": "C1"}}


def _state(t1s, f01=5.0e9):
    qs = {f"q{i}": {"id": f"q{i}", "T1": t, "f_01": f01} for i, t in enumerate(t1s)}
    return {"qubits": qs, "qubit_pairs": {}, "active_qubit_names": sorted(qs)}


@pytest.fixture
def env(tmp_path):
    live = tmp_path / "chip" / "quam_state"
    live.mkdir(parents=True)
    (live / "state.json").write_text(json.dumps(_state([1e-5] * 6)), encoding="utf-8")
    (live / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")
    routes._quam_cache.clear()
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    return {"c": c, "live": live}


def test_my_edits_come_before_the_live_chips_changes(env):
    c = env["c"]
    assert c.post("/field/edit", data={"dot_path": "qubits.q0.T1", "value": "2e-05"}).get_json()["ok"]
    # the lab writes other fields of every qubit
    lab = _state([1e-5] * 6, f01=5.1e9)
    (env["live"] / "state.json").write_text(json.dumps(lab), encoding="utf-8")
    html = c.get("/state/review").get_data(as_text=True)
    mine, live = html.find("sp-group-mine"), html.find("sp-group-live")
    assert mine > 0 and live > 0, (mine, live)
    assert mine < live, "your unapplied edits are listed before the live chip's changes"
    assert html.count('class="sp-row sp-row-live') >= 6
