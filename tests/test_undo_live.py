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

    def test_the_toggle_sends_the_state_it_shows(self):
        """review (code-review sweep): a bare POST flips the SERVER state; two
        windows share the setting, so a stale button would turn it the wrong
        way. The client sends the inverse of what its own button shows --
        a press means what the presser could see (docs/120)."""
        js = (Path(__file__).resolve().parent.parent / "quam_state_manager" / "web" / "static" / "app.js").read_text(encoding="utf-8")
        i = js.index("window.toggleUndoLive = function()")
        seg = js[i:i + 900]
        assert 'getAttribute("data-on") === "1" ? "0" : "1"' in seg
        assert "new URLSearchParams({ enabled: want })" in seg

    def test_an_explicit_enabled_never_flips(self, env):
        c = env["client"]
        for _ in range(2):                                     # idempotent, not a toggle
            assert c.post("/settings/undo-live", data={"enabled": "0"}).get_json()["enabled"] is False
        for _ in range(2):
            assert c.post("/settings/undo-live", data={"enabled": "1"}).get_json()["enabled"] is True

    def test_the_applied_log_shows_a_live_undone_unit_as_undone(self, env):
        """review (code-review sweep): an Auto-Sync row whose unit Ctrl+Z
        undid on the chip kept an armed ✕ that then 409'd "has changed
        since", blaming a foreign write. It renders undone, and its ✕ says so."""
        c = env["client"]
        assert c.post("/auto-apply/arm").status_code == 200
        _edit(c, 0.10); _apply(c)                              # an auto flush → applied-log row
        with env["app"].app_context():
            rows = routes_mod._applied_log_rows()
        assert rows and rows[0]["reverted_by"] is None
        uid = rows[0]["id"]
        r = c.post("/auto-apply/disarm")
        c.post("/undo")                                        # live undo of that unit
        assert _live_off(env) == 0.08
        with env["app"].app_context():
            rows = routes_mod._applied_log_rows()
        assert rows[0]["reverted_by"] == "undo"
        assert ">undone<" in c.get("/state/tray").get_data(as_text=True)
        r = c.post("/auto-apply/revert", data={"unit_id": uid})
        assert r.status_code == 409 and b"already undone" in r.data
        c.post("/redo")                                        # back on the chip → the row is live again
        with env["app"].app_context():
            rows = routes_mod._applied_log_rows()
        assert rows[0]["reverted_by"] is None

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

    def test_a_mixed_apply_journals_each_path_once(self, env):
        """sweep F1: a staged snapshot applied TOGETHER with tray edits
        (docs/65 mixed state) used to journal the edited paths twice -- once
        as the edit unit, once inside the wholesale unit whose `after` is the
        working copy WITH the edits -- so the second Ctrl+Z staged a value the
        chip never held. The wholesale unit excludes the edit unit's paths
        and sits BELOW it; both Ctrl+Z presses write the chip's real history."""
        c = env["client"]
        hm = env["app"].config["history_manager"]
        hm.check_and_snapshot(_ctx(env)["path"], "manual", force=True)   # snapshot @ off 0.08, f_01 5.0e9
        ts = self._snapshot_ts(env)
        _edit(c, 0.10); _apply(c)                                          # chip: off 0.10
        _edit(c, 5.3e9, dot_path="qubits.qA1.f_01"); _apply(c)             # chip: f_01 5.3e9
        assert c.post(f"/state-history/{ts}/stage").status_code == 200    # working: off 0.08, f_01 5.0e9
        _edit(c, 0.55)                                                      # a tray edit ON TOP of the stage
        _apply(c)                                                           # chip: off 0.55, f_01 5.0e9
        assert _live_off(env) == 0.55 and _live_f01(env) == 5.0e9
        units, cur = undo_journal.load_state(_sidecar(env))
        assert cur == len(units) == 4
        w, e = units[-2], units[-1]
        assert w["meta"].get("wholesale") and not e["meta"].get("wholesale")
        assert {x["path"] for x in e["entries"]} == {"qubits.qA1.z.joint_offset"}
        assert {x["path"]: (x["old"], x["new"]) for x in w["entries"]} == {"qubits.qA1.f_01": (5.3e9, 5.0e9)}
        # Ctrl+Z: the edit (0.55 → 0.08 as the edit recorded it: old=0.08 from the stage)
        r = c.post("/undo")
        assert _trig(r)["cellsReverted"]["live"] is True, _trig(r)
        assert _live_off(env) == 0.08 and _live_f01(env) == 5.0e9
        # Ctrl+Z: the wholesale base (f_01 back to what the chip held before the stage)
        r = c.post("/undo")
        assert _trig(r)["cellsReverted"]["live"] is True, _trig(r)
        assert _live_off(env) == 0.08 and _live_f01(env) == 5.3e9
        c.post("/redo"); c.post("/redo")
        assert _live_off(env) == 0.55 and _live_f01(env) == 5.0e9

    def test_nan_leaves_are_not_phantom_changes(self, env):
        """sweep F7: `nan != nan` made every NaN leaf a phantom wholesale
        entry, and the drift check then refused every wholesale undo."""
        c = env["client"]
        hm = env["app"].config["history_manager"]
        ctx = _ctx(env)
        with ctx["store"]._lock:
            ctx["modifier"].create_subtree("qubits.qA1.T2echo", float("nan"))
        _apply(c)
        hm.check_and_snapshot(ctx["path"], "manual", force=True)          # snapshot with the NaN
        ts = self._snapshot_ts(env)
        _edit(c, 0.10); _apply(c)
        assert c.post(f"/state-history/{ts}/stage").status_code == 200
        _apply(c)
        units, _ = undo_journal.load_state(_sidecar(env))
        paths = {e["path"] for e in units[-1]["entries"]}
        assert paths == {"qubits.qA1.z.joint_offset"}, paths                 # no NaN phantom
        r = c.post("/undo")
        assert _trig(r)["cellsReverted"]["live"] is True and _live_off(env) == 0.10

    def test_a_large_wholesale_undo_ships_no_header_entries(self, env, monkeypatch):
        """sweep F4: the walk response rides the HX-Trigger HEADER; past the
        project's header cap the entries are dropped and `structural` makes
        the client resync wholesale -- Chrome rejected the whole response
        otherwise, AFTER the chip was written."""
        c = env["client"]
        monkeypatch.setattr(routes_mod, "_HEADER_PATCH_CAP", 1)
        hm = env["app"].config["history_manager"]
        hm.check_and_snapshot(_ctx(env)["path"], "manual", force=True)
        ts = self._snapshot_ts(env)
        _edit(c, 0.10); _apply(c)
        _edit(c, 5.3e9, dot_path="qubits.qA1.f_01"); _apply(c)
        c.post(f"/state-history/{ts}/stage"); _apply(c)                  # a 2-entry wholesale unit
        r = c.post("/undo")
        t = _trig(r)["cellsReverted"]
        assert t["live"] is True and t["entries"] == [] and t["structural"] is True and t["n_entries"] == 2
        assert _live_off(env) == 0.10 and _live_f01(env) == 5.3e9
        r = c.post("/redo")
        t = _trig(r)["cellsReverted"]
        assert t["live"] is True and t["entries"] == [] and t["structural"] is True

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
        # pretend another live SM process applied it (a REGISTERED peer, sweep F6)
        ctx = _ctx(env)
        ctx["undo_units"][-1]["meta"]["owner_pid"] = 424242
        from types import SimpleNamespace
        from quam_state_manager.core import instances
        monkeypatch.setattr(instances, "peers", lambda *a, **k: [SimpleNamespace(pid=424242)])
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
        monkeypatch.setattr(instances, "peers", lambda *a, **k: [])      # no SM peer registered
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
        from types import SimpleNamespace
        from quam_state_manager.core import instances
        monkeypatch.setattr(instances, "peers", lambda *a, **k: [SimpleNamespace(pid=424242)])
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

        def spy(ctx, **kw):
            seen["held"] = store._lock._is_owned()
            return orig(ctx, **kw)
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

    def test_the_tray_x_on_a_staged_step_moves_the_cursor_back(self, env):
        """sweep F2: ✕ on the (last) entry of a staged journal step un-stages
        it -- the unit is back in effect, so the cursor must move back up, or
        the next LIVE undo persists a cursor under a unit still on the chip
        and the next save truncates that unit away."""
        c = env["client"]
        _set_setting(env, False)
        _edit(c, 0.10); _apply(c)
        _edit(c, 0.12); _apply(c)
        c.post("/undo")                                        # staged: 0.12 → 0.10, cursor 1
        ctx = _ctx(env)
        assert ctx["undo_cursor"] == 1 and len(ctx["store"].change_log) == 1
        r = c.post("/discard", data={"index": "0", "expect_path": "qubits.qA1.z.joint_offset"})
        assert r.status_code == 200
        assert not ctx["store"].change_log and _work_off(env) == 0.12
        assert ctx["undo_cursor"] == 2, "the ✕ un-staged the step: the unit is in effect again"
        _set_setting(env, True)
        c.post("/undo")                                        # live: 0.12 → 0.10, persisted 1
        assert _live_off(env) == 0.10 and undo_journal.load_state(_sidecar(env))[1] == 1
        _edit(c, 0.30); _apply(c)
        units, _ = undo_journal.load_state(_sidecar(env))
        assert [u["entries"][0]["new"] for u in units] == [0.10, 0.30]   # u1 kept, u2 (undone) discarded

    def test_a_failed_save_leaves_no_phantom_edits(self, env, monkeypatch):
        """sweep F3: when the door's own save raises, the staged step is
        STILL in the log; the rollback must pop it, not append inverses."""
        c = env["client"]
        _edit(c, 0.10); _apply(c)
        ctx = _ctx(env)
        saver = ctx["saver"]
        calls = {"n": 0}
        real_save = saver.save

        def failing_save(*a, **k):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError(13, "locked")
            return real_save(*a, **k)
        monkeypatch.setattr(saver, "save", failing_save)
        r = c.post("/undo")
        t = _trig(r)["cellsReverted"]
        assert t["message"].startswith("Not undone") and "Save failed" in t["message"]
        assert not ctx["store"].change_log, "no phantom entries in the tray"
        assert _work_off(env) == 0.10 and _live_off(env) == 0.10
        assert not ctx.get("pending_reapply") and not ctx["working_dirty"]
        assert ctx["undo_cursor"] == 1

    def test_a_concurrent_edit_never_rides_a_walk_step(self, env, monkeypatch):
        """sweep F5: an edit landing in the shared log between the staging and
        the door must not reach the chip under this keypress."""
        c = env["client"]
        _edit(c, 0.10); _apply(c)
        ctx = _ctx(env)
        real = routes_mod._flush_walk_step_live

        def flush_with_intruder(ctx_, **kw):
            # window B's edit lands after the staging lock was released and
            # before the walk step reaches the door
            with ctx_["store"]._lock:
                ctx_["modifier"].set_value("qubits.qA1.f_01", 9.9e9)
            return real(ctx_, **kw)
        monkeypatch.setattr(routes_mod, "_flush_walk_step_live", flush_with_intruder)
        r = c.post("/undo")
        t = _trig(r)["cellsReverted"]
        assert t["message"].startswith("Not undone") and "another window" in t["message"]
        assert _live_off(env) == 0.10 and _live_f01(env) == 5.0e9          # nothing reached the chip
        # the intruder's edit is still in the tray for review; our step is gone
        paths = [e.dot_path for e in ctx["store"].change_log]
        assert paths == ["qubits.qA1.f_01"]
        assert ctx["undo_cursor"] == 1

    def test_a_pointer_relink_redoes(self, env):
        """sweep F8: the redo CAS compared the pointer TARGET's value with the
        unit's old (the pointer string) and refused every pointer redo."""
        c = env["client"]
        ctx = _ctx(env)
        with ctx["store"]._lock:
            ctx["modifier"].create_subtree("qubits.qA1.xy.ops.x90", {"amp": 0.1})
            ctx["modifier"].create_subtree("qubits.qA1.xy.ops.y90", {"amp": "#../x90/amp"})
        _apply(c)
        # re-link the pointer LEAF itself (what the Json Tree's pointer edit
        # does -- the modifier writes the raw leaf and never chases it; the
        # grid's edit-batch would instead write THROUGH the alias to its target)
        with ctx["store"]._lock:
            ctx["modifier"].set_value("qubits.qA1.xy.ops.y90.amp", "#../x180/amp", coerce=False)
        _apply(c)
        units, _ = undo_journal.load_state(_sidecar(env))
        assert units[-1]["entries"][0]["path"] == "qubits.qA1.xy.ops.y90.amp"
        assert units[-1]["entries"][0]["old"] == "#../x90/amp"

        def live_ptr():
            return json.loads((env["live"] / "state.json").read_text(encoding="utf-8"))["qubits"]["qA1"]["xy"]["ops"]["y90"]["amp"]
        assert live_ptr() == "#../x180/amp"
        r = c.post("/undo")
        assert _trig(r)["cellsReverted"]["live"] is True, _trig(r)
        assert live_ptr() == "#../x90/amp"
        r = c.post("/redo")
        assert _trig(r)["cellsReverted"].get("live") is True, _trig(r)
        assert live_ptr() == "#../x180/amp"

    def test_an_off_mode_redo_drops_the_live_frame_and_reaches_beneath(self, env):
        """F-REDOJAM (final review): a jrn_live redo frame cannot be honoured in
        OFF mode (it would write live), and re-pushing it -- a PERMANENT refusal
        that never clears on its own -- JAMMED the whole stack: every
        Ctrl+Shift+Z met the same OFF refusal and an ordinary in-memory frame
        BENEATH it was unreachable forever (the only escapes were turning the
        setting back ON, which writes the chip -- the very thing the user turned
        off -- or a new edit, which discards the stack). The OFF press now DROPS
        the live frame, so the redo order stays correct and what is under it is
        reached. (A TRANSIENT refusal -- the chip moved -- still keeps its frame;
        see test_a_transient_redo_refusal_keeps_its_frame_and_unblocks.)"""
        c = env["client"]
        # build [ordinary(B), jrn_live(A)] with NO edit between the two presses,
        # so the ordinary frame is not discarded before the live one is pushed.
        _edit(c, 0.30); _apply(c)                              # A: applied -> journal unit
        _edit(c, 0.44)                                         # B: unapplied -> tray
        c.post("/undo")                                        # in-memory undo of B -> ordinary frame
        c.post("/undo")                                        # live undo of A -> jrn_live frame ON TOP
        stack = [f.get("kind") for f in _ctx(env)["redo_stack"]]
        assert stack[-1] == "jrn_live" and any(k is None for k in stack), stack
        _set_setting(env, False)
        r1 = c.post("/redo")                                   # drops the jrn_live frame, no jam
        assert _trig(r1)["cellsReverted"].get("live") is not True
        assert not [f for f in _ctx(env)["redo_stack"] if f.get("kind") == "jrn_live"], \
            "the OFF press dropped the live frame instead of re-pushing it"
        c.post("/redo")                                        # reaches the ordinary frame beneath
        assert _work_off(env) == 0.44, \
            "the ordinary frame under the dropped jrn_live is reachable in OFF mode"

    def test_a_recycled_pid_is_not_a_foreign_window(self, env, monkeypatch):
        """sweep F6: `pid_alive` is an existence probe; after a restart any
        process Windows handed the old pid would have made the user's own
        history "another window's". Only a registered SM peer is foreign."""
        c = env["client"]
        _edit(c, 0.10); _apply(c)
        ctx = _ctx(env)
        ctx["undo_units"][-1]["meta"]["owner_pid"] = 424242
        from quam_state_manager.core import instances
        monkeypatch.setattr(instances, "pid_alive", lambda pid: True)      # alive, but not SM
        monkeypatch.setattr(instances, "peers", lambda *a, **k: [])
        r = c.post("/undo")
        assert _trig(r)["cellsReverted"]["live"] is True and _live_off(env) == 0.08

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


# ======================================================================
# D. the second /code-review round (docs/160 §5d)
# ======================================================================

class TestReviewRound2:
    def _snapshot_ts(self, env):
        hm = env["app"].config["history_manager"]
        snaps = hm.list_snapshots(_ctx(env)["path"])
        assert snaps, "no snapshot to stage"
        s = snaps[0]
        return s.timestamp if hasattr(s, "timestamp") else s["timestamp"]

    def test_a_transient_redo_refusal_keeps_its_frame_and_unblocks(self, env):
        """F1, the TRANSIENT case (final review): an environmental refusal that
        WILL clear on its own -- the live chip drifted, a locked file -- keeps
        its frame so the retry is THIS step, never an older one out of order;
        the frame must not multiply, and once the drift is resolved the step
        lands. (The setting being OFF is a PERMANENT refusal handled by dropping
        the frame -- F-REDOJAM -- so this pin uses a chip-drift refusal, which is
        what the original OFF-based pin should have used to exercise the retry.)"""
        c = env["client"]
        _edit(c, 0.10); _apply(c)                         # chip 0.10, journal unit
        c.post("/undo")                                   # live undo -> chip 0.08, jrn_live frame
        assert _live_off(env) == 0.08
        # the chip drifts out-of-band -> the redo conflicts (the setting stays ON)
        st = json.loads((env["live"] / "state.json").read_text(encoding="utf-8"))
        st["qubits"]["qA1"]["z"]["joint_offset"] = 0.99
        (env["live"] / "state.json").write_text(json.dumps(st), encoding="utf-8")
        for _ in range(3):
            r = c.post("/redo")
            assert "changed since" in _trig(r)["cellsReverted"]["message"]
            assert _live_off(env) == 0.99, "a refused redo writes nothing"
        frames = [f for f in _ctx(env)["redo_stack"] if f.get("kind") == "jrn_live"]
        assert len(frames) == 1, "the retried frame must not multiply"
        # resolve the drift and pull; the step then lands
        st["qubits"]["qA1"]["z"]["joint_offset"] = 0.08
        (env["live"] / "state.json").write_text(json.dumps(st), encoding="utf-8")
        c.post("/state/sync")
        c.post("/redo")
        assert _live_off(env) == 0.10, "the step is redone once the refusal is gone"

    def test_a_skipped_unit_keeps_the_walk_and_the_stack_in_step(self, env):
        """F2: the empty/too-large skip moved the cursor with NO redo frame, so
        the next redo popped a frame whose index no longer named the cursor and
        dead-ended -- the unit UNDER it could never be redone again."""
        c = env["client"]
        _edit(c, 0.10); _apply(c)                          # u1: 0.08 -> 0.10
        _edit(c, 0.12); _apply(c)                          # u3: 0.10 -> 0.12
        sc = _sidecar(env)
        # an unwalkable unit BETWEEN them (what a too-large wholesale load
        # leaves behind), so the walk has to step over one
        undo_journal.insert_units(sc, [{"id": "skipme", "ts": 1.0, "entries": [],
                                        "meta": {"owner_pid": os.getpid()}}],
                                  before_tail=1)
        os.utime(sc, (10 ** 9, 10 ** 9))
        ctx = _ctx(env)
        with env["app"].app_context():
            routes_mod._journal_sync(ctx)
        assert [u["id"] for u in ctx["undo_units"]][1] == "skipme" and ctx["undo_cursor"] == 3
        c.post("/undo")                                    # u3, live
        assert _live_off(env) == 0.10
        r = c.post("/undo")                                # the empty one, skipped
        assert "recorded no values" in _trig(r)["cellsReverted"]["message"]
        c.post("/undo")                                    # u1, live
        assert _live_off(env) == 0.08 and ctx["undo_cursor"] == 0
        c.post("/redo")                                    # u1 forward
        assert _live_off(env) == 0.10
        r = c.post("/redo")                                # the skip, walked back up
        assert "skipped an empty step" in _trig(r)["cellsReverted"]["message"]
        c.post("/redo")                                    # u3 forward -- reachable again
        assert _live_off(env) == 0.12

    def test_a_re_staged_step_puts_the_cursor_back(self, env):
        """F3: the X that un-staged a journal step moved the cursor UP; the redo
        that re-stages it must move the cursor back DOWN, or the next Ctrl+Z
        stages that same unit's inverse a second time on top of itself."""
        c = env["client"]
        _set_setting(env, False)
        _edit(c, 0.10); _apply(c)
        _edit(c, 0.12); _apply(c)
        c.post("/undo")                                    # staged u2's inverse, cursor 1
        ctx = _ctx(env)
        assert ctx["undo_cursor"] == 1
        c.post("/discard", data={"index": "0", "expect_path": "qubits.qA1.z.joint_offset"})
        assert ctx["undo_cursor"] == 2
        c.post("/redo")                                    # re-stage it
        assert ctx["undo_cursor"] == 1, "the re-staged step consumes its unit again"
        assert len(ctx["store"].change_log) == 1
        c.post("/undo")                                    # the NEXT unit, not the same one
        assert ctx["undo_cursor"] == 0 and _work_off(env) == 0.08

    def test_a_walk_step_keeps_the_applys_revert_anchor(self, env):
        """F6: the "Revert last apply" button must keep meaning 'put the chip
        back to before the apply I pressed'. Re-anchoring it on every Ctrl+Z
        turned it into 'redo what I just undid', with no wording change.
        F5: and one press takes at most one history snapshot, not two."""
        c = env["client"]
        _edit(c, 0.10); _apply(c)
        ctx = _ctx(env)
        anchor = ctx["last_apply"]["pre_ts"]
        hm = env["app"].config["history_manager"]
        before = len(hm.list_snapshots(ctx["path"]))
        c.post("/undo")
        assert _live_off(env) == 0.08
        assert ctx["last_apply"]["pre_ts"] == anchor, "the apply's own anchor stands"
        after = len(hm.list_snapshots(ctx["path"]))
        assert after - before <= 1, f"a walk press took {after - before} snapshots"

    def test_a_burst_press_is_told_where_it_stopped(self, env):
        """F15: one press walks one unit, so a coalesced ?n=k must come back
        saying it stopped at the journal boundary -- else the client drops the
        remaining k-1 presses silently."""
        c = env["client"]
        _edit(c, 0.10); _apply(c)
        _edit(c, 0.12); _apply(c)
        t = _trig(c.post("/undo?n=3"))["cellsReverted"]
        assert (t["requested"], t["consumed"], t["stopped"]) == (3, 1, "journal")
        t = _trig(c.post("/redo?n=2"))["cellsReverted"]
        assert (t["requested"], t["consumed"], t["stopped"]) == (2, 1, "journal")
        assert _live_off(env) == 0.12

    def test_a_deleted_subtree_is_recorded_even_around_an_edited_leaf(self, env):
        """F8: a subtree the apply deleted is ONE entry even when an edit unit
        names a path inside it -- dropping it left the subtree unrecoverable.
        F7: and the owning file comes from the store's own rule, not a copy."""
        ctx = _ctx(env)
        store = ctx["store"]
        before = json.loads(json.dumps(store.merged))
        before["network"] = dict(before.get("network") or {}, host="9.9.9.9")
        with env["app"].app_context():
            ctx["modifier"].delete_subtree("qubits.qA1")
            unit = routes_mod._wholesale_unit(before, ctx, "t",
                                              exclude={"qubits.qA1.f_01"})
        paths = {e["path"]: e for e in (unit or {}).get("entries") or []}
        assert "qubits.qA1" in paths and paths["qubits.qA1"]["deleted"] is True
        assert paths["qubits.qA1"]["old"]["f_01"] == 5.0e9, "the whole subtree is the old value"
        assert paths["network.host"]["source_file"] == "wiring"
        assert paths["qubits.qA1"]["source_file"] == "state"

    def test_live_redo_frames_are_capped(self, env):
        """F12: they were appended raw, bypassing the cap every other frame
        obeys -- a 200-unit walk kept 200 of them for the life of the ctx."""
        ctx = _ctx(env)
        for i in range(routes_mod._REDO_MAX_FRAMES + 25):
            routes_mod._push_jrn_live_frame(ctx, ctx["store"], i, f"u{i}")
        frames = ctx["redo_stack"]
        assert len(frames) == routes_mod._REDO_MAX_FRAMES
        assert frames[-1]["unit_id"] == f"u{routes_mod._REDO_MAX_FRAMES + 24}"


class TestReplayKeepsTheGesture:
    """Final review, R-A1 — found in a real browser on the customer's own 20Q chip.

    The grid's ⚡ "Apply to live now" does not go through /state/apply-to-live: it
    PULLS the live chip, re-applies the pending edits on top, and pushes
    (`doStateSync('apply')`). The re-apply wrote every path with no group id, so
    one user gesture that touched several leaves — the coupled f_01 ↔
    xy.RF_frequency pair, a multi-cell row, an FSP change bundled with its
    compensating amplitudes (docs/126) — came back as N separate journal units.
    Before docs/160 that only cost extra Ctrl+Z presses in the tray; now each
    press is its own LIVE WRITE, so the chip sat holding half a gesture:
    f_01 reverted while RF_frequency still held the new value.
    """

    def test_one_gesture_stays_one_unit_through_pull_and_reapply(self, env):
        c = env["client"]
        r = c.post("/field/edit-batch", json={"updates": [
            {"dot_path": "qubits.qA1.f_01", "value": "5100000000"},
            {"dot_path": "qubits.qA1.z.joint_offset", "value": "0.11"},
        ], "expect_chip": ""})
        assert r.status_code == 200 and r.get_json()["ok"], r.data
        r = c.post("/state/sync?mode=apply")          # the ⚡ button's route
        assert r.status_code == 200 and (r.get_json() or {}).get("status") == "ok"
        assert (_live_f01(env), _live_off(env)) == (5.1e9, 0.11)

        units, cursor = undo_journal.load_state(_sidecar(env))
        assert len(units) == 1 and cursor == 1, \
            f"one gesture must be one unit, got {[[e['path'] for e in u['entries']] for u in units]}"
        assert {e["path"] for e in units[0]["entries"]} == {
            "qubits.qA1.f_01", "qubits.qA1.z.joint_offset"}

        c.post("/undo")
        assert (_live_f01(env), _live_off(env)) == (5.0e9, 0.08), \
            "one Ctrl+Z must take the WHOLE gesture off the chip, never half of it"

    def test_separate_gestures_still_undo_one_at_a_time(self, env):
        """…and the fix must not over-group: two gestures re-applied together
        stay two units (one press = one user action, docs/107)."""
        c = env["client"]
        _edit(c, 0.10)                                    # gesture 1 (ungrouped)
        r = c.post("/field/edit-batch", json={
            "updates": [{"dot_path": "qubits.qA1.f_01", "value": "5100000000"}],
            "expect_chip": ""})
        assert r.status_code == 200                       # gesture 2 (ungrouped)
        assert c.post("/state/sync?mode=apply").status_code == 200
        units, _ = undo_journal.load_state(_sidecar(env))
        assert len(units) == 2, [[e["path"] for e in u["entries"]] for u in units]
        c.post("/undo")
        assert _live_f01(env) == 5.0e9 and _live_off(env) == 0.10, "only the newest gesture"


class TestJournalInsertCursor:
    def test_an_insert_truncates_the_redo_branch_like_an_append(self, tmp_path):
        """F9: `insert_units` read the persisted cursor and threw it away,
        writing the tip -- resurrecting units a live undo had walked off the
        chip (the applied log then reports them in effect and offers an X that
        409s). The append one line below always handled this."""
        p = tmp_path / "j.json"

        def _u(i):
            return {"id": f"u{i}", "ts": 1.0, "entries": [
                {"path": "a.b", "old": i, "new": i + 1, "source_file": "state",
                 "created": False, "deleted": False}], "meta": {}}

        undo_journal.append_units(p, [_u(1), _u(2), _u(3)])
        undo_journal.save_cursor(p, 1)                      # two units undone live
        units = undo_journal.insert_units(p, [_u(9)], before_tail=0)
        got, cursor = undo_journal.load_state(p)
        assert [u["id"] for u in got] == ["u1", "u9"], f"got {[u['id'] for u in got]}"
        assert cursor == len(got) == 2 and got == units


class TestFinalReviewWalk:
    """Pre-customer review: three live-walk defects reproduced on the real
    routes and fixed (docs/160 §5e2)."""

    def test_a_nan_undo_writes_live_and_does_not_blame_drift(self, env):
        """F-NAN: a leaf applied as NaN (an autofit/wholesale write, not through
        the finite-checked edit door) could never be undone live -- the walk's
        drift counter compared with ``!=`` and ``nan != nan`` is True, so every
        NaN step counted phantom drift and stayed staged with a false 'the value
        had moved' toast. The drift check now uses the NaN-aware ``_differs``."""
        c = env["client"]
        # inject NaN the way a wholesale/autofit write does (coerce=False, no
        # finite gate) so the journal unit's recorded `new` is NaN
        _ctx(env)["modifier"].set_value("qubits.qA1.z.joint_offset",
                                        float("nan"), coerce=False)
        c.post("/state/apply-to-live")
        import math
        assert math.isnan(_live_off(env))
        r = c.post("/undo")
        m = _trig(r)["cellsReverted"]
        assert m.get("live") is True, m.get("message")
        assert "had moved" not in (m.get("message") or ""), m.get("message")
        assert _live_off(env) == 0.08, "the NaN step wrote the old value to the chip"

    def test_a_refused_walk_step_refreshes_the_drift_banner(self, env):
        """F-DRIFTBANNER: when the chip drifted out-of-band, the refused walk
        step's toast told the user to 'take the live changes (drift banner)' but
        the response never escalated liveDriftChanged, so no banner appeared and
        the tray still said Synced until a full re-render. The refusal now
        refreshes the drift/version surfaces like the success path does."""
        c = env["client"]
        _edit(c, 0.20); _apply(c)
        st = json.loads((env["live"] / "state.json").read_text(encoding="utf-8"))
        st["qubits"]["qA1"]["z"]["joint_offset"] = 0.99
        (env["live"] / "state.json").write_text(json.dumps(st), encoding="utf-8")
        r = c.post("/undo")
        trig = _trig(r)
        assert trig.get("liveDriftChanged") is True, "the refusal must refresh the drift banner"
        assert _live_off(env) == 0.99, "the chip was correctly left untouched"

    def test_a_redo_over_a_skipped_unit_reports_the_consumed_step(self, env, monkeypatch):
        """F-BURST-SKIP: redoing over a skipped (too-large/empty) unit moves the
        cursor -- a step IS consumed -- but the response reported
        consumed:0/exhausted, so a coalesced Ctrl+Shift+Z dropped its remaining
        presses. The skip now reports consumed:1/journal like the down side."""
        monkeypatch.setattr(routes_mod, "_WHOLESALE_UNIT_CAP", 0)
        c = env["client"]
        hm = env["app"].config["history_manager"]
        hm.check_and_snapshot(_ctx(env)["path"], "manual", force=True)
        snaps = hm.list_snapshots(_ctx(env)["path"])
        ts = snaps[0].timestamp if hasattr(snaps[0], "timestamp") else snaps[0]["timestamp"]
        _edit(c, 0.20); _apply(c)                       # move the chip so the stage is a real diff
        assert c.post(f"/state-history/{ts}/stage").status_code == 200
        c.post("/state/apply-to-live")                  # -> a too_large wholesale unit
        cur0 = int(_ctx(env)["undo_cursor"] or 0)
        c.post("/undo")                                 # skip the too_large unit (cursor moves)
        cur1 = int(_ctx(env)["undo_cursor"] or 0)
        assert cur1 != cur0, "the down walk skipped the too-large unit"
        r = c.post("/redo?n=3")                         # a coalesced burst lands on the skip
        cur2 = int(_ctx(env)["undo_cursor"] or 0)
        m = _trig(r)["cellsReverted"]
        assert cur2 != cur1, "the redo consumed the skipped step"
        assert m.get("consumed") == 1 and m.get("stopped") == "journal", \
            f"the skip must report the consumed step, got {m.get('consumed')}/{m.get('stopped')}"
