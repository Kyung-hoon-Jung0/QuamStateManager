"""The third choice when the live chip drifted (docs/86).

The reported gap: when something outside SM (a QUAlibrate node, an IDE, a
second window) rewrites the live state, SM shows a diff and offers Sync or
Close — one direction. But the case behind the reports is a test run that wrote
parameters by mistake, where the state SM is holding is the one worth keeping.
The capability existed (State History restore-live; the conflict tray's
force-overwrite); what was missing was reaching it at the moment the user is
told about the drift.

Pins:
  - the preflight tells the confirm what disappears: how many live values
    differ, unsaved-edit count, a run-in-progress warning, reversibility
  - it never 500s on an unreadable live folder — the user may still choose
  - archives are refused (409) and never render the button
  - the button appears in ALL THREE review-modal branches, above all the CLEAN
    one that had only Sync, and in the drift banner
  - the push itself lands, and leaves the tray's "Revert last apply" armed,
    which is what makes offering the button acceptable at all
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from quam_state_manager.core import scheduler
from quam_state_manager.web.app import create_app

_WIRING = {"network": {"host": "1.1.1.1", "cluster_name": "C1"}}


def _state(off=0.08, f01=5.0e9):
    return {
        "qubits": {"qA1": {"id": "qA1", "f_01": f01, "z": {"joint_offset": off}}},
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
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    return {"app": app, "client": c, "live": live, "tmp": tmp_path}


def _live(env) -> dict:
    return json.loads((env["live"] / "state.json").read_text(encoding="utf-8"))


def _rewrite_live_out_of_band(env, **kw):
    """What a calibration node (or a mis-run test) does to the live files."""
    _write_chip(env["live"], _state(**kw))


class TestPreflight:
    def test_counts_the_live_values_that_would_disappear(self, env):
        c = env["client"]
        _rewrite_live_out_of_band(env, off=0.5, f01=6.0e9)
        d = c.get("/state/overwrite-live/preflight").get_json()
        assert d["ok"] is True
        assert d["live_changes"] == 2, d
        assert d["unsaved"] == 0
        assert d["reversible"] is True

    def test_zero_when_live_matches(self, env):
        d = env["client"].get("/state/overwrite-live/preflight").get_json()
        assert d["ok"] is True and d["live_changes"] == 0

    def test_counts_the_users_own_unsaved_edits(self, env):
        c = env["client"]
        c.post("/field/edit-batch", json={"updates": [
            {"dot_path": "qubits.qA1.z.joint_offset", "value": "0.09"}],
            "expect_chip": ""})
        d = c.get("/state/overwrite-live/preflight").get_json()
        assert d["unsaved"] == 1

    def test_unreadable_live_is_not_an_error(self, env):
        """The live folder is gone/locked. The user may still legitimately want
        to write the working state there, so this reports an UNKNOWN count
        rather than refusing — the confirm says so."""
        c = env["client"]
        (env["live"] / "state.json").unlink()
        d = c.get("/state/overwrite-live/preflight").get_json()
        assert d["ok"] is True and d["live_changes"] is None

    def test_run_in_progress_is_reported_not_blocked(self, env, monkeypatch):
        """A node writing this chip will re-write whatever we push when it
        finishes — worth saying, never worth blocking (the user may be
        overwriting precisely because that run went wrong)."""
        c = env["client"]
        monkeypatch.setattr(scheduler, "is_active", lambda _inst: True)
        d = c.get("/state/overwrite-live/preflight").get_json()
        assert d["ok"] is True and d["run_active"] is True

    def test_a_broken_run_probe_never_breaks_the_gate(self, env, monkeypatch):
        def boom(_inst):
            raise RuntimeError("queue file corrupt")
        monkeypatch.setattr(scheduler, "is_active", boom)
        d = env["client"].get("/state/overwrite-live/preflight").get_json()
        assert d["ok"] is True and d["run_active"] is False

    def test_no_chip_loaded(self, tmp_path):
        app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
        r = app.test_client().get("/state/overwrite-live/preflight")
        assert r.status_code == 400


class TestSurfaces:
    def _review(self, c) -> str:
        return c.get("/state/review").get_data(as_text=True)

    def test_clean_branch_gains_the_third_choice(self, env):
        """The branch the report is about: SM holds no edits of its own, an
        experiment rewrote live, and the only offer used to be Sync."""
        # sync-ux 2026-09-25: the review modal became the sync panel; its
        # clean branch is the "live" state (re-scoped from review-sync-clean)
        c = env["client"]
        _rewrite_live_out_of_band(env, off=0.5)
        body = self._review(c)
        assert 'data-sync-state="live"' in body
        assert "overwriteLiveWithWorking(this)" in body
        # still un-primary and LAST (docs/86: pull and push are not symmetric)
        m = re.search(r'<button[^>]*state-review-overwrite-btn[^>]*>', body)
        assert m and "primary" not in m.group(0)
        assert body.index("sp-take") < body.index("state-review-overwrite-btn")

    def test_offered_once_in_every_branch(self, env):
        """One button, outside the three branch spans — 'keep mine' is
        meaningful with pending edits, with saved edits, and with neither."""
        c = env["client"]
        _rewrite_live_out_of_band(env, off=0.5)
        assert self._review(c).count("overwriteLiveWithWorking(this)") == 1
        c.post("/field/edit-batch", json={"updates": [
            {"dot_path": "qubits.qA1.f_01", "value": "5.1e9"}], "expect_chip": ""})
        body = self._review(c)
        assert body.count("overwriteLiveWithWorking(this)") == 1
        assert "Pull &amp; apply" in body

    def test_absent_when_there_is_nothing_to_overwrite(self, env):
        """No differences → no diff rows → no third choice (the modal's whole
        action block is gated on total > 0)."""
        assert "overwriteLiveWithWorking()" not in self._review(env["client"])

    def test_banner_offers_both_directions(self, env):
        """The drift banner used to be look-at-it or take-theirs. sync-ux
        2026-09-25: the banner is gone (user decision); the status control
        carries ↓ Take live and the panel it opens carries both directions."""
        c = env["client"]
        _rewrite_live_out_of_band(env, off=0.5)
        c.get("/state/drift")
        tray = c.get("/state/tray").get_data(as_text=True)
        assert "Take live" in tray and "overwriteLiveWithWorking" not in tray, \
            "Keep mine is never attached to the control (docs/86)"
        body = self._review(c)
        assert "Take live" in body
        assert "overwriteLiveWithWorking(this)" in body
        assert "Keep mine" in body

    def test_banner_hides_it_on_an_archive(self, env):
        with env["app"].test_request_context("/"):
            from flask import render_template
            html = render_template("_state_review.html",
                                   sync={"state": "archive", "sig": "x"},
                                   chip_origin="dataset_archive", total=1,
                                   conflict_rows=[], external_rows=[], mine_rows=[],
                                   staged_rows=[], unsaved=0, live_total=1,
                                   external_total=1, working_dirty=False)
        assert "overwriteLiveWithWorking" not in html
        assert "Take live" not in html


class TestThePushItself:
    def test_working_state_wins_and_stays_reversible(self, env):
        """End to end: a mis-run rewrote live, the user keeps theirs, and the
        pre-push live is snapshotted so the tray can offer Revert last apply.
        That reversibility is what makes offering this button acceptable."""
        c = env["client"]
        _rewrite_live_out_of_band(env, off=0.5, f01=6.0e9)
        r = c.post("/state/apply-to-live?force=1")
        assert r.status_code == 200, r.data[:400]
        live = _live(env)
        assert live["qubits"]["qA1"]["z"]["joint_offset"] == 0.08
        assert live["qubits"]["qA1"]["f_01"] == 5.0e9
        ctx = next(iter(env["app"].config["contexts"].values()))
        assert ctx.get("last_apply"), "Revert last apply must be armed"

    def test_preflight_is_zero_afterwards(self, env):
        c = env["client"]
        _rewrite_live_out_of_band(env, off=0.5)
        c.post("/state/apply-to-live?force=1")
        assert c.get("/state/overwrite-live/preflight").get_json()["live_changes"] == 0


class TestNoBackupNoOverwrite:
    """QA correctness-r2-01: Keep mine while the live pair was unreadable (a
    QUAlibrate save caught mid-write; a save written with a UTF-8 BOM) wrote
    anyway -- 'Snapshot capture failed ... not valid JSON' in the log, the
    outside value in no version, no Revert offered -- while the confirm had
    promised 'the current live state is snapshotted first'. A forced push now
    refuses when it could not back up content that is really there, exactly
    like restore-live does."""

    @staticmethod
    def _bom(env):
        st = _state(off=0.5)
        (env["live"] / "state.json").write_text(json.dumps(st), encoding="utf-8-sig")

    @staticmethod
    def _torn(env):
        full = json.dumps(_state(off=0.5), indent=4)
        (env["live"] / "state.json").write_text(full[: len(full) // 2], encoding="utf-8")

    @pytest.mark.parametrize("spoil", ["_bom", "_torn"])
    def test_a_forced_push_over_unreadable_live_writes_nothing(self, env, spoil):
        c = env["client"]
        getattr(self, spoil)(env)
        before = (env["live"] / "state.json").read_bytes()
        ctx = next(iter(env["app"].config["contexts"].values()))
        ctx.pop("last_apply", None)
        r = c.post("/state/apply-to-live?force=1")
        body = r.get_data(as_text=True)
        assert (env["live"] / "state.json").read_bytes() == before, \
            "the unreadable live file was overwritten without a backup"
        assert "Nothing was written" in body and "back up" in body, body[:600]
        assert not ctx.get("last_apply"), "no Revert may be offered for a write that did not happen"
        # not a dead end: sync-ux 2026-09-25 re-scoped from the 4-row conflict
        # tray's buttons -- the one-row control now says what happened and
        # carries the way on (Resolve… opens the panel; Retry re-reads)
        assert "sync-control" in body and ("Resolve" in body or "Retry" in body), body[:600]

    def test_a_json_caller_gets_a_409_naming_it(self, env):
        self._bom(env)
        r = env["client"].post("/state/apply-to-live?force=1",
                               headers={"Accept": "application/json"})
        assert r.status_code == 409
        assert r.get_json()["conflict"] == "no_backup"

    def test_a_missing_live_folder_still_takes_the_working_state(self, env):
        """docs/86 point 7: nothing on the live chip to lose -> the user may
        still write the working state there (no Revert: nothing to revert to)."""
        (env["live"] / "state.json").unlink()
        (env["live"] / "wiring.json").unlink()
        r = env["client"].post("/state/apply-to-live?force=1")
        assert r.status_code == 200, r.data[:400]
        assert _live(env)["qubits"]["qA1"]["z"]["joint_offset"] == 0.08

    def test_the_preflight_stops_promising_a_backup_it_cannot_take(self, env):
        self._torn(env)
        d = env["client"].get("/state/overwrite-live/preflight").get_json()
        assert d["live_changes"] is None
        assert d["reversible"] is False and d["live_read"] == "unreadable"
        (env["live"] / "state.json").unlink()
        (env["live"] / "wiring.json").unlink()
        d = env["client"].get("/state/overwrite-live/preflight").get_json()
        assert d["reversible"] is False and d["live_read"] == "missing"


class TestARefusedApplyWritesNoVersion:
    """QA F5: an Apply refused by the staleness gate (a staged snapshot, then
    an outside write) recorded a BACKUP version -- byte-identical to the live
    chip -- for a write that never happened, because the pre-apply backup was
    taken before the gate. And the conflict lived only in the returned
    fragment, never in ctx."""

    @staticmethod
    def _versions(env) -> set[str]:
        root = env["tmp"] / "_inst" / "history"
        return {f"{d.parent.name}/{d.name}" for d in root.glob("*/*")
                if d.is_dir() and re.match(r"^\d{8}_\d{6}", d.name)}

    def _stage_then_drift(self, env):
        c = env["client"]
        c.post("/api/history/snapshot")
        ts = sorted(v.split("/")[1] for v in self._versions(env))[0]
        assert c.post(f"/state-history/{ts}/stage").status_code == 200
        _rewrite_live_out_of_band(env, off=0.5)

    def test_a_refused_apply_records_no_backup(self, env):
        self._stage_then_drift(env)
        before = self._versions(env)
        live_before = (env["live"] / "state.json").read_bytes()
        r = env["client"].post("/state/apply-to-live")
        body = r.get_data(as_text=True)
        # sync-ux 2026-09-25: the refused control answers (re-scoped from the
        # conflict tray's force button; the choices are in the panel)
        assert 'data-sync-state="refused"' in body and "Resolve" in body, "the refused control answers"
        assert (env["live"] / "state.json").read_bytes() == live_before
        assert self._versions(env) == before, \
            "a refused Apply recorded a version for a write that never happened"

    def test_the_conflict_is_kept_in_ctx(self, env):
        self._stage_then_drift(env)
        env["client"].post("/state/apply-to-live")
        ctx = next(iter(env["app"].config["contexts"].values()))
        assert ctx.get("live_diverged") is True

    def test_the_forced_push_after_it_still_takes_the_backup(self, env):
        """Keep mine after the refusal: the write is certain, so the backup
        (holding the outside value) is taken and Revert is armed."""
        self._stage_then_drift(env)
        env["client"].post("/state/apply-to-live")
        before = self._versions(env)
        assert env["client"].post("/state/apply-to-live?force=1").status_code == 200
        ctx = next(iter(env["app"].config["contexts"].values()))
        assert ctx.get("last_apply", {}).get("pre_ts")
        assert self._versions(env) - before, "the pre-push backup was taken"

    def test_an_adopt_still_arms_revert(self, env):
        """docs/116: live already holds the payload -> no write, and Revert
        last apply is armed exactly as before (the backup is taken after)."""
        c = env["client"]
        c.post("/field/edit-batch", json={"updates": [
            {"dot_path": "qubits.qA1.z.joint_offset", "value": "0.5"}], "expect_chip": ""})
        c.post("/save")
        _rewrite_live_out_of_band(env, off=0.5)     # live == the payload now
        r = c.post("/state/apply-to-live")
        assert r.status_code == 200 and "tray-force-btn" not in r.get_data(as_text=True)
        ctx = next(iter(env["app"].config["contexts"].values()))
        assert ctx.get("last_apply", {}).get("pre_ts")


class TestKeepMineHoldsToWhatTheConfirmNamed:
    """QA correctness-r2-09: the Keep-mine confirm counts the live values it
    will replace from a preflight read at click time; a write landing while
    the confirm is open was overwritten too, unnamed (force skips every
    staleness check). The preflight now returns the content hash it counted
    from and the push is held to it."""

    def test_the_preflight_returns_the_hash_it_counted(self, env):
        from quam_state_manager.core import working_copy
        _rewrite_live_out_of_band(env, off=0.5)
        d = env["client"].get("/state/overwrite-live/preflight").get_json()
        st = json.loads((env["live"] / "state.json").read_text(encoding="utf-8"))
        assert d["live_hash"] == working_copy.content_hash(st, _WIRING)

    def test_a_write_landing_during_the_confirm_is_refused_and_asked_again(self, env):
        c = env["client"]
        _rewrite_live_out_of_band(env, off=0.5)
        h0 = c.get("/state/overwrite-live/preflight").get_json()["live_hash"]
        _rewrite_live_out_of_band(env, off=0.5, f01=6.1e9)   # lands while the confirm is open
        before = TestARefusedApplyWritesNoVersion._versions(env)
        r = c.post(f"/state/apply-to-live?force=1&expect_live_hash={h0}")
        assert TestARefusedApplyWritesNoVersion._versions(env) == before, \
            "a refused push must not record a backup version"
        live = _live(env)
        assert live["qubits"]["qA1"]["f_01"] == 6.1e9, "the unnamed write was overwritten"
        assert live["qubits"]["qA1"]["z"]["joint_offset"] == 0.5
        assert "keepMineReask" in (r.headers.get("HX-Trigger") or "")
        assert "Nothing was written" in r.get_data(as_text=True)

    def test_the_tight_recheck_inside_the_write_also_holds(self, env, monkeypatch):
        """A write between the route's check and the file write itself."""
        from quam_state_manager.core import working_copy
        c = env["client"]
        _rewrite_live_out_of_band(env, off=0.5)
        h0 = c.get("/state/overwrite-live/preflight").get_json()["live_hash"]
        real = working_copy.read_live
        calls = {"n": 0}

        def read_live_then_write(wc, **kw):
            out = real(wc, **kw)
            calls["n"] += 1
            if calls["n"] == 1:           # right after the route's own check
                _rewrite_live_out_of_band(env, off=0.5, f01=6.2e9)
            return out
        monkeypatch.setattr(working_copy, "read_live", read_live_then_write)
        r = c.post(f"/state/apply-to-live?force=1&expect_live_hash={h0}")
        assert _live(env)["qubits"]["qA1"]["f_01"] == 6.2e9
        assert "keepMineReask" in (r.headers.get("HX-Trigger") or "")

    def test_unchanged_live_is_written(self, env):
        c = env["client"]
        _rewrite_live_out_of_band(env, off=0.5)
        h0 = c.get("/state/overwrite-live/preflight").get_json()["live_hash"]
        r = c.post(f"/state/apply-to-live?force=1&expect_live_hash={h0}")
        assert r.status_code == 200
        assert _live(env)["qubits"]["qA1"]["z"]["joint_offset"] == 0.08


class TestAMissingLiveFolderStillFailsHonestly:
    """QA F5 follow-up: the backup deferral stats the live files before the
    apply; a missing live pair must still reach apply_to_live's own honest
    "Apply to live failed" answer, never an unhandled 500."""

    def test_unforced_apply_with_no_live_files(self, env):
        c = env["client"]
        c.post("/field/edit-batch", json={"updates": [
            {"dot_path": "qubits.qA1.z.joint_offset", "value": "0.09"}], "expect_chip": ""})
        (env["live"] / "state.json").unlink()
        (env["live"] / "wiring.json").unlink()
        r = c.post("/state/apply-to-live")
        assert r.status_code == 500
        assert "Apply to live failed" in r.get_data(as_text=True)
