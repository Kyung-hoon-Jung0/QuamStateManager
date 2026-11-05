"""W2-2: a per-row take of a live value is not an unapplied edit -- the field
then agrees with the live chip, and nothing says SM's old value is "live".

W2-3 / W3-1 / F-3, the decided contracts for an applied value an outside save
took away (one place, below):
  - a session that armed pull restores it without asking (docs/187) -- a
    replace-pull too, which discards only the UNAPPLIED edits (F-3);
  - with no session armed the loss is flagged, never written back;
  - the loss note (a restore the chip refused) ends only once BOTH the working
    copy and the live chip hold the applied value again, or on Dismiss.
"""
from __future__ import annotations

import re

from tests.test_sync_review_applied import _outside, _st, env  # noqa: F401


def test_a_taken_row_is_not_an_unapplied_edit(env):  # noqa: F811
    c = env["client"]
    _outside(env, _st(t2=1.5e-5, f01=5.2e9))
    r = c.post("/field/edit-batch", json={"updates": [
        {"dot_path": "qubits.q1.T2ramsey", "value": "1.5e-05", "create": False}], "source": "live"})
    assert r.status_code == 200 and r.get_json()["ok"], r.get_data(as_text=True)[:300]
    d = c.get("/state/drift").get_json()["sync"]
    assert d["unapplied"] == 0, d
    html = c.get("/state/review").get_data(as_text=True)
    m = re.search(r'<tr[^>]*data-path="qubits.q1.T2ramsey".*?</tr>', html, re.S)
    assert not m or "1e-05" not in m.group(0), m.group(0)


def test_an_armed_pull_restores_the_applied_value_without_asking(env):  # noqa: F811
    """Contract 1. The pull writes SM's applied value back over the outside
    save's reverted one, keeps the save's own values, and records no loss."""
    import json
    from tests.test_autosync_lost_applied import _outside_save_and_pull
    from tests.test_autosync_merge import _arm
    from tests.test_sync_review_applied import _apply_t1, _live
    _arm(env, pull=True, push=False)
    _apply_t1(env)
    r = _outside_save_and_pull(env, _st(f01=5.2e9))          # T1 back to 2e-5
    assert r.status_code == 200
    trig = json.loads(r.headers["HX-Trigger"])
    assert trig["autoSyncRestored"]["count"] == 1 and trig["autoSyncPulled"]["lost"] == []
    assert _live(env)["T1"] == 3.3e-05 and _live(env)["f_01"] == 5.2e9
    assert "data-sync-lost" not in env["client"].get("/state/tray").get_data(as_text=True)


def test_with_no_session_the_loss_is_flagged_and_nothing_is_written(env):  # noqa: F811
    """Contract 2. No Auto-Sync: no pull, no write. The control flags the
    live change and the review names the applied value the save took."""
    from tests.test_sync_review_applied import _apply_t1, _live
    _apply_t1(env)
    _outside(env, _st(f01=5.2e9))                             # T1 back to 2e-5
    assert _live(env)["T1"] == 2.0e-05, "nothing may write the chip without a session"
    c = env["client"]
    assert c.get("/state/drift").get_json()["sync"]["state"] == "live"
    html = c.get("/state/review").get_data(as_text=True)
    row = re.search(r'<tr[^>]*data-path="qubits.q1.T1".*?</tr>', html, re.S)
    assert row and "you applied" in row.group(0), html[:2000]
    assert _live(env)["T1"] == 2.0e-05


def _replace_pull_over_an_edit(env):
    """An applied T1, an unapplied T2 edit, then an outside save that reverts
    T1 and moves f_01, pulled by a session that armed pull + replace."""
    from tests.test_autosync_lost_applied import _outside_save_and_pull
    from tests.test_autosync_merge import _arm
    from tests.test_sync_review_applied import _apply_t1
    _arm(env, pull=True, push=False, replace=True)
    _apply_t1(env)
    c = env["client"]
    assert c.post("/field/edit", data={"dot_path": "qubits.q1.T2ramsey",
                                       "value": "2.2e-05"}).status_code == 200
    return _outside_save_and_pull(env, _st(f01=5.2e9))          # T1 back to 2e-5


def test_a_replace_pull_discards_the_edit_and_restores_the_applied_value(env):  # noqa: F811
    """F-3 (user decision 2026-11-05): "replace" governs only the UNAPPLIED
    edits. They are discarded as before (backup, toast); the applied value
    the outside save reverted is restored exactly as in the clean case --
    no prompt, one notice, nothing named as lost, the session still armed and
    its merge budget refilled. Before this the pull adopted the chip and only
    NAMED the applied value it took away."""
    import json
    from tests.test_sync_review_applied import _live
    r = _replace_pull_over_an_edit(env)
    assert r.status_code == 200
    trig = json.loads(r.headers["HX-Trigger"])
    pulled = trig["autoSyncPulled"]
    assert pulled["replaced"] is True and pulled["count"] == 1, pulled   # the T2 edit, discarded
    assert pulled["lost"] == [], pulled
    assert trig["autoSyncRestored"]["count"] == 1, trig
    live = _live(env)
    assert live["T1"] == 3.3e-05, "the applied value was not restored"
    assert live["f_01"] == 5.2e9, "the outside save's own value was not kept"
    assert live["T2ramsey"] == 1e-05, "the discarded edit reached the chip"
    c = env["client"]
    assert "data-sync-lost" not in c.get("/state/tray").get_data(as_text=True)
    app = env["app"]
    ctx = app.config["contexts"][app.config["active_context"]]
    assert not ctx["store"].change_log and not ctx.get("working_dirty")
    sess = ctx.get("auto_apply")
    assert sess and sess.get("pull") and sess.get("merge_tries", 0) == 0, sess


def test_a_restore_the_chip_refuses_leaves_the_loss_named(env, monkeypatch):  # noqa: F811
    """The chip moved again between the pull and the restore's write: the
    restore is refused, and the applied value is NAMED in the status control
    (W2-3) rather than dropped in silence."""
    import json
    import quam_state_manager.web.routes as routes_mod
    from flask import jsonify
    monkeypatch.setattr(routes_mod, "state_sync",
                        lambda **k: (jsonify({"status": "conflict", "tray_html": ""}), 409))
    r = _replace_pull_over_an_edit(env)
    assert r.status_code == 200
    trig = json.loads(r.headers["HX-Trigger"])
    assert "autoSyncRestored" not in trig
    assert [x["path"] for x in trig["autoSyncPulled"]["lost"]] == ["qubits.q1.T1"]
    tray = env["app"].test_client().get("/state/tray").get_data(as_text=True)
    assert 'data-sync-lost="1"' in tray and "qubits.q1.T1" in tray


def test_a_saved_but_never_applied_value_is_not_restored(env):  # noqa: F811
    """A /save's journal unit no press has landed (W3-2's shape) is not an
    applied value: the replace-pull neither writes it to the chip nor names
    it as lost."""
    import json
    from tests.test_autosync_lost_applied import _outside_save_and_pull
    from tests.test_autosync_merge import _arm
    from tests.test_sync_review_applied import _live
    _arm(env, pull=True, push=False, replace=True)
    c = env["client"]
    assert c.post("/field/edit", data={"dot_path": "qubits.q1.T1", "value": "9.9e-05"}).status_code == 200
    assert c.post("/save").status_code in (200, 204)                  # saved, never applied
    assert c.post("/field/edit", data={"dot_path": "qubits.q1.T2ramsey",
                                       "value": "2.2e-05"}).status_code == 200
    r = _outside_save_and_pull(env, _st(f01=5.2e9))
    assert r.status_code == 200
    trig = json.loads(r.headers["HX-Trigger"])
    assert "autoSyncRestored" not in trig, trig
    assert trig["autoSyncPulled"]["lost"] == [], trig
    assert _live(env)["T1"] == 2.0e-05, "a value nobody applied reached the chip"
    assert "data-sync-lost" not in c.get("/state/tray").get_data(as_text=True)


def test_a_lost_applied_value_stays_in_the_control_until_dismissed(env):  # noqa: F811
    """W2-3: the note (now only for a restore the chip refused) survives later
    renders and other windows, until the person dismisses it."""
    _seed_lost(env)
    c = env["client"]
    for _ in range(2):          # survives later renders, other windows too
        tray = env["app"].test_client().get("/state/tray").get_data(as_text=True)
        assert 'data-sync-lost="1"' in tray and "qubits.q1.T1" in tray
    assert c.post("/sync/lost-ack").status_code == 200
    assert "data-sync-lost" not in c.get("/state/tray").get_data(as_text=True)


def test_a_take_stays_undoable_and_keeps_the_log(env):  # noqa: F811
    """Review of W2-2: the row stays in the change log -- Ctrl+Z undoes a
    take like any other commit, and a grouped gesture is never split. The
    first cut deleted the rows, which made Ctrl+Z after a take do nothing."""
    c = env["client"]
    _outside(env, _st(t2=1.5e-5, f01=5.2e9))
    r = c.post("/field/edit-batch", json={"updates": [
        {"dot_path": "qubits.q1.T2ramsey", "value": "1.5e-05", "create": False}], "source": "live"})
    assert r.status_code == 200 and r.get_json()["ok"], r.get_data(as_text=True)[:300]
    app = env["app"]
    ctx = app.config["contexts"][app.config["active_context"]]
    assert [e.dot_path for e in ctx["store"].change_log] == ["qubits.q1.T2ramsey"]
    assert ctx["store"].merged["qubits"]["q1"]["T2ramsey"] == 1.5e-05
    assert c.get("/state/drift").get_json()["sync"]["unapplied"] == 0
    r = c.post("/undo")
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    assert ctx["store"].merged["qubits"]["q1"]["T2ramsey"] == 1e-05, "Ctrl+Z after a take did nothing"
    assert not ctx["store"].change_log


def test_a_later_edit_of_a_taken_field_counts_again(env):  # noqa: F811
    c = env["client"]
    _outside(env, _st(t2=1.5e-5, f01=5.2e9))
    assert c.post("/field/edit-batch", json={"updates": [
        {"dot_path": "qubits.q1.T2ramsey", "value": "1.5e-05", "create": False}],
        "source": "live"}).status_code == 200
    assert c.get("/state/drift").get_json()["sync"]["unapplied"] == 0
    assert c.post("/field/edit", data={"dot_path": "qubits.q1.T2ramsey",
                                       "value": "1.7e-05"}).status_code == 200
    assert c.get("/state/drift").get_json()["sync"]["unapplied"] == 1


# ----------------------------------------------------------------------
# W3-1: the note's own lifetime (contract 3)
# ----------------------------------------------------------------------

def _seed_lost(env, applied=3.3e-05):
    """A lost applied value in the control, as the pull records it. Since the
    applied-chain restore (base 010b50b8, and F-3 for the replace-pull) an
    armed pull puts a reverted value back itself, so the note now appears only
    when the chip refuses that restore; the note's own lifetime is what these
    pins are about, so they seed the record directly."""
    import time
    app = env["app"]
    ctx = app.config["contexts"][app.config["active_context"]]
    ctx["_lost_unacked"] = [{"path": "qubits.q1.T1", "applied": applied,
                             "now": 2.0e-05, "removed": False, "at": time.time()}]
    assert 'data-sync-lost="1"' in env["client"].get("/state/tray").get_data(as_text=True)


def test_the_lost_note_ends_when_the_value_is_applied_again(env):  # noqa: F811
    """W3-1: the note outlived the situation -- the user re-applied the
    field and it still said the value was lost, until Dismiss."""
    from tests.test_sync_review_applied import _apply_t1
    _seed_lost(env)
    _apply_t1(env)
    tray = env["app"].test_client().get("/state/tray").get_data(as_text=True)
    assert "data-sync-lost" not in tray


def test_the_lost_note_ends_when_live_holds_the_value_again(env):  # noqa: F811
    from tests.test_autosync_lost_applied import _outside_save_and_pull
    from tests.test_autosync_merge import _arm
    _arm(env, pull=True, push=False)
    _seed_lost(env)
    assert _outside_save_and_pull(env, _st(t1=3.3e-05, f01=5.3e9)).status_code == 200
    assert "data-sync-lost" not in env["client"].get("/state/tray").get_data(as_text=True)


def test_a_pending_edit_back_to_the_value_is_not_the_end(env):  # noqa: F811
    """Typed back but not applied: the chip still lacks it."""
    _seed_lost(env)
    c = env["client"]
    assert c.post("/field/edit", data={"dot_path": "qubits.q1.T1", "value": "3.3e-05"}).status_code == 200
    assert 'data-sync-lost="1"' in c.get("/state/tray").get_data(as_text=True)


def test_the_note_stays_while_live_still_lacks_it(env):  # noqa: F811
    _seed_lost(env)
    assert 'data-sync-lost="1"' in env["app"].test_client().get("/state/tray").get_data(as_text=True)


def test_the_note_stays_when_live_changed_again(env):  # noqa: F811
    """W3-1 review: a clean working copy holding the applied value is not
    the end when the live chip changed again since."""
    from tests.test_sync_review_applied import _outside
    _outside(env, _st(t1=4.4e-05))
    _seed_lost(env, applied=2.0e-05)          # the working copy holds this
    assert 'data-sync-lost="1"' in env["app"].test_client().get("/state/tray").get_data(as_text=True)
