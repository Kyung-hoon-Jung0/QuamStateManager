"""docs/160 — Ctrl+Z after Apply-to-live writes the chip.

Customer (CRITICAL, many users): after "Apply to chip", Ctrl+Z must still
undo — on the chip. The covenant is amended (docs/107 → 117 → 120 → 160):
the Apply press was the consent for the change, and Ctrl+Z withdraws that
same consent, so the withdrawal needs no second press. Everything still goes
through the ONE apply door with its staleness gate.

Pinned here, against the real routes on a tmp chip:

A. the live walk — edit → apply → Ctrl+Z writes the LIVE file back, the log
   stays empty, the cursor moves down and is persisted; Ctrl+Shift+Z re-applies
   forward and writes live; a new action after an undo discards the redo
   branch (the journal is a straight line); the setting OFF is the docs/107
   stage-only behaviour; every refusal (pending edits, drift, a chip that
   moved, read-only, foreign owner) stages instead and says why — and never
   clobbers.
B. wholesale loads — a State-History stage → Apply and a restore-live each
   become ONE journal unit of leaf changes, so Ctrl+Z walks them live.
C. another window — a unit applied by a LIVE foreign process is staged, not
   written; a dead owner (this window restarted) is ours; the sidecar written
   by another process is re-read before a walk.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from quam_state_manager.core import undo_journal
from quam_state_manager.web import routes as routes_mod
from quam_state_manager.web.app import create_app

_WIRING = {"network": {"host": "1.1.1.1", "cluster_name": "C1"}}


def _state(off=0.08, f01=5.0e9):
    return {
        "qubits": {"qA1": {"id": "qA1", "f_01": f01,
                           "z": {"joint_offset": off},
                           "xy": {"ops": {"x180": {"amp": 0.2}}}}},
        "qubit_pairs": {},
        "active_qubit_names": ["qA1"],
    }


def _write_chip(folder: Path, state: dict):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")


@pytest.fixture
def env(tmp_path):
    live = tmp_path / "chips" / "live"
    _write_chip(live, _state())
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    r = c.post("/load", data={"folder": str(live)})
    assert r.status_code in (200, 302)
    return {"app": app, "client": c, "live": live, "tmp": tmp_path}


def _ctx(env):
    return next(iter(env["app"].config["contexts"].values()))


def _edit(c, value, dot_path="qubits.qA1.z.joint_offset"):
    r = c.post("/field/edit-batch", json={
        "updates": [{"dot_path": dot_path, "value": str(value)}],
        "expect_chip": "",
    })
    assert r.status_code == 200 and r.get_json()["ok"], r.data
    return r


def _apply(c):
    r = c.post("/state/apply-to-live")
    assert r.status_code == 200, r.data
    return r


def _live_off(env):
    return json.loads((env["live"] / "state.json").read_text(encoding="utf-8"))["qubits"]["qA1"]["z"]["joint_offset"]


def _live_f01(env):
    return json.loads((env["live"] / "state.json").read_text(encoding="utf-8"))["qubits"]["qA1"]["f_01"]


def _work_off(env):
    return _ctx(env)["store"].merged["qubits"]["qA1"]["z"]["joint_offset"]


def _trig(r) -> dict:
    raw = r.headers.get("HX-Trigger")
    return json.loads(raw) if raw else {}


def _sidecar(env) -> Path:
    files = sorted((Path(env["app"].instance_path) / "working_state").glob("*.undo_journal.json"))
    assert files, "no journal sidecar"
    return files[0]


def _set_setting(env, on: bool):
    r = env["client"].post("/settings/undo-live", data={"enabled": "1" if on else "0"})
    assert r.status_code == 200 and r.get_json()["enabled"] is on


# ======================================================================
# A. the live walk
# ======================================================================

class TestLiveWalk:
    def test_default_is_on_and_the_toggle_persists(self, env):
        c = env["client"]
        with env["app"].app_context():
            assert routes_mod._undo_live_enabled() is True      # absent file ⇒ ON
        assert 'id="undo-live-toggle"' in c.get("/").get_data(as_text=True)
        assert "Ctrl+Z writes live: ON" in c.get("/").get_data(as_text=True)
        r = c.post("/settings/undo-live")                        # bare POST toggles
        assert r.get_json() == {"ok": True, "enabled": False}
        assert (Path(env["app"].instance_path) / "undo_live.json").exists()
        assert "Ctrl+Z writes live: OFF" in c.get("/").get_data(as_text=True)
        _set_setting(env, True)

    def test_ctrl_z_after_apply_writes_the_live_file(self, env):
        c = env["client"]
        _edit(c, 0.10); _apply(c)
        assert _live_off(env) == 0.10
        ctx = _ctx(env)
        assert ctx["undo_cursor"] == 1 and not ctx["store"].change_log
        r = c.post("/undo")
        t = _trig(r)["cellsReverted"]
        assert t["live"] is True and t["message"].startswith("Undone → live")
        assert t["entries"][0]["dot_path"] == "qubits.qA1.z.joint_offset"
        assert _live_off(env) == 0.08                 # the CHIP took it
        assert _work_off(env) == 0.08
        assert not ctx["store"].change_log            # nothing left in the tray
        assert ctx["undo_cursor"] == 0
        # the walk position is persisted for a restart / another window
        _units, cur = undo_journal.load_state(_sidecar(env))
        assert len(_units) == 1 and cur == 0
        # the journal itself is unchanged: the walk step is NOT a new unit
        assert _units[0]["entries"][0]["new"] == 0.10

    def test_ctrl_shift_z_re_applies_forward_and_writes_live(self, env):
        c = env["client"]
        _edit(c, 0.10); _apply(c)
        c.post("/undo")
        assert _live_off(env) == 0.08
        r = c.post("/redo")
        t = _trig(r)["cellsReverted"]
        assert t["message"].startswith("Redone → live"), t["message"]
        assert t.get("live") is True
        assert _live_off(env) == 0.10 and _work_off(env) == 0.10
        ctx = _ctx(env)
        assert not ctx["store"].change_log and ctx["undo_cursor"] == 1
        assert undo_journal.load_state(_sidecar(env))[1] == 1
        # and back again
        c.post("/undo")
        assert _live_off(env) == 0.08 and ctx["undo_cursor"] == 0

    def test_two_applies_walk_down_then_up(self, env):
        c = env["client"]
        _edit(c, 0.10); _apply(c)
        _edit(c, 0.12); _apply(c)
        c.post("/undo"); assert _live_off(env) == 0.10
        c.post("/undo"); assert _live_off(env) == 0.08
        r = c.post("/undo")                       # exhausted: silent no-op
        assert "HX-Trigger" not in r.headers and _live_off(env) == 0.08
        c.post("/redo"); assert _live_off(env) == 0.10
        c.post("/redo"); assert _live_off(env) == 0.12
        r = c.post("/redo")                       # nothing above the tip
        assert _live_off(env) == 0.12

    def test_a_new_action_after_an_undo_discards_the_redo_branch(self, env):
        """The editor rule: the journal is a straight line of what is in
        effect. Without it Ctrl+Z after the new apply would first re-apply
        the undone value on its way down."""
        c = env["client"]
        _edit(c, 0.10); _apply(c)
        _edit(c, 0.12); _apply(c)
        c.post("/undo")                           # 0.12 → 0.10 (live)
        _edit(c, 0.30); _apply(c)                 # a NEW action at cursor 1
        units, cur = undo_journal.load_state(_sidecar(env))
        assert [u["entries"][0]["new"] for u in units] == [0.10, 0.30]
        assert cur == 2
        c.post("/undo"); assert _live_off(env) == 0.10
        c.post("/undo"); assert _live_off(env) == 0.08
        assert _ctx(env)["undo_cursor"] == 0
        r = c.post("/redo"); assert _live_off(env) == 0.10
        r = c.post("/redo"); assert _live_off(env) == 0.30

    def test_setting_off_is_the_stage_only_behaviour(self, env):
        c = env["client"]
        _set_setting(env, False)
        _edit(c, 0.10); _apply(c)
        r = c.post("/undo")
        t = _trig(r)["cellsReverted"]
        assert t["live"] is False and t["message"].startswith("Undone (staged)")
        assert t.get("tier_note") is None            # OFF is not a refusal, no note
        assert _live_off(env) == 0.10                # chip untouched (docs/107)
        assert _work_off(env) == 0.08
        ctx = _ctx(env)
        assert ctx["store"].change_log[-1].group_id.startswith("jrn:")
        # a persisted cursor is never written by a staged-only step
        assert undo_journal.load_state(_sidecar(env))[1] == 1

    def test_pending_edits_in_the_tray_stage_instead(self, env):
        c = env["client"]
        _edit(c, 0.10); _apply(c)
        _edit(c, 5.1e9, dot_path="qubits.qA1.f_01")    # unapplied edit in the tray
        c.post("/undo")                                  # pops that edit (in-memory tier)
        assert _live_f01(env) == 5.0e9
        _edit(c, 5.2e9, dot_path="qubits.qA1.f_01")    # pending again
        # now the journal step: must NOT flush (the f_01 edit would ride along)
        # -- force the journal branch by leaving the pending edit and walking
        ctx = _ctx(env)
        with env["app"].app_context(), env["app"].test_request_context("/undo", method="POST"):
            resp = routes_mod._undo_journal_step(ctx)
        t = json.loads(resp.headers["HX-Trigger"])["cellsReverted"]
        assert t["live"] is False and "unapplied edits" in t["message"]
        assert _live_off(env) == 0.10                    # chip untouched
        assert _live_f01(env) == 5.0e9

    def test_a_chip_that_moved_is_never_clobbered(self, env):
        """An experiment wrote the live file after our apply: the staleness
        gate refuses, the step stays staged, the toast says so."""
        c = env["client"]
        _edit(c, 0.10); _apply(c)
        moved = _state(off=0.10, f01=5.5e9)               # someone else's write
        (env["live"] / "state.json").write_text(json.dumps(moved), encoding="utf-8")
        os.utime(env["live"] / "state.json", None)
        r = c.post("/undo")
        t = _trig(r)["cellsReverted"]
        assert t["live"] is False
        assert t["message"].startswith("Not undone") and "changed since" in t["message"]
        assert t["entries"] == []
        live = json.loads((env["live"] / "state.json").read_text(encoding="utf-8"))
        assert live["qubits"]["qA1"]["f_01"] == 5.5e9 and live["qubits"]["qA1"]["z"]["joint_offset"] == 0.10
        # review M2: ALL-OR-NOTHING -- the door had saved the inverse into the
        # working copy before it refused, so the step is rolled back: working
        # copy back at the synced content, nothing dirty or stashed, the walk
        # position where it was. The drift banner is the way forward.
        ctx = _ctx(env)
        assert _work_off(env) == 0.10
        assert not ctx["store"].change_log and not ctx["working_dirty"]
        assert not ctx.get("pending_reapply")
        assert ctx["undo_cursor"] == 1
        assert undo_journal.load_state(_sidecar(env))[1] == 1
        # Ctrl+Shift+Z has nothing above the cursor; Ctrl+Z again refuses the same way
        r = c.post("/undo")
        assert _trig(r)["cellsReverted"]["message"].startswith("Not undone")
        assert _live_off(env) == 0.10 and _work_off(env) == 0.10
        # take the live changes (the drift banner's pull), then the walk works
        r = c.post("/state/sync", data={"mode": "pull"})
        assert r.status_code == 200
        r = c.post("/undo")
        t = _trig(r)["cellsReverted"]
        # the journal recorded new=0.10 and the chip holds 0.10 → no drift → live
        assert t["live"] is True and _live_off(env) == 0.08
        live = json.loads((env["live"] / "state.json").read_text(encoding="utf-8"))
        assert live["qubits"]["qA1"]["f_01"] == 5.5e9      # the foreign write survived

    def test_drift_stages_instead(self, env):
        """The journal recorded new=0.10; if the working value is not 0.10
        any more (a later edit reached the chip by another route), the
        inverse would assume a state the chip is not in."""
        c = env["client"]
        _edit(c, 0.10); _apply(c)
        ctx = _ctx(env)
        # simulate a later write nobody journaled (a foreign apply through a
        # path with no log) by rewriting BOTH copies and the sync point
        wc = ctx["working_copy"]
        for folder in (wc.working_folder, env["live"]):
            (Path(folder) / "state.json").write_text(json.dumps(_state(off=0.11)), encoding="utf-8")
        with env["app"].app_context():
            routes_mod._rebuild_after_working_copy_replaced(ctx)
        r = c.post("/state/sync", data={"mode": "pull"})     # re-sync so live is not "stale"
        r = c.post("/undo")
        t = _trig(r)["cellsReverted"]
        assert t["live"] is False and "moved since" in t["message"]

    def test_saved_but_unapplied_content_never_rides_along(self, env):
        """review C2: `/save` puts content in the working copy with an empty
        log. The door pushes the WHOLE working copy, so a live walk step must
        refuse (stage only) until that content is applied or discarded."""
        c = env["client"]
        _edit(c, 0.10); _apply(c)
        _edit(c, 5.1e9, dot_path="qubits.qA1.f_01")
        assert c.post("/save").status_code == 200          # saved, NOT applied
        ctx = _ctx(env)
        assert ctx["working_dirty"] and not ctx["store"].change_log
        r = c.post("/undo")
        t = _trig(r)["cellsReverted"]
        # the top journal unit is now the SAVE (f_01) -- staged only, with the reason
        assert t["live"] is False and "saved-but-unapplied" in t["message"]
        assert _live_f01(env) == 5.0e9 and _live_off(env) == 0.10
        assert ctx["store"].change_log[-1].group_id.startswith("jrn:")

    def test_a_staged_snapshot_never_rides_along(self, env):
        """review C2 (the worse half): a staged snapshot that AGREES on the
        undone path would have been pushed to the chip whole, unjournaled."""
        c = env["client"]
        hm = env["app"].config["history_manager"]
        _edit(c, 0.10); _apply(c)                              # unit: off 0.08→0.10
        hm.check_and_snapshot(_ctx(env)["path"], "manual", force=True)   # snapshot @ off 0.10
        _edit(c, 5.1e9, dot_path="qubits.qA1.f_01"); _apply(c)           # unit: f_01
        snaps = hm.list_snapshots(_ctx(env)["path"])
        ts = [s.timestamp if hasattr(s, "timestamp") else s["timestamp"] for s in snaps][0]
        # stage that snapshot (off 0.10, f_01 5.0e9) -- differs from live only on f_01
        assert c.post(f"/state-history/{ts}/stage").status_code == 200
        ctx = _ctx(env)
        assert ctx["staged_base"]
        r = c.post("/undo")                                    # top unit: the f_01 apply
        t = _trig(r)["cellsReverted"]
        assert t["live"] is False and "staged snapshot" in t["message"]
        assert _live_f01(env) == 5.1e9                          # the chip did not move
        assert ctx["staged_base"]                               # and the stage is intact

    def test_read_only_archive_stages(self, env):
        c = env["client"]
        _edit(c, 0.10); _apply(c)
        ctx = _ctx(env)
        ctx["origin"] = "dataset_archive"
        r = c.post("/undo")
        # an archive never walks the journal at all (docs/107): silent no-op
        assert _live_off(env) == 0.10
        ctx["origin"] = "live"

    def test_the_walk_survives_a_context_rebuild(self, env):
        """A restart re-reads the sidecar: the persisted cursor puts Ctrl+Z
        and Ctrl+Shift+Z back where the chip actually stands."""
        c = env["client"]
        _edit(c, 0.10); _apply(c)
        _edit(c, 0.12); _apply(c)
        c.post("/undo")                                   # live: 0.10, cursor 1
        ctx = _ctx(env)
        ctx.pop("redo_stack", None)                       # RAM redo dies with the process
        with env["app"].app_context():
            routes_mod._journal_reset(ctx)                # what a rebuild does
        assert ctx["undo_cursor"] == 1
        r = c.post("/redo")                               # the journal-forward fallback
        assert _live_off(env) == 0.12 and ctx["undo_cursor"] == 2
        c.post("/undo"); c.post("/undo")
        assert _live_off(env) == 0.08 and ctx["undo_cursor"] == 0


# ======================================================================
# B. wholesale loads become ONE unit
# ======================================================================

class TestWholesale:
    def _snapshot_ts(self, env):
        hm = env["app"].config["history_manager"]
        snaps = hm.list_snapshots(_ctx(env)["path"])
        assert snaps, "no snapshot to stage"
        return snaps[0].timestamp if hasattr(snaps[0], "timestamp") else snaps[0]["timestamp"]

    def test_stage_then_apply_is_walkable(self, env):
        c = env["client"]
        # a snapshot of the seed (0.08), then the chip moves to 0.10 and 5.1e9
        hm = env["app"].config["history_manager"]
        hm.check_and_snapshot(_ctx(env)["path"], "manual", force=True)
        ts = self._snapshot_ts(env)
        _edit(c, 0.10); _apply(c)
        _edit(c, 5.1e9, dot_path="qubits.qA1.f_01"); _apply(c)
        assert _live_off(env) == 0.10 and _live_f01(env) == 5.1e9
        # stage the old snapshot (wholesale, no change-log entries) and apply
        r = c.post(f"/state-history/{ts}/stage")
        assert r.status_code == 200, r.data
        _apply(c)
        assert _live_off(env) == 0.08 and _live_f01(env) == 5.0e9
        units, cur = undo_journal.load_state(_sidecar(env))
        assert cur == len(units) == 3
        u = units[-1]
        assert u["meta"]["wholesale"] is True and u["meta"]["src"] == "apply-staged"
        changed = {e["path"]: (e["old"], e["new"]) for e in u["entries"]}
        assert changed["qubits.qA1.z.joint_offset"] == (0.10, 0.08)
        assert changed["qubits.qA1.f_01"] == (5.1e9, 5.0e9)
        # Ctrl+Z walks the wholesale step back onto the chip
        r = c.post("/undo")
        t = _trig(r)["cellsReverted"]
        assert t["live"] is True and len(t["entries"]) == 2
        assert _live_off(env) == 0.10 and _live_f01(env) == 5.1e9
        c.post("/redo")
        assert _live_off(env) == 0.08 and _live_f01(env) == 5.0e9

    def test_the_units_old_is_what_the_chip_held(self, env):
        """review C3: an unsaved in-memory edit discarded by a forced stage must
        not become the unit's `old` -- Ctrl+Z would write a value the chip
        never had. `old` is read from the LIVE files right before the write."""
        c = env["client"]
        hm = env["app"].config["history_manager"]
        hm.check_and_snapshot(_ctx(env)["path"], "manual", force=True)   # @ 0.08
        ts = self._snapshot_ts(env)
        _edit(c, 0.10); _apply(c)                              # chip: 0.10
        _edit(c, 0.55)                                          # typed, never applied
        assert c.post(f"/state-history/{ts}/stage?force=1").status_code == 200
        _apply(c)                                               # chip: 0.08
        units, _ = undo_journal.load_state(_sidecar(env))
        u = units[-1]
        assert u["meta"]["wholesale"] is True
        assert {e["path"]: (e["old"], e["new"]) for e in u["entries"]} == {
            "qubits.qA1.z.joint_offset": (0.10, 0.08)}          # 0.10, never 0.55
        c.post("/undo")
        assert _live_off(env) == 0.10

    def test_structural_changes_are_subtree_ops(self, env):
        """review M1: a pulse the chip gained or lost is ONE create/delete at
        its subtree root (per-leaf entries could not be replayed: no parent
        to create into, an empty `{}` shell left behind); a list is a whole
        value. Round-trips on the live chip."""
        c = env["client"]
        hm = env["app"].config["history_manager"]
        ctx = _ctx(env)
        with ctx["store"]._lock:
            ctx["modifier"].create_subtree("qubits.qA1.z.taps", [1])
        _apply(c)
        hm.check_and_snapshot(ctx["path"], "manual", force=True)   # seed: x180 only, taps [1]
        ts = self._snapshot_ts(env)
        # add a pulse (a subtree) and lengthen a list, apply
        with ctx["store"]._lock:
            ctx["modifier"].create_subtree("qubits.qA1.xy.ops.y90", {"amp": 0.1, "len": 20})
        r = c.post("/field/edit-batch", json={"updates": [
            {"dot_path": "qubits.qA1.z.taps", "value": [1, 2]},
        ], "expect_chip": ""})
        assert r.status_code == 200 and r.get_json()["ok"], r.data
        _apply(c)
        live = json.loads((env["live"] / "state.json").read_text(encoding="utf-8"))
        assert "y90" in live["qubits"]["qA1"]["xy"]["ops"] and live["qubits"]["qA1"]["z"]["taps"] == [1, 2]
        # stage the seed snapshot (no y90, one name) and apply: the wholesale unit
        assert c.post(f"/state-history/{ts}/stage").status_code == 200
        _apply(c)
        units, _ = undo_journal.load_state(_sidecar(env))
        u = units[-1]
        ents = {e["path"]: e for e in u["entries"]}
        assert ents["qubits.qA1.xy.ops.y90"]["deleted"] is True
        assert ents["qubits.qA1.xy.ops.y90"]["old"] == {"amp": 0.1, "len": 20}
        assert ents["qubits.qA1.z.taps"]["old"] == [1, 2] and ents["qubits.qA1.z.taps"]["new"] == [1]
        assert not any(k.startswith("qubits.qA1.xy.ops.y90.") for k in ents)     # no per-leaf entries
        assert not any(k.startswith("qubits.qA1.z.taps.") for k in ents)
        live = json.loads((env["live"] / "state.json").read_text(encoding="utf-8"))
        assert "y90" not in live["qubits"]["qA1"]["xy"]["ops"]
        # Ctrl+Z restores the pulse and the list ON THE CHIP, whole
        r = c.post("/undo")
        assert _trig(r)["cellsReverted"]["live"] is True, _trig(r)
        live = json.loads((env["live"] / "state.json").read_text(encoding="utf-8"))
        assert live["qubits"]["qA1"]["xy"]["ops"]["y90"] == {"amp": 0.1, "len": 20}
        assert live["qubits"]["qA1"]["z"]["taps"] == [1, 2]
        # and Ctrl+Shift+Z removes them again -- no `{}` shell
        r = c.post("/redo")
        assert _trig(r)["cellsReverted"].get("live") is True, _trig(r)
        live = json.loads((env["live"] / "state.json").read_text(encoding="utf-8"))
        assert "y90" not in live["qubits"]["qA1"]["xy"]["ops"]
        assert live["qubits"]["qA1"]["z"]["taps"] == [1]

    def test_restore_live_is_walkable(self, env):
        c = env["client"]
        hm = env["app"].config["history_manager"]
        hm.check_and_snapshot(_ctx(env)["path"], "manual", force=True)
        ts = self._snapshot_ts(env)
        _edit(c, 0.10); _apply(c)
        r = c.post(f"/state-history/{ts}/restore-live?force=1")
        assert r.status_code == 200, r.data
        assert _live_off(env) == 0.08
        units, cur = undo_journal.load_state(_sidecar(env))
        assert units[-1]["meta"]["src"] == "restore-live" and cur == len(units)
        r = c.post("/undo")
        assert _trig(r)["cellsReverted"]["live"] is True
        assert _live_off(env) == 0.10

    def test_dataset_apply_to_chip_is_walkable(self, env):
        """docs/108's one-press button stages a run's state and pushes it
        through the shared apply core — the SAME wholesale unit lands."""
        c = env["client"]
        root = env["tmp"] / "data"
        run = root / "2026-12-30" / "#31_08_spec_010000"
        run.mkdir(parents=True)
        (run / "node.json").write_text(json.dumps({
            "metadata": {"name": "08_spec", "status": "successful",
                         "run_start": "2026-12-30T01:00:00", "run_end": "2026-12-30T01:00:01"},
            "data": {"parameters": {"model": {"qubits": ["qA1"]}}, "outcomes": {}},
            "id": 31, "parents": [], "created_at": "2026-12-30T01:00:00",
        }), encoding="utf-8")
        (run / "data.json").write_text("{}", encoding="utf-8")
        _write_chip(run / "quam_state", _state(off=0.079))
        c.post("/workspace/add", data={"folder": str(root)})
        uid = f"{routes_mod._folder_key(root)}:31"
        r = c.post(f"/dataset/{uid}/load-state?apply=1")
        assert r.status_code == 200, r.data
        assert _live_off(env) == 0.079
        units, cur = undo_journal.load_state(_sidecar(env))
        assert cur == len(units) == 1
        assert units[0]["meta"]["wholesale"] is True
        assert {e["path"]: (e["old"], e["new"]) for e in units[0]["entries"]} == {
            "qubits.qA1.z.joint_offset": (0.08, 0.079)}
        r = c.post("/undo")
        assert _trig(r)["cellsReverted"]["live"] is True
        assert _live_off(env) == 0.08
        c.post("/redo")
        assert _live_off(env) == 0.079

    def test_too_large_is_named_not_walked(self, env, monkeypatch):
        c = env["client"]
        monkeypatch.setattr(routes_mod, "_WHOLESALE_UNIT_CAP", 0)
        hm = env["app"].config["history_manager"]
        hm.check_and_snapshot(_ctx(env)["path"], "manual", force=True)
        ts = self._snapshot_ts(env)
        _edit(c, 0.10); _apply(c)
        c.post(f"/state-history/{ts}/stage"); _apply(c)
        units, _ = undo_journal.load_state(_sidecar(env))
        assert units[-1]["entries"] == [] and units[-1]["meta"]["too_large"] >= 1
        r = c.post("/undo")
        assert "too many for Ctrl+Z" in _trig(r)["cellsReverted"]["message"]
        assert _live_off(env) == 0.08                     # nothing written


# ======================================================================
# C. another window
# ======================================================================

class TestForeignWindow:
    def test_a_live_foreign_owner_stages(self, env, monkeypatch):
        c = env["client"]
        _edit(c, 0.10); _apply(c)
        units, _ = undo_journal.load_state(_sidecar(env))
        assert units[-1]["meta"]["owner_pid"] == os.getpid()
        # pretend another live SM process applied it
        ctx = _ctx(env)
        ctx["undo_units"][-1]["meta"]["owner_pid"] = 424242
        from quam_state_manager.core import instances
        monkeypatch.setattr(instances, "pid_alive", lambda pid: pid == 424242)
        r = c.post("/undo")
        t = _trig(r)["cellsReverted"]
        assert t["live"] is False and "another SM window" in t["message"]
        assert _live_off(env) == 0.10

    def test_a_dead_owner_is_ours(self, env, monkeypatch):
        c = env["client"]
        _edit(c, 0.10); _apply(c)
        ctx = _ctx(env)
        ctx["undo_units"][-1]["meta"]["owner_pid"] = 424242
        from quam_state_manager.core import instances
        monkeypatch.setattr(instances, "pid_alive", lambda pid: False)
        r = c.post("/undo")
        assert _trig(r)["cellsReverted"]["live"] is True
        assert _live_off(env) == 0.08

    def test_a_live_foreign_owner_is_not_redone_either(self, env, monkeypatch):
        """review m1: the owner rule holds on the way UP as well."""
        c = env["client"]
        _edit(c, 0.10); _apply(c)
        c.post("/undo")                                        # ours: live 0.08
        ctx = _ctx(env)
        ctx["undo_units"][-1]["meta"]["owner_pid"] = 424242
        from quam_state_manager.core import instances
        monkeypatch.setattr(instances, "pid_alive", lambda pid: pid == 424242)
        r = c.post("/redo")
        t = _trig(r)["cellsReverted"]
        assert "another SM window" in t["message"] and _live_off(env) == 0.08

    def test_the_applied_log_revert_keeps_the_cursor(self, env):
        """review M3: the ✕ of an applied-log row rewrites the sidecar --
        the persisted cursor must survive it."""
        c = env["client"]
        _edit(c, 0.10); _apply(c)
        _edit(c, 0.12); _apply(c)
        c.post("/undo")                                        # live 0.10, cursor 1
        units, cur = undo_journal.load_state(_sidecar(env))
        assert cur == 1
        undo_journal.mark_unit(_sidecar(env), units[0]["id"], {"reverted_by": "alr:test"})
        units2, cur2 = undo_journal.load_state(_sidecar(env))
        assert cur2 == 1 and units2[0]["meta"]["reverted_by"] == "alr:test"

    def test_a_live_redo_refreshes_the_drift_and_versions_surfaces(self, env):
        """review m2: a live redo wrote the chip -- the same triggers as an Apply."""
        c = env["client"]
        _edit(c, 0.10); _apply(c)
        r = c.post("/undo")
        trig = _trig(r)
        assert trig.get("liveDriftChanged") and trig.get("stateHistoryChanged")
        r = c.post("/redo")
        trig = _trig(r)
        assert trig["cellsReverted"]["live"] is True
        assert trig.get("liveDriftChanged") and trig.get("stateHistoryChanged")

    def test_a_redo_frame_names_its_unit(self, env):
        """review m4: a redo frame that no longer points at its unit (another
        window truncated the journal) is dropped, never applied to a stranger."""
        c = env["client"]
        _edit(c, 0.10); _apply(c)
        c.post("/undo")
        ctx = _ctx(env)
        ctx["redo_stack"][-1]["unit_id"] = "someone-elses"
        r = c.post("/redo")
        t = _trig(r)["cellsReverted"]
        assert "nothing to redo" in t["message"] and _live_off(env) == 0.08

    def test_the_flush_runs_outside_the_store_lock(self, env, monkeypatch):
        """review C1: the door takes the build lock; every wholesale-replace
        path takes the build lock and then `store._lock`. A walk step holding
        `store._lock` across the flush was an ABBA deadlock."""
        c = env["client"]
        _edit(c, 0.10); _apply(c)
        store = _ctx(env)["store"]
        seen = {}
        orig = routes_mod._flush_walk_step_live

        def spy(ctx):
            seen["held"] = store._lock._is_owned()
            return orig(ctx)
        monkeypatch.setattr(routes_mod, "_flush_walk_step_live", spy)
        c.post("/undo")
        assert seen.get("held") is False, "the live flush must not run under store._lock"
        seen.clear()
        c.post("/redo")
        assert seen.get("held") is False

    def test_a_refused_step_rolls_back_a_two_entry_unit_to_its_start(self, env):
        """review N1: a unit that touched one path twice (0.08→0.10→0.12 in
        one gid) must roll back to 0.08's successor... i.e. to the value the
        chip holds (0.12), newest-first -- the first cut walked the staged
        inverse forwards and saved the MIDDLE value with dirty=False."""
        c = env["client"]
        r = c.post("/field/edit-batch", json={"updates": [
            {"dot_path": "qubits.qA1.z.joint_offset", "value": "0.10"},
            {"dot_path": "qubits.qA1.z.joint_offset", "value": "0.12"},
        ], "expect_chip": ""})
        assert r.status_code == 200 and r.get_json()["ok"], r.data
        _apply(c)
        assert _live_off(env) == 0.12
        units, _ = undo_journal.load_state(_sidecar(env))
        assert len(units) == 1 and len(units[0]["entries"]) == 2
        moved = _state(off=0.12, f01=5.5e9)                    # someone else's write
        (env["live"] / "state.json").write_text(json.dumps(moved), encoding="utf-8")
        os.utime(env["live"] / "state.json", None)
        r = c.post("/undo")
        assert _trig(r)["cellsReverted"]["message"].startswith("Not undone")
        ctx = _ctx(env)
        assert _work_off(env) == 0.12                          # the START, not the middle
        wc_state = json.loads((Path(ctx["working_copy"].working_folder) / "state.json").read_text(encoding="utf-8"))
        assert wc_state["qubits"]["qA1"]["z"]["joint_offset"] == 0.12
        assert not ctx["working_dirty"] and not ctx.get("pending_reapply")

    def test_a_sync_never_reopens_a_step_already_in_the_tray(self, env, monkeypatch):
        """review N2 (OFF mode, docs/107): a staged step sitting BELOW an
        `alr:` revert must still count as alive when the sidecar mirror
        re-reads, or the RAM cursor jumps to the tip and the next Ctrl+Z
        re-stages a unit that is already in the tray."""
        c = env["client"]
        _set_setting(env, False)
        _edit(c, 0.10); _apply(c)
        _edit(c, 5.1e9, dot_path="qubits.qA1.f_01"); _apply(c)
        c.post("/undo")                                        # B⁻¹ staged, cursor 1
        ctx = _ctx(env)
        assert ctx["undo_cursor"] == 1
        # the applied-log ✕ on unit A: an alr: group lands ABOVE the staged step
        a_id = ctx["undo_units"][0]["id"]
        r = c.post("/auto-apply/revert", data={"unit_id": a_id})
        assert r.status_code == 200, r.data
        assert ctx["store"].change_log[-1].group_id.startswith("alr:")
        # another window touched the sidecar meanwhile (mtime moved)
        os.utime(_sidecar(env), None)
        with env["app"].app_context():
            routes_mod._journal_sync(ctx)
        assert ctx["undo_cursor"] == 1, "the staged step is alive: the cursor must not jump to the tip"
        _set_setting(env, True)

    def test_skipping_a_too_large_unit_never_persists_below_it(self, env, monkeypatch):
        """review N3: the skipped unit is still IN EFFECT on the chip -- a
        persisted cursor below it would let the next save truncate it away."""
        c = env["client"]
        monkeypatch.setattr(routes_mod, "_WHOLESALE_UNIT_CAP", 0)
        hm = env["app"].config["history_manager"]
        hm.check_and_snapshot(_ctx(env)["path"], "manual", force=True)
        snaps = hm.list_snapshots(_ctx(env)["path"])
        ts = snaps[0].timestamp if hasattr(snaps[0], "timestamp") else snaps[0]["timestamp"]
        _edit(c, 0.10); _apply(c)
        c.post(f"/state-history/{ts}/stage"); _apply(c)         # a too_large unit at the tip
        units, cur = undo_journal.load_state(_sidecar(env))
        assert cur == len(units) == 2 and units[-1]["entries"] == []
        r = c.post("/undo")                                    # skip
        assert "too many for Ctrl+Z" in _trig(r)["cellsReverted"]["message"]
        assert _ctx(env)["undo_cursor"] == 1
        assert undo_journal.load_state(_sidecar(env))[1] == 2   # NOT persisted
        r = c.post("/redo")                                    # walk back up over it
        assert "skipped a step too large" in _trig(r)["cellsReverted"]["message"]
        assert _ctx(env)["undo_cursor"] == 2 and _live_off(env) == 0.08
        # a new apply keeps the skipped unit
        _edit(c, 0.30); _apply(c)
        units, cur = undo_journal.load_state(_sidecar(env))
        assert len(units) == 3 and units[1]["meta"].get("too_large")

    def test_another_process_write_is_re_read_before_the_walk(self, env):
        c = env["client"]
        _edit(c, 0.10); _apply(c)
        sc = _sidecar(env)
        # another window appends a unit + moves the cursor (as its own apply would)
        units, _ = undo_journal.load_state(sc)
        extra = dict(units[-1]); extra = json.loads(json.dumps(extra))
        extra["id"] = "deadbeef0001"
        extra["entries"][0]["old"], extra["entries"][0]["new"] = 0.10, 0.10
        extra["meta"]["owner_pid"] = 0                    # unowned: ours to walk
        undo_journal.append_units(sc, [extra])
        os.utime(sc, None)
        ctx = _ctx(env)
        assert len(ctx["undo_units"]) == 1                # stale RAM mirror
        with env["app"].app_context():
            routes_mod._journal_sync(ctx)
        assert len(ctx["undo_units"]) == 2 and ctx["undo_cursor"] == 2
