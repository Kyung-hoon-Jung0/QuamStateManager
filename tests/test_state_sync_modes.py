"""Tests for the mode-based ``/state/sync`` (apply / reapply / discard) — the
non-destructive "pull" UX.

When the live chip changed since load, pulling it used to discard the user's
pending edits. These tests cover the explicit choices that replace that:
``apply`` (re-apply the edits on the fresh state and push the merged result
straight to the live chip in one step), ``reapply`` (re-apply them but leave
them pending for review), or ``discard``.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app


# ---------------------------------------------------------------------------
# Minimal synth — same shape as test_web.py / test_chip_compare_routes.py so
# this file stays self-contained.
# ---------------------------------------------------------------------------


def _make_state(f_01: float = 6.25e9, t1: float = 8834) -> dict:
    return {
        "qubits": {
            "qA1": {
                "id": "qA1",
                "f_01": f_01,
                "T1": t1,
                "T2ramsey": 1.5e-6,
                "anharmonicity": -220e6,
                "xy": {
                    "RF_frequency": f_01,
                    "opx_output": "#/wiring/qubits/qA1/xy/opx_output",
                    "operations": {
                        "x180_DragCosine": {"amplitude": 0.115, "length": 40},
                    },
                },
                "resonator": {
                    "RF_frequency": 7.64e9,
                    "operations": {"readout": {"amplitude": 0.042, "length": 1000}},
                },
            },
        },
        "qubit_pairs": {},
        "active_qubit_names": ["qA1"],
    }


def _make_wiring() -> dict:
    return {
        "wiring": {
            "qubits": {
                "qA1": {
                    "xy": {"opx_output": "MW-FEM/1/2"},
                    "rr": {"opx_output": "MW-FEM/1/1", "opx_input": "MW-FEM/1/1"},
                },
            },
        },
        "network": {"host": "10.1.1.18"},
    }


def _write_live_state(folder: Path, state: dict) -> None:
    """Rewrite the live state.json with a future mtime (simulates an experiment
    program saving over it after the app loaded it)."""
    p = folder / "state.json"
    p.write_text(json.dumps(state, indent=2), encoding="utf-8")
    future = time.time() + 100
    os.utime(p, (future, future))


@pytest.fixture
def synth_folder(tmp_path: Path) -> Path:
    (tmp_path / "state.json").write_text(json.dumps(_make_state(), indent=2), encoding="utf-8")
    (tmp_path / "wiring.json").write_text(json.dumps(_make_wiring(), indent=2), encoding="utf-8")
    return tmp_path


@pytest.fixture
def loaded_client(tmp_path, synth_folder):
    app = create_app(testing=True, instance_path=str(tmp_path / "_app_instance"))
    client = app.test_client()
    client.post("/load", data={"folder": str(synth_folder)})
    return client


def _edit(client, dot_path, value):
    return client.post("/field/edit", data={"dot_path": dot_path, "value": str(value)})


# ---------------------------------------------------------------------------
# The real conflict → pull flow. Apply-to-live SAVES the edits to the working
# copy first (clearing the change log) and only THEN hits the staleness
# conflict — so the pull choices must recover the edits from the reapply stash,
# not the (now-empty) change log. This is the flow the feature exists for.
# ---------------------------------------------------------------------------


class TestConflictPullFlow:
    def _make_stale_conflict(self, client, folder, value="5.0e9"):
        _edit(client, "qubits.qA1.f_01", value)
        _write_live_state(folder, _make_state(f_01=7.0e9))
        html = client.post("/state/apply-to-live").data.decode()
        assert "changed since you loaded it" in html

    def test_conflict_then_reapply_restores_saved_edit(self, loaded_client, synth_folder):
        self._make_stale_conflict(loaded_client, synth_folder)
        data = loaded_client.post("/state/sync", data={"mode": "reapply"}).get_json()
        assert data["replay"]["applied"] == 1
        assert data["replay"]["failed"] == []
        assert "qubits.qA1.f_01" in loaded_client.get("/state/review").data.decode()

    def test_conflict_then_apply_pushes_merged_result_to_live(self, loaded_client, synth_folder):
        # The flow the one-click button exists for: edit, hit a conflict on
        # apply-to-live, then "Pull & apply my edits" pulls the live, re-applies
        # the edit on top, and writes the merged result straight to live.
        self._make_stale_conflict(loaded_client, synth_folder, value="5.0e9")
        data = loaded_client.post("/state/sync", data={"mode": "apply"}).get_json()
        assert data["status"] == "ok"
        assert data["mode"] == "apply"
        assert data["replay"]["applied"] == 1
        assert data["replay"]["failed"] == []
        # The live file now holds the user's edit on top of the pulled live state.
        live = json.loads((synth_folder / "state.json").read_text(encoding="utf-8"))
        assert live["qubits"]["qA1"]["f_01"] == 5.0e9
        # Working copy == live now; a follow-up pull has nothing left to re-apply.
        assert "No differences" in loaded_client.get("/state/review").data.decode()
        follow = loaded_client.post("/state/sync", data={"mode": "apply"}).get_json()
        assert follow["replay"] is None

    def test_apply_reconflict_returns_conflict_and_keeps_stash(self, loaded_client, synth_folder, monkeypatch):
        # If the live chip changes *again* during the one-click apply (the final
        # push raises StaleLiveError), the route returns status "conflict" with
        # the conflict tray and preserves the stash so a retry still works.
        from quam_state_manager.core import working_copy

        _edit(loaded_client, "qubits.qA1.f_01", "5.0e9")

        def _boom(wc, *, force=False):
            raise working_copy.StaleLiveError("changed again mid-apply")

        monkeypatch.setattr(working_copy, "apply_to_live", _boom)
        data = loaded_client.post("/state/sync", data={"mode": "apply"}).get_json()
        assert data["status"] == "conflict"
        assert data["mode"] == "apply"
        assert "changed since you loaded it" in data["tray_html"]

        # Stash preserved: with the real apply restored, a retry pushes the edit.
        monkeypatch.undo()
        retry = loaded_client.post("/state/sync", data={"mode": "apply"}).get_json()
        assert retry["status"] == "ok"
        assert retry["replay"]["applied"] == 1
        assert json.loads((synth_folder / "state.json").read_text(encoding="utf-8"))["qubits"]["qA1"]["f_01"] == 5.0e9

    def test_conflict_then_discard_drops_saved_edit(self, loaded_client, synth_folder):
        self._make_stale_conflict(loaded_client, synth_folder)
        data = loaded_client.post("/state/sync", data={"mode": "discard"}).get_json()
        assert data["mode"] == "discard"
        # The live value (7e9) won; nothing of the user's edit remains.
        assert "No differences" in loaded_client.get("/state/review").data.decode()

    def test_explicit_save_then_conflict_then_reapply(self, loaded_client, synth_folder):
        # User Saves to the working copy first, THEN applies — the change log is
        # already empty at apply time, so the /save stash is what's recovered.
        _edit(loaded_client, "qubits.qA1.f_01", "5.0e9")
        loaded_client.post("/save")
        _write_live_state(synth_folder, _make_state(f_01=7.0e9))
        assert "changed since you loaded it" in loaded_client.post("/state/apply-to-live").data.decode()
        data = loaded_client.post("/state/sync", data={"mode": "reapply"}).get_json()
        assert data["replay"]["applied"] == 1

    def test_successful_apply_clears_stash(self, loaded_client, synth_folder):
        # No conflict: apply succeeds, the stash is cleared, so a later pull has
        # nothing to re-apply.
        _edit(loaded_client, "qubits.qA1.f_01", "5.0e9")
        assert loaded_client.post("/state/apply-to-live").status_code == 200
        data = loaded_client.post("/state/sync", data={"mode": "reapply"}).get_json()
        assert data["replay"] is None


# ---------------------------------------------------------------------------
# discard
# ---------------------------------------------------------------------------


class TestSyncDiscard:
    def test_discard_drops_pending(self, loaded_client):
        _edit(loaded_client, "qubits.qA1.f_01", "5.0e9")
        data = loaded_client.post("/state/sync", data={"mode": "discard"}).get_json()
        assert data["status"] == "ok"
        assert data["mode"] == "discard"
        assert data["replay"] is None
        assert "tray_html" in data
        # Working copy now matches live again — the edit was dropped.
        assert "No differences" in loaded_client.get("/state/review").data.decode()

    def test_default_mode_is_discard(self, loaded_client):
        _edit(loaded_client, "qubits.qA1.f_01", "5.0e9")
        data = loaded_client.post("/state/sync").get_json()
        assert data["status"] == "ok"
        assert data["mode"] == "discard"


# ---------------------------------------------------------------------------
# reapply
# ---------------------------------------------------------------------------


class TestSyncReapply:
    def test_reapply_replays_all_edits(self, loaded_client, synth_folder):
        _edit(loaded_client, "qubits.qA1.f_01", "5.0e9")
        _edit(loaded_client, "qubits.qA1.T1", "12345")
        # An experiment program rewrote the live state in the meantime.
        _write_live_state(synth_folder, _make_state(f_01=7.0e9, t1=4321))

        data = loaded_client.post("/state/sync", data={"mode": "reapply"}).get_json()
        assert data["status"] == "ok"
        assert data["mode"] == "reapply"
        assert data["replay"]["applied"] == 2
        assert data["replay"]["failed"] == []
        # The re-applied edits are pending again on top of the fresh live state.
        review = loaded_client.get("/state/review").data.decode()
        assert "qubits.qA1.f_01" in review
        assert "qubits.qA1.T1" in review

    def test_reapply_reports_now_invalid_path(self, loaded_client, synth_folder):
        _edit(loaded_client, "qubits.qA1.f_01", "5.0e9")
        _edit(loaded_client, "qubits.qA1.T1", "12345")
        # Live state pulled in no longer has a T1 field — that replay must fail
        # while the still-valid f_01 edit survives.
        stale = _make_state(f_01=7.0e9)
        del stale["qubits"]["qA1"]["T1"]
        _write_live_state(synth_folder, stale)

        data = loaded_client.post("/state/sync", data={"mode": "reapply"}).get_json()
        assert data["status"] == "ok"
        assert data["replay"]["applied"] == 1
        failed_paths = [f["dot_path"] for f in data["replay"]["failed"]]
        assert failed_paths == ["qubits.qA1.T1"]

    def test_reapply_with_no_pending_is_noop(self, loaded_client):
        data = loaded_client.post("/state/sync", data={"mode": "reapply"}).get_json()
        assert data["status"] == "ok"
        assert data["replay"] is None


# ---------------------------------------------------------------------------
# apply (one-click: pull → re-apply → push to live)
# ---------------------------------------------------------------------------


class TestSyncApply:
    def test_apply_replays_then_pushes_to_live(self, loaded_client, synth_folder):
        _edit(loaded_client, "qubits.qA1.f_01", "5.0e9")
        _edit(loaded_client, "qubits.qA1.T1", "12345")
        # An experiment program rewrote the live state in the meantime.
        _write_live_state(synth_folder, _make_state(f_01=7.0e9, t1=4321))

        data = loaded_client.post("/state/sync", data={"mode": "apply"}).get_json()
        assert data["status"] == "ok"
        assert data["mode"] == "apply"
        assert data["replay"]["applied"] == 2
        assert data["replay"]["failed"] == []
        # Both edits landed on the live file, on top of the pulled live state.
        live = json.loads((synth_folder / "state.json").read_text(encoding="utf-8"))
        assert live["qubits"]["qA1"]["f_01"] == 5.0e9
        assert live["qubits"]["qA1"]["T1"] == 12345
        # Working copy now matches live — nothing pending.
        assert "No differences" in loaded_client.get("/state/review").data.decode()

    def test_apply_reports_now_invalid_path_but_still_pushes(self, loaded_client, synth_folder):
        _edit(loaded_client, "qubits.qA1.f_01", "5.0e9")
        _edit(loaded_client, "qubits.qA1.T1", "12345")
        # Pulled-in live no longer has T1 — that replay fails, f_01 still applies.
        stale = _make_state(f_01=7.0e9)
        del stale["qubits"]["qA1"]["T1"]
        _write_live_state(synth_folder, stale)

        data = loaded_client.post("/state/sync", data={"mode": "apply"}).get_json()
        assert data["status"] == "ok"
        assert data["replay"]["applied"] == 1
        assert [f["dot_path"] for f in data["replay"]["failed"]] == ["qubits.qA1.T1"]
        live = json.loads((synth_folder / "state.json").read_text(encoding="utf-8"))
        assert live["qubits"]["qA1"]["f_01"] == 5.0e9


# ---------------------------------------------------------------------------
# On-the-fly accept: the Review modal renders each live value as an editable
# input + ✓; accepting pulls just that (possibly edited) field into the working
# copy via /field/edit-batch — no full pull. (Phase D.)
# ---------------------------------------------------------------------------


class TestReviewOnTheFlyAccept:
    def test_review_renders_editable_live_inputs(self, loaded_client, synth_folder):
        # Live chip drifted from the working copy on one field.
        _write_live_state(synth_folder, _make_state(f_01=7.0e9))
        html = loaded_client.get("/state/review").data.decode()
        # The changed path is offered as an editable live value + an accept button.
        assert "review-live-input" in html
        assert 'data-dot-path="qubits.qA1.f_01"' in html
        assert "reviewAccept(this)" in html
        # The live value is rendered grouped (lossless full digits + commas).
        assert "7,000,000,000" in html

    def test_accept_edited_live_value_lands_in_working_copy(self, loaded_client, synth_folder):
        _write_live_state(synth_folder, _make_state(f_01=7.0e9))
        # User tweaks the live value in the box (with grouping commas) and accepts.
        resp = loaded_client.post(
            "/field/edit-batch",
            json={"updates": [{"dot_path": "qubits.qA1.f_01", "value": "8,000,000,000"}]},
        )
        data = resp.get_json()
        assert data["ok"] is True
        assert data["results"][0]["applied"] is True
        # Comma grouping stripped + committed as the field's numeric type.
        assert data["results"][0]["new_value"] == 8.0e9
        # The edit is now pending in the working copy (the change log).
        review = loaded_client.get("/state/review").data.decode()
        assert "qubits.qA1.f_01" in review

    def test_apply_with_no_pending_just_syncs(self, loaded_client):
        data = loaded_client.post("/state/sync", data={"mode": "apply"}).get_json()
        assert data["status"] == "ok"
        assert data["mode"] == "apply"
        assert data["replay"] is None


# ---------------------------------------------------------------------------
# Regression: the review overlay opens on a CLEAN working copy that has merely
# drifted from live (the qualibrate-just-fit case), so unsaved==0 and the only
# server-rendered Sync button is "Pull & discard". Accepting an edited live
# value on the fly makes the change log non-empty — the action bar must then
# offer an edit-PRESERVING sync (apply/reapply), never silently discard the
# value the user just accepted. The bug: the popup baked mode='discard' at
# render and never upgraded after an accept, so Sync wrote the qualibrate value
# to live + the table, dropping the user's edit.
# ---------------------------------------------------------------------------


class TestAcceptThenSyncPreservesEdit:
    def _diverge_clean(self, synth_folder, f_01=7.0e9):
        """Live drifts while the working copy stays clean (no pending edits)."""
        _write_live_state(synth_folder, _make_state(f_01=f_01))

    def test_clean_review_renders_trio_hidden_and_plain_pull_visible(
        self, loaded_client, synth_folder
    ):
        # Clean working copy, live drifted → unsaved==0.
        self._diverge_clean(synth_folder)
        html = loaded_client.get("/state/review").data.decode()
        # The edit-preserving trio is rendered but hidden (so JS can reveal it),
        # while the plain "pull the live state" button is the visible default.
        assert '<span class="review-sync-edits" hidden>' in html
        assert '<span class="review-sync-clean">' in html
        # The trio markup (apply + reapply) is present even though hidden.
        assert "doStateSync('apply')" in html
        assert "doStateSync('reapply')" in html

    def test_saved_unapplied_review_offers_safe_push_not_discard_only(
        self, loaded_client
    ):
        # Regression (data-loss, found by the adversarial review): "Save to working
        # state" clears the change log (unsaved==0) but sets working_dirty. The modal
        # must NOT leave the discard-only "Sync" (doStateSync('discard')) as the lone
        # visible action — that pull would silently overwrite the saved edits. It must
        # surface the safe direct push and hide the plain-pull span.
        loaded_client.post(
            "/field/edit-batch",
            json={"updates": [{"dot_path": "qubits.qA1.f_01", "value": "5.0e9"}]},
        )
        loaded_client.post("/save")
        html = loaded_client.get("/state/review").data.decode()
        # The saved-but-unapplied branch is visible; the destructive plain-pull span
        # and the (empty) change-log trio are both hidden.
        assert '<span class="review-sync-saved">' in html
        assert '<span class="review-sync-clean" hidden>' in html
        assert '<span class="review-sync-edits" hidden>' in html
        # It offers the safe direct push to live (which preserves the saved edits).
        assert "/state/apply-to-live" in html

    def test_saved_then_edited_tray_routes_to_safe_direct_push(self, loaded_client):
        # Grid ⚡ steering anchor (audit fix): after "Save to working state"
        # (working_dirty=1) AND a new pending edit (change_count>0), the tray must render
        # the SAFE direct push "Apply to live chip" (/state/apply-to-live) and carry
        # data-working-dirty="1" — NOT the ⚡ pull-merge "Apply to live now" (which would
        # drop the saved edits). The grid's applyEditsToLive() reads data-working-dirty to
        # pick the safe push, so this attribute + branch must stay correct.
        loaded_client.post(
            "/field/edit-batch",
            json={"updates": [{"dot_path": "qubits.qA1.f_01", "value": "5.0e9"}]},
        )
        loaded_client.post("/save")
        loaded_client.post(
            "/field/edit-batch",
            json={"updates": [{"dot_path": "qubits.qA1.f_01", "value": "5.1e9"}]},
        )
        html = loaded_client.get("/qubits").data.decode()
        assert 'data-working-dirty="1"' in html
        assert "/state/apply-to-live" in html
        assert "Apply to live chip" in html
        # The ⚡ pull-merge button must be suppressed in this state (it pulls first).
        assert "Apply to live now" not in html

    def test_after_accept_review_reveals_edit_preserving_sync(
        self, loaded_client, synth_folder
    ):
        # The server-side mirror of the JS reveal: once an edit sits in the
        # change log, a re-render flips the trio visible and hides the lone pull.
        self._diverge_clean(synth_folder)
        loaded_client.post(
            "/field/edit-batch",
            json={"updates": [{"dot_path": "qubits.qA1.f_01", "value": "8.0e9"}]},
        )
        html = loaded_client.get("/state/review").data.decode()
        assert '<span class="review-sync-edits">' in html  # visible (no hidden)
        assert '<span class="review-sync-clean" hidden>' in html

    def test_accept_edit_then_apply_writes_user_value_not_live(
        self, loaded_client, synth_folder
    ):
        # The core data-loss regression. Live was fit to 7e9; the user edits the
        # live value to 8e9 in the box, accepts it, then syncs with the
        # edit-preserving button. Live + working copy must end at 8e9 (the user's
        # value), NOT 7e9 (the qualibrate value).
        self._diverge_clean(synth_folder, f_01=7.0e9)
        accept = loaded_client.post(
            "/field/edit-batch",
            json={"updates": [{"dot_path": "qubits.qA1.f_01", "value": "8,000,000,000"}]},
        ).get_json()
        assert accept["ok"] is True
        assert accept["results"][0]["new_value"] == 8.0e9

        data = loaded_client.post("/state/sync", data={"mode": "apply"}).get_json()
        assert data["status"] == "ok"
        assert data["replay"]["applied"] == 1
        assert data["replay"]["failed"] == []
        # The live file holds the USER's edited value, on top of the pulled state.
        live = json.loads((synth_folder / "state.json").read_text(encoding="utf-8"))
        assert live["qubits"]["qA1"]["f_01"] == 8.0e9
        # Working copy == live; nothing left to reconcile.
        assert "No differences" in loaded_client.get("/state/review").data.decode()

    def test_accept_edit_then_reapply_keeps_edit_pending(
        self, loaded_client, synth_folder
    ):
        # The "review first" choice: the accepted edit survives the pull as a
        # pending change (not yet pushed to live).
        self._diverge_clean(synth_folder, f_01=7.0e9)
        loaded_client.post(
            "/field/edit-batch",
            json={"updates": [{"dot_path": "qubits.qA1.f_01", "value": "8.0e9"}]},
        )
        data = loaded_client.post("/state/sync", data={"mode": "reapply"}).get_json()
        assert data["status"] == "ok"
        assert data["replay"]["applied"] == 1
        # Live still holds the pulled qualibrate value; the edit is pending.
        live = json.loads((synth_folder / "state.json").read_text(encoding="utf-8"))
        assert live["qubits"]["qA1"]["f_01"] == 7.0e9
        review = loaded_client.get("/state/review").data.decode()
        assert "qubits.qA1.f_01" in review  # still diverges → edit is pending

    def test_accept_survives_live_changing_field_type_to_list(
        self, loaded_client, synth_folder
    ):
        # Replay must honor the user's accepted value even when the pulled live
        # state changed the field's TYPE. Here qualibrate rewrote f_01 as a list;
        # re-coercing the user's scalar against a list used to raise → the edit
        # was dropped and the list (qualibrate's value) won on live. coerce=False
        # in the 'set' replay branch keeps the user's value.
        live_state = _make_state()
        live_state["qubits"]["qA1"]["f_01"] = [7.0e9, 7.1e9]
        _write_live_state(synth_folder, live_state)
        loaded_client.post(
            "/field/edit-batch",
            json={"updates": [{"dot_path": "qubits.qA1.f_01", "value": "8.0e9"}]},
        )
        data = loaded_client.post("/state/sync", data={"mode": "apply"}).get_json()
        assert data["status"] == "ok"
        assert data["replay"]["applied"] == 1
        assert data["replay"]["failed"] == []
        live = json.loads((synth_folder / "state.json").read_text(encoding="utf-8"))
        assert live["qubits"]["qA1"]["f_01"] == 8.0e9  # user's value, not the list

    def test_accept_survives_live_changing_field_type_to_string(
        self, loaded_client, synth_folder
    ):
        # The more insidious variant: qualibrate left a string placeholder at the
        # path. Re-coercing the user's float against a str used to stringify it to
        # "8000000000.0" and report SUCCESS — corrupting the live file's numeric
        # field. coerce=False writes the real number.
        live_state = _make_state()
        live_state["qubits"]["qA1"]["f_01"] = "placeholder"
        _write_live_state(synth_folder, live_state)
        loaded_client.post(
            "/field/edit-batch",
            json={"updates": [{"dot_path": "qubits.qA1.f_01", "value": "8.0e9"}]},
        )
        data = loaded_client.post("/state/sync", data={"mode": "apply"}).get_json()
        assert data["status"] == "ok"
        assert data["replay"]["applied"] == 1
        live = json.loads((synth_folder / "state.json").read_text(encoding="utf-8"))
        assert live["qubits"]["qA1"]["f_01"] == 8.0e9
        assert not isinstance(live["qubits"]["qA1"]["f_01"], str)


# ---------------------------------------------------------------------------
# Audit finding #3: pull-with-reapply must NOT clobber a qualibrate-created twin.
# If the user created key X in their session and the pulled live ALSO has X (with
# real calibrated params), replaying the user's create used to overwrite the
# calibrated subtree wholesale and count it as a silent success. Now a differing
# value is kept (live) + reported; only a byte-equal value is a no-op apply.
# ---------------------------------------------------------------------------


class TestReplayCreateCollision:
    def _store_mod(self, state):
        from quam_state_manager.core.loader import QuamStore
        from quam_state_manager.core.modifier import Modifier
        store = QuamStore.from_dicts(state, _make_wiring())
        return store, Modifier(store)

    def test_create_collision_with_different_value_keeps_live(self):
        from quam_state_manager.web import routes
        # The pulled live already has x90 with qualibrate's calibrated params.
        state = _make_state()
        state["qubits"]["qA1"]["xy"]["operations"]["x90"] = {"amplitude": 0.9, "length": 40}
        store, mod = self._store_mod(state)
        # The user's session created x90 with DIFFERENT (stale) params.
        updates = {"qubits.qA1.xy.operations.x90":
                   ("create", {"amplitude": 0.1, "length": 40})}
        res = routes._replay_updates(mod, updates)
        assert res["applied"] == 0
        assert len(res["failed"]) == 1
        assert "kept the live version" in res["failed"][0]["error"]
        # qualibrate's calibrated amplitude is preserved — NOT clobbered to 0.1.
        assert store.get_value("qubits.qA1.xy.operations.x90.amplitude") == 0.9

    def test_create_collision_with_equal_value_is_noop_applied(self):
        from quam_state_manager.web import routes
        state = _make_state()
        state["qubits"]["qA1"]["xy"]["operations"]["x90"] = {"amplitude": 0.1, "length": 40}
        store, mod = self._store_mod(state)
        updates = {"qubits.qA1.xy.operations.x90":
                   ("create", {"amplitude": 0.1, "length": 40})}
        res = routes._replay_updates(mod, updates)
        assert res["applied"] == 1
        assert res["failed"] == []


# ---------------------------------------------------------------------------
# Audit finding #4: the ✓ "pull just this field" on an ADDED diff row (qualibrate
# added new structure) used to KeyError because accept only did set_value and the
# working copy lacked the key. The accept path is now op-aware: an "added" row
# sends create:true and the server create_subtree's it; a wholly-new subtree
# (parent absent) returns a clear "use Pull" message instead of a raw KeyError.
# ---------------------------------------------------------------------------


class TestAcceptAddedRow:
    def test_review_renders_added_row_class(self, loaded_client, synth_folder):
        live = _make_state()
        live["qubits"]["qA1"]["T2echo"] = 3.3e-6  # a new leaf qualibrate added
        _write_live_state(synth_folder, live)
        html = loaded_client.get("/state/review").data.decode()
        assert "diff-row-added" in html
        assert 'data-dot-path="qubits.qA1.T2echo"' in html

    def test_accept_added_leaf_creates_with_flag(self, loaded_client):
        # Working copy has qA1 but no T2echo. create:true must CREATE it, not 400.
        resp = loaded_client.post(
            "/field/edit-batch",
            json={"updates": [{"dot_path": "qubits.qA1.T2echo",
                               "value": "3.3e-6", "create": True}]},
        )
        data = resp.get_json()
        assert data["ok"] is True
        assert data["results"][0]["applied"] is True
        # Now navigable in the working copy — it shows up if we drift+review.
        assert "qubits.qA1.T2echo" in loaded_client.get("/changes").data.decode()

    def test_accept_added_leaf_without_flag_fails(self, loaded_client):
        # No create flag → set_value KeyErrors → 400 (the flag gates creation, so a
        # generic bulk/plot edit can't silently create a mistyped path).
        resp = loaded_client.post(
            "/field/edit-batch",
            json={"updates": [{"dot_path": "qubits.qA1.T2echo", "value": "3.3e-6"}]},
        )
        assert resp.status_code == 400
        assert resp.get_json()["ok"] is False

    def test_accept_added_whole_subtree_guides_to_pull(self, loaded_client):
        # Parent absent (a wholly-new qubit) → clear "use Pull" message, not KeyError.
        resp = loaded_client.post(
            "/field/edit-batch",
            json={"updates": [{"dot_path": "qubits.qZ9.f_01",
                               "value": "5e9", "create": True}]},
        )
        assert resp.status_code == 400
        err = (resp.get_json().get("results") or [{}])[0].get("error", "")
        assert "Pull" in err


class TestPulledOtherChanges:
    """mode=apply reports whether the pull absorbed live changes BEYOND the
    user's own edits — the client uses it to refresh the surface only when the
    on-screen values are provably stale (the blink-vs-stale-grid balance)."""

    def test_false_when_live_unchanged(self, loaded_client):
        _edit(loaded_client, "qubits.qA1.f_01", 6.3e9)
        data = loaded_client.post("/state/sync", data={"mode": "apply"}).get_json()
        assert data["status"] == "ok"
        assert data["pulled_other_changes"] is False

    def test_true_when_external_writer_changed_live(self, loaded_client, synth_folder):
        _edit(loaded_client, "qubits.qA1.f_01", 6.3e9)
        # An experiment rewrites the live file between the edit and the apply.
        state = json.loads((synth_folder / "state.json").read_text())
        state["qubits"]["qA1"]["T1"] = 99e-6
        _write_live_state(synth_folder, state)   # future-mtime external write
        data = loaded_client.post("/state/sync", data={"mode": "apply"}).get_json()
        assert data["status"] == "ok"
        assert data["pulled_other_changes"] is True
