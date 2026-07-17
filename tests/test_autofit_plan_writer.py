"""plan.py validation/resolution + writer.py edge semantics (docs/56 §3, §2f,
§7b-C/D). The E2E tier covers the happy write paths; these pin the edges:
CAS conflicts, non-revertible patch ops, exact-typed restores, preset
portability resolution."""
from __future__ import annotations

import threading

import pytest

from quam_state_manager.core import safe_io, working_copy
from quam_state_manager.core.autofit import plan as af_plan
from quam_state_manager.core.autofit import synth, writer
from quam_state_manager.core.loader import QuamStore
from quam_state_manager.core.modifier import Modifier
from quam_state_manager.core.saver import Saver


# ---------------------------------------------------------------------------
# plan.py
# ---------------------------------------------------------------------------

class TestPlanValidation:
    def test_presets_validate_and_carry_families(self):
        for key in af_plan.PRESETS:
            p = af_plan.preset_plan(key)
            assert p.steps
            assert all(s.family or s.node for s in p.steps)

    @pytest.mark.parametrize("mutation,msg", [
        ({"name": ""}, "name"),
        ({"targets_kind": "couplers"}, "targets_kind"),
        ({"autonomy": "yolo"}, "autonomy"),
        ({"steps": []}, "step"),
        ({"steps": [{"id": "a"}]}, "node file or a family"),
        ({"steps": [{"id": "a", "node": "../evil.py"}]}, "relative"),
        ({"steps": [{"id": "a", "node": "x.txt"}]}, ".py"),
        ({"steps": [{"id": "a", "family": "T1", "retry_max": 9}]}, "retry_max"),
        ({"steps": [{"id": "a", "family": "T1"},
                    {"id": "a", "family": "echo"}]}, "duplicate"),
    ])
    def test_rejects_bad_plans(self, mutation, msg):
        raw = {"name": "x", "targets_kind": "qubits",
               "steps": [{"id": "s1", "family": "T1"}]}
        raw.update(mutation)
        with pytest.raises(af_plan.PlanError, match=msg):
            af_plan.validate_plan(raw)

    def test_persistence_round_trip(self, tmp_path):
        p = af_plan.preset_plan("1q_bringup")
        pid = af_plan.save_plan(tmp_path, p)
        loaded = af_plan.load_plans(tmp_path)
        assert pid in loaded
        assert af_plan.validate_plan(loaded[pid]).name == "1Q bringup"
        assert af_plan.delete_plan(tmp_path, pid) is True
        assert af_plan.load_plans(tmp_path) == {}


class TestStepResolution:
    AVAILABLE = [
        {"name": "08_qubit_spectroscopy", "path": "/lib/08_qubit_spectroscopy.py"},
        {"name": "1Q_08_qubit_spectroscopy_new",
         "path": "/lib/1Q_08_qubit_spectroscopy_new.py"},
        {"name": "25_T1", "path": "/lib/25_T1.py"},
        {"name": "12_ramsey", "path": "/lib/12_ramsey.py"},
    ]

    def _plan(self, steps):
        return af_plan.validate_plan({"name": "x", "targets_kind": "qubits",
                                      "steps": steps})

    def test_family_resolves_across_lab_renumbering(self):
        p = self._plan([{"id": "t1", "family": "T1"},
                        {"id": "ramsey", "family": "ramsey"}])
        res = af_plan.resolve_steps(p, self.AVAILABLE)
        assert res["t1"] == {"status": "resolved", "path": "/lib/25_T1.py",
                             "candidates": ["/lib/25_T1.py"]}
        assert res["ramsey"]["status"] == "resolved"

    def test_ambiguity_prefers_exact_family_and_surfaces_candidates(self):
        p = self._plan([{"id": "qs", "family": "qubit_spectroscopy"}])
        res = af_plan.resolve_steps(p, self.AVAILABLE)
        # the plain node is the exact normalized match; the _new variant stays
        # visible as a candidate for the UI dropdown
        assert res["qs"]["status"] == "resolved"
        assert res["qs"]["path"] == "/lib/08_qubit_spectroscopy.py"
        assert len(res["qs"]["candidates"]) == 2

    def test_missing_family_is_missing(self):
        p = self._plan([{"id": "x", "family": "chevron_11_02"}])
        res = af_plan.resolve_steps(p, self.AVAILABLE)
        assert res["x"]["status"] == "missing"

    def test_explicit_node_file_wins(self):
        p = self._plan([{"id": "qs", "family": "qubit_spectroscopy",
                         "node": "1Q_08_qubit_spectroscopy_new.py"}])
        res = af_plan.resolve_steps(p, self.AVAILABLE)
        assert res["qs"]["path"] == "/lib/1Q_08_qubit_spectroscopy_new.py"


# ---------------------------------------------------------------------------
# writer.py edges (over a real store on a sim chip)
# ---------------------------------------------------------------------------

@pytest.fixture
def world(tmp_path):
    chip = synth.make_sim_chip(("qA1",), (), seed=5)
    live = tmp_path / "live"
    live.mkdir()
    safe_io.write_state_wiring(live, chip.state, chip.wiring)
    wc = working_copy.create(tmp_path / "inst", live)
    store = QuamStore(wc.working_folder)
    handle = writer.ChipHandle(store=store, modifier=Modifier(store),
                               saver=Saver(store), wc=wc,
                               build_lock=threading.RLock(),
                               live_path=str(live))
    return chip, live, handle


def _patch(path, old, value):
    return {"op": "replace", "path": path, "old": old, "value": value}


class TestWriterEdges:
    def test_cas_conflict_defers_instead_of_clobbering(self, world):
        chip, live, handle = world
        cur = handle.store.state["qubits"]["qA1"]["f_01"]
        # patch claims the node wrote 9e9, but the chip holds something else
        out = writer.revert_patches(handle, [_patch("/quam/qubits/qA1/f_01",
                                                    5.0e9, 9e9)],
                                    apply_live=True, label="t")
        assert out.ok is False
        assert out.conflicts and "CAS" in out.conflicts[0]["reason"]
        assert handle.store.state["qubits"]["qA1"]["f_01"] == cur

    def test_add_and_remove_ops_are_non_revertible(self, world):
        chip, live, handle = world
        out = writer.revert_patches(handle, [
            {"op": "add", "path": "/quam/qubits/qA1/new_key", "value": 1,
             "old": None},
            {"op": "remove", "path": "/quam/qubits/qA1/f_01", "old": 5e9,
             "value": None},
        ], apply_live=True, label="t")
        assert out.ok is False
        assert len(out.conflicts) == 2
        assert all("non-revertible" in c["reason"] for c in out.conflicts)

    def test_revert_restores_exact_type_and_hits_live(self, world):
        chip, live, handle = world
        cur = handle.store.state["qubits"]["qA1"]["f_01"]
        # simulate a node write: value already on the chip; old was an INT
        handle.store.state["qubits"]["qA1"]["f_01"] = 5.1e9
        out = writer.revert_patches(handle,
                                    [_patch("/quam/qubits/qA1/f_01",
                                            4_800_000_000, 5.1e9)],
                                    apply_live=True, label="t")
        assert out.ok, out.error
        restored = handle.store.state["qubits"]["qA1"]["f_01"]
        assert restored == 4_800_000_000
        assert isinstance(restored, int)          # coerce=False kept the int
        state, _ = safe_io.read_state_wiring(live)
        assert state["qubits"]["qA1"]["f_01"] == 4_800_000_000

    def test_apply_rows_survives_stale_live_with_one_reconcile(self, world):
        chip, live, handle = world
        # an out-of-band writer (the node) touches live AFTER our sync point
        state, wiring = safe_io.read_state_wiring(live)
        state["qubits"]["qA1"]["T1"] = 1.23e-5
        safe_io.write_state_wiring(live, state, wiring)

        reconciled = []

        def reconcile():
            reconciled.append(1)
            res = working_copy.reconcile_with_live(
                handle.wc, sync_if_clean=False)
            # our staged edit makes the wc dirty — adopt live's sync point the
            # way the engine's pull+re-stage would: force-sync then re-stage
            working_copy.sync_from_live(handle.wc)
            handle.store.reload()
            handle.modifier.batch_set({"qubits.qA1.f_01": 5.2e9})
            handle.saver.save()

        handle.reconcile = reconcile
        out = writer.apply_rows(handle,
                                [{"path": "qubits.qA1.f_01", "value": 5.2e9}],
                                apply_live=True, label="t")
        assert out.ok, out.error
        assert reconciled, "StaleLiveError retry did not reconcile"
        state, _ = safe_io.read_state_wiring(live)
        assert state["qubits"]["qA1"]["f_01"] == 5.2e9
        # the out-of-band write itself survived the pull (never clobbered)
        assert state["qubits"]["qA1"]["T1"] == 1.23e-5

    def test_restore_values_is_exact_typed_and_logged(self, world):
        chip, live, handle = world
        pre = handle.store.state["qubits"]["qA1"]["f_01"]
        handle.store.state["qubits"]["qA1"]["f_01"] = 9.9e9
        out = writer.restore_values(handle,
                                    [{"path": "qubits.qA1.f_01", "value": pre}],
                                    apply_live=True, label="end")
        assert out.ok
        assert out.paths[0]["old"] == 9.9e9 and out.paths[0]["new"] == pre
        state, _ = safe_io.read_state_wiring(live)
        assert state["qubits"]["qA1"]["f_01"] == pre
