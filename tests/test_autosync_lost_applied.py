"""F2 (final walk): an outside writer (a node saving its in-memory copy) took
away a value SM had just applied; the Auto-Sync pull adopted the chip in
silence and the control said In sync. The pull now names the lost values."""
from __future__ import annotations

import json

import quam_state_manager.web.routes as routes_mod
from tests.test_autosync_merge import _arm, _state, _write_chip, env  # noqa: F401


def _apply_name(env):
    c = env["client"]
    r = c.post("/field/edit", data={"dot_path": "qubits.qA1.T1", "value": "3.3e-05"})
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    r = c.post("/state/apply-to-live")
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    doc = json.loads((env["live"] / "state.json").read_text(encoding="utf-8"))
    assert doc["qubits"]["qA1"]["T1"] == 3.3e-05


def _outside_save_and_pull(env, state):
    _write_chip(env["live"], state)
    with env["app"].test_request_context():
        ctx = routes_mod._active_ctx()
        ctx["_live_hash_checked_at"] = None
        routes_mod._refresh_live_diverged(ctx)
    return env["client"].post("/auto-sync/pull", data={"dom_dirty": "0"})


def test_the_pull_names_an_applied_value_the_outside_save_removed(env):  # noqa: F811
    _arm(env, pull=True, push=False)
    _apply_name(env)
    r = _outside_save_and_pull(env, _state(f01=5.2e9))      # T1 back to 2e-5, f_01 moved
    assert r.status_code == 200
    lost = json.loads(r.headers["HX-Trigger"])["autoSyncPulled"]["lost"]
    assert [x["path"] for x in lost] == ["qubits.qA1.T1"], lost
    assert lost[0]["applied"] == 3.3e-05 and lost[0]["now"] == 2.0e-05


def test_an_outside_save_that_kept_the_value_names_nothing(env):  # noqa: F811
    _arm(env, pull=True, push=False)
    _apply_name(env)
    r = _outside_save_and_pull(env, _state(f01=5.2e9, t1=3.3e-05))
    assert r.status_code == 200
    assert json.loads(r.headers["HX-Trigger"])["autoSyncPulled"]["lost"] == []


def test_the_client_says_it():
    from pathlib import Path
    js = (Path(routes_mod.__file__).parent / "static" / "auto-apply.js").read_text(encoding="utf-8")
    i = js.index("var lost = Array.isArray(d.lost)")
    body = js[i:i + 900]
    assert "removed" in body and "toast(" in body
    assert "d.replaced" not in body, "the loss must be said whether or not edits were replaced"
