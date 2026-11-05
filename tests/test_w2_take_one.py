"""W2-2: a per-row take of a live value is not an unapplied edit -- the field
then agrees with the live chip, and nothing says SM's old value is "live"."""
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


def test_a_lost_applied_value_stays_in_the_control_until_dismissed(env):  # noqa: F811
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



def _seed_lost(env, applied=3.3e-05):
    """A lost applied value in the control, as the pull records it. Since the
    applied-chain restore (base 010b50b8) an armed pull puts a reverted value
    back itself, so the note now appears only where that restore cannot run;
    the note's own lifetime is what these pins are about."""
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
