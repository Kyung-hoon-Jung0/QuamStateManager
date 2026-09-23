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
        banner = env["client"].get("/state/diverged-banner").data.decode()
        assert "changed both here and on" in banner and _T2R in banner

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

    def test_the_conflict_tray_retry_is_not_asked_again(self, env):
        # apply-to-live hit the staleness conflict: the user WAS told the chip
        # moved, and their edits live in the reapply stash
        _edit(env, _T2R, "1.23e-6")
        _write_chip(env["live"], _state(t2r=9.99e-7), future=True)
        html = env["client"].post("/state/apply-to-live").data.decode()
        assert "changed since you loaded it" in html
        assert _ctx(env).get("pending_reapply")
        d = _apply(env).get_json()
        assert d["status"] == "ok", d


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
        banner = env["client"].get("/state/diverged-banner").data.decode()
        assert "changed both here and on" in banner and "qubits.qA1.f_01" in banner

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
