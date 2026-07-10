"""Tests for the State History page (sidebar peer of Bulk Edit): a view +
restore layer over the existing HistoryManager snapshot store.

Restore Mode 1 (stage into working copy → review → apply) and Mode 2 (replace
live directly, gated by origin / fingerprint-align / build-lock / post-rebuild)
plus labels/pinning and the experiment-attribution framing.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from quam_state_manager.core import working_copy as W
from quam_state_manager.web.app import create_app


def _make_live(tmp_path, f01=6.0e9):
    live = tmp_path / "live" / "LabA"
    live.mkdir(parents=True)
    state = {"qubits": {"q1": {"id": "q1", "f_01": f01, "xy": {"operations": {}}}},
             "qubit_pairs": {}, "active_qubit_names": ["q1"]}
    wiring = {"wiring": {"qubits": {"q1": {"xy": {"opx_output": "MW/1/2"}}}},
              "network": {"host": "10.0.0.1"}}
    (live / "state.json").write_text(json.dumps(state, indent=4))
    (live / "wiring.json").write_text(json.dumps(wiring, indent=4))
    return live


@pytest.fixture
def app(tmp_path):
    return create_app(testing=True, instance_path=str(tmp_path / "_inst"))


@pytest.fixture
def live(tmp_path):
    return _make_live(tmp_path)


@pytest.fixture
def client(app, live):
    c = app.test_client()
    c.post("/load", data={"folder": str(live)})
    return c


def _take_snapshot(client):
    """Force a manual snapshot, return its timestamp."""
    client.post("/api/history/snapshot")
    r = client.get("/state-history").data.decode()
    import re
    ts = re.findall(r'data-ts="([^"]+)"', r)
    return ts


class TestStateHistoryPage:
    def test_page_renders(self, client):
        html = client.get("/state-history").data.decode()
        assert "State History" in html
        assert 'href="/state-history"' in html   # sidebar entry

    def test_no_state(self, app):
        html = app.test_client().get("/state-history").data.decode()
        assert "No chip loaded" in html

    def test_snapshot_appears_with_actions(self, client):
        ts = _take_snapshot(client)
        assert ts
        html = client.get("/state-history").data.decode()
        assert "Load as working state" in html
        assert "Restore to live" in html
        assert "View changes vs current" in html

    def test_take_snapshot_returns_state_history_not_param_panel(self, client):
        # the dedicated route re-renders the State History page (timeline +
        # restore/pin controls), NOT the Param-History panel that would wipe it.
        r = client.post("/state-history/snapshot")
        assert r.status_code == 200
        html = r.data.decode()
        assert "State History" in html
        assert 'id="state-history-detail"' in html   # timeline fragment intact
        assert "Restore to live" in html

    def test_page_has_timeline_autorefresh_listener(self, client):
        # the page carries a hidden listener that re-fetches the timeline body
        # on stateRestored, OUTSIDE #state-history-body so the swap can't drop it.
        _take_snapshot(client)
        html = client.get("/state-history").data.decode()
        # listens for stateRestored (stage/restore) AND stateHistoryChanged (a routine
        # apply-to-live, which must NOT fire stateRestored — that blanks the inspector)
        assert "stateRestored from:body" in html and "stateHistoryChanged from:body" in html
        assert "/state-history?body=1" in html

    def test_body_mode_returns_timeline_only(self, client):
        # body=1 returns just the timeline inner (for the auto-refresh swap into
        # #state-history-body) — no outer body div, no detail pane (which would
        # nest / clobber the result shown beside it).
        _take_snapshot(client)
        r = client.get("/state-history?body=1")
        assert r.status_code == 200
        html = r.data.decode()
        assert "sh-timeline" in html and "Restore to live" in html
        assert 'id="state-history-body"' not in html
        assert 'id="state-history-detail"' not in html


class TestRestoreMode1Stage:
    def test_stage_loads_snapshot_into_working_copy(self, client, live):
        # snapshot the original, then change the working state, then stage back
        ts = _take_snapshot(client)[0]
        client.post("/field/edit", data={"dot_path": "qubits.q1.f_01", "value": "9.9e9"})
        client.post("/save")
        # stage the original snapshot (force past the pending-edits guard)
        r = client.post(f"/state-history/{ts}/stage", data={"force": "1"})
        assert r.status_code == 200
        # staging replaces the working copy wholesale → refresh open pulse
        # tables (pulses-changed) AND any open inspector (stateRestored).
        assert r.headers.get("HX-Trigger") == "pulses-changed, stateRestored, diagnostics-changed"
        # working copy now reflects the snapshot (f_01 back to 6.0e9), but live
        # is untouched until the user applies
        peek = client.get("/field/peek?dot_path=qubits.q1.f_01").get_json()
        assert peek["values"]["qubits.q1.f_01"] == 6.0e9
        live_state = json.loads((live / "state.json").read_text())
        assert live_state["qubits"]["q1"]["f_01"] == 6.0e9  # original (untouched)

    def test_stage_guards_pending_edits(self, client):
        ts = _take_snapshot(client)[0]
        client.post("/field/edit", data={"dot_path": "qubits.q1.f_01", "value": "9.9e9"})
        # without force, staging over pending edits is refused
        r = client.post(f"/state-history/{ts}/stage")
        assert r.status_code == 409
        assert b"unsaved edits" in r.data.lower()


class TestRestoreMode2Live:
    def test_restore_live_replaces_and_is_reversible(self, client, live):
        # snapshot A (f_01=6.0e9), then edit+apply to make live f_01=7.0e9
        ts_a = _take_snapshot(client)[0]
        client.post("/field/edit", data={"dot_path": "qubits.q1.f_01", "value": "7.0e9"})
        client.post("/save")
        client.post("/state/apply-to-live", data={"force": "1"})
        assert json.loads((live / "state.json").read_text())["qubits"]["q1"]["f_01"] == 7.0e9
        # restore-live to snapshot A
        r = client.post(f"/state-history/{ts_a}/restore-live", data={"force": "1"})
        assert r.status_code == 200
        # live is now back to 6.0e9
        assert json.loads((live / "state.json").read_text())["qubits"]["q1"]["f_01"] == 6.0e9
        # and a pre-restore snapshot of the 7.0e9 state was captured (reversible)
        html = client.get("/state-history").data.decode()
        import re
        assert len(re.findall(r'data-ts=', html)) >= 2

    def test_restore_live_blocked_on_dataset_archive(self, client):
        ts = _take_snapshot(client)[0]
        name = list(client.application.config["contexts"].keys())[0]
        client.application.config["contexts"][name]["origin"] = "dataset_archive"
        r = client.post(f"/state-history/{ts}/restore-live", data={"force": "1"})
        assert r.status_code == 409
        assert b"archive" in r.data.lower()

    def test_restore_live_caches_rebuilt(self, client, app, live):
        ts_a = _take_snapshot(client)[0]
        client.post("/field/edit", data={"dot_path": "qubits.q1.f_01", "value": "7.0e9"})
        client.post("/save")
        client.post("/state/apply-to-live", data={"force": "1"})
        client.post(f"/state-history/{ts_a}/restore-live", data={"force": "1"})
        # the store/engine reflect the restored content (no stale-chip)
        name = list(app.config["contexts"].keys())[0]
        store = app.config["contexts"][name]["store"]
        assert store.merged["qubits"]["q1"]["f_01"] == 6.0e9
        # working copy is in sync with live → no nag flags
        assert app.config["contexts"][name]["working_dirty"] is False


class TestRestoreLiveSafetyGates:
    """Gates surfaced by the adversarial review of the restore-live route."""

    def _ctx(self, app):
        name = list(app.config["contexts"].keys())[0]
        return app.config["contexts"][name]

    def test_restore_live_warns_on_unsaved_edits(self, client):
        # in-memory (unsaved) edits must not be silently overwritten by a
        # direct restore-live — the route warns (409) with a force button.
        ts = _take_snapshot(client)[0]
        client.post("/field/edit",
                    data={"dot_path": "qubits.q1.f_01", "value": "9.9e9"})
        r = client.post(f"/state-history/{ts}/restore-live")
        assert r.status_code == 409
        body = r.data.decode().lower()
        assert "unsaved edits" in body
        assert "force_pending=1" in body     # the proceed button re-posts forced

    def test_force_pending_does_not_collapse_align_gate(self, client, app, monkeypatch):
        # THE P0 fix: one token must not bypass both gates. Forcing past the
        # unsaved-edits gate must STILL surface the wiring-mismatch warning
        # before live wiring is overwritten.
        ts = _take_snapshot(client)[0]
        client.post("/field/edit",
                    data={"dot_path": "qubits.q1.f_01", "value": "9.9e9"})
        import quam_state_manager.core.history as H
        monkeypatch.setattr(H, "align", lambda a, b: "topology_changed")
        r = client.post(f"/state-history/{ts}/restore-live",
                        data={"force_pending": "1"})
        assert r.status_code == 409                 # not a 200 silent restore
        body = r.data.decode().lower()
        assert "does not match" in body             # align warning shown
        assert "force_align=1" in body

    def test_restore_live_force_overrides_unsaved_warning(self, client, live):
        ts = _take_snapshot(client)[0]
        client.post("/field/edit",
                    data={"dot_path": "qubits.q1.f_01", "value": "9.9e9"})
        r = client.post(f"/state-history/{ts}/restore-live", data={"force": "1"})
        assert r.status_code == 200

    def test_restore_live_gated_by_working_dirty_alone(self, client, app):
        # after an LRU eviction / restart, saved-but-unapplied edits survive
        # only as working_dirty (change_log empty, pending_reapply None). The
        # gate must still warn (409), not silently overwrite them.
        ts = _take_snapshot(client)[0]
        name = list(app.config["contexts"].keys())[0]
        ctx = app.config["contexts"][name]
        ctx["working_dirty"] = True            # simulate recovered saved-unapplied state
        r = client.post(f"/state-history/{ts}/restore-live")
        assert r.status_code == 409
        assert b"unsaved edits" in r.data.lower()

    def test_restore_live_clears_stale_reapply_stash(self, client, app):
        # edit+save populates the reapply stash; a subsequent restore-live makes
        # that stash stale (it targets the pre-restore state). The route must
        # drop it so a later sync 'reapply' can't replay it onto the restored
        # chip (the confirmed missing-_clear_reapply finding).
        ts_a = _take_snapshot(client)[0]
        client.post("/field/edit",
                    data={"dot_path": "qubits.q1.f_01", "value": "7.0e9"})
        client.post("/save")
        ctx = self._ctx(app)
        assert ctx.get("pending_reapply")          # stash present after save
        r = client.post(f"/state-history/{ts_a}/restore-live", data={"force": "1"})
        assert r.status_code == 200
        assert not ctx.get("pending_reapply")       # stash dropped after restore

    def test_align_mismatch_offers_force_button(self, client, app, monkeypatch):
        # a non-aligned snapshot is refused (409) but the warning carries a
        # force-to-restore button so the gate isn't a dead end.
        ts = _take_snapshot(client)[0]
        import quam_state_manager.core.history as H
        monkeypatch.setattr(H, "align", lambda a, b: "topology_changed")
        r = client.post(f"/state-history/{ts}/restore-live")
        assert r.status_code == 409
        body = r.data.decode().lower()
        assert "does not match" in body
        assert "force_align=1" in body

    def test_stage_guard_offers_force_button(self, client):
        ts = _take_snapshot(client)[0]
        client.post("/field/edit",
                    data={"dot_path": "qubits.q1.f_01", "value": "9.9e9"})
        r = client.post(f"/state-history/{ts}/stage")
        assert r.status_code == 409
        body = r.data.decode().lower()
        assert "unsaved edits" in body
        assert "force=1" in body and "anyway" in body


class TestBuildLockBinding:
    """The build lock must pin to the folder being written, not the live
    active context (a concurrent /load could otherwise hand back the wrong
    folder's lock mid-write)."""

    def test_active_wc_lock_binds_to_passed_ctx(self, app):
        from quam_state_manager.web import routes
        lock_a = routes._get_quam_build_lock("/chipA")
        lock_b = routes._get_quam_build_lock("/chipB")
        assert lock_a is not lock_b
        # passing a captured ctx pins to THAT folder regardless of active ctx
        assert routes._active_wc_lock({"path": "/chipA"}) is lock_a
        assert routes._active_wc_lock({"path": "/chipB"}) is lock_b


class TestLabelPin:
    def test_pin_and_unpin(self, client):
        ts = _take_snapshot(client)[0]
        r = client.post(f"/state-history/{ts}/label", data={"pinned": "1"})
        assert r.status_code == 200
        assert "Unpin" in r.data.decode()        # now pinned → button flips
        r2 = client.post(f"/state-history/{ts}/label", data={"pinned": "0"})
        assert "Pin" in r2.data.decode()

    def test_label_set(self, client):
        ts = _take_snapshot(client)[0]
        r = client.post(f"/state-history/{ts}/label",
                        data={"label": "known-good baseline", "pinned": "1"})
        assert b"known-good baseline" in r.data


class TestPinnedExemptFromPrune:
    def test_pinned_snapshot_survives_prune(self, tmp_path):
        from quam_state_manager.core.history import HistoryManager
        inst = tmp_path / "inst"
        live = _make_live(tmp_path)
        hm = HistoryManager(str(inst), max_snapshots=1)
        # snapshot A, pin it, then snapshot B+C (which would prune A unpinned)
        a = hm.check_and_snapshot(str(live), "manual", force=True)
        hm.annotate_snapshot(str(live), a.timestamp, label="golden", pinned=True)
        for f in (6.1e9, 6.2e9):
            s = json.loads((live / "state.json").read_text())
            s["qubits"]["q1"]["f_01"] = f
            (live / "state.json").write_text(json.dumps(s, indent=4))
            t = time.time() + 5
            import os
            os.utime(live / "state.json", (t, t)); os.utime(live / "wiring.json", (t, t))
            hm.check_and_snapshot(str(live), "manual", force=True)
        timestamps = [m.timestamp for m in hm.list_snapshots(str(live))]
        assert a.timestamp in timestamps   # pinned snapshot survived the prune
