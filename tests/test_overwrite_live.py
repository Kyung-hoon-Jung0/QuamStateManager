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
        c = env["client"]
        _rewrite_live_out_of_band(env, off=0.5)
        body = self._review(c)
        assert "review-sync-clean" in body
        assert "overwriteLiveWithWorking()" in body
        # still un-primary, and Sync is still the primary action
        m = re.search(r'<button[^>]*state-review-overwrite-btn[^>]*>', body)
        assert m and "primary" not in m.group(0)

    def test_offered_once_in_every_branch(self, env):
        """One button, outside the three branch spans — 'keep mine' is
        meaningful with pending edits, with saved edits, and with neither."""
        c = env["client"]
        _rewrite_live_out_of_band(env, off=0.5)
        assert self._review(c).count("overwriteLiveWithWorking()") == 1
        c.post("/field/edit-batch", json={"updates": [
            {"dot_path": "qubits.qA1.f_01", "value": "5.1e9"}], "expect_chip": ""})
        body = self._review(c)
        assert body.count("overwriteLiveWithWorking()") == 1
        assert "review-sync-edits" in body

    def test_absent_when_there_is_nothing_to_overwrite(self, env):
        """No differences → no diff rows → no third choice (the modal's whole
        action block is gated on total > 0)."""
        assert "overwriteLiveWithWorking()" not in self._review(env["client"])

    def test_banner_offers_both_directions(self, env):
        """The drift banner used to be look-at-it or take-theirs."""
        with env["app"].test_request_context("/"):
            from flask import render_template
            html = render_template("_live_diverged_banner.html",
                                   live_diverged=True, active_name="chip")
        assert "Take live" in html
        assert "overwriteLiveWithWorking()" in html
        assert "Keep mine" in html

    def test_banner_hides_it_on_an_archive(self, env):
        with env["app"].test_request_context("/"):
            from flask import render_template
            html = render_template("_live_diverged_banner.html",
                                   live_diverged=True, active_name="chip",
                                   chip_origin="dataset_archive")
        assert "Take live" in html
        assert "overwriteLiveWithWorking()" not in html


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
