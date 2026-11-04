"""W2-1: every window learns the Auto-Sync session from the drift poll (the
session moves the sync signature), and an edit pressed in a window whose tray
still showed Auto-Sync off is staged and HELD -- no flush writes it until a
person presses Apply."""
from __future__ import annotations

from tests.test_sync_review_applied import _live, env  # noqa: F401
from tests.test_autosync_merge import _arm

_OFF = {"X-SM-Auto-Seen": "0"}


def _sig(c):
    return c.get("/state/drift").get_json()["sync"]["sig"]


def test_arming_moves_the_signature_every_window_follows(env):  # noqa: F811
    other = env["app"].test_client()
    s0 = _sig(other)
    _arm(env, pull=True, push=True)
    s1 = _sig(other)
    assert s1 != s0
    env["client"].post("/auto-sync/set", data={"pull": "0", "pull_replace": "0", "push": "0"})
    assert _sig(other) != s1


def test_an_edit_from_a_window_that_showed_off_is_held(env):  # noqa: F811
    c = env["client"]
    _arm(env, pull=True, push=True)
    r = c.post("/field/edit", data={"dot_path": "qubits.q1.T1", "value": "3.3e-05"}, headers=_OFF)
    assert r.status_code == 200
    tray = c.get("/state/tray").get_data(as_text=True)
    assert 'data-auto-hold="1"' in tray and "Auto-Sync holding" in tray
    r = c.post("/state/apply-to-live", data={"auto_flush": "1"})
    assert r.status_code == 409
    assert _live(env)["T1"] == 2.0e-5, "a held edit reached the live chip"
    assert c.post("/state/apply-to-live").status_code == 200    # a person's Apply
    assert _live(env)["T1"] == 3.3e-05
    assert 'data-auto-hold' not in c.get("/state/tray").get_data(as_text=True)


def test_an_edit_from_a_window_that_showed_on_flushes(env):  # noqa: F811
    c = env["client"]
    _arm(env, pull=True, push=True)
    r = c.post("/field/edit", data={"dot_path": "qubits.q1.T1", "value": "3.3e-05"},
               headers={"X-SM-Auto-Seen": "1"})
    assert r.status_code == 200
    assert 'data-auto-hold' not in c.get("/state/tray").get_data(as_text=True)
    assert c.post("/state/apply-to-live", data={"auto_flush": "1"}).status_code == 200
    assert _live(env)["T1"] == 3.3e-05


def test_the_client_declares_what_it_saw():
    from pathlib import Path
    import quam_state_manager.web.routes as r
    st = Path(r.__file__).parent / "static"
    tc = (st / "tab-chip.js").read_text(encoding="utf-8")
    assert "X-SM-Auto-Seen" in tc and "data-auto-apply" in tc
    aa = (st / "auto-apply.js").read_text(encoding="utf-8")
    assert "hasAttribute('data-auto-hold')) return false" in aa


def test_a_no_op_request_never_releases_a_hold_another_request_set(env):  # noqa: F811
    """Review of W2-1: two off-window requests overlap -- A (a no-op that
    ends 4xx) began first, B staged meanwhile. A's end must not release the
    hold B relies on: the hold is anchored on the mutation sequence it was
    set at, and released only when nothing was staged since."""
    from flask import g
    import quam_state_manager.web.routes as R
    c = env["client"]
    _arm(env, pull=True, push=True)
    app = env["app"]
    ctx = app.config["contexts"][app.config["active_context"]]
    with app.test_request_context("/field/edit", method="POST", headers=_OFF):
        R._auto_seen_begin()                       # A begins: sets the hold
        rec_a = g._auto_seen_off
    assert ctx["auto_apply"].get("hold") is True
    assert c.post("/field/edit", data={"dot_path": "qubits.q1.T1", "value": "3.3e-05"},
                  headers=_OFF).status_code == 200          # B stages meanwhile
    with app.test_request_context("/field/edit", method="POST", headers=_OFF):
        g._auto_seen_off = rec_a
        R._auto_seen_end(app.response_class(status=400))    # A ends, staged nothing
    assert ctx["auto_apply"].get("hold") is True, "A's end released B's hold"
    assert c.post("/state/apply-to-live", data={"auto_flush": "1"}).status_code == 409
    assert _live(env)["T1"] == 2.0e-5
    # and a no-op alone still leaves no hold behind
    assert c.post("/state/apply-to-live").status_code == 200
    with app.test_request_context("/field/edit", method="POST", headers=_OFF):
        R._auto_seen_begin()
        R._auto_seen_end(app.response_class(status=400))
    assert not ctx["auto_apply"].get("hold")
