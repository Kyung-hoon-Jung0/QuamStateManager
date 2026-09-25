# -*- coding: utf-8 -*-
"""QA live-sync package -- the server halves (fix/qa-live-sync).

- F3: the tray ✕ (``/discard``) tells the grid whether the path is still
  pending (``still_pending``), so the repaint can drop the red box exactly when
  the path's LAST log entry went.
- F5: Column History follows a pointer ALIAS (``x180 == "#./x180_DragCosine"``)
  to the leaf it names before reading history -- the alias columns showed no
  history at all.
- liveedit-r2-05: the one-click ``/state/sync?mode=apply`` refuses a same-field
  collision (the chip moved a field the user also edited) when asked
  (``check_collisions=1``), names it, raises the banner, and is answered only
  by its own token ``ack_collision=1``.
- liveedit-r2-07: a declined Auto-Sync pull still answers 204, now carrying a
  ``liveConflict`` signal, and ``GET /state/diverged-banner`` renders the
  "choose which to keep" banner in place.
- liveedit-r2-09: the drift poll's ``edit_seq`` moves on a round trip that
  ends on the same change set, and every tray names the one it rendered at.
- liveedit-r2-17: the applied log lists only pushes that LANDED.
- F8: /undo, the journal step and /redo say per path whether it is still
  pending.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

import quam_state_manager.web.routes as routes_mod
from quam_state_manager.web.app import create_app

_WIRING = {"network": {"host": "1.1.1.1", "cluster_name": "C1"}}


def _state(f01=5.0e9, t1=2.0e-5, t2r=1.5e-6, amp=0.25):
    return {
        "qubits": {"qA1": {
            "id": "qA1", "f_01": f01, "T1": t1, "T2ramsey": t2r,
            "xy": {"operations": {
                "x180_DragCosine": {"amplitude": amp, "length": 40},
                "x180": "#./x180_DragCosine",
            }},
        }},
        "qubit_pairs": {},
        "active_qubit_names": ["qA1"],
    }


def _write_chip(folder: Path, state: dict, *, future=False):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")
    if future:        # an experiment saving over the chip after SM loaded it
        t = time.time() + 100
        os.utime(folder / "state.json", (t, t))


@pytest.fixture
def env(tmp_path):
    live = tmp_path / "chips" / "live"
    _write_chip(live, _state())
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    return {"app": app, "client": c, "live": live, "tmp": tmp_path}


def _ctx(env):
    with env["app"].app_context():
        return routes_mod._active_ctx()


def _live(env) -> dict:
    return json.loads((env["live"] / "state.json").read_text(encoding="utf-8"))


def _edit(env, path, value):
    r = env["client"].post("/field/edit", data={"dot_path": path, "value": str(value)})
    assert r.status_code == 200, r.data[:300]
    return r


def _trigger(resp) -> dict:
    return json.loads(resp.headers.get("HX-Trigger") or "{}")


# ── F3 ──────────────────────────────────────────────────────────────────────
class TestDiscardSaysWhetherThePathIsStillPending:
    def test_the_only_entry_for_a_path(self, env):
        _edit(env, "qubits.qA1.T1", "1.2e-5")
        r = env["client"].post("/discard", data={"index": "0",
                                                 "expect_path": "qubits.qA1.T1"})
        d = _trigger(r)["cellDiscarded"]
        assert d["dot_path"] == "qubits.qA1.T1"
        assert d["still_pending"] is False, d
        assert d["old_value_disp"], d          # what the grid repaints with

    def test_a_second_entry_for_the_same_path_remains(self, env):
        _edit(env, "qubits.qA1.T1", "1.2e-5")
        _edit(env, "qubits.qA1.T1", "1.3e-5")
        log = _ctx(env)["store"].change_log
        assert [e.dot_path for e in log] == ["qubits.qA1.T1", "qubits.qA1.T1"]
        r = env["client"].post("/discard", data={"index": "1",
                                                 "expect_path": "qubits.qA1.T1"})
        assert _trigger(r)["cellDiscarded"]["still_pending"] is True
        r = env["client"].post("/discard", data={"index": "0",
                                                 "expect_path": "qubits.qA1.T1"})
        assert _trigger(r)["cellDiscarded"]["still_pending"] is False


# ── F5 ──────────────────────────────────────────────────────────────────────
_ALIAS = "qubits.qA1.xy.operations.x180.amplitude"


def _seed_run(root: Path, run_id: int, state: dict) -> Path:
    date = "2026-12-30"
    run = root / date / f"#{run_id}_power_rabi_{run_id % 24:02d}0000"
    run.mkdir(parents=True)
    (run / "node.json").write_text(json.dumps({
        "metadata": {"name": "power_rabi", "status": "successful",
                     "run_start": f"{date}T01:00:00", "run_end": f"{date}T01:00:01"},
        "data": {"parameters": {"model": {"qubits": ["qA1"]}}, "outcomes": {}},
        "id": run_id, "parents": [], "created_at": f"{date}T01:00:00",
    }), encoding="utf-8")
    (run / "data.json").write_text("{}", encoding="utf-8")
    _write_chip(run / "quam_state", state)
    return run


class TestAliasColumnHistory:
    def test_an_alias_column_reads_the_leaf_it_names(self, env):
        c = env["client"]
        hm = env["app"].config["history_manager"]
        hm.check_and_snapshot(str(env["live"]), "manual", force=True)       # 0.25
        _write_chip(env["live"], _state(amp=0.3046))
        hm.check_and_snapshot(str(env["live"]), "manual", force=True)       # 0.3046
        data_root = env["tmp"] / "data"
        _seed_run(data_root, 31, _state(amp=0.2838))
        c.post("/workspace/add", data={"folder": str(data_root)})

        r = c.post("/bulk/column-history", data={
            "grid": "qubit", "label": "x180 amp", "unit": "", "col_key": "x180_amp",
            "paths": json.dumps({"qA1": _ALIAS}),
        })
        assert r.status_code == 200
        html = r.data.decode()
        changes, byrun = html.split("ch-view-byrun")[0], html.split("ch-view-byrun")[1]
        assert 'data-fill="0.25"' in changes and 'data-fill="0.3046"' in changes, (
            "the alias column must show the change points of the leaf it names")
        assert 'data-fill="0.2838"' in byrun, "the By-run tab reads it too"

    def test_a_direct_leaf_and_a_garbage_path_are_unchanged(self, env):
        c = env["client"]
        hm = env["app"].config["history_manager"]
        hm.check_and_snapshot(str(env["live"]), "manual", force=True)
        r = c.post("/bulk/column-history", data={
            "grid": "qubit", "label": "T1", "unit": "s", "col_key": "T1",
            "paths": json.dumps({"qA1": "qubits.qA1.T1", "qX": "qubits.nope.x.y"}),
        })
        assert r.status_code == 200
        assert 'data-fill="2e-05"' in r.data.decode()


# ── liveedit-r2-05 ──────────────────────────────────────────────────────────
_T2R = "qubits.qA1.T2ramsey"


def _apply(env, **extra):
    data = {"mode": "apply", "check_collisions": "1"}
    data.update(extra)
    return env["client"].post("/state/sync", data=data)


class TestOneClickApplyAsksAboutASameFieldCollision:
    def _collide(self, env):
        _edit(env, _T2R, "1.23e-6")
        _write_chip(env["live"], _state(t2r=9.99e-7), future=True)   # a node wrote it

    def test_the_collision_is_refused_named_and_bannered(self, env):
        self._collide(env)
        r = _apply(env)
        assert r.status_code == 200
        d = r.get_json()
        assert d["status"] == "collision" and d["paths"] == [_T2R], d
        assert _live(env)["qubits"]["qA1"]["T2ramsey"] == 9.99e-7, "nothing written"
        assert len(_ctx(env)["store"].change_log) == 1, "the edit is kept"
        assert _ctx(env)["live_diverged"] is True
        assert _ctx(env)["live_conflicts"] == [_T2R]
        # sync-ux 2026-09-25 (user decision: one control + one panel): the status control names it (the banner is gone)
        ctl = env["client"].get("/state/tray").data.decode()
        assert "changed on both sides" in ctl and _T2R in ctl

    def test_only_its_own_token_answers_it(self, env):
        self._collide(env)
        assert _apply(env, force="1").get_json()["status"] == "collision", \
            "force=1 answers the STALENESS question, not this one (docs/41)"
        assert _apply(env, ack_unseen="1").get_json()["status"] == "collision", \
            "ack_unseen=1 answers the other-window question, not this one"
        d = _apply(env, ack_collision="1").get_json()
        assert d["status"] == "ok", d
        assert _live(env)["qubits"]["qA1"]["T2ramsey"] == 1.23e-6, "the user's value, asked for"
        assert not _ctx(env).get("live_conflicts"), "a resolved collision is not named again"
        assert env["client"].get("/state/diverged-banner").data.decode().strip() == ""

    def test_keep_mine_from_the_banner_clears_the_named_collision(self, env):
        # the banner's "Keep mine -- overwrite live" (a forced apply-to-live)
        self._collide(env)
        assert _apply(env).get_json()["status"] == "collision"
        r = env["client"].post("/state/apply-to-live", data={"force": "1"})
        assert r.status_code == 200
        assert _live(env)["qubits"]["qA1"]["T2ramsey"] == 1.23e-6
        assert not _ctx(env).get("live_conflicts"), (
            "a stale name would reappear on the next generic drift banner")
        # (review) ...and the banner on the OPEN page comes down with it: the
        # landed apply answers with the slot, empty, as an OOB swap
        html = r.data.decode()
        slot = html[html.index('id="live-diverged-slot"'):]
        assert 'hx-swap-oob="outerHTML"' in slot[:80], html[-400:]
        assert "live-diverged-banner" not in html and "choose which to keep" not in html

    def test_an_auto_apply_flush_that_lands_takes_the_banner_down_too(self, env):
        c = env["client"]
        assert c.post("/auto-apply/arm").status_code == 200   # armed before the drift
        self._collide(env)
        assert _apply(env).get_json()["status"] == "collision"
        # sync-ux 2026-09-25 (user decision: one control + one panel): the control says it; the landed flush takes it down
        assert "changed on both sides" in c.get("/state/tray").data.decode()
        r = c.post("/state/apply-to-live", data={"force": "1"})
        assert "autoApplyApplied" in r.headers.get("HX-Trigger", ""), "the armed branch"
        html = r.data.decode()
        assert 'id="live-diverged-slot" hx-swap-oob="outerHTML"' in html
        assert "changed on both sides" not in html

    def test_take_live_from_the_banner_clears_the_named_collision(self, env):
        # the banner's "Take live -- discard my edits" (a discard pull)
        self._collide(env)
        assert _apply(env).get_json()["status"] == "collision"
        d = env["client"].post("/state/sync", data={"mode": "discard"}).get_json()
        assert d["status"] == "ok"
        assert _live(env)["qubits"]["qA1"]["T2ramsey"] == 9.99e-7
        assert not _ctx(env).get("live_diverged")
        assert not _ctx(env).get("live_conflicts")

    def test_a_different_field_still_merges_without_a_word(self, env):
        _edit(env, "qubits.qA1.f_01", "5.1e9")
        _write_chip(env["live"], _state(t1=9.9e-5), future=True)
        d = _apply(env).get_json()
        assert d["status"] == "ok", d
        live = _live(env)["qubits"]["qA1"]
        assert live["f_01"] == 5.1e9 and live["T1"] == 9.9e-5

    def test_a_press_without_the_flag_is_judged_as_before(self, env):
        # the review modal / conflict tray (informed) and every legacy caller
        self._collide(env)
        d = env["client"].post("/state/sync", data={"mode": "apply"}).get_json()
        assert d["status"] == "ok"
        assert _live(env)["qubits"]["qA1"]["T2ramsey"] == 1.23e-6

    def test_the_conflict_tray_retry_asks_about_a_same_field_move(self, env):
        # (review) apply-to-live hit the staleness conflict and the edits went
        # into the reapply stash. The conflict tray says only "an experiment
        # program updated the live state" -- it never names the field -- so its
        # "Pull & apply (merge)" used to replace the node's value unasked.
        _edit(env, _T2R, "1.23e-6")
        _write_chip(env["live"], _state(t2r=9.99e-7), future=True)
        html = env["client"].post("/state/apply-to-live").data.decode()
        assert "Apply wrote nothing" in html
        assert _ctx(env).get("pending_reapply")
        d = _apply(env).get_json()
        assert d["status"] == "collision" and d["paths"] == [_T2R], d
        assert _live(env)["qubits"]["qA1"]["T2ramsey"] == 9.99e-7, "nothing written"
        assert "tray_html" not in d, "a person's press keeps the tray it is looking at"
        d = _apply(env, ack_collision="1").get_json()
        assert d["status"] == "ok", d
        assert _live(env)["qubits"]["qA1"]["T2ramsey"] == 1.23e-6, "asked, then written"

    def test_the_conflict_tray_retry_merges_a_different_field_without_a_word(self, env):
        _edit(env, _T2R, "1.23e-6")
        _node_writes(env, T1=9.9e-5)
        env["client"].post("/state/apply-to-live")
        assert _ctx(env).get("pending_reapply")
        d = _apply(env).get_json()
        assert d["status"] == "ok", d
        live = _live(env)["qubits"]["qA1"]
        assert live["T2ramsey"] == 1.23e-6 and live["T1"] == 9.9e-5

    def test_after_a_save_the_gate_still_runs(self, env):
        # /save stashes too; the gate used to be skipped from then on
        _edit(env, _T2R, "1.23e-6")
        assert env["client"].post("/save").status_code == 200
        assert _ctx(env).get("pending_reapply") and not _ctx(env)["store"].change_log
        _node_writes(env, T2ramsey=9.99e-7)
        d = _apply(env).get_json()
        assert d["status"] == "collision" and d["paths"] == [_T2R], d
        assert _live(env)["qubits"]["qA1"]["T2ramsey"] == 9.99e-7

    def test_saved_then_edited_again_is_judged_from_the_sync_point(self, env):
        # the stash keeps the EARLIEST original: after a save the log's own
        # original is the user's saved value, and judging live against THAT
        # called the chip's untouched value "the chip moved it too"
        _edit(env, _T2R, "1.23e-6")
        assert env["client"].post("/save").status_code == 200
        _edit(env, _T2R, "1.3e-6")
        _node_writes(env, T1=9.9e-5)                       # a different field
        env["client"].post("/state/apply-to-live")         # refused, stashed again
        assert _ctx(env)["pending_reapply_orig"][_T2R] == 1.5e-6
        d = _apply(env).get_json()
        assert d["status"] == "ok", d
        live = _live(env)["qubits"]["qA1"]
        assert live["T2ramsey"] == 1.3e-6 and live["T1"] == 9.9e-5

    def test_the_automatic_merge_after_a_same_field_refusal_writes_nothing(self, env):
        c = env["client"]
        assert c.post("/auto-sync/set", data={"pull": "1", "pull_replace": "0",
                                               "push": "1"}).status_code == 200
        _edit(env, "qubits.qA1.T1", "1.25e-5")
        assert c.post("/state/apply-to-live").status_code == 200
        _node_writes(env, T1=1.66e-5)                      # the node wrote T1
        _edit(env, "qubits.qA1.T1", "1.23e-5")             # ...and so did I
        r = c.post("/state/apply-to-live")                 # refused -> merge signal
        chip = json.loads(r.headers["HX-Trigger"])["autoSyncMerge"]["chip"]
        assert "resolving this itself" in r.data.decode()
        d = _apply(env, expect_chip=chip).get_json()       # what autoSyncMerge sends
        assert d["status"] == "collision" and d["paths"] == ["qubits.qA1.T1"], d
        assert _live(env)["qubits"]["qA1"]["T1"] == 1.66e-5, "the node's value survives"
        # sync-ux 2026-09-25 (user decision: one control + one panel): named by the control, not a banner
        ctl = c.get("/state/tray").data.decode()
        assert "changed on both sides" in ctl and "qubits.qA1.T1" in ctl
        tray = d.get("tray_html") or ""
        assert 'id="pending-tray"' in tray and "resolving this itself" not in tray, (
            "the tray must stop saying Auto-Sync is resolving what the user now decides")

    def test_a_merge_that_landed_leaves_no_stale_original_behind(self, env):
        # the originals go with the stash: after a landed merge the sync point
        # moved, and a stale original would call MY earlier value "the chip's"
        c = env["client"]
        _edit(env, "qubits.qA1.T1", "1.25e-5")
        _node_writes(env, f_01=7.7e9)
        c.post("/state/apply-to-live")                     # refused, stashed
        assert _apply(env).get_json()["status"] == "ok"    # merged and landed
        assert not _ctx(env).get("pending_reapply_orig")
        _edit(env, "qubits.qA1.T1", "1.3e-5")
        _node_writes(env, f_01=7.8e9)                      # a different field again
        c.post("/state/apply-to-live")                     # refused, stashed
        d = _apply(env).get_json()
        assert d["status"] == "ok", d
        assert _live(env)["qubits"]["qA1"]["T1"] == 1.3e-5


# ── liveedit-r2-07 ──────────────────────────────────────────────────────────
def _arm_pull(env):
    return env["client"].post("/auto-sync/set", data={
        "pull": "1", "pull_replace": "0", "push": "0"})


class TestADeclinedPullPutsTheBannerUpInPlace:
    def test_the_collision_204_carries_the_signal(self, env):
        assert _arm_pull(env).status_code == 200
        _edit(env, "qubits.qA1.f_01", "5.1e9")
        _write_chip(env["live"], _state(f01=7.7e9))
        _ctx(env)["live_diverged"] = True
        r = env["client"].post("/auto-sync/pull")
        assert r.status_code == 204, "still 204 -- nothing was pulled"
        raw = r.headers.get("HX-Trigger") or ""
        assert "autoSyncMerge" not in raw
        sig = json.loads(raw)["liveConflict"]
        assert sig["paths"] == ["qubits.qA1.f_01"] and sig["chip"], sig
        # sync-ux 2026-09-25 (user decision: one control + one panel): the page the signal re-renders is the control
        ctl = env["client"].get("/state/tray").data.decode()
        assert "changed on both sides" in ctl and "qubits.qA1.f_01" in ctl

    def test_the_exhausted_merge_budget_signals_too(self, env):
        assert _arm_pull(env).status_code == 200
        _edit(env, "qubits.qA1.f_01", "5.1e9")
        _write_chip(env["live"], _state(t1=9.9e-5))       # nothing collides
        _ctx(env)["live_diverged"] = True
        last = None
        for _ in range(routes_mod._AUTO_MERGE_TRIES + 1):
            last = env["client"].post("/auto-sync/pull")
        assert last.status_code == 204
        trig = _trigger(last)
        assert "liveConflict" in trig and "autoSyncMergePull" not in trig, trig

    def test_the_banner_route_is_empty_when_nothing_diverged(self, env):
        r = env["client"].get("/state/diverged-banner")
        assert r.status_code == 200 and r.data.decode().strip() == ""


# ── liveedit-r2-09 ──────────────────────────────────────────────────────────
def _seq(env) -> str:
    return env["client"].get("/state/drift").get_json()["edit_seq"]


class TestAPassiveWindowLearnsARoundTrip:
    """A window that is only LOOKING follows the drift poll's ``edit_seq``.
    It was the change-log signature alone, so another window's apply followed
    by a Ctrl+Z that wrote the chip back (or any round trip ending on the same
    change set) left it unchanged -- the passive grid kept a value no longer
    anywhere."""

    def test_the_same_change_set_after_a_write_is_still_a_move(self, env):
        _edit(env, "qubits.qA1.T1", "2.5e-5")
        assert env["client"].post("/state/sync", data={
            "mode": "apply", "seen_changes": "1"}).status_code == 200
        d1 = _seq(env)
        _edit(env, "qubits.qA1.T1", "2.0e-5")
        assert env["client"].post("/state/sync", data={
            "mode": "apply", "seen_changes": "1"}).status_code == 200
        assert _live(env)["qubits"]["qA1"]["T1"] == 2.0e-5
        assert not _ctx(env)["store"].change_log
        assert _seq(env) != d1, "an empty log before and after hid the round trip"

    def test_a_mere_open_does_not_move_it(self, env):
        d0 = _seq(env)
        env["client"].get("/bulk")
        env["client"].get("/state/tray")
        assert _seq(env) == d0

    def test_every_tray_names_the_edit_seq_it_was_rendered_at(self, env):
        import re
        _edit(env, "qubits.qA1.T1", "2.5e-5")
        want = _seq(env)
        for url in ("/state/tray", "/bulk"):
            html = env["client"].get(url).data.decode()
            m = re.search(r'data-edit-seq="([^"]*)"', html)
            assert m and m.group(1) == want, (url, m and m.group(1), want)

    def test_the_conflict_tray_names_it_too(self, env):
        # (review) a refused push's conflict tray IS #pending-tray; without the
        # stamp this window's own refused apply always read as foreign
        import re
        _edit(env, "qubits.qA1.T1", "2.5e-5")
        _node_writes(env, T1=9.9e-5)
        html = env["client"].post("/state/apply-to-live").data.decode()
        assert "pending-tray-conflict" in html
        m = re.search(r'data-edit-seq="([^"]*)"', html)
        assert m and m.group(1) and m.group(1) == _seq(env), (m and m.group(1), _seq(env))


# ── liveedit-r2-17 ──────────────────────────────────────────────────────────
def _rows(env) -> list[dict]:
    with env["app"].app_context():
        return routes_mod._applied_log_rows()


def _node_writes(env, **fields):
    """An experiment saves the chip: the live file as it is, fields changed."""
    doc = _live(env)
    doc["qubits"]["qA1"].update(fields)
    t = time.time() + 100
    (env["live"] / "state.json").write_text(json.dumps(doc), encoding="utf-8")
    os.utime(env["live"] / "state.json", (t, t))


class TestTheAppliedLogListsOnlyWhatLanded:
    def test_a_refused_push_is_not_listed_as_applied(self, env):
        c = env["client"]
        assert c.post("/auto-apply/arm").status_code == 200
        for v in ("1.31e-5", "1.32e-5"):
            _edit(env, "qubits.qA1.T1", v)
            assert c.post("/state/apply-to-live").status_code == 200
        assert _live(env)["qubits"]["qA1"]["T1"] == 1.32e-5
        _node_writes(env, T1=1.77e-5)
        _edit(env, "qubits.qA1.T1", "1.34e-5")
        r = c.post("/state/apply-to-live")
        assert "autoApplyDisarm" in r.headers.get("HX-Trigger", "")
        assert _live(env)["qubits"]["qA1"]["T1"] == 1.77e-5, "nothing was written"
        rows = _rows(env)
        assert [row["entries"][-1]["new"] for row in rows] == [1.32e-5, 1.31e-5], rows
        # ...while Ctrl+Z still has the saved edit to walk (docs/107)
        assert len(_ctx(env)["undo_units"]) == 3
        # the applied log renders exactly those rows -- sync-ux 2026-09-25 (user decision: one control + one panel): in the panel
        assert c.get("/state/review").data.decode().count('class="applied-log-row') == 2

    def test_the_merged_write_that_landed_is_the_row(self, env):
        c = env["client"]
        assert c.post("/auto-sync/set", data={"pull": "1", "pull_replace": "0",
                                               "push": "1"}).status_code == 200
        _edit(env, "qubits.qA1.T1", "1.25e-5")
        assert c.post("/state/apply-to-live").status_code == 200
        _node_writes(env, f_01=7.7e9)
        _edit(env, "qubits.qA1.T1", "1.3e-5")
        r = c.post("/state/apply-to-live")                 # refused -> merge signal
        assert "autoSyncMerge" in r.headers.get("HX-Trigger", "")
        assert c.post("/state/sync", data={"mode": "apply"}).status_code == 200
        assert _live(env)["qubits"]["qA1"]["T1"] == 1.3e-5
        assert _live(env)["qubits"]["qA1"]["f_01"] == 7.7e9
        units = _ctx(env)["undo_units"]
        rows = _rows(env)
        assert len(rows) == 2, rows
        assert rows[0]["id"] == units[-1]["id"], "the landed merge is the row, not the refused flush"

    def test_the_x_says_when_it_only_staged(self, env):
        c = env["client"]
        assert c.post("/auto-apply/arm").status_code == 200
        _edit(env, "qubits.qA1.T1", "1.31e-5")
        assert c.post("/state/apply-to-live").status_code == 200
        uid = _rows(env)[0]["id"]
        assert c.post("/auto-apply/disarm").status_code == 200
        r = c.post("/auto-apply/revert", data={"unit_id": uid})
        assert r.status_code == 200
        msg = _trigger(r)["cellsReverted"]["message"]
        assert msg.startswith("Reverted (staged") and "Auto-Sync is off" in msg, msg
        assert _live(env)["qubits"]["qA1"]["T1"] == 1.31e-5, "staged, not written"


# ── QA F8: the red box follows the server's per-path pending truth ─────────
def _reverted(r) -> dict:
    return {e["dot_path"]: e for e in _trigger(r)["cellsReverted"]["entries"]}


class TestUndoRedoSayWhetherAPathIsStillPending:
    def test_a_partial_undo_says_the_undone_path_is_clean(self, env):
        c = env["client"]
        _edit(env, "qubits.qA1.T1", "1.3e-5")
        _edit(env, "qubits.qA1.T2ramsey", "1.2e-6")
        r = c.post("/undo")
        e = _reverted(r)["qubits.qA1.T2ramsey"]
        assert e["pending"] is False and "pending_old_disp" not in e
        assert len(_ctx(env)["store"].change_log) == 1       # the tray still says 1

    def test_the_same_path_edited_twice_stays_pending_with_its_first_original(self, env):
        c = env["client"]
        _edit(env, "qubits.qA1.T1", "1.3e-5")
        _edit(env, "qubits.qA1.T1", "1.4e-5")
        e = _reverted(c.post("/undo"))["qubits.qA1.T1"]
        assert e["pending"] is True
        with env["app"].app_context():
            assert e["pending_old_disp"] == routes_mod._bulk_display(2.0e-5)

    def test_a_redo_re_stages_it_as_pending(self, env):
        c = env["client"]
        _edit(env, "qubits.qA1.T1", "1.3e-5")
        c.post("/undo")
        e = _reverted(c.post("/redo"))["qubits.qA1.T1"]
        assert e["pending"] is True
        with env["app"].app_context():
            assert e["pending_old_disp"] == routes_mod._bulk_display(2.0e-5)

    def test_a_journal_step_says_where_it_landed(self, env):
        c = env["client"]
        _edit(env, "qubits.qA1.T1", "1.3e-5")
        assert c.post("/state/sync", data={"mode": "apply", "seen_changes": "1"}).status_code == 200
        r = c.post("/undo")
        ents = _trigger(r)["cellsReverted"]["entries"]
        assert ents, _trigger(r)
        log = {x.dot_path for x in _ctx(env)["store"].change_log}
        for e in ents:        # staged -> pending; written live -> not
            assert e["pending"] is (e["dot_path"] in log), (e, log)

    def test_a_staged_journal_step_is_pending(self, env):
        c = env["client"]
        assert c.post("/settings/undo-live", data={"enabled": "0"}).get_json()["enabled"] is False
        _edit(env, "qubits.qA1.T1", "1.3e-5")
        assert c.post("/state/sync", data={"mode": "apply", "seen_changes": "1"}).status_code == 200
        r = c.post("/undo")
        e = _reverted(r)["qubits.qA1.T1"]
        assert _trigger(r)["cellsReverted"].get("live") is False
        assert e["pending"] is True, "a staged inverse waits in the tray: it IS pending"


# ── liveedit-r2-27 ──────────────────────────────────────────────────────────
def _pair_env(tmp_path):
    st = _state()
    st["qubits"]["qA2"] = {"id": "qA2", "T1": 1.0e-5}
    st["qubit_pairs"] = {"qA1-2": {"id": "qA1-2", "qubit_control": "#/qubits/qA1",
                                   "qubit_target": "#/qubits/qA2"}}
    live = tmp_path / "chips" / "live"
    _write_chip(live, st)
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    return c


class TestANoteMutationAnswersWithTheRowMarks:
    """The grids' row-head markers were server-rendered only, so a note added
    or deleted left them stale until a reload. Every note mutation now answers
    with the same `marks` the grid render uses (one mapping, `_note_marks`)."""

    def test_add_then_delete_moves_the_qubit_mark(self, env):
        c = env["client"]
        j = c.post("/note", data={"subject": "qubits.qA1", "text": "drifts after cooldown"}).get_json()
        assert j["ok"] and j["marks"] == {"qubits": {"qA1": "drifts after cooldown"}, "pairs": {}}, j.get("marks")
        j = c.post("/note/delete", data={"subject": "qubits.qA1"}).get_json()
        assert j["ok"] and j["marks"] == {"qubits": {}, "pairs": {}}, j.get("marks")

    def test_a_leaf_note_lights_its_entity(self, env):
        j = env["client"].post("/note", data={"subject": "qubits.qA1.T1", "text": "T1 noisy"}).get_json()
        assert j["marks"]["qubits"] == {"qA1": "T1 noisy"}

    def test_a_pair_note_lands_in_the_pair_map_only(self, tmp_path):
        c = _pair_env(tmp_path)
        j = c.post("/note", data={"subject": "qubit_pairs.qA1-2", "text": "CZ phase drifts"}).get_json()
        assert j["marks"] == {"qubits": {}, "pairs": {"qA1-2": "CZ phase drifts"}}, j["marks"]

    def test_readdress_moves_the_mark(self, tmp_path):
        c = _pair_env(tmp_path)
        c.post("/note", data={"subject": "qubits.qA1", "text": "moved"})
        j = c.post("/note/readdress", data={"subject": "qubits.qA1", "new_subject": "qubits.qA2"}).get_json()
        assert j["ok"] and j["marks"]["qubits"] == {"qA2": "moved"}, j

    def test_a_conflict_answers_with_the_marks_too(self, env):
        c = env["client"]
        c.post("/note", data={"subject": "qubits.qA1", "text": "theirs"})
        r = c.post("/note", data={"subject": "qubits.qA1", "text": "mine", "expect_rev": "999"})
        assert r.status_code == 409 and r.get_json()["marks"]["qubits"] == {"qA1": "theirs"}

    def test_the_grid_render_reads_the_same_mapping(self, env):
        c = env["client"]
        c.post("/note", data={"subject": "qubits.qA1", "text": "drifts after cooldown"})
        body = c.get("/bulk").data.decode()
        assert 'bulk-rowhead-note' in body and 'title="drifts after cooldown"' in body


# ── F19 ─────────────────────────────────────────────────────────────────────
class TestTheStageMessagePointsAtAReviewThatExists:
    """"Review the diff below" pointed at nothing: the tray's Revert last apply
    lands in #status-bar and no diff renders under it. The review that exists
    is the top-bar badge (openReview -> /state/review). The id is a UTC stamp."""

    def _ts(self, env):
        c = env["client"]
        assert c.post("/state-history/snapshot").status_code == 200
        with env["app"].app_context():
            snaps = routes_mod._history().list_snapshots(env["live"])
        s0 = snaps[0]
        return getattr(s0, "timestamp", None) or s0["timestamp"]

    def test_the_revert_door_names_the_badge_and_a_utc_time(self, env):
        ts = self._ts(env)
        body = env["client"].post(f"/state-history/{ts}/stage?force=1&from=tray").data.decode()
        assert "diff below" not in body, body[:400]
        assert "loaded as the working state" in body
        # sync-ux 2026-09-25 (user decision: one control + one panel): the message names the control that exists
        assert "sync status in the top bar" in body and "↑ Apply" in body, body[:400]
        pretty = f"{ts[0:4]}-{ts[4:6]}-{ts[6:8]} {ts[9:11]}:{ts[11:13]}:{ts[13:15]} UTC"
        assert pretty in body, (pretty, body[:400])

    def test_armed_the_note_replaces_the_review_instruction(self, env):
        ts = self._ts(env)
        c = env["client"]
        c.post("/auto-apply/arm")
        body = c.post(f"/state-history/{ts}/stage?force=1").data.decode()
        assert "ARMED" in body and "sync status in the top bar" not in body, body[:400]

    def test_the_revert_button_title_says_its_time_is_local(self, env):
        c = env["client"]
        _edit(env, "qubits.qA1.T1", "1.3e-5")
        assert c.post("/state/sync", data={"mode": "apply", "seen_changes": "1"}).status_code == 200
        # sync-ux 2026-09-25 (user decision: one control + one panel): Revert last apply lives in the panel's History
        tray = c.get("/state/review").data.decode()
        assert "tray-revert-apply" in tray, tray[:600]
        import re
        m = re.search(r"Revert last apply \(done ([^)]*)\)", tray)
        assert m and m.group(1).endswith(" local time") and "T" not in m.group(1), m and m.group(1)


# ── liveedit-r2-31 ──────────────────────────────────────────────────────────
@pytest.fixture
def null_env(tmp_path):
    st = _state()
    st["qubits"]["qA1"]["z"] = {"settle_time": None}
    live = tmp_path / "chips" / "live"
    _write_chip(live, st)
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    return {"app": app, "client": c, "live": live, "tmp": tmp_path}


class TestAnUndoBackToNullSaysNotSet:
    """"Undone: qubits.q1.z.settle_time →" ended on a bare arrow: _fmt_val
    renders null as "" (right for a cell input, wrong for a sentence)."""

    P = "qubits.qA1.z.settle_time"

    def test_the_staged_undo(self, null_env):
        c = null_env["client"]
        _edit(null_env, self.P, "200")
        cr = _trigger(c.post("/undo"))["cellsReverted"]
        assert cr["message"] == f"Undone: {self.P} → not set", cr["message"]
        e = {x["dot_path"]: x for x in cr["entries"]}[self.P]
        assert e["old_value_str"] == "", "the cell itself still repaints EMPTY"

    def test_the_live_undo(self, null_env):
        c = null_env["client"]
        _edit(null_env, self.P, "200")
        assert c.post("/state/sync", data={"mode": "apply", "seen_changes": "1"}).status_code == 200
        cr = _trigger(c.post("/undo"))["cellsReverted"]
        assert cr["message"].endswith(f"{self.P} → not set"), cr["message"]

    def test_the_message_helper_and_the_cell_helper_stay_apart(self):
        assert routes_mod._fmt_msg_val(None) == "not set"
        assert routes_mod._fmt_val(None) == ""
        assert routes_mod._fmt_msg_val(0.5) == routes_mod._fmt_val(0.5)
