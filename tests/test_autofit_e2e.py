"""Autofit END-TO-END over the REAL chip machinery (docs/56 §6.3, task-level
validation): a sim chip's live folder + the genuine QuamStore / WorkingCopy /
Modifier / Saver / RealWriter stack, with the LiveSimBackend acting exactly
like a node process (Quam.load → run → machine.save on the LIVE files).

This is the tier that proves the write path — not the SimWriter shortcut:
engine decisions land through batch_set → saver.save → apply_to_live, reverts
are CAS'd against the real store, and reconcile pulls node writes off disk.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from quam_state_manager.core import safe_io, working_copy
from quam_state_manager.core.autofit import synth
from quam_state_manager.core.autofit.auditor import Auditor
from quam_state_manager.core.autofit.engine import PlanEngine
from quam_state_manager.core.autofit.plan import validate_plan
from quam_state_manager.core.autofit.simbackend import LiveSimBackend
from quam_state_manager.core.autofit.writer import ChipHandle, RealWriter
from quam_state_manager.core.loader import QuamStore
from quam_state_manager.core.modifier import Modifier
from quam_state_manager.core.saver import Saver


def _mk_world(tmp_path):
    """Sim chip + live folder + the real activation stack (mirrors
    _activate_quam without Flask)."""
    chip = synth.make_sim_chip(("qA1", "qA2"), ("qA2-qA1",), seed=7)
    live = tmp_path / "live_chip"
    live.mkdir()
    safe_io.write_state_wiring(live, chip.state, chip.wiring)

    inst = tmp_path / "instance"
    wc = working_copy.create(inst, live)
    store = QuamStore(wc.working_folder)
    modifier = Modifier(store)
    saver = Saver(store)
    build_lock = threading.RLock()

    def reconcile():
        with build_lock:
            res = working_copy.reconcile_with_live(
                wc, sync_if_clean=not store.change_log)
            if res == working_copy.RECONCILE_SYNCED:
                store.reload()

    handle = ChipHandle(store=store, modifier=modifier, saver=saver, wc=wc,
                        build_lock=build_lock, live_path=str(live),
                        reconcile=reconcile)
    return chip, live, inst, handle, reconcile


class _ReconcilingBackend(LiveSimBackend):
    """After a 'node' writes live, pull it into the store the way the
    engine-owned post-item ingest does on the real chassis."""

    def __init__(self, *a, reconcile=None, **kw):
        super().__init__(*a, **kw)
        self._reconcile = reconcile or (lambda: None)

    def run_step(self, step, targets, params, attempt, abort):
        res = super().run_step(step, targets, params, attempt, abort)
        self._reconcile()
        return res


def _run_plan(tmp_path, steps, *, corruption=None, autonomy="full",
              targets=("qA1", "qA2"), kind="qubits"):
    chip, live, inst, handle, reconcile = _mk_world(tmp_path)
    backend = _ReconcilingBackend(chip, tmp_path / "data", live, seed=3,
                                  corruption_plan=corruption,
                                  reconcile=reconcile)
    writer = RealWriter(handle, apply_live=True)
    plan = validate_plan({"name": "e2e", "targets_kind": kind,
                          "autonomy": autonomy,
                          "targets": list(targets), "steps": steps})
    eng = PlanEngine(inst, plan, list(targets), backend, writer,
                     Auditor({"provider": "off"}), autonomy=autonomy)
    eng.start()
    eng._thread.join(60)
    assert not eng.is_running()
    return eng.status(), chip, live, handle


def _live_state(live):
    state, _ = safe_io.read_state_wiring(live)
    return state


STEPS_1Q = [
    {"id": "qubit_spec", "family": "qubit_spectroscopy", "retry_max": 2,
     "criticality": "hard"},
    {"id": "power_rabi", "family": "power_rabi", "retry_max": 1,
     "criticality": "hard"},
    {"id": "ramsey", "family": "ramsey", "retry_max": 1, "criticality": "hard"},
    {"id": "t1", "family": "T1", "retry_max": 1, "criticality": "soft"},
]


class TestE2EFullAutonomy:
    def test_live_files_converge_to_ground_truth(self, tmp_path):
        st, chip, live, handle = _run_plan(tmp_path, STEPS_1Q)
        assert st["status"] == "done"
        assert st["review_queue"] == []
        state = _live_state(live)
        for t in ("qA1", "qA2"):
            truth = chip.qubits[t]
            assert abs(state["qubits"][t]["f_01"] - truth.f_01) < 5e4
            assert abs(state["qubits"][t]["xy"]["operations"]["x180"]["amplitude"]
                       - truth.x180_amp) < 0.02 * truth.x180_amp
            assert abs(state["qubits"][t]["T1"] - truth.t1) < 0.2 * truth.t1
        # the in-memory store tracked the live evolution (no divergence)
        assert working_copy.content_hash(handle.store.state,
                                         handle.store.wiring) \
            == working_copy.content_hash(*safe_io.read_state_wiring(live))

    def test_bad_fit_is_reverted_ON_LIVE_and_retry_converges(self, tmp_path):
        st, chip, live, handle = _run_plan(
            tmp_path, STEPS_1Q[:1],
            corruption={("qubit_spec", "qA1"): ["wrong_peak"]})
        assert st["status"] == "done"
        assert st["board"]["qubit_spec"]["qA1"]["state"] == "corrected"
        state = _live_state(live)
        # the sidelobe value is NOT on the chip — the retry's clean fit is
        truth = chip.qubits["qA1"]
        assert abs(state["qubits"]["qA1"]["f_01"] - truth.f_01) < 4e5
        # xy RF stayed coupled to f_01 (the paired write)
        assert state["qubits"]["qA1"]["xy"]["RF_frequency"] == \
            state["qubits"]["qA1"]["f_01"]

    def test_exhausted_reject_leaves_live_at_pre_step_value(self, tmp_path):
        chip0 = synth.make_sim_chip(("qA1", "qA2"), ("qA2-qA1",), seed=7)
        pre = chip0.state["qubits"]["qA1"]["f_01"]
        st, chip, live, handle = _run_plan(
            tmp_path, [dict(STEPS_1Q[0], retry_max=1)],
            corruption={("qubit_spec", "qA1"): ["wrong_peak", "wrong_peak"]})
        assert st["board"]["qubit_spec"]["qA1"]["state"] == "reverted"
        assert _live_state(live)["qubits"]["qA1"]["f_01"] == pytest.approx(pre)
        assert any(r["target"] == "qA1" for r in st["review_queue"])


class TestE2EReviewAutonomy:
    def test_review_mode_restores_live_files_at_plan_end(self, tmp_path):
        chip0 = synth.make_sim_chip(("qA1", "qA2"), ("qA2-qA1",), seed=7)
        pre_state = json.loads(json.dumps(chip0.state))
        st, chip, live, handle = _run_plan(tmp_path, STEPS_1Q,
                                           autonomy="review")
        assert st["status"] == "done"
        state = _live_state(live)
        for t in ("qA1", "qA2"):
            assert state["qubits"][t]["f_01"] == \
                pytest.approx(pre_state["qubits"][t]["f_01"])
            assert state["qubits"][t]["T1"] == \
                pytest.approx(pre_state["qubits"][t]["T1"])
            assert state["qubits"][t]["xy"]["operations"]["x180"]["amplitude"] \
                == pytest.approx(
                    pre_state["qubits"][t]["xy"]["operations"]["x180"]["amplitude"])


class TestE2ECZ:
    def test_cz_chain_on_live_pair(self, tmp_path):
        steps = [
            {"id": "chevron", "family": "chevron_11_02", "retry_max": 1},
            {"id": "cond_phase", "family": "cz_conditional_phase",
             "retry_max": 1, "params": {"operation": "cz_unipolar"}},
        ]
        st, chip, live, handle = _run_plan(tmp_path, steps,
                                           targets=("qA2-qA1",),
                                           kind="qubit_pairs")
        assert st["status"] == "done"
        state = _live_state(live)
        macro = state["qubit_pairs"]["qA2-qA1"]["macros"]["cz_unipolar"]
        pt = chip.pairs["qA2-qA1"]
        assert abs(macro["flux_pulse_qubit"]["amplitude"] - pt.phase_amp) \
            < 0.02 * pt.phase_amp
        assert macro["flux_pulse_qubit"]["length"] % 4 == 0
