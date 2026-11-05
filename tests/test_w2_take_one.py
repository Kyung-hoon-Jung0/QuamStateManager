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
    """W2-3. A session that armed pull restores a reverted applied value
    (docs/187), so the note is for the pull that does not: a replace-pull
    over unapplied edits adopts the chip, as the person asked, and names the
    applied value that took away -- until the person dismisses it."""
    from tests.test_autosync_lost_applied import _outside_save_and_pull
    from tests.test_autosync_merge import _arm
    from tests.test_sync_review_applied import _apply_t1
    _arm(env, pull=True, push=False, replace=True)
    _apply_t1(env)
    c = env["client"]
    assert c.post("/field/edit", data={"dot_path": "qubits.q1.T2ramsey",
                                       "value": "2.2e-05"}).status_code == 200
    assert _outside_save_and_pull(env, _st(f01=5.2e9)).status_code == 200
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
