"""v1.5.1 SB-F1: an Auto-Sync pull racing an edit commit.

The pull (``/auto-sync/pull``, and the machine reconcile the Runner's
post-node hook runs) reads the live chip, writes it into the working copy and
advances the sync point to it. An edit landing in that window is kept: the
old content plus the edit is written back over the working copy and the
banner asks. The sync point stayed at the NEW live content -- so the apply
gate compared live with it, saw nothing, and the next Apply (or the Auto-Sync
push) wrote the OLD lab value back over the lab's newer one, reporting "in
sync". Reproduced 7 of 16 times over the real routes and once in Chrome.

After the race either the working copy holds live + the edit, or the sync
point stays at the old content so the next Apply sees the move. The race is
made deterministic: the edit is committed between the pull's live read and
its working-copy write.
"""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path

import pytest

from quam_state_manager.core import working_copy as WC
from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app

_WIRING = {"network": {"host": "127.0.0.1", "cluster_name": "C1"}}
OLD, LAB, EDIT = 2.1e-05, 2.2e-05, 1.3e-05


def _state(q2=OLD):
    qs = {f"q{i}": {"id": f"q{i}", "T1": 1.0e-5 * i} for i in (1, 2, 3)}
    qs["q2"]["T1"] = q2
    return {"qubits": qs, "qubit_pairs": {}, "active_qubit_names": sorted(qs)}


def _write(folder: Path, state: dict):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")


@pytest.fixture
def env(tmp_path):
    live = tmp_path / "chip" / "quam_state"
    _write(live, _state())
    routes._quam_cache.clear()
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    return {"app": app, "c": c, "live": live, "inst": tmp_path / "_inst"}


def _ctx(env):
    return env["app"].config["contexts"][env["app"].config["active_context"]]


def _live_t1(env, q):
    return json.loads((env["live"] / "state.json").read_text(encoding="utf-8"))["qubits"][q]["T1"]


def _seen(env):
    t = env["c"].get("/state/tray", headers={"Accept": "text/html"}).get_data(as_text=True)
    n = re.search(r'data-change-count="(\d+)"', t)
    s = re.search(r'data-change-sig="([0-9a-f]*)"', t)
    return {"seen_changes": n.group(1) if n else "0", "seen_sig": s.group(1) if s else ""}


def _edit_between_read_and_write(monkeypatch, env):
    """Commit ``q3.T1 = EDIT`` once, between the pull's live read and its
    working-copy write (``sync_from_live`` reads through ``doc_cache.read_pair``
    in mode "fresh", then writes)."""
    real_dc = WC.doc_cache
    fired: list = []

    def commit():
        out = {}

        def run():
            out["r"] = env["c"].post("/field/edit", data={"dot_path": "qubits.q3.T1",
                                                          "value": repr(EDIT)})
        t = threading.Thread(target=run)
        t.start()
        t.join(20)
        assert out["r"].status_code == 200, out["r"].data
        fired.append(1)

    class _Hooked:
        def __getattr__(self, name):
            return getattr(real_dc, name)

        def read_pair(self, folder, *a, **k):
            pair = real_dc.read_pair(folder, *a, **k)
            if not fired and k.get("mode") == "fresh":
                commit()
            return pair

    monkeypatch.setattr(WC, "doc_cache", _Hooked())
    return fired


def _lab_writes(env):
    _write(env["live"], _state(q2=LAB))
    _ctx(env)["live_diverged"] = True      # what the drift poll sets


def _after_race_is_consistent(env):
    """Either the working copy holds live + the edit, or the sync point is
    not the live content (so the gate sees the move)."""
    ctx = _ctx(env)
    wc = ctx["working_copy"]
    work = json.loads((Path(wc.working_folder) / "state.json").read_text(encoding="utf-8"))
    st = ctx["store"].state
    holds_live_and_edit = (work["qubits"]["q2"]["T1"] == LAB and st["qubits"]["q2"]["T1"] == LAB
                           and st["qubits"]["q3"]["T1"] == EDIT)
    live_hash = WC.live_content_hash(wc)
    # the persisted meta too: a restart must not adopt the stale point
    on_disk = WC.load(str(env["inst"]), env["live"])
    point_left_behind = (wc.synced_live_hash != live_hash
                         and on_disk is not None and on_disk.synced_live_hash != live_hash)
    return holds_live_and_edit or point_left_behind, {
        "work_q2": work["qubits"]["q2"]["T1"], "ram_q3": st["qubits"]["q3"]["T1"],
        "synced": wc.synced_live_hash, "live": live_hash}


class TestAutoSyncPullRace:
    def test_the_next_apply_keeps_the_lab_value(self, env, monkeypatch):
        c = env["c"]
        c.post("/auto-sync/set", data={"pull": "1", "pull_replace": "0", "push": "0"})
        _lab_writes(env)
        fired = _edit_between_read_and_write(monkeypatch, env)
        c.post("/auto-sync/pull", data={"dom_dirty": "0"})
        assert fired, "the hook never ran: the pull did not reach its live read"
        ok, why = _after_race_is_consistent(env)
        assert ok, f"working copy old while the sync point says new: {why}"
        monkeypatch.undo()

        # the top bar's Apply (mode=apply with the screen's seen set)
        r = c.post("/state/sync", data={"mode": "apply", **_seen(env)})
        assert r.status_code in (200, 409), r.data
        assert _live_t1(env, "q2") == LAB, (
            "the Apply wrote the old lab value back over the lab's newer one: "
            f"{r.get_json()}")
        # the edit is not lost either: applied, or still pending
        assert (_live_t1(env, "q3") == EDIT
                or _ctx(env)["store"].state["qubits"]["q3"]["T1"] == EDIT)

    def test_the_auto_apply_flush_keeps_the_lab_value(self, env, monkeypatch):
        c = env["c"]
        c.post("/auto-sync/set", data={"pull": "1", "pull_replace": "0", "push": "0"})
        _lab_writes(env)
        _edit_between_read_and_write(monkeypatch, env)
        c.post("/auto-sync/pull", data={"dom_dirty": "0"})
        monkeypatch.undo()
        # the push door the auto-apply flush presses
        c.post("/state/apply-to-live", data=_seen(env), headers={"Accept": "application/json"})
        assert _live_t1(env, "q2") == LAB, "the push wrote the old lab value back"

    def test_a_failed_rebuild_puts_the_sync_point_back(self, env, monkeypatch):
        """The failure branch documents the same absorbing state: the pull
        advanced the point, the rebuild failed, RAM kept the old content."""
        c = env["c"]
        c.post("/auto-sync/set", data={"pull": "1", "pull_replace": "0", "push": "0"})
        _lab_writes(env)
        monkeypatch.setattr(routes, "_rebuild_after_working_copy_replaced",
                            lambda ctx, **k: (_ for _ in ()).throw(OSError("boom")))
        c.post("/auto-sync/pull", data={"dom_dirty": "0"})
        monkeypatch.undo()
        wc = _ctx(env)["working_copy"]
        assert wc.synced_live_hash != WC.live_content_hash(wc)
        c.post("/state/apply-to-live", headers={"Accept": "application/json"})
        assert _live_t1(env, "q2") == LAB


    def test_an_edit_after_the_recheck_is_not_cleared_by_the_rebuild(self, env, monkeypatch):
        """The re-check and the rebuild's reload were two lock holds: an edit
        committed between them was cleared by the reload without a word. One
        hold now -- the edit waits and lands on top of the pulled content."""
        c = env["c"]
        c.post("/auto-sync/set", data={"pull": "1", "pull_replace": "0", "push": "0"})
        _lab_writes(env)
        real = routes._rebuild_after_working_copy_replaced
        done = threading.Event()
        out = {}

        def edit():
            out["r"] = c.post("/field/edit", data={"dot_path": "qubits.q3.T1", "value": repr(EDIT)})
            done.set()

        t = threading.Thread(target=edit)

        def hooked(ctx, **k):
            t.start()
            done.wait(1.5)          # lands now unless the store lock holds it off
            return real(ctx, **k)

        monkeypatch.setattr(routes, "_rebuild_after_working_copy_replaced", hooked)
        c.post("/auto-sync/pull", data={"dom_dirty": "0"})
        t.join(20)
        monkeypatch.undo()
        assert out["r"].status_code == 200
        st = _ctx(env)["store"]
        assert st.state["qubits"]["q3"]["T1"] == EDIT, "the edit was cleared by the pull's reload"
        assert st.state["qubits"]["q2"]["T1"] == LAB
        assert [e.dot_path for e in st.change_log] == ["qubits.q3.T1"]


class TestTheMergeDoorRace:
    """The Auto-Sync merge presses /state/sync (reapply / apply): it captured
    the edits to replay BEFORE the pull, so one committed while the pull read
    and wrote was dropped by the rebuild."""

    @pytest.mark.parametrize("mode", ["reapply", "apply"])
    def test_an_edit_inside_the_pull_is_replayed(self, env, monkeypatch, mode):
        c = env["c"]
        assert c.post("/field/edit", data={"dot_path": "qubits.q1.T1",
                                           "value": "1.7e-05"}).status_code == 200
        _write(env["live"], _state(q2=LAB))            # the lab writes
        _edit_between_read_and_write(monkeypatch, env)
        r = c.post("/state/sync", data={"mode": mode, "ack_unseen": "1", "ack_collision": "1"})
        monkeypatch.undo()
        assert r.status_code == 200, r.data
        st = _ctx(env)["store"].state["qubits"]
        assert st["q2"]["T1"] == LAB and st["q1"]["T1"] == 1.7e-5
        assert st["q3"]["T1"] == EDIT, "the edit committed inside the pull was dropped"
        if mode == "apply":
            assert (_live_t1(env, "q2"), _live_t1(env, "q3")) == (LAB, EDIT)


class TestAnUndoInsideThePull:
    """R2 (the gap the review left open under SB-F1): the merge door replays
    the edits it captured BEFORE the pull. An undo pressed while the pull read
    and wrote took a row out of the change log -- and the stale capture put
    it back on the pulled content (and, with apply, on the live chip)."""

    @pytest.mark.parametrize("mode", ["reapply", "apply"])
    def test_it_stays_undone(self, env, monkeypatch, mode):
        c = env["c"]
        assert c.post("/field/edit", data={"dot_path": "qubits.q1.T1",
                                           "value": "1.7e-05"}).status_code == 200
        assert c.post("/field/edit", data={"dot_path": "qubits.q3.T1",
                                           "value": repr(EDIT)}).status_code == 200
        _write(env["live"], _state(q2=LAB))            # the lab writes
        real_dc = WC.doc_cache
        fired: list = []

        class _Hooked:
            def __getattr__(self, name):
                return getattr(real_dc, name)

            def read_pair(self, folder, *a, **k):
                pair = real_dc.read_pair(folder, *a, **k)
                if not fired and k.get("mode") == "fresh":
                    out = {}
                    t = threading.Thread(target=lambda: out.update(r=c.post("/undo")))
                    t.start()
                    t.join(20)
                    assert out.get("r") is not None and out["r"].status_code == 200
                    fired.append(1)
                return pair
        monkeypatch.setattr(WC, "doc_cache", _Hooked())
        r = c.post("/state/sync", data={"mode": mode, "ack_unseen": "1", "ack_collision": "1"})
        monkeypatch.undo()
        assert fired and r.status_code == 200, r.data
        st = _ctx(env)["store"].state["qubits"]
        assert st["q2"]["T1"] == LAB and st["q1"]["T1"] == 1.7e-5
        assert st["q3"]["T1"] == 1.0e-5 * 3, "the undo pressed during the pull was reverted"
        if mode == "reapply":
            assert [e.dot_path for e in _ctx(env)["store"].change_log] == ["qubits.q1.T1"]
        else:
            assert (_live_t1(env, "q1"), _live_t1(env, "q3")) == (1.7e-5, 1.0e-5 * 3)


class TestMachineReconcileRace:
    """The Runner's post-node hook adopts live by itself (auto_adopt=True) and
    keeps a late edit the same way -- the same sync point was left behind."""

    def test_the_next_apply_keeps_the_lab_value(self, env, monkeypatch):
        c = env["c"]
        _write(env["live"], _state(q2=LAB))
        fired = _edit_between_read_and_write(monkeypatch, env)
        ctx = _ctx(env)
        key = env["app"].config["active_context"]
        with env["app"].test_request_context("/"):
            routes._reconcile_cached_quam_ctx(key, ctx, auto_adopt=True)
        assert fired, "the hook never ran: the reconcile did not pull"
        ok, why = _after_race_is_consistent(env)
        assert ok, f"working copy old while the sync point says new: {why}"
        monkeypatch.undo()
        c.post("/state/sync", data={"mode": "apply", **_seen(env)})
        assert _live_t1(env, "q2") == LAB
