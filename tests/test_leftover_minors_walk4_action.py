from flask import render_template
import quam_state_manager.web.routes as routes
from tests.test_autosync_merge import env, _ctx


def test_lost_applied_topbar_offers_merge(env):
    ctx = _ctx(env)
    ctx["live_diverged"] = True
    ctx["pull_lost"] = [{"path": "qubits.qA1.T1", "applied": 3e-5, "now": 2e-5}]
    # Exercise the cached loss used by every window, without live I/O on render.
    from unittest.mock import patch
    with env["app"].test_request_context(), patch.object(routes, "_lost_still_lost", return_value=ctx["pull_lost"]):
        verdict = routes._sync_view(ctx)
        assert verdict["action"] == "merge"
        html = render_template("_sync_control.html", sync=verdict, change_count=0, working_dirty=False)
        assert "Pull &amp; apply" in html
        assert "doStateSync('apply', false, false, null, {keepApplied: true})" in html
        assert "doStateSync('discard')" not in html


def test_auto_off_loss_before_any_pull_offers_and_keeps_applied(env):
    from tests.test_autosync_lost_applied import _apply_name
    from tests.test_autosync_merge import _state, _write_chip
    _apply_name(env)
    _write_chip(env["live"], _state(f01=5.2e9))
    env["client"].get("/state/drift")
    with env["app"].test_request_context():
        verdict = routes._sync_view(_ctx(env))
        assert verdict["action"] == "merge"
    response = env["client"].post("/state/sync", data={"mode": "apply", "keep_applied": "1"})
    assert response.get_json()["status"] == "ok"
    import json
    live = json.loads((env["live"] / "state.json").read_text(encoding="utf-8"))
    assert live["qubits"]["qA1"]["T1"] == 3.3e-5
    assert live["qubits"]["qA1"]["f_01"] == 5.2e9
