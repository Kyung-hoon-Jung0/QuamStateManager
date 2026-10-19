"""R3 MINOR 10a: Keep mine confirms exactly the live values it replaces."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app


@pytest.fixture
def env(tmp_path):
    live = tmp_path / "chip"
    live.mkdir()
    state = {"qubits": {"q1": {"id": "q1", "T1": 1e-5, "f_01": 5e9}},
             "qubit_pairs": {}, "active_qubit_names": ["q1"]}
    wiring = {"network": {"host": "127.0.0.1"}}
    (live / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (live / "wiring.json").write_text(json.dumps(wiring), encoding="utf-8")
    routes._quam_cache.clear()
    app = create_app(testing=True, instance_path=str(tmp_path / "inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    assert c.post("/field/edit", data={"dot_path": "qubits.q1.T1", "value": "3e-05"}).get_json()["ok"]
    state["qubits"]["q1"]["T1"] = 2e-5
    (live / "state.json").write_text(json.dumps(state), encoding="utf-8")
    return app, c, live


def _push(c, preflight):
    key = "expect_replaced_hash" if preflight.get("replaced_hash") else "expect_live_hash"
    value = preflight.get("replaced_hash") or preflight["live_hash"]
    return c.post("/state/apply-to-live", data={"force": "1", key: value},
                  headers={"Accept": "application/json"})


def test_an_unrelated_live_write_carried_by_the_payload_does_not_refuse(env):
    app, c, live = env
    preflight = c.get("/state/overwrite-live/preflight").get_json()
    path = live / "state.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    state["qubits"]["q1"]["f_01"] = 5.1e9
    path.write_text(json.dumps(state), encoding="utf-8")
    # Both copies carry this unrelated value; the push would preserve it.
    assert c.post("/field/edit", data={"dot_path": "qubits.q1.f_01", "value": "5.1e9"}).get_json()["ok"]
    r = _push(c, preflight)
    assert r.status_code == 200, r.get_data(as_text=True)[:500]
    written = json.loads(path.read_text(encoding="utf-8"))["qubits"]["q1"]
    assert written["T1"] == 3e-5 and written["f_01"] == 5.1e9


@pytest.mark.parametrize("change", ["named", "unrelated", "new", "wiring"])
def test_every_changed_value_the_payload_would_erase_requires_a_new_confirm(env, change):
    app, c, live = env
    preflight = c.get("/state/overwrite-live/preflight").get_json()
    path = live / ("wiring.json" if change == "wiring" else "state.json")
    state = json.loads(path.read_text(encoding="utf-8"))
    if change == "named":
        state["qubits"]["q1"]["T1"] = 2.5e-5
    elif change == "unrelated":
        state["qubits"]["q1"]["f_01"] = 5.2e9
    elif change == "new":
        state["extras"] = {"lab_value": 17}
    else:
        state["network"]["host"] = "127.0.0.2"
    raw = json.dumps(state)
    path.write_text(raw, encoding="utf-8")
    ctx = app.config["contexts"][app.config["active_context"]]
    before = list(ctx["store"].change_log)
    r = _push(c, preflight)
    assert r.status_code == 409 and r.get_json()["conflict"] == "live_moved", r.data[:500]
    assert path.read_text(encoding="utf-8") == raw
    assert ctx["store"].change_log == before, "a refusal must keep the unsaved rows"


def test_the_actual_keep_mine_push_declares_the_replaced_values():
    import shutil
    import subprocess
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    script = Path(__file__).with_name("v151_r3_overwrite_values_selfcheck.cjs")
    result = subprocess.run([node, str(script)], capture_output=True, text=True, timeout=60)
    if result.returncode == 2 and "jsdom not installed" in result.stderr:
        pytest.skip("jsdom not installed")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "DONE (3 assertions)" in result.stdout, result.stdout


def test_a_write_after_the_route_check_is_refused_at_the_final_gate(env, monkeypatch):
    from quam_state_manager.core import working_copy
    app, c, live = env
    preflight = c.get("/state/overwrite-live/preflight").get_json()
    real = working_copy.apply_to_live
    path = live / "state.json"
    def late(wc, **kwargs):
        state = json.loads(path.read_text(encoding="utf-8"))
        state["qubits"]["q1"]["T1"] = 2.7e-5
        path.write_text(json.dumps(state), encoding="utf-8")
        return real(wc, **kwargs)
    monkeypatch.setattr(working_copy, "apply_to_live", late)
    r = _push(c, preflight)
    assert r.status_code == 409 and r.get_json()["conflict"] == "live_moved", r.data[:500]
    assert json.loads(path.read_text(encoding="utf-8"))["qubits"]["q1"]["T1"] == 2.7e-5


def test_the_replaced_set_is_exact_and_ignores_what_the_payload_carries():
    from quam_state_manager.core import working_copy as wcm
    live = {"a": {"x": 1, "flag": True, "l": [1, 2]}, "b": 2.5}
    mine = {"a": {"x": 2, "flag": True, "l": [1, 2]}, "b": 2.5}
    h0 = wcm.replaced_values_hash(live, {}, payload=(mine, {}))
    assert list(wcm.replaced_values(live, {}, payload=(mine, {}))) == ['["state.json", "a", "x"]']
    # a live change the payload carries is not a replaced value
    live2 = {"a": {"x": 1, "flag": True, "l": [1, 2]}, "b": 3.5}
    assert wcm.replaced_values_hash(live2, {}, payload=(dict(mine, b=3.5), {})) == h0
    # a type-only live change (true -> 1) is one, and so is a list item or a wiring value
    live3 = {"a": {"x": 1, "flag": 1, "l": [1, 2]}, "b": 2.5}
    assert wcm.replaced_values_hash(live3, {}, payload=(mine, {})) != h0
    live4 = {"a": {"x": 1, "flag": True, "l": [1, 3]}, "b": 2.5}
    assert wcm.replaced_values_hash(live4, {}, payload=(mine, {})) != h0
    assert wcm.replaced_values_hash(live, {"w": 1}, payload=(mine, {})) != h0
    # the same value under another key or file is another path
    assert (wcm.replaced_values_hash({}, {"k": 1}, payload=({}, {}))
            != wcm.replaced_values_hash({"k": 1}, {}, payload=({}, {})))
