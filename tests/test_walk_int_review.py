"""walk-int review of the Auto-Sync failure-kind policy (docs/187, 2026-11-05).

Four places where the policy judged something it must not, each found by
reading the wrapper against the decisions that bind it:

* a person's OWN press that the door answers to the presser (a needs_confirm
  the client turns into a confirm(), a collision for the sync panel) turned
  the automatic session off underneath them;
* a typed, uncommitted cell colliding with a node write under an armed
  pull+push session disarmed the session -- a collision under pull+push never
  disarms; the commit itself makes the user's value win;
* a stash (a /save, a merge part-way through) under an armed pull+push session
  was refused as a "collision" -- the stash IS the user's edits, and the door
  judges each against the original recorded when it was stashed;
* a second live SM window on the same chip disarmed every automatic request
  (F-5's two-process case), where before the policy it gated only
  auto-arm-on-open.
"""
from __future__ import annotations

import json
import time as _time

import quam_state_manager.web.routes as routes_mod
from quam_state_manager.core import undo_journal
from tests.test_autosync_merge import (  # noqa: F401
    _arm, _ctx, _edit, _live_f01, _sess, _state, _write_chip, env,
)

F01 = "qubits.qA1.f_01"


def _wc_f01(env) -> float:
    return _ctx(env)["store"].state["qubits"]["qA1"]["f_01"]


def _trig(r) -> dict:
    return json.loads(r.headers.get("HX-Trigger") or "{}")


def _node_writes(env, f01):
    _write_chip(env["live"], _state(f01=f01))
    _ctx(env)["live_diverged"] = True


# ─── a person's own press is answered to the person, not judged ──────────────

def test_a_human_take_live_confirm_does_not_turn_auto_sync_off(env):  # noqa: F811
    """`mode=discard` over saved content answers needs_confirm (the client
    asks); the armed session must still be there when the person says yes."""
    _arm(env, pull=True, push=True)
    ctx = _ctx(env)
    ctx["working_dirty"] = True              # a wholesale load / a saved state
    routes_mod._clear_reapply(ctx)           # ...with no stash to replay
    r = env["client"].post("/state/sync", data={"mode": "discard"})
    assert r.status_code == 200 and r.get_json()["status"] == "needs_confirm"
    assert _sess(env) is not None, "the person's confirm turned Auto-Sync off"
    assert "autoApplyDisarm" not in _trig(r)
    assert not ctx.get("auto_merge_blocker")


def test_a_human_collision_press_keeps_the_session(env):  # noqa: F811
    """A person's Pull & apply that finds a same-field collision is answered
    with the sync panel; the session is not the one that failed."""
    _arm(env, pull=True, push=True)
    _edit(env)                                # f_01 -> 5.1e9, pending
    _node_writes(env, 7.7e9)                  # the same field
    r = env["client"].post("/state/sync", data={"mode": "apply"})
    assert r.get_json()["status"] == "collision", r.get_json()
    assert _live_f01(env) == 7.7e9, "nothing was applied"
    assert _sess(env) is not None
    assert "autoApplyDisarm" not in _trig(r)


# ─── a typed cell under pull+push: wait for the commit, then mine wins ───────

def test_a_typed_cell_colliding_under_pull_push_neither_disarms_nor_pulls(env):  # noqa: F811
    _arm(env, pull=True, push=True)
    _node_writes(env, 7.7e9)
    r = env["client"].post("/auto-sync/pull",
                           data={"auto_sync": "1", "dom_dirty": "1", "dom_path": F01})
    assert r.status_code == 204, r.get_data(as_text=True)
    assert "liveConflict" in _trig(r) and "autoApplyDisarm" not in _trig(r)
    assert "autoSyncRetry" not in _trig(r), "a typed cell is not a failure to retry on a timer"
    assert _sess(env) is not None
    assert _wc_f01(env) == 5.0e9, "the node's value was pulled under the typed cell"
    assert _ctx(env)["live_conflicts"] == [F01]
    with env["app"].app_context():
        assert not routes_mod._auto_pull_due(_ctx(env)), "the 5 s poll must stay quiet"
    # the commit: the flush meets the moved chip, the merge makes mine win
    _edit(env)                                # Enter: f_01 -> 5.1e9
    r = env["client"].post("/state/apply-to-live", data={"auto_flush": "1"})
    assert "autoSyncMerge" in _trig(r), _trig(r)
    _sess(env)["merge_due"] = 0
    r = env["client"].post("/state/sync", data={"mode": "apply", "auto_sync": "1"})
    assert r.get_json()["status"] == "ok", r.get_json()
    assert _live_f01(env) == 5.1e9 and _sess(env) is not None
    assert "live_conflicts" not in _ctx(env)
    assert _ctx(env)["take_live_backup"]["updates"][F01][1] == 7.7e9


# ─── a stash under pull+push is the user's own edits: mine wins ──────────────

def test_a_saved_edit_colliding_under_pull_push_wins_with_the_node_value_saved(env):  # noqa: F811
    c = env["client"]
    _arm(env, pull=True, push=True)
    _edit(env)                                # f_01 -> 5.1e9
    assert c.post("/save").status_code in (200, 204)
    ctx = _ctx(env)
    assert ctx.get("working_dirty") and ctx.get("pending_reapply"), "a /save stashes"
    _node_writes(env, 7.7e9)                  # the same field
    r = c.post("/auto-sync/pull", data={"auto_sync": "1"})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert "autoApplyDisarm" not in _trig(r) and "liveConflict" not in _trig(r), _trig(r)
    assert _sess(env) is not None
    assert _live_f01(env) == 5.1e9, "the saved value did not win"
    assert "kept your value for qA1.f_01" in _trig(r)["syncToast"]["message"]
    assert _ctx(env)["take_live_backup"]["updates"][F01][1] == 7.7e9
    assert not _ctx(env).get("pending_reapply"), "the stash was consumed"


# ─── two live windows on one chip are not a blocker on the doors ─────────────

def test_a_second_live_window_does_not_disarm_the_automatic_pull(env, monkeypatch):  # noqa: F811
    monkeypatch.setattr(routes_mod, "_auto_sync_foreign_window", lambda ctx: True)
    _arm(env, pull=True, push=False)
    _node_writes(env, 7.7e9)
    r = env["client"].post("/auto-sync/pull", data={"auto_sync": "1"})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert "autoApplyDisarm" not in _trig(r), _trig(r)
    assert _sess(env) is not None
    assert _wc_f01(env) == 7.7e9, "the clean pull did not happen"


# ─── the undone tail is read with the units, from one document ───────────────

def _u(i, path="a.b", undone=None):
    meta = {} if undone is None else {"undone_ts": undone}
    return {"id": f"u{i}", "ts": _time.time(), "meta": meta, "entries": [
        {"path": path, "old": i, "new": i + 1, "source_file": "state",
         "created": False, "deleted": False}]}


def test_the_sidecar_is_read_once_for_units_cursor_and_tail(tmp_path):
    p = tmp_path / "j.undo_journal.json"
    undo_journal.append_units(p, [_u(1), _u(2, "x.y")])
    undo_journal.save_cursor(p, 1)                 # u2 undone live
    undo_journal.append_units(p, [_u(3, "z")])     # truncates u2 -> tail
    units, cur, tail = undo_journal.load_state_and_tail(p)
    assert (units, cur) == undo_journal.load_state(p)
    assert tail == undo_journal.load_tail(p)
    assert [e["path"] for r in tail for e in r["entries"]] == ["x.y"]
    assert undo_journal.load_state_and_tail(tmp_path / "absent.json") == ([], 0, [])
