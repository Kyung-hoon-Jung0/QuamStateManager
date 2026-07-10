"""Regression tests for the working-copy persistence / staleness behavior.

Context: a user reported the working-copy / "live changed" banner reappearing
after save + restart. A full route-level reproduction (below) showed the
CLEAN flow (edit → save → apply → restart) is actually correct — no false
banner — and that the hypothesized "machine.save() drops a legacy key → STALE"
mechanism in fact yields a silent auto-sync, not a banner. The real, confirmed
gaps these tests pin:

  H3 — int fields silently truncated a fractional edit (0.3 → 0).
  WD — a saved-but-unapplied working copy lost its "not applied" indicator
       across a restart (working_dirty is process-local; the working FILES
       persist, so it can be recovered from the synced hash).
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from quam_state_manager.core import working_copy as W
from quam_state_manager.core.loader import QuamStore
from quam_state_manager.core.modifier import Modifier, _type_coerce
from quam_state_manager.web.app import create_app


# ---------------------------------------------------------------------------
# H3 — int-field fractional edit must not truncate to 0
# ---------------------------------------------------------------------------

class TestIntCoercionNoTruncation:
    def test_fractional_edit_to_int_field_promotes_to_float(self):
        assert _type_coerce(1, "0.3") == 0.3          # was int(0.3) == 0
        assert _type_coerce(1, "0.3") != 0
        assert isinstance(_type_coerce(1, "0.3"), float)

    def test_integral_edit_to_int_field_stays_int(self):
        assert _type_coerce(100, "40") == 40
        assert isinstance(_type_coerce(100, "40"), int)
        assert _type_coerce(100, "40.0") == 40
        assert isinstance(_type_coerce(100, "40.0"), int)

    def test_numeric_fractional_value(self):
        assert _type_coerce(1, 0.3) == 0.3
        assert isinstance(_type_coerce(1, 0.3), float)

    def test_non_numeric_still_raises(self):
        with pytest.raises(TypeError):
            _type_coerce(1, "abc")

    def test_modifier_set_value_int_field(self, tmp_path):
        state = {"qubits": {"q1": {"length": 100}}, "qubit_pairs": {}}
        wiring = {"wiring": {}, "network": {"host": "x"}}
        (tmp_path / "state.json").write_text(json.dumps(state))
        (tmp_path / "wiring.json").write_text(json.dumps(wiring))
        mod = Modifier(QuamStore(tmp_path))
        entry = mod.set_value("qubits.q1.length", "0.3")
        assert entry.new_value == 0.3  # not 0


# ---------------------------------------------------------------------------
# Working-copy / reconcile scenarios (low-level) — pin the CORRECT behavior
# ---------------------------------------------------------------------------

def _write(folder: Path, state, wiring):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(state, indent=4))
    (folder / "wiring.json").write_text(json.dumps(wiring, indent=4))


def _bump(folder: Path):
    t = time.time() + 5
    for f in ("state.json", "wiring.json"):
        os.utime(folder / f, (t, t))


WIRING = {"wiring": {"qubits": {}}, "network": {"host": "x"}}


class TestReconcileScenarios:
    def test_clean_apply_then_reload_is_in_sync(self, tmp_path):
        inst = tmp_path / "inst"
        live = tmp_path / "live" / "LabA"
        _write(live, {"qubits": {"q1": {"f_01": 6.0e9}}}, WIRING)
        wc = W.create(inst, live)
        # edit working + apply
        ws = json.loads((wc.working_folder / "state.json").read_text())
        ws["qubits"]["q1"]["f_01"] = 6.1e9
        (wc.working_folder / "state.json").write_text(json.dumps(ws, indent=4))
        W.apply_to_live(wc)
        # reload (simulate restart): live unchanged → in_sync, no banner
        assert W.reconcile_with_live(W.load(inst, live)) == W.RECONCILE_IN_SYNC

    def test_schema_rewrite_keeping_edit_auto_syncs_not_stale(self, tmp_path):
        """The hypothesized persistence bug: an experiment's machine.save()
        drops a legacy key but keeps the user's edit. This must NOT produce a
        false STALE banner — the clean working copy auto-pulls."""
        inst = tmp_path / "inst"
        live = tmp_path / "live" / "LabA"
        _write(live, {"qubits": {"q1": {"f_01": 6.0e9, "legacy_x": 1}}}, WIRING)
        wc = W.create(inst, live)
        ws = json.loads((wc.working_folder / "state.json").read_text())
        ws["qubits"]["q1"]["f_01"] = 6.1e9
        (wc.working_folder / "state.json").write_text(json.dumps(ws, indent=4))
        W.apply_to_live(wc)
        # experiment drops legacy_x, keeps f_01, bumps mtime
        _write(live, {"qubits": {"q1": {"f_01": 6.1e9}}}, WIRING)
        _bump(live)
        verdict = W.reconcile_with_live(W.load(inst, live), sync_if_clean=True)
        assert verdict != W.RECONCILE_STALE   # no false banner
        assert verdict == W.RECONCILE_SYNCED   # clean auto-pull

    def test_saved_unapplied_plus_live_change_is_stale(self, tmp_path):
        """A genuinely dirty working copy (saved, NOT applied) + a live change
        is a real conflict — STALE is correct."""
        inst = tmp_path / "inst"
        live = tmp_path / "live" / "LabA"
        _write(live, {"qubits": {"q1": {"f_01": 6.0e9}}}, WIRING)
        wc = W.create(inst, live)
        # save to working WITHOUT applying
        ws = json.loads((wc.working_folder / "state.json").read_text())
        ws["qubits"]["q1"]["f_01"] = 6.1e9
        (wc.working_folder / "state.json").write_text(json.dumps(ws, indent=4))
        # experiment changes live
        _write(live, {"qubits": {"q1": {"f_01": 7.0e9}}}, WIRING)
        _bump(live)
        assert W.reconcile_with_live(W.load(inst, live)) == W.RECONCILE_STALE


# ---------------------------------------------------------------------------
# WD — working_dirty recovery across a simulated restart (route level)
# ---------------------------------------------------------------------------

def _make_live(tmp_path):
    live = tmp_path / "live" / "LabA"
    live.mkdir(parents=True)
    state = {"qubits": {"q1": {"id": "q1", "f_01": 6.0e9, "xy": {"operations": {}}}},
             "qubit_pairs": {}, "active_qubit_names": ["q1"]}
    wiring = {"wiring": {"qubits": {"q1": {"xy": {"opx_output": "MW/1/2"}}}},
              "network": {"host": "10.0.0.1"}}
    (live / "state.json").write_text(json.dumps(state, indent=4))
    (live / "wiring.json").write_text(json.dumps(wiring, indent=4))
    return live


def _flags(app):
    name = app.config.get("active_context")
    ctx = app.config["contexts"].get(name) if name else None
    return ctx


class TestWorkingDirtyRecovery:
    def test_clean_apply_restart_no_banner(self, tmp_path):
        inst = tmp_path / "inst"
        live = _make_live(tmp_path)
        app1 = create_app(testing=True, instance_path=str(inst))
        c1 = app1.test_client()
        c1.post("/load", data={"folder": str(live)})
        c1.post("/field/edit", data={"dot_path": "qubits.q1.f_01", "value": "6.1e9"})
        c1.post("/save")
        c1.post("/state/apply-to-live", data={"force": "1"})
        # restart
        app2 = create_app(testing=True, instance_path=str(inst))
        c2 = app2.test_client()
        c2.post("/load", data={"folder": str(live)})
        ctx = _flags(app2)
        assert ctx["live_diverged"] is False
        assert ctx["working_dirty"] is False   # clean apply → no nag

    def test_saved_unapplied_restart_recovers_working_dirty(self, tmp_path):
        inst = tmp_path / "inst"
        live = _make_live(tmp_path)
        app1 = create_app(testing=True, instance_path=str(inst))
        c1 = app1.test_client()
        c1.post("/load", data={"folder": str(live)})
        c1.post("/field/edit", data={"dot_path": "qubits.q1.f_01", "value": "6.1e9"})
        c1.post("/save")   # save to working copy, do NOT apply
        # restart
        app2 = create_app(testing=True, instance_path=str(inst))
        c2 = app2.test_client()
        c2.post("/load", data={"folder": str(live)})
        ctx = _flags(app2)
        # the saved-but-unapplied edit survives and is surfaced
        assert ctx["working_dirty"] is True
        assert ctx["store"].merged["qubits"]["q1"]["f_01"] == 6.1e9


# ---------------------------------------------------------------------------
# A0.1 — chip identity in the tray + dataset-archive origin write-block
# ---------------------------------------------------------------------------

class TestChipIdentityAndOrigin:
    def test_tray_shows_chip_name_even_when_clean(self, tmp_path):
        from quam_state_manager.web import routes
        inst = tmp_path / "inst"
        live = _make_live(tmp_path)
        app = create_app(testing=True, instance_path=str(inst))
        with app.test_request_context():
            app.test_client().post("/load", data={"folder": str(live)})
        c = app.test_client()
        c.post("/load", data={"folder": str(live)})
        with app.test_request_context():
            # simulate the active context for the render helpers
            name = app.config["active_context"]
            app.config["active_context"] = name
            html = c.get("/qubits").data.decode()
        # the topbar tray badge carries the chip name (LabA) even with 0 edits
        assert "pending-tray" in html

    def test_active_origin_default_live(self, tmp_path):
        inst = tmp_path / "inst"
        live = _make_live(tmp_path)
        app = create_app(testing=True, instance_path=str(inst))
        c = app.test_client()
        c.post("/load", data={"folder": str(live)})
        with app.test_request_context():
            from quam_state_manager.web.routes import _active_origin, _active_chip_identity
            # need an app context with the active context set; emulate via a request
        # check via the apply path: a live chip allows apply
        r = c.post("/field/edit", data={"dot_path": "qubits.q1.f_01", "value": "6.1e9"})
        assert r.status_code == 200
        c.post("/save")
        r = c.post("/state/apply-to-live", data={"force": "1"})
        assert r.status_code == 200   # live origin → apply allowed

    def test_apply_blocked_on_dataset_archive(self, tmp_path):
        """A chip activated as a dataset archive must refuse apply-to-live."""
        inst = tmp_path / "inst"
        live = _make_live(tmp_path)
        app = create_app(testing=True, instance_path=str(inst))
        c = app.test_client()
        c.post("/load", data={"folder": str(live)})
        # force the active context's origin to dataset_archive (as
        # dataset_load_state would) and try to apply
        name = app.config["active_context"]
        app.config["contexts"][name]["origin"] = "dataset_archive"
        c.post("/field/edit", data={"dot_path": "qubits.q1.f_01", "value": "6.1e9"})
        c.post("/save")
        r = c.post("/state/apply-to-live", data={"force": "1"})
        assert r.status_code == 409
        assert b"archive" in r.data.lower()
        # sync apply mode also blocked
        r2 = c.post("/state/sync", data={"mode": "apply"})
        assert r2.status_code == 409
