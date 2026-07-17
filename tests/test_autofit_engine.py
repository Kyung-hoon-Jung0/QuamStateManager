"""Autofit engine state machine over the SimBackend (docs/56 §6.3).

Everything runs hardware-free and deterministic: the SimBackend emits real
run folders (h5 + node.json + patches applied to the SimChip), the engine
gates/decides/writes through the SimWriter, and each scenario asserts BOTH
the control flow (board/ledger/review queue) and the physics outcome (the
chip's state converged to ground truth / was reverted / was restored).
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from quam_state_manager.core.autofit import synth
from quam_state_manager.core.autofit.auditor import Auditor, FakeProvider
from quam_state_manager.core.autofit.engine import PlanEngine, is_active
from quam_state_manager.core.autofit.plan import validate_plan
from quam_state_manager.core.autofit.simbackend import SimBackend, SimWriter


def _plan(steps, kind="qubits", autonomy="full", targets=None):
    return validate_plan({
        "name": "test plan", "targets_kind": kind, "autonomy": autonomy,
        "targets": targets or [], "steps": steps,
    })


def _mk(tmp_path, plan, *, corruption=None, crash=None, auditor=None,
        chip=None, autonomy=None, targets=("qA1", "qA2")):
    chip = chip or synth.make_sim_chip(("qA1", "qA2"), ("qA2-qA1",), seed=7)
    backend = SimBackend(chip, tmp_path / "data", seed=3,
                         corruption_plan=corruption, crash_plan=crash)
    writer = SimWriter(chip)
    eng = PlanEngine(tmp_path / "inst", plan, list(targets), backend, writer,
                     auditor or Auditor({"provider": "off"}),
                     autonomy=autonomy)
    return eng, chip, backend, writer


def _run(eng, timeout=30.0):
    eng.start()
    eng._thread.join(timeout)
    assert not eng.is_running(), "engine thread did not finish"
    return eng.status()


def _ledger(eng):
    p = Path(eng.instance_path) / "autofit" / "runs" / eng.plan_run_id \
        / "ledger.jsonl"
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()]


ONE_Q_STEPS = [
    {"id": "qubit_spec", "family": "qubit_spectroscopy", "retry_max": 2,
     "criticality": "hard"},
    {"id": "power_rabi", "family": "power_rabi", "retry_max": 1,
     "criticality": "hard"},
    {"id": "ramsey", "family": "ramsey", "retry_max": 1, "criticality": "hard"},
    {"id": "t1", "family": "T1", "retry_max": 1, "criticality": "soft"},
]


class TestHappyPath:
    def test_full_plan_converges_to_ground_truth(self, tmp_path):
        eng, chip, backend, _ = _mk(tmp_path, _plan(ONE_Q_STEPS))
        st = _run(eng)
        assert st["status"] == "done"
        assert st["review_queue"] == []
        for t in ("qA1", "qA2"):
            for sid in ("qubit_spec", "power_rabi", "ramsey", "t1"):
                assert st["board"][sid][t]["state"] == "pass", (sid, t)
            truth = chip.qubits[t]
            # spec + ramsey converged f_01 to the truth
            assert abs(chip.get(f"qubits.{t}.f_01") - truth.f_01) < 5e4
            # rabi converged the pi-pulse amplitude
            assert abs(chip.get(f"qubits.{t}.xy.operations.x180.amplitude")
                       - truth.x180_amp) < 0.02 * truth.x180_amp
            # T1 landed (node applies; engine keeps)
            assert abs(chip.get(f"qubits.{t}.T1") - truth.t1) < 0.2 * truth.t1
        events = {e["event"] for e in _ledger(eng)}
        assert {"plan_started", "step_started", "verdict", "decision",
                "plan_done"} <= events
        assert "revert_applied" not in events

    def test_cz_plan_over_pairs(self, tmp_path):
        plan = _plan([
            {"id": "chevron", "family": "chevron_11_02", "retry_max": 1},
            {"id": "cond_phase", "family": "cz_conditional_phase",
             "retry_max": 1, "params": {"operation": "cz_unipolar"}},
        ], kind="qubit_pairs")
        eng, chip, _, _ = _mk(tmp_path, plan, targets=("qA2-qA1",))
        st = _run(eng)
        assert st["status"] == "done"
        pt = chip.pairs["qA2-qA1"]
        amp = chip.get("qubit_pairs.qA2-qA1.macros.cz_unipolar"
                       ".flux_pulse_qubit.amplitude")
        assert abs(amp - pt.phase_amp) < 0.02 * pt.phase_amp
        assert chip.get("qubit_pairs.qA2-qA1.macros.cz_unipolar"
                        ".flux_pulse_qubit.length") % 4 == 0


class TestCorrectionLoop:
    def test_wrong_peak_reverts_then_retry_converges(self, tmp_path):
        chip = synth.make_sim_chip(("qA1", "qA2"), ("qA2-qA1",), seed=7)
        pre_f01 = chip.get("qubits.qA1.f_01")
        eng, chip, backend, _ = _mk(
            tmp_path, _plan(ONE_Q_STEPS[:1]), chip=chip,
            corruption={("qubit_spec", "qA1"): ["wrong_peak"]})
        st = _run(eng)
        assert st["status"] == "done"
        assert st["board"]["qubit_spec"]["qA1"]["state"] == "corrected"
        assert st["board"]["qubit_spec"]["qA2"]["state"] == "pass"
        # converged despite the corrupted first attempt (spec-only plan: the
        # sim fitter's own scatter is ~fwhm/40 ≈ 100 kHz; ramsey would refine)
        assert abs(chip.get("qubits.qA1.f_01") - chip.qubits["qA1"].f_01) < 4e5
        ev = _ledger(eng)
        assert any(e["event"] == "revert_applied" and e["target"] == "qA1"
                   and e["ok"] for e in ev)
        adapted = [e for e in ev if e["event"] == "params_adapted"]
        assert adapted and adapted[0]["mode"] == "wrong_peak"
        # the adaptation actually reshaped the scan (span ×2, amp ×0.5)
        ov = adapted[0]["overrides"]
        assert ov["frequency_span_in_mhz"] == pytest.approx(120.0)
        assert ov["operation_amplitude_factor"] == pytest.approx(0.5)
        # qA2 ran only once (its attempt-0 result was kept)
        runs = [e for e in ev if e["event"] == "step_started"]
        assert runs[1]["targets"] == ["qA1"]

    def test_exhaustion_reverts_defers_and_halts_hard_chain(self, tmp_path):
        chip = synth.make_sim_chip(("qA1", "qA2"), ("qA2-qA1",), seed=7)
        pre_f01 = chip.get("qubits.qA1.f_01")
        steps = [dict(ONE_Q_STEPS[0], retry_max=1), ONE_Q_STEPS[1]]
        eng, chip, _, _ = _mk(
            tmp_path, _plan(steps), chip=chip,
            corruption={("qubit_spec", "qA1"): ["wrong_peak", "wrong_peak"]})
        st = _run(eng)
        assert st["status"] == "done"
        # qA1: both attempts rejected → reverted + deferred + chain halted
        assert st["board"]["qubit_spec"]["qA1"]["state"] == "reverted"
        assert "qA1" in st["halted"]
        assert any(r["target"] == "qA1" for r in st["review_queue"])
        # the bad value never survived on the chip
        assert chip.get("qubits.qA1.f_01") == pytest.approx(pre_f01)
        # qA1's chain stopped; qA2's continued
        assert "qA1" not in st["board"]["power_rabi"]
        assert st["board"]["power_rabi"]["qA2"]["state"] == "pass"

    def test_soft_step_failure_does_not_halt(self, tmp_path):
        steps = [dict(ONE_Q_STEPS[3], retry_max=0),      # t1, soft
                 dict(ONE_Q_STEPS[2])]                   # ramsey after
        eng, chip, _, _ = _mk(
            tmp_path, _plan(steps),
            corruption={("t1", "qA1"): ["out_of_band"]})
        st = _run(eng)
        assert st["board"]["t1"]["qA1"]["state"] == "reverted"
        assert st["halted"] == {}
        assert st["board"]["ramsey"]["qA1"]["state"] == "pass"

    def test_node_crash_defers_all_and_halts_hard(self, tmp_path):
        eng, chip, _, _ = _mk(tmp_path, _plan(ONE_Q_STEPS[:2]),
                              crash={("qubit_spec", 0): "OPX exploded",
                                     ("qubit_spec", 1): "OPX exploded",
                                     ("qubit_spec", 2): "OPX exploded"})
        st = _run(eng)
        assert st["status"] == "done"
        for t in ("qA1", "qA2"):
            assert st["board"]["qubit_spec"][t]["state"] == "deferred"
            assert t in st["halted"]
        assert st["board"].get("power_rabi", {}) == {}


class TestLLMIntegration:
    def test_llm_resolves_suspects_accept_and_reject(self, tmp_path):
        # noisy T1 on both qubits → gate suspect(noisy). Fake LLM: accept qA1
        # (keep the node's write), reject qA2 (revert + no budget → defer).
        fake = FakeProvider({
            "qA1": {"verdict": "accept", "failure_mode": None,
                    "reason": "decay visible, fit tracks it"},
            "qA2": {"verdict": "reject", "failure_mode": "noisy",
                    "reason": "fit does not track the decay"},
        })
        auditor = Auditor({"provider": "fake", "max_calls_per_plan": 10},
                          fake_provider=fake)
        chip = synth.make_sim_chip(("qA1", "qA2"), ("qA2-qA1",), seed=7)
        pre_t1_qA2 = chip.get("qubits.qA2.T1")
        steps = [{"id": "t1", "family": "T1", "retry_max": 0,
                  "criticality": "soft"}]
        eng, chip, _, _ = _mk(tmp_path, _plan(steps), chip=chip,
                              corruption={("t1", "qA1"): ["noisy"],
                                          ("t1", "qA2"): ["noisy"]},
                              auditor=auditor)
        st = _run(eng)
        assert st["board"]["t1"]["qA1"]["state"] == "pass"       # LLM accept
        assert st["board"]["t1"]["qA2"]["state"] == "reverted"   # LLM reject
        assert chip.get("qubits.qA2.T1") == pytest.approx(pre_t1_qA2)
        assert st["llm_calls"] == 2
        ev = [e for e in _ledger(eng) if e["event"] == "llm_verdict"]
        assert {e["target"]: e["verdict"] for e in ev} == \
            {"qA1": "accept", "qA2": "reject"}

    def test_llm_disabled_suspects_defer_without_reverting_good_data(
            self, tmp_path):
        steps = [{"id": "t1", "family": "T1", "retry_max": 0,
                  "criticality": "soft"}]
        eng, chip, _, _ = _mk(tmp_path, _plan(steps),
                              corruption={("t1", "qA1"): ["noisy"]})
        st = _run(eng)
        assert st["board"]["t1"]["qA1"]["state"] == "deferred"
        assert any(r["target"] == "qA1" for r in st["review_queue"])
        # abstain-defer keeps the node's write (suspect ≠ proven bad)
        assert st["board"]["t1"]["qA2"]["state"] == "pass"


class TestAutonomyReview:
    def test_review_mode_restores_pre_plan_state(self, tmp_path):
        chip = synth.make_sim_chip(("qA1", "qA2"), ("qA2-qA1",), seed=7)
        pre = {p: chip.get(p) for p in (
            "qubits.qA1.f_01", "qubits.qA2.f_01",
            "qubits.qA1.xy.operations.x180.amplitude",
            "qubits.qA1.T1")}
        eng, chip, _, writer = _mk(tmp_path, _plan(ONE_Q_STEPS), chip=chip,
                                   autonomy="review")
        st = _run(eng)
        assert st["status"] == "done"
        for p, v in pre.items():
            assert chip.get(p) == pytest.approx(v), p
        ev = _ledger(eng)
        restored = [e for e in ev if e["event"] == "plan_restored"]
        assert restored and restored[0]["ok"]
        # the report still carries what WOULD have been applied
        assert any(e["event"] == "decision" and e["decision"] == "keep"
                   for e in ev)


class TestLifecycle:
    def test_abort_stops_between_steps(self, tmp_path):
        eng, chip, _, _ = _mk(tmp_path, _plan(ONE_Q_STEPS))
        eng.abort()               # set before start: halts at the first check
        st = _run(eng)
        assert st["status"] == "aborted"
        assert st["board"] == {} or all(
            c.get("state") in ("aborted", "running")
            for cells in st["board"].values() for c in cells.values())

    def test_is_active_and_double_start_guard(self, tmp_path):
        eng, chip, _, _ = _mk(tmp_path, _plan(ONE_Q_STEPS[:1]))
        assert not is_active(tmp_path / "inst")
        eng.start()
        eng2, *_ = _mk(tmp_path, _plan(ONE_Q_STEPS[:1]))
        if eng.is_running():
            with pytest.raises(RuntimeError):
                eng2.start()
        eng._thread.join(30)
        assert not is_active(tmp_path / "inst")

    def test_state_persisted_for_ui_poll(self, tmp_path):
        eng, chip, _, _ = _mk(tmp_path, _plan(ONE_Q_STEPS[:1]))
        _run(eng)
        persisted = json.loads(
            (Path(eng.instance_path) / "autofit_run.json").read_text())
        assert persisted["plan_run_id"] == eng.plan_run_id
        assert persisted["status"] == "done"
        assert persisted["board"]["qubit_spec"]["qA1"]["state"] == "pass"
