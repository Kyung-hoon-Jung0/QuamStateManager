"""Cross-save undo journal + redo + Discard all (docs/107).

Pins:
  - segmentation == iterated undo_group (incl. a rename's create+delete unit);
  - the sidecar round-trips a restart, cursor at tip;
  - the editor walk: edit→save→Ctrl+Z STAGES the inverse into the tray under a
    ``jrn:`` gid (live untouched — the SM covenant), Z again walks deeper,
    Ctrl+Shift+Z un-stages LIFO, then replays the redo stack;
  - a new edit (any foreign mutation_seq move) invalidates redo silently;
  - inverse staging iterates entries in REVERSE (rename: create old before
    deleting new);
  - drift is reported, not blocking; partial failure is atomic (409, log and
    cursor unchanged);
  - archive => silent no-op, no sidecar; cursor==0 == today's no-op tray;
  - saving staged journal steps appends them as ordinary units (emacs-style
    walk continues);
  - Discard all = mass-undo, each group redo-able in order; the single-✕ is
    redo-able and carries NO hx-confirm.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from quam_state_manager.core import undo_journal
from quam_state_manager.core.loader import QuamStore
from quam_state_manager.core.modifier import Modifier
from quam_state_manager.web import routes as routes_mod
from quam_state_manager.web.app import create_app

_WIRING = {"network": {"host": "1.1.1.1", "cluster_name": "C1"}}


def _state(off_a=0.08):
    return {
        "qubits": {"qA1": {"id": "qA1", "f_01": 5.0e9,
                           "z": {"joint_offset": off_a},
                           "xy": {"ops": {"x180": {"amp": 0.2}}}}},
        "qubit_pairs": {},
        "active_qubit_names": ["qA1"],
    }


def _write_chip(folder: Path, state: dict, wiring: dict | None = None):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(wiring or _WIRING),
                                        encoding="utf-8")


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


def _off(env):
    return _ctx(env)["store"].merged["qubits"]["qA1"]["z"]["joint_offset"]


def _f01(env):
    return _ctx(env)["store"].merged["qubits"]["qA1"]["f_01"]


def _trigger(r) -> dict:
    raw = r.headers.get("HX-Trigger")
    return json.loads(raw) if raw else {}


def _sidecars(env) -> list[Path]:
    return sorted((Path(env["app"].instance_path) / "working_state")
                  .glob("*.undo_journal.json"))


# ======================================================================
# Core: segmentation + inverse ordering
# ======================================================================

class TestSegmentation:
    def _store(self, tmp_path):
        folder = tmp_path / "seg_chip"
        _write_chip(folder, _state())
        store = QuamStore(str(folder))
        return store, Modifier(store)

    def test_segmentation_equals_iterated_undo_group(self, tmp_path):
        """The load-bearing property: units == exactly what repeated Ctrl+Z
        (undo_group) would pop, in reverse — mixed singles, batches, renames."""
        store, mod = self._store(tmp_path)
        mod.set_value("qubits.qA1.z.joint_offset", 0.09)
        mod.batch_set({"qubits.qA1.f_01": 5.1e9,
                       "qubits.qA1.z.joint_offset": 0.10})
        mod.rename_subtree("qubits.qA1.xy.ops.x180", "qubits.qA1.xy.ops.x180_v2")
        mod.set_value("qubits.qA1.f_01", 5.2e9)

        log = list(store.change_log)
        units = undo_journal.segment_change_log(log)
        assert [len(u) for u in units] == [1, 2, 2, 1]

        popped = []
        while store.change_log:
            g = mod.undo_group()
            # undo_group returns newest-first inside the group; restore
            # chronological order for comparison with the unit's entries.
            popped.append(list(reversed(g)))
        assert popped == list(reversed(units))

    def test_inverse_ops_reverse_order_for_rename(self, tmp_path):
        """A rename unit's inverse must re-create the OLD subtree BEFORE
        deleting the new one (LIFO mirror of undo_group) — and replaying the
        ops through a real modifier restores the pre-rename state exactly."""
        store, mod = self._store(tmp_path)
        before = json.loads(json.dumps(store.merged["qubits"]["qA1"]["xy"]))
        mod.rename_subtree("qubits.qA1.xy.ops.x180", "qubits.qA1.xy.ops.x180_v2")
        unit = undo_journal.make_unit(list(store.change_log))
        ops = undo_journal.inverse_ops(unit)
        assert [op[0] for op in ops] == ["create", "delete"]
        assert ops[0][1].endswith("x180")      # create the old name first
        assert ops[1][1].endswith("x180_v2")   # then delete the new name

        store.change_log.clear()   # simulate the save boundary
        for op, path, value, _src in ops:
            if op == "create":
                mod.create_subtree(path, value)
            elif op == "delete":
                mod.delete_subtree(path)
            else:
                mod.set_value(path, value, coerce=False)
        assert store.merged["qubits"]["qA1"]["xy"] == before


# ======================================================================
# Sidecar + restart
# ======================================================================

class TestSidecarPersistence:
    def test_save_writes_sidecar_and_cursor_tip(self, env):
        c = env["client"]
        _edit(c, 0.10)
        assert c.post("/save").status_code == 200
        files = _sidecars(env)
        assert len(files) == 1
        doc = json.loads(files[0].read_text(encoding="utf-8"))
        assert len(doc["units"]) == 1
        assert doc["units"][0]["entries"][0]["path"] == "qubits.qA1.z.joint_offset"
        assert doc["units"][0]["entries"][0]["old"] == 0.08
        assert doc["units"][0]["entries"][0]["new"] == 0.10
        ctx = _ctx(env)
        assert ctx["undo_cursor"] == 1 == len(ctx["undo_units"])

    def test_journal_survives_restart(self, env):
        """LRU eviction / app restart: units reload from the sidecar, cursor
        at tip, and Ctrl+Z stages across the boundary."""
        c = env["client"]
        _edit(c, 0.10)
        assert c.post("/save").status_code == 200
        # Simulate a restart: drop every in-RAM context, re-activate.
        with routes_mod._quam_cache_lock:
            routes_mod._quam_cache.clear()
        env["app"].config["contexts"].clear()
        assert c.post("/load", data={"folder": str(env["live"])}).status_code in (200, 302)
        ctx = _ctx(env)
        assert len(ctx["undo_units"]) == 1 and ctx["undo_cursor"] == 1
        r = c.post("/undo")
        assert "staged" in _trigger(r)["cellsReverted"]["message"]
        assert _off(env) == 0.08


# ======================================================================
# The editor walk (Z / Shift+Z)
# ======================================================================

class TestEditorWalk:
    def test_z_stages_inverse_across_save(self, env):
        c = env["client"]
        _edit(c, 0.10)
        assert c.post("/save").status_code == 200
        store = _ctx(env)["store"]
        assert not store.change_log            # the save boundary
        r = c.post("/undo")
        trig = _trigger(r)["cellsReverted"]
        assert "staged" in trig["message"]
        assert trig["entries"][0]["dot_path"] == "qubits.qA1.z.joint_offset"
        # STAGED, not applied: the working value moved, the tray holds it,
        # and the LIVE file is untouched (the covenant).
        assert _off(env) == 0.08
        assert len(store.change_log) == 1
        assert store.change_log[-1].group_id.startswith("jrn:")
        live = json.loads((env["live"] / "state.json").read_text(encoding="utf-8"))
        assert live["qubits"]["qA1"]["z"]["joint_offset"] == 0.08  # seed value

    def test_walk_deeper_then_unstage_then_replay(self, env):
        c = env["client"]
        _edit(c, 0.10); assert c.post("/save").status_code == 200
        _edit(c, 0.12); assert c.post("/save").status_code == 200
        ctx = _ctx(env)
        assert ctx["undo_cursor"] == 2

        c.post("/undo")                        # stage unit2⁻¹: 0.12 → 0.10
        assert _off(env) == 0.10 and ctx["undo_cursor"] == 1
        c.post("/undo")                        # deeper: 0.10 → 0.08
        assert _off(env) == 0.08 and ctx["undo_cursor"] == 0
        r = c.post("/undo")                    # exhausted → silent no-op
        assert r.status_code == 200 and "HX-Trigger" not in r.headers
        assert _off(env) == 0.08 and ctx["undo_cursor"] == 0

        r = c.post("/redo")                    # un-stage LIFO: back to 0.10
        assert "un-staged" in _trigger(r)["cellsReverted"]["message"]
        assert _off(env) == 0.10 and ctx["undo_cursor"] == 1
        c.post("/redo")                        # un-stage: back to 0.12
        assert _off(env) == 0.12 and ctx["undo_cursor"] == 2
        assert not ctx["store"].change_log
        r = c.post("/redo")                    # nothing left → silent no-op
        assert r.status_code == 200 and "HX-Trigger" not in r.headers

    def test_ordinary_undo_then_redo(self, env):
        c = env["client"]
        _edit(c, 0.09)
        r = c.post("/undo")                    # ordinary pop, byte-identical path
        assert "Undone" in _trigger(r)["cellsReverted"]["message"]
        assert _off(env) == 0.08 and not _ctx(env)["store"].change_log
        r = c.post("/redo")                    # replay from the redo stack
        assert "Redone" in _trigger(r)["cellsReverted"]["message"]
        assert _off(env) == 0.09
        assert len(_ctx(env)["store"].change_log) == 1

    def test_undo_n_pops_k_actions_in_one_request(self, env):
        # docs/141 4e: a burst the client coalesced -- one request, k actions,
        # one response naming every reverted path (newest first)
        c = env["client"]
        for v in (0.09, 0.10, 0.11):
            _edit(c, v)
        r = c.post("/undo?n=2")
        trig = _trigger(r)["cellsReverted"]
        assert trig["message"].startswith("Undone: 2 actions, 2 changes")
        assert [e["dot_path"] for e in trig["entries"]] == ["qubits.qA1.z.joint_offset"] * 2
        assert _off(env) == 0.09 and len(_ctx(env)["store"].change_log) == 1
        r = c.post("/redo?n=2")                # the two frames come back, in order
        trig = _trigger(r)["cellsReverted"]
        assert trig["message"].startswith("Redone: 2 actions, 2 changes")
        assert _off(env) == 0.11 and len(_ctx(env)["store"].change_log) == 3
        # The client writes the entries IN ORDER, last one wins: a redo burst
        # over one path must therefore end with the NEWEST value (real-Chrome
        # 2026-08-28: newest-first left the oldest value in the cell)
        assert [e["old_value_str"] for e in trig["entries"]] == ["0.1", "0.11"]
        # more than there is: undoes what exists, says so, never errors
        r = c.post("/undo?n=50")
        assert _trigger(r)["cellsReverted"]["message"].startswith("Undone: 3 actions")
        assert _off(env) == 0.08 and not _ctx(env)["store"].change_log
        # n is clamped and non-numeric n is one press
        _edit(c, 0.2)
        r = c.post("/undo?n=zzz")
        assert "Undone:" in _trigger(r)["cellsReverted"]["message"] and _off(env) == 0.08

    def test_undo_burst_stops_at_the_journal_boundary(self, env):
        # a cross-save (jrn:) step is its own press, never a side effect of a burst
        c = env["client"]
        _edit(c, 0.09)
        c.post("/save")                        # journal unit; the log is empty again
        _edit(c, 0.10)
        r = c.post("/undo?n=5")
        trig = _trigger(r)["cellsReverted"]
        assert trig["message"].startswith("Undone: qubits.qA1.z.joint_offset")   # ONE ordinary action
        assert _off(env) == 0.09
        # ... and the response SAYS the burst stopped, so the client can
        # re-queue the four remaining presses (review of eaa0f05)
        assert (trig["requested"], trig["consumed"], trig["stopped"], trig["level"]) == (5, 1, "journal", "success")
        # a burst that runs out of log says "exhausted" (nothing to re-queue)
        _edit(c, 0.2)
        trig = _trigger(c.post("/undo?n=3"))["cellsReverted"]
        assert (trig["consumed"], trig["stopped"]) == (1, "journal")   # the saved unit is next: a boundary, not exhaustion
        c.post("/redo")
        _edit(c, 0.21)
        _ctx(env)["undo_units"] = []            # no journal: the log simply runs out
        trig = _trigger(c.post("/undo?n=3"))["cellsReverted"]
        assert trig["consumed"] >= 1 and trig["stopped"] in ("exhausted", "journal")
        gids = [e.group_id for e in _ctx(env)["store"].change_log]
        assert not any(isinstance(g, str) and g.startswith("jrn:") for g in gids), \
            "the burst did not walk into the journal"

    def test_new_edit_invalidates_redo(self, env):
        c = env["client"]
        _edit(c, 0.09)
        c.post("/undo")
        _edit(c, 0.095)                        # foreign mutation → fork
        r = c.post("/redo")
        assert r.status_code == 200 and "HX-Trigger" not in r.headers
        assert _off(env) == 0.095              # nothing clobbered

    def test_reload_invalidates_redo(self, env):
        c = env["client"]
        _edit(c, 0.09)
        c.post("/undo")
        _ctx(env)["store"].reload()            # seq bumps — dead timeline
        r = c.post("/redo")
        assert "HX-Trigger" not in r.headers
        assert _off(env) == 0.08

    def test_save_of_staged_steps_appends_units(self, env):
        """Emacs-style: saving staged journal steps journals THEM as ordinary
        units, so the history stays walkable (never a dead end)."""
        c = env["client"]
        _edit(c, 0.10); assert c.post("/save").status_code == 200
        _edit(c, 0.12); assert c.post("/save").status_code == 200
        c.post("/undo"); c.post("/undo")       # stage 0.10 then 0.08
        assert c.post("/save").status_code == 200
        ctx = _ctx(env)
        assert len(ctx["undo_units"]) == 4 and ctx["undo_cursor"] == 4
        assert not ctx["store"].change_log
        r = c.post("/undo")                    # inverse of the LAST jrn step
        assert "staged" in _trigger(r)["cellsReverted"]["message"]
        assert _off(env) == 0.10


# ======================================================================
# Honesty + atomicity + archives
# ======================================================================

class TestJournalStepHonesty:
    def test_drift_is_reported_not_blocking(self, env):
        c = env["client"]
        _edit(c, 0.10)
        assert c.post("/save").status_code == 200
        ctx = _ctx(env)
        # The world moved since the unit was recorded (out-of-band writer).
        ctx["undo_units"][-1]["entries"][0]["new"] = 999.0
        r = c.post("/undo")
        msg = _trigger(r)["cellsReverted"]["message"]
        assert "moved since" in msg
        assert _off(env) == 0.08               # staged anyway (covenant-safe)

    def test_partial_failure_is_atomic(self, env):
        """A unit whose inverse half-fails (create clobber — the key exists
        again) rolls back the staged prefix: 409, log and cursor unchanged."""
        c = env["client"]
        _edit(c, 0.10)
        assert c.post("/save").status_code == 200
        ctx = _ctx(env)
        # Hand-build a poison unit: [delete-entry, set-entry] → inverse ops
        # run [set (succeeds), create (clobbers — path still exists)].
        ctx["undo_units"].append({
            "id": "poison", "ts": 0.0, "entries": [
                {"path": "qubits.qA1.f_01", "old": 4.9e9, "new": None,
                 "source_file": "state", "created": False, "deleted": True},
                {"path": "qubits.qA1.z.joint_offset", "old": 0.05, "new": 0.10,
                 "source_file": "state", "created": False, "deleted": False},
            ],
        })
        ctx["undo_cursor"] = len(ctx["undo_units"])
        log_before = len(ctx["store"].change_log)
        r = c.post("/undo")
        assert r.status_code == 409
        assert b"nothing changed" in r.data
        assert len(ctx["store"].change_log) == log_before
        assert ctx["undo_cursor"] == len(ctx["undo_units"])
        assert _off(env) == 0.10               # the set was rolled back
        assert _f01(env) == 5.0e9

    def test_archive_is_silent_noop_and_never_writes_sidecar(self, env):
        c = env["client"]
        ctx = _ctx(env)
        ctx["origin"] = "dataset_archive"
        ctx["undo_units"] = [{"id": "x", "ts": 0.0, "entries": [
            {"path": "qubits.qA1.f_01", "old": 4.9e9, "new": 5.0e9,
             "source_file": "state", "created": False, "deleted": False}]}]
        ctx["undo_cursor"] = 1
        r = c.post("/undo")
        assert r.status_code == 200 and "HX-Trigger" not in r.headers
        assert _f01(env) == 5.0e9              # nothing staged
        assert not _sidecars(env)              # no sidecar ever created

    def test_cursor_zero_matches_todays_noop(self, env):
        """Empty log + empty journal == the pre-docs/107 no-op, byte-equal."""
        c = env["client"]
        r = c.post("/undo")
        baseline = c.get("/state/tray")
        assert r.status_code == 200
        assert "HX-Trigger" not in r.headers
        assert r.data == baseline.data


# ======================================================================
# Tray UX: ✕ no-confirm + Discard all
# ======================================================================

class TestTrayDiscard:
    def test_single_discard_is_redoable(self, env):
        c = env["client"]
        _edit(c, 0.09)
        r = c.post("/discard", data={"index": "0",
                                     "expect_path": "qubits.qA1.z.joint_offset"})
        assert r.status_code == 200
        assert _off(env) == 0.08
        r = c.post("/redo")
        assert "Redone" in _trigger(r)["cellsReverted"]["message"]
        assert _off(env) == 0.09

    def test_tray_has_no_confirm_and_has_discard_all(self, env):
        c = env["client"]
        _edit(c, 0.09)
        html = c.get("/state/tray").data.decode("utf-8")
        assert "hx-confirm" not in html
        assert "/discard_all" in html
        assert "Discard all" in html

    def test_discard_all_then_shift_z_restores_in_order(self, env):
        c = env["client"]
        _edit(c, 0.09)
        _edit(c, 5.1e9, dot_path="qubits.qA1.f_01")
        _edit(c, 0.10)
        r = c.post("/discard_all")
        trig = _trigger(r)["cellsReverted"]
        assert "Discarded all" in trig["message"]
        assert len(trig["entries"]) == 3
        assert _off(env) == 0.08 and _f01(env) == 5.0e9
        assert not _ctx(env)["store"].change_log

        c.post("/redo")                        # restores the FIRST edit first
        assert _off(env) == 0.09 and _f01(env) == 5.0e9
        c.post("/redo")
        assert _f01(env) == 5.1e9
        c.post("/redo")
        assert _off(env) == 0.10
        r = c.post("/redo")
        assert "HX-Trigger" not in r.headers   # stack drained — silent no-op

    def test_discard_all_unstages_journal_steps_and_restores_cursor(self, env):
        c = env["client"]
        _edit(c, 0.10); assert c.post("/save").status_code == 200
        c.post("/undo")                        # staged jrn step, cursor 0
        ctx = _ctx(env)
        assert ctx["undo_cursor"] == 0
        r = c.post("/discard_all")
        assert r.status_code == 200
        assert not ctx["store"].change_log
        assert ctx["undo_cursor"] == 1         # the un-consumed unit is back
        assert _off(env) == 0.10
        c.post("/redo")                        # re-stage the journal step
        assert _off(env) == 0.08
        assert ctx["undo_cursor"] == 0
        assert ctx["store"].change_log[-1].group_id.startswith("jrn:")

    def test_discard_all_empty_log_is_silent_noop(self, env):
        c = env["client"]
        r = c.post("/discard_all")
        assert r.status_code == 200 and "HX-Trigger" not in r.headers
