"""v2 closed-loop convergence scenarios (docs/56 v2 — the only re-runnable
verification ground for "measure → judge like a human → adapt → re-measure →
converge on the true value").

Three physical failure classes reconstructed from the real archive's operator
loops (local LOOP_STUDY), now with HONEST sim physics (an uncorrupted run
whose window/grid/visibility missed the feature reports a FAILED fit, never
a magically-correct claim):

  (a) out-of-window  — true peak outside the initial sweep; recovered by the
      widen rung, or by a scan seed when direction evidence exists;
  (b) undersampling  — feature visible but the grid too coarse for the fit
      (case C: the human burned 3 drive-strength attempts + a day; the
      machine prescribes step-refine on attempt 1);
  (c) visibility collapse — a mis-calibrated READOUT kills qubit-spec SNR at
      any span/power; only the cross-node escalation (re-run the resonator
      node) restores it (case A).

Plus the in-loop vision E2E: for a family with no deterministic localizer the
FakeProvider's presence reading (feature_visible/direction — qualitative,
number-free) selects the ladder and the seed direction.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from quam_state_manager.core.autofit import families as fam_mod
from quam_state_manager.core.autofit import synth
from quam_state_manager.core.autofit.auditor import Auditor, FakeProvider
from quam_state_manager.core.autofit.engine import PlanEngine
from quam_state_manager.core.autofit.families import Rung
from quam_state_manager.core.autofit.plan import validate_plan
from quam_state_manager.core.autofit.simbackend import SimBackend, SimWriter

_XY = ("qubits.{q}.f_01", "qubits.{q}.xy.RF_frequency")


def _plan(steps):
    return validate_plan({"name": "scenario", "targets_kind": "qubits",
                          "autonomy": "full", "targets": [], "steps": steps})


def _mk(tmp_path, plan, *, auditor=None):
    chip = synth.make_sim_chip(("qA1",), (), seed=7)
    backend = SimBackend(chip, tmp_path / "data", seed=3)
    writer = SimWriter(chip)
    eng = PlanEngine(tmp_path / "inst", plan, ["qA1"], backend, writer,
                     auditor or Auditor({"provider": "off"}))
    return eng, chip


def _run(eng, timeout=30.0):
    eng.start()
    eng._thread.join(timeout)
    assert not eng.is_running()
    return eng.status()


def _ledger(eng):
    p = Path(eng.instance_path) / "autofit" / "runs" / eng.plan_run_id \
        / "ledger.jsonl"
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()]


def _detune(chip, hz):
    t = chip.qubits["qA1"]
    chip.set("qubits.qA1.f_01", t.f_01 + hz)
    chip.set("qubits.qA1.xy.RF_frequency", t.f_01 + hz)


class TestOutOfWindow:
    def test_honest_miss_then_widen_finds_and_verifies(self, tmp_path):
        """(a): the peak sits 22 MHz off a ±15 MHz window. Attempt 0 must be
        an HONEST failure (no magic claim), the widen rung brings it in
        range, and the discovery is wide-verified before adoption."""
        plan = _plan([{"id": "qs", "family": "qubit_spectroscopy",
                       "retry_max": 2,
                       "params": {"frequency_span_in_mhz": 30}}])
        eng, chip = _mk(tmp_path, plan)
        truth = chip.qubits["qA1"].f_01
        _detune(chip, 22e6)
        st = _run(eng)
        assert st["status"] == "done"
        ev = _ledger(eng)
        v0 = next(e for e in ev if e["event"] == "verdict"
                  and e["step"] == "qs" and e["attempt"] == 0)
        assert v0["verdict"] == "fail"
        assert v0["failure_mode"] == "no_signal"
        assert v0["checks"].get("G1_presence") == "no_feature"
        adapted = [e for e in ev if e["event"] == "params_adapted"]
        assert adapted[0]["mode"] == "no_signal" and adapted[0]["rung"] == 0
        assert adapted[0]["overrides"]["frequency_span_in_mhz"] == 60.0
        assert any(e["event"] == "verify_wide_inserted" for e in ev)
        assert st["board"]["qs"]["qA1"]["state"] == "corrected"
        assert st["board"]["qs__verify_wide"]["qA1"]["state"] in (
            "pass", "corrected")
        assert abs(chip.get("qubits.qA1.f_01") - truth) < 4e5

    def test_edge_tail_direction_drives_a_scan_seed(self, tmp_path,
                                                    monkeypatch):
        """(a′): the peak is just past the window edge — its truncated tail
        gives deterministic direction evidence, and a seed-only ladder walks
        the window onto the feature (magnitude = 0.75 × span, pure window
        math; the seed is consumed by the node's own write on success)."""
        fam = fam_mod.FAMILIES["qubit_spectroscopy"]
        monkeypatch.setitem(
            fam.adaptations, "no_signal",
            [Rung(kind="seed_shift", seed_paths=_XY, span_default=30.0)])
        plan = _plan([{"id": "qs", "family": "qubit_spectroscopy",
                       "retry_max": 2,
                       "params": {"frequency_span_in_mhz": 30,
                                  "num_shots": 800}}])
        eng, chip = _mk(tmp_path, plan)
        truth = chip.qubits["qA1"].f_01
        _detune(chip, 21e6)          # tail z in the hint band (2.5..5)
        st = _run(eng)
        assert st["status"] == "done"
        ev = _ledger(eng)
        seeds = [e for e in ev if e["event"] == "seed_write"]
        assert seeds and seeds[0]["direction"] == "left"
        assert seeds[0]["delta_hz"] == pytest.approx(-22.5e6)
        assert st["board"]["qs"]["qA1"]["state"] == "corrected"
        assert abs(chip.get("qubits.qA1.f_01") - truth) < 4e5
        assert not eng._seeds        # consumed by the successful node write


class TestUndersampling:
    def test_step_refine_prescribed_on_first_retry(self, tmp_path):
        """(b) = LOOP_STUDY case C (#194): feature clearly visible, fit dead
        on a 3.3 MHz grid across a 4 MHz line. The machine reads
        feature_present_fit_failed off the raw data and refines the step on
        the FIRST retry — no drive-strength flailing."""
        plan = _plan([{"id": "qs", "family": "qubit_spectroscopy",
                       "retry_max": 1,
                       "params": {"frequency_span_in_mhz": 30,
                                  "frequency_step_in_mhz": 3.0}}])
        eng, chip = _mk(tmp_path, plan)
        truth = chip.qubits["qA1"].f_01
        st = _run(eng)
        assert st["status"] == "done"
        ev = _ledger(eng)
        v0 = next(e for e in ev if e["event"] == "verdict"
                  and e["step"] == "qs" and e["attempt"] == 0)
        assert v0["failure_mode"] == "feature_present_fit_failed"
        assert v0["feature_present"] is True
        adapted = [e for e in ev if e["event"] == "params_adapted"]
        assert adapted[0]["mode"] == "feature_present_fit_failed"
        assert adapted[0]["overrides"]["frequency_step_in_mhz"] == 1.5
        assert st["board"]["qs"]["qA1"]["state"] == "corrected"
        assert abs(chip.get("qubits.qA1.f_01") - truth) < 4e5


class TestVisibilityCollapse:
    def test_cross_node_escalation_restores_visibility(self, tmp_path):
        """(c) = LOOP_STUDY case A: qubit-spec is blind because the READOUT
        is mis-centered — widen/power/seed can't help; the escalation rung
        re-runs the resonator node, which re-centers the readout, and the
        continuation finds the qubit."""
        plan = _plan([{"id": "qs", "family": "qubit_spectroscopy",
                       "retry_max": 4,
                       "params": {"frequency_span_in_mhz": 30,
                                  "operation_amplitude_factor": 1.0}}])
        eng, chip = _mk(tmp_path, plan)
        t = chip.qubits["qA1"]
        # readout 20 MHz off a 2 MHz-wide resonator → visibility ~1% — no
        # span/power/averaging surfaces the peak (10 MHz off would let the
        # compounding shots eventually raise the tiny peak past the presence
        # bar and re-route into the step-refine ladder — physically sensible,
        # but case A is the DEEP collapse only a readout re-cal can fix)
        chip.set("qubits.qA1.resonator.f_01", t.f_res + 20e6)
        chip.set("qubits.qA1.resonator.RF_frequency", t.f_res + 20e6)
        st = _run(eng)
        assert st["status"] == "done"
        ev = _ledger(eng)
        esc = [e for e in ev if e["event"] == "escalation_inserted"]
        assert esc and esc[0]["recal_family"] == "resonator_spectroscopy"
        # the ladder was actually walked first: widen (0), power (1),
        # seed skipped (no direction evidence in flat noise)
        adapted = [e for e in ev if e["event"] == "params_adapted"]
        assert [e["rung"] for e in adapted[:2]] == [0, 1]
        assert any(e["event"] == "seed_skipped" for e in ev)
        # the re-cal genuinely fixed the readout…
        assert abs(chip.get("qubits.qA1.resonator.RF_frequency")
                   - t.f_res) < 4e5
        assert st["board"]["qs__recal"]["qA1"]["state"] in ("pass", "applied",
                                                            "corrected")
        # …and the continuation found the qubit
        assert st["board"]["qs__retry"]["qA1"]["state"] in ("pass",
                                                            "corrected")
        assert abs(chip.get("qubits.qA1.f_01") - t.f_01) < 4e5

    def test_without_escalation_the_target_defers_honestly(self, tmp_path):
        """Budget too small to reach the escalate rung ⇒ the target defers
        ('re-measure advised'), state intact — never a forced adoption."""
        plan = _plan([{"id": "qs", "family": "qubit_spectroscopy",
                       "retry_max": 1, "criticality": "soft",
                       "params": {"frequency_span_in_mhz": 30}}])
        eng, chip = _mk(tmp_path, plan)
        t = chip.qubits["qA1"]
        chip.set("qubits.qA1.resonator.f_01", t.f_res + 20e6)
        chip.set("qubits.qA1.resonator.RF_frequency", t.f_res + 20e6)
        pre = chip.get("qubits.qA1.f_01")
        st = _run(eng)
        assert st["status"] == "done"
        assert st["board"]["qs"]["qA1"]["state"] == "deferred"
        assert any(r["target"] == "qA1" for r in st["review_queue"])
        assert chip.get("qubits.qA1.f_01") == pytest.approx(pre)


class TestInLoopVision:
    def test_vision_presence_and_direction_drive_the_seed(self, tmp_path,
                                                          monkeypatch):
        """Full in-loop vision E2E: a family with NO deterministic localizer
        fails opaquely; the vision presence reading (feature_visible=False +
        direction=left — qualitative only) selects the no_signal ladder and
        the seed direction; the loop converges on the true value."""
        fam = fam_mod.FAMILIES["qubit_spectroscopy"]
        monkeypatch.setattr(fam, "feature_check", None)
        monkeypatch.setitem(
            fam.adaptations, "no_signal",
            [Rung(kind="seed_shift", seed_paths=_XY, span_default=40.0)])
        fake = FakeProvider({"qA1": {
            "verdict": "abstain", "failure_mode": None,
            "reason": "flat noise; structure suggested below the window",
            "feature_visible": False, "direction": "left"}})
        plan = _plan([{"id": "qs", "family": "qubit_spectroscopy",
                       "retry_max": 2,
                       "params": {"frequency_span_in_mhz": 40}}])
        eng, chip = _mk(tmp_path, plan,
                        auditor=Auditor({"provider": "fake"},
                                        fake_provider=fake))
        truth = chip.qubits["qA1"].f_01
        _detune(chip, 30e6)          # out of the ±20 MHz window
        st = _run(eng)
        assert st["status"] == "done"
        ev = _ledger(eng)
        llm = [e for e in ev if e["event"] == "llm_verdict"]
        assert llm and llm[0]["feature_visible"] is False
        assert llm[0]["direction"] == "left"
        seeds = [e for e in ev if e["event"] == "seed_write"]
        assert seeds and seeds[0]["direction"] == "left"
        assert seeds[0]["delta_hz"] == pytest.approx(-30e6)
        assert st["board"]["qs"]["qA1"]["state"] == "corrected"
        assert abs(chip.get("qubits.qA1.f_01") - truth) < 4e5
        # the vision reading never un-failed the verdict — attempt 0 stayed
        # a fail (retry), no write happened on it
        v0 = next(e for e in ev if e["event"] == "verdict"
                  and e["attempt"] == 0)
        assert v0["verdict"] == "fail"
