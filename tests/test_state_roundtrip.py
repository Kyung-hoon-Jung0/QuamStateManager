"""State back-and-forth roundtrip (docs/65).

The reported failure class: content loaded WHOLESALE into the working copy
(dataset "Load State", State-History stage, "Revert last apply", or plain
/save'd edits) is not representable as change-log entries — yet every
/state/sync mode ran pull-first, overwriting the working files with the live
content and (mode=apply) pushing the live chip back onto itself. The user saw
"I pressed apply and it FETCHED the live state instead".

Pins:
  - /state/sync mode=apply with a dirty working copy PUSHES it whole (no pull).
  - mode=discard/reapply with a dirty working copy answers needs_confirm until
    force=1 (the pull would destroy the staged content).
  - the conflict carve-out: a pending_reapply stash re-enables pull-first so
    conflict resolution can't loop.
  - dataset Load State → sync apply lands the run's values on the live chip
    (the exact user workflow), and the stage response refreshes the tray to
    the working-dirty "Apply to live chip" affordance + fires stateRestored.
  - Revert last apply → sync apply restores the pre-apply live content.
  - the staleness conflict tray is HONEST for staged content (no meaningless
    "re-apply my edits" when the stash is empty).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.web import routes as routes_mod
from quam_state_manager.web.app import create_app

_WIRING = {"network": {"host": "1.1.1.1", "cluster_name": "C1"}}


def _state(off_a=0.08):
    return {
        "qubits": {"qA1": {"id": "qA1", "f_01": 5.0e9,
                           "z": {"joint_offset": off_a}}},
        "qubit_pairs": {},
        "active_qubit_names": ["qA1"],
    }


def _write_chip(folder: Path, state: dict, wiring: dict | None = None):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(wiring or _WIRING),
                                        encoding="utf-8")


def _live_off(env) -> float:
    st = json.loads((env["live"] / "state.json").read_text(encoding="utf-8"))
    return st["qubits"]["qA1"]["z"]["joint_offset"]


def _working_off(env) -> float:
    ctx = next(iter(env["app"].config["contexts"].values()))
    wf = ctx["working_copy"].working_folder
    st = json.loads((Path(wf) / "state.json").read_text(encoding="utf-8"))
    return st["qubits"]["qA1"]["z"]["joint_offset"]


@pytest.fixture
def env(tmp_path):
    live = tmp_path / "chips" / "live"
    _write_chip(live, _state())
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    r = c.post("/load", data={"folder": str(live)})
    assert r.status_code in (200, 302)
    return {"app": app, "client": c, "live": live, "tmp": tmp_path}


def _edit(c, value, dot_path="qubits.qA1.z.joint_offset"):
    r = c.post("/field/edit-batch", json={
        "updates": [{"dot_path": dot_path, "value": str(value)}],
        "expect_chip": "",
    })
    assert r.status_code == 200 and r.get_json()["ok"], r.data
    return r


class TestSyncApplyPushesDirtyWorkingCopy:
    def test_saved_edits_reach_live_on_sync_apply(self, env):
        """THE reported bug: edit → save → 'apply' used to pull live over the
        saved working copy and push live back onto itself (edit lost)."""
        c = env["client"]
        _edit(c, 0.095)
        assert c.post("/save").status_code == 200
        assert _live_off(env) == 0.08          # not applied yet
        r = c.post("/state/sync", data={"mode": "apply"})
        body = r.get_json()
        assert body["status"] == "ok" and body["mode"] == "apply", body
        assert _live_off(env) == 0.095, "the saved working copy must be PUSHED"

    def test_staged_plus_unsaved_edits_both_reach_live(self, env):
        """Mixed state: saved base + a fresh change-log edit on top — the push
        must carry BOTH (the review modal's 'Pull & apply' used to keep only
        the change-log edit and destroy the staged base)."""
        c = env["client"]
        _edit(c, 0.095)
        assert c.post("/save").status_code == 200
        _edit(c, 5.05e9, dot_path="qubits.qA1.f_01")
        r = c.post("/state/sync", data={"mode": "apply"})
        assert r.get_json()["status"] == "ok"
        live = json.loads((env["live"] / "state.json").read_text(encoding="utf-8"))
        assert live["qubits"]["qA1"]["z"]["joint_offset"] == 0.095
        assert live["qubits"]["qA1"]["f_01"] == 5.05e9

    def test_pure_changelog_apply_still_merges(self, env):
        """No staged content → the pull-first merge model is unchanged."""
        c = env["client"]
        _edit(c, 0.091)
        r = c.post("/state/sync", data={"mode": "apply"})
        body = r.get_json()
        assert body["status"] == "ok" and body["mode"] == "apply"
        assert body["replay"] and body["replay"]["applied"] == 1
        assert _live_off(env) == 0.091


def _stage_pre_apply(env):
    """edit → apply (live=0.095) → stage the pre-apply snapshot back.

    Leaves the canonical STAGED shape: working=0.08, live=0.095,
    working_dirty=True, change_log empty, re-apply stash EMPTY (stage clears
    it — that emptiness is exactly what made the old pull-first model destroy
    staged content; /save'd edits were never affected because /save stashes)."""
    c = env["client"]
    _edit(c, 0.095)
    assert c.post("/state/apply-to-live").status_code == 200
    assert _live_off(env) == 0.095
    ctx = next(iter(env["app"].config["contexts"].values()))
    pre_ts = ctx["last_apply"]["pre_ts"]
    assert c.post(f"/state-history/{pre_ts}/stage").status_code == 200
    assert _working_off(env) == 0.08
    assert not ctx.get("pending_reapply")
    return ctx


class TestPullNeedsConfirmOnStagedWorkingCopy:
    @pytest.mark.parametrize("mode", ["discard", "reapply"])
    def test_pull_gated_until_forced(self, env, mode):
        c = env["client"]
        _stage_pre_apply(env)
        r = c.post("/state/sync", data={"mode": mode})
        body = r.get_json()
        assert body["status"] == "needs_confirm", body
        assert _working_off(env) == 0.08, "refused pull must not touch the copy"
        r2 = c.post("/state/sync", data={"mode": mode, "force": "1"})
        assert r2.get_json()["status"] == "ok"
        assert _working_off(env) == 0.095, "forced pull restores live content"
        assert _live_off(env) == 0.095

    def test_clean_pull_needs_no_confirm(self, env):
        c = env["client"]
        r = c.post("/state/sync", data={"mode": "discard"})
        assert r.get_json()["status"] == "ok"

    def test_staged_sync_apply_pushes_snapshot(self, env):
        """A staged snapshot + any doStateSync('apply') entry point (review
        modal, stale tray) must push the SNAPSHOT — under the old pull-first
        model this pulled live over it and pushed live back onto itself."""
        c = env["client"]
        _stage_pre_apply(env)
        r = c.post("/state/sync", data={"mode": "apply"})
        assert r.get_json()["status"] == "ok"
        assert _live_off(env) == 0.08, "the staged snapshot must land on live"


class TestDatasetLoadStateRoundtrip:
    def _seed_run(self, root: Path, run_id: int, state: dict) -> None:
        run = root / "2026-12-30" / f"#{run_id}_08_spec_010000"
        run.mkdir(parents=True)
        (run / "node.json").write_text(json.dumps({
            "metadata": {"name": "08_spec", "status": "successful",
                         "run_start": "2026-12-30T01:00:00",
                         "run_end": "2026-12-30T01:00:01"},
            "data": {"parameters": {"model": {"qubits": ["qA1"]}},
                     "outcomes": {}},
            "id": run_id, "parents": [],
            "created_at": "2026-12-30T01:00:00",
        }), encoding="utf-8")
        (run / "data.json").write_text("{}", encoding="utf-8")
        _write_chip(run / "quam_state", state)

    def test_load_state_then_sync_apply_lands_on_live(self, env):
        """The exact user workflow: dataset State tab → Load State → the run's
        values are the working state (tray flips to 'Apply to live chip') →
        apply → they are on the live chip."""
        c = env["client"]
        data_root = env["tmp"] / "data"
        self._seed_run(data_root, 31, _state(off_a=0.079))
        c.post("/workspace/add", data={"folder": str(data_root)})
        uid = f"{routes_mod._folder_key(data_root)}:31"
        r = c.post(f"/dataset/{uid}/load-state")
        assert r.status_code == 200, r.data
        html = r.data.decode()
        # the OOB tray flips to the staged affordance in the SAME response
        assert 'data-working-dirty="1"' in html
        assert "Apply to live chip" in html
        # the client bridge (grid refresh) rides this trigger
        assert "stateRestored" in r.headers.get("HX-Trigger", "")
        assert _working_off(env) == 0.079
        assert _live_off(env) == 0.08          # live untouched until apply
        r2 = c.post("/state/sync", data={"mode": "apply"})
        assert r2.get_json()["status"] == "ok"
        assert _live_off(env) == 0.079, "run's state must land on the live chip"


class TestRevertLastApplyRoundtrip:
    def test_revert_then_apply_restores_pre_apply_live(self, env):
        c = env["client"]
        _edit(c, 0.095)
        assert c.post("/state/apply-to-live").status_code == 200
        assert _live_off(env) == 0.095
        ctx = next(iter(env["app"].config["contexts"].values()))
        pre_ts = ctx["last_apply"]["pre_ts"]
        r = c.post(f"/state-history/{pre_ts}/stage")
        assert r.status_code == 200
        html = r.data.decode()
        assert 'data-working-dirty="1"' in html
        assert "Apply to live chip" in html
        assert "stateRestored" in r.headers.get("HX-Trigger", "")
        assert _working_off(env) == 0.08 and _live_off(env) == 0.095
        r2 = c.post("/state/sync", data={"mode": "apply"})
        assert r2.get_json()["status"] == "ok"
        assert _live_off(env) == 0.08, "revert + apply = pre-apply live content"


class TestStagedStalenessConflict:
    def test_conflict_tray_is_honest_for_staged_content(self, env):
        """Staged working copy + live changed out-of-band → apply conflicts.
        With an empty re-apply stash, 'Pull & re-apply my edits' would replay
        NOTHING and destroy the staged content — the tray must offer only the
        honest choices; and the pull side stays confirm-gated."""
        c = env["client"]
        _stage_pre_apply(env)
        # an experiment writes the live files behind our back
        _write_chip(env["live"], _state(off_a=0.070))
        r = c.post("/state/sync", data={"mode": "apply"})
        body = r.get_json()
        assert body["status"] == "conflict", body
        tray = body["tray_html"]
        assert "Apply my working state" in tray
        assert "Pull &amp; apply my edits" not in tray
        # resolution A: force-overwrite live with the staged content
        # resolution B: pull latest (confirm-gated because staged)
        r2 = c.post("/state/sync", data={"mode": "discard"})
        assert r2.get_json()["status"] == "needs_confirm"
        r3 = c.post("/state/sync", data={"mode": "discard", "force": "1"})
        assert r3.get_json()["status"] == "ok"
        assert _working_off(env) == 0.070

    def test_staged_base_survives_stash(self, env):
        """audit-r10 F-C: stage → edit → /save fills the re-apply stash with
        only the EDIT; a stash-based carve-out would then pull-destroy the
        staged base. The staged_base marker must keep the push delegation:
        live gets base + edit."""
        c = env["client"]
        _stage_pre_apply(env)                       # working=0.08 staged
        _edit(c, 6.0e9, dot_path="qubits.qA1.f_01")
        assert c.post("/save").status_code == 200   # stash now non-empty
        r = c.post("/state/sync", data={"mode": "apply"})
        assert r.get_json()["status"] == "ok", r.get_json()
        live = json.loads((env["live"] / "state.json").read_text(encoding="utf-8"))
        assert live["qubits"]["qA1"]["z"]["joint_offset"] == 0.08, \
            "the STAGED BASE must reach live (not be pulled away)"
        assert live["qubits"]["qA1"]["f_01"] == 6.0e9

    def test_staged_then_edited_conflict_stays_honest(self, env):
        """audit-r10 F-C: with a staged base + saved edit + live drift, the
        conflict tray must stay the HONEST staged variant and pull stays
        confirm-gated."""
        c = env["client"]
        _stage_pre_apply(env)
        _edit(c, 6.0e9, dot_path="qubits.qA1.f_01")
        assert c.post("/save").status_code == 200
        _write_chip(env["live"], _state(off_a=0.070))   # out-of-band drift
        r = c.post("/state/sync", data={"mode": "apply"})
        body = r.get_json()
        assert body["status"] == "conflict", body
        assert "Apply my working state" in body["tray_html"]
        assert "Pull &amp; apply my edits" not in body["tray_html"]
        r2 = c.post("/state/sync", data={"mode": "discard"})
        assert r2.get_json()["status"] == "needs_confirm"

    def test_second_revert_cycle_targets_true_pre_apply(self, env):
        """audit-r10 F-B: after an A-B-A content cycle the dedup makes the
        NEWEST snapshot the wrong revert target — pre_ts must be resolved by
        content match."""
        c = env["client"]
        hm = env["app"].config["history_manager"]
        ctx = _stage_pre_apply(env)                 # live=0.095, staged 0.08
        assert c.post("/state/sync",
                      data={"mode": "apply"}).get_json()["status"] == "ok"
        assert _live_off(env) == 0.08               # reverted (A-B-A complete)
        _edit(c, 0.099)
        assert c.post("/state/apply-to-live").status_code == 200
        pre_ts = ctx["last_apply"]["pre_ts"]
        hist_dir = hm._history_dir(Path(str(env["live"])))
        snap = json.loads((hist_dir / pre_ts / "state.json").read_text(
            encoding="utf-8"))
        assert snap["qubits"]["qA1"]["z"]["joint_offset"] == 0.08, \
            "pre_ts must hold the TRUE pre-apply content, not the newest snap"

    def test_no_trustworthy_target_drops_last_apply(self, env):
        """audit-r10 F-D: a failed capture must not leave the PREVIOUS
        apply's memo offering a two-applies-deep revert."""
        c = env["client"]
        _edit(c, 0.095)
        assert c.post("/state/apply-to-live").status_code == 200
        ctx = next(iter(env["app"].config["contexts"].values()))
        assert ctx.get("last_apply")
        hm = env["app"].config["history_manager"]

        def _boom(*a, **k):
            raise RuntimeError("capture failed")

        env["app"].config["history_manager"].check_and_snapshot = _boom  # type: ignore
        try:
            _edit(c, 0.097)
            assert c.post("/state/apply-to-live").status_code == 200
            assert "last_apply" not in ctx, \
                "stale memo must be dropped when no trustworthy target exists"
        finally:
            env["app"].config["history_manager"] = hm

    def test_stage_confirm_from_tray_targets_status_bar(self, env):
        """audit-r10 F-I: the tray's revert 409 confirm must target an
        element that exists outside the State History page."""
        c = env["client"]
        _edit(c, 0.095)
        assert c.post("/state/apply-to-live").status_code == 200
        ctx = next(iter(env["app"].config["contexts"].values()))
        pre_ts = ctx["last_apply"]["pre_ts"]
        _edit(c, 0.091)                              # pending → 409 gate
        r = c.post(f"/state-history/{pre_ts}/stage?from=tray")
        assert r.status_code == 409
        body = r.data.decode()
        assert 'hx-target="#status-bar"' in body
        assert "from=tray" in body

    def test_saved_edits_keep_stash_replay_flow(self, env):
        """The carve-out: /save stashes the edits, so a dirty-but-stashed
        working copy keeps the pull-first merge — an out-of-band live change
        is absorbed by the pull and the saved edit replays on top (no
        needs_confirm friction, no conflict loop)."""
        c = env["client"]
        _edit(c, 0.095)
        assert c.post("/save").status_code == 200
        _write_chip(env["live"], _state(off_a=0.070))
        r = c.post("/state/sync", data={"mode": "apply"})
        body = r.get_json()
        assert body["status"] == "ok", body
        assert body["replay"] and body["replay"]["applied"] == 1
        assert _live_off(env) == 0.095, "saved edit replays over the pulled live"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_state_sync_client_wiring():
    """The client half (tests/state_sync_selfcheck.cjs under jsdom): the
    needs_confirm confirm+force retry, the stateRestored surface-refresh
    bridge (with the dataset-detail inspector exemption), the plot-apply
    popup closing after ONE successful apply, and the bulk toolbar
    pointerdown stamp that stops the lost-click row-commit race."""
    node = shutil.which("node")
    try:
        subprocess.run([node, "-e", "require('jsdom')"],
                       check=True, capture_output=True, timeout=30)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pytest.skip("jsdom not installed for node")
    res = subprocess.run(
        [node, str(Path(__file__).resolve().parent / "state_sync_selfcheck.cjs")],
        capture_output=True, text=True, timeout=120)
    assert res.returncode == 0, f"selfcheck failed:\n{res.stdout}\n{res.stderr}"
    assert res.stdout.count("ok - ") >= 20, res.stdout
