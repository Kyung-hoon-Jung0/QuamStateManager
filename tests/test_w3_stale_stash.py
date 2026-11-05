"""W3-2: after an edit, Save, Ctrl+Z and Save the working copy equals live
and nothing is pending, yet the top bar said "1 staged, not applied": the
re-apply stash kept the undone field. Take live did not clear it either."""
from __future__ import annotations

from tests.test_sync_review_applied import _live, env  # noqa: F401


def _sync(env):
    return env["client"].get("/state/drift").get_json()["sync"]


def _edit_save_undo_save(env):
    c = env["client"]
    assert c.post("/field/edit", data={"dot_path": "qubits.q1.T1", "value": "3.3e-05"}).status_code == 200
    assert c.post("/save").status_code in (200, 204)
    r = c.post("/undo")
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    assert c.post("/save").status_code in (200, 204)
    assert _live(env)["T1"] == 2.0e-05


def test_an_undone_saved_edit_is_not_staged(env):  # noqa: F811
    _edit_save_undo_save(env)
    s = _sync(env)
    assert s["unapplied"] == 0 and s["state"] == "synced", s


def test_take_live_clears_a_stale_stash(env):  # noqa: F811
    app = env["app"]
    ctx = app.config["contexts"][app.config["active_context"]]
    from quam_state_manager.web import routes
    ctx["pending_reapply"] = {"qubits.q1.T1": routes._tagged("set", 3.3e-05, "g1", None)}
    assert _sync(env)["unapplied"] == 1
    r = env["client"].post("/state/sync", data={"mode": "discard", "force": "1"})
    assert r.status_code in (200, 204), r.get_data(as_text=True)[:300]
    s = _sync(env)
    assert s["unapplied"] == 0, s



def test_a_saved_real_change_stays_staged(env):  # noqa: F811
    c = env["client"]
    assert c.post("/field/edit", data={"dot_path": "qubits.q1.T1", "value": "3.3e-05"}).status_code == 200
    assert c.post("/save").status_code in (200, 204)
    assert _sync(env)["state"] != "synced"
