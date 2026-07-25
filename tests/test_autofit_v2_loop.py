"""v2 judgment loop — ladder rungs, scan seeds, wide verification, vision
hints (docs/56 v2; the human vocabulary from LOOP_STUDY cases A/B/C).

Engine-level over SimBackend/SimWriter (hardware-free, deterministic):
* the no_feature ladder walks widen → drive-up → (seed) → (escalate) one
  rung per use, and a discovery on a later attempt inserts a wide
  verification step before the plan moves on;
* a failed wide verification reverts the discovery (never adopt unverified);
* scan seeds are direction-gated (edge/vision evidence only — a blind shift
  is a guess), written through the audited writer, and restored on failure;
* the LLM contract carries feature_visible/direction as bools/enums (still
  structurally number-free) and a presence reading splits an opaque 2-D
  node failure into the right ladder.
"""
from __future__ import annotations

import json
from collections import deque
from pathlib import Path

import pytest

from quam_state_manager.core.autofit import families as fam_mod
from quam_state_manager.core.autofit import synth
from quam_state_manager.core.autofit.auditor import Auditor, FakeProvider
from quam_state_manager.core.autofit.engine import PlanEngine, StepRunResult
from quam_state_manager.core.autofit.families import Rung
from quam_state_manager.core.autofit.gates import GateVerdict
from quam_state_manager.core.autofit.plan import Step, validate_plan
from quam_state_manager.core.autofit.simbackend import SimBackend, SimWriter


_XY = ("qubits.{q}.f_01", "qubits.{q}.xy.RF_frequency")


def _plan(steps, kind="qubits", autonomy="full", targets=None):
    return validate_plan({
        "name": "v2 test plan", "targets_kind": kind, "autonomy": autonomy,
        "targets": targets or [], "steps": steps,
    })


def _mk(tmp_path, plan, *, corruption=None, auditor=None,
        targets=("qA1",)):
    chip = synth.make_sim_chip(tuple(targets), (), seed=7)
    backend = SimBackend(chip, tmp_path / "data", seed=3,
                         corruption_plan=corruption)
    writer = SimWriter(chip)
    eng = PlanEngine(tmp_path / "inst", plan, list(targets), backend, writer,
                     auditor or Auditor({"provider": "off"}))
    # direct-call tests (no eng.start()) still need the ledger/instance dirs
    eng._ledger_dir.mkdir(parents=True, exist_ok=True)
    (tmp_path / "inst").mkdir(parents=True, exist_ok=True)
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


class TestLadderAndVerify:
    def test_no_signal_ladder_walks_rungs_then_discovery_gets_verified(
            self, tmp_path):
        """LOOP_STUDY case B shape: no feature → widen (rung 0) → drive up
        (rung 1) → found → the discovery is wide-verified before the plan
        trusts it."""
        plan = _plan([{"id": "qs", "family": "qubit_spectroscopy",
                       "retry_max": 3,
                       "params": {"frequency_span_in_mhz": 30,
                                  "operation_amplitude_factor": 1.0}}])
        eng, chip, _b, _w = _mk(
            tmp_path, plan,
            corruption={("qs", "qA1"): ["no_signal", "no_signal", None]})
        st = _run(eng)
        assert st["status"] == "done"
        ev = _ledger(eng)
        adapted = [e for e in ev if e["event"] == "params_adapted"]
        assert [(e["mode"], e["rung"]) for e in adapted] == \
            [("no_signal", 0), ("no_signal", 1)]
        # rung 0 = widen (span ×2), rung 1 = drive up (amp ×4)
        assert adapted[0]["overrides"]["frequency_span_in_mhz"] == 60.0
        assert adapted[1]["overrides"]["operation_amplitude_factor"] == 4.0
        # the discovery earned a wide verification and it passed
        assert any(e["event"] == "verify_wide_inserted" for e in ev)
        board = st["board"]
        assert board["qs"]["qA1"]["state"] == "corrected"
        assert board["qs__verify_wide"]["qA1"]["state"] in ("pass", "corrected")
        # verify ran WIDE: span quadrupled on top of the adapted 60 MHz
        vstart = [e for e in ev if e["event"] == "step_started"
                  and e["step"] == "qs__verify_wide"]
        assert vstart and vstart[0]["params"]["frequency_span_in_mhz"] == 240.0
        # and the chip genuinely converged (final value = the wide verify
        # run's own fit; synth claim noise is σ = fwhm/40 = 100 kHz, so the
        # honest bound is a few σ — still ≪ the 4 MHz linewidth and the
        # 3 MHz seeded detuning)
        t = eng.backend.chip.qubits["qA1"]
        assert abs(chip.get("qubits.qA1.f_01") - t.f_01) < 4e5

    def test_failed_wide_verification_reverts_the_discovery(self, tmp_path):
        """Never adopt an unverified discovery: when the wide scan refutes
        it, both the verify run's write and the discovered write are undone
        and the target lands in review."""
        plan = _plan([{"id": "qs", "family": "qubit_spectroscopy",
                       "retry_max": 1, "criticality": "soft",
                       "params": {"frequency_span_in_mhz": 100}}])
        eng, chip, _b, _w = _mk(
            tmp_path, plan,
            corruption={("qs", "qA1"): ["wrong_peak", None],
                        ("qs__verify_wide", "qA1"): "wrong_peak"})
        pre = chip.get("qubits.qA1.f_01")
        st = _run(eng)
        assert st["status"] == "done"
        ev = _ledger(eng)
        assert any(e["event"] == "verify_wide_inserted" for e in ev)
        assert any(e["event"] == "verify_failed_original_reverted"
                   for e in ev)
        # chip is back at its pre-plan value — discovery NOT adopted
        assert chip.get("qubits.qA1.f_01") == pytest.approx(pre)
        # the original cell was demoted and the review queue carries it
        assert st["board"]["qs"]["qA1"]["state"] == "deferred"
        assert any(r["step_id"] == "qs__verify_wide"
                   for r in st["review_queue"])

    def test_clean_first_attempt_gets_no_verify_step(self, tmp_path):
        plan = _plan([{"id": "qs", "family": "qubit_spectroscopy",
                       "retry_max": 2}])
        eng, _c, _b, _w = _mk(tmp_path, plan)
        st = _run(eng)
        assert st["status"] == "done"
        assert "qs__verify_wide" not in st["board"]
        assert not any(e["event"] == "verify_wide_inserted"
                       for e in _ledger(eng))


class TestSeedRung:
    _RUNG = Rung(kind="seed_shift",
                 seed_paths=("qubits.{q}.f_01", "qubits.{q}.xy.RF_frequency"),
                 span_default=100.0)

    def test_seed_shift_writes_window_math_and_restores_on_failure(
            self, tmp_path):
        plan = _plan([{"id": "qs", "family": "qubit_spectroscopy"}])
        eng, chip, _b, _w = _mk(tmp_path, plan)
        step = eng.plan.steps[0]
        pre = chip.get("qubits.qA1.f_01")
        ok = eng._seed_shift(step, self._RUNG, "qA1",
                             {"frequency_span_in_mhz": 40.0}, "right")
        assert ok
        # magnitude is pure window math: 0.75 × 40 MHz, sign from the hint
        assert chip.get("qubits.qA1.f_01") == pytest.approx(pre + 30e6)
        assert chip.get("qubits.qA1.xy.RF_frequency") == pytest.approx(
            chip.get("qubits.qA1.f_01"))
        assert ("qs", "qA1") in eng._seeds
        # terminal failure → the seed goes back (CAS)
        eng._restore_seed("qs", "qA1")
        assert chip.get("qubits.qA1.f_01") == pytest.approx(pre)
        assert ("qs", "qA1") not in eng._seeds

    def test_seed_requires_direction_evidence(self, tmp_path):
        plan = _plan([{"id": "qs", "family": "qubit_spectroscopy"}])
        eng, chip, _b, _w = _mk(tmp_path, plan)
        step = eng.plan.steps[0]
        fam = fam_mod.FAMILIES["qubit_spectroscopy"]
        pre = chip.get("qubits.qA1.f_01")
        v = GateVerdict(target="qA1", verdict="fail",
                        failure_mode="no_signal")     # no direction_hint
        params, escalated = eng._adapt(
            step, fam, "no_signal", {"frequency_span_in_mhz": 40.0},
            {"no_signal": 2}, ["qA1"], {"qA1": v}, attempt=2, queue=deque())
        assert not escalated
        assert chip.get("qubits.qA1.f_01") == pytest.approx(pre)  # no write
        assert not eng._seeds

    def test_seed_success_is_superseded_by_the_node_write(self, tmp_path):
        """rail ③: a seed never lingers — a passing decision consumes it."""
        plan = _plan([{"id": "qs", "family": "qubit_spectroscopy"}])
        eng, chip, _b, _w = _mk(tmp_path, plan)
        step = eng.plan.steps[0]
        eng._seed_shift(step, self._RUNG, "qA1",
                        {"frequency_span_in_mhz": 40.0}, "left")
        assert ("qs", "qA1") in eng._seeds
        res = StepRunResult(status="done", run={
            "experiment_name": "08_qubit_spectroscopy",
            "fit_results": {"qA1": {"success": True}}, "outcomes":
            {"qA1": "successful"}, "parameters": {}, "patches": []})
        v = GateVerdict(target="qA1", verdict="pass")
        fam = fam_mod.FAMILIES["qubit_spectroscopy"]
        eng._decide(step, "qA1", v, res, fam, attempt=1)
        assert ("qs", "qA1") not in eng._seeds


class TestEscalation:
    def test_escalate_rung_inserts_recal_then_continuation(self, tmp_path):
        plan = _plan([{"id": "qs", "family": "qubit_spectroscopy"}])
        eng, _c, _b, _w = _mk(tmp_path, plan)
        step = eng.plan.steps[0]
        fam = fam_mod.FAMILIES["qubit_spectroscopy"]
        q: deque = deque()
        v = GateVerdict(target="qA1", verdict="fail",
                        failure_mode="no_signal")
        params, escalated = eng._adapt(
            step, fam, "no_signal", {"frequency_span_in_mhz": 40.0},
            {"no_signal": 3}, ["qA1"], {"qA1": v}, attempt=3, queue=q)
        assert escalated
        recal, cont = q[0], q[1]
        assert recal.family == "resonator_spectroscopy"
        assert recal.inserted_by == "escalation_recal"
        assert recal.only_targets == ("qA1",)
        assert cont.id == "qs__retry" and cont.family == "qubit_spectroscopy"
        assert cont.inserted_by == "escalation"
        assert cont.carry_window_failure == ("qA1",)

    def test_escalation_continuation_cannot_escalate_again(self, tmp_path):
        plan = _plan([{"id": "qs", "family": "qubit_spectroscopy"}])
        eng, _c, _b, _w = _mk(tmp_path, plan)
        fam = fam_mod.FAMILIES["qubit_spectroscopy"]
        cont = Step(id="qs__retry", family="qubit_spectroscopy",
                    inserted_by="escalation")
        q: deque = deque()
        v = GateVerdict(target="qA1", verdict="fail",
                        failure_mode="no_signal")
        _params, escalated = eng._adapt(
            cont, fam, "no_signal", {}, {"no_signal": 3}, ["qA1"],
            {"qA1": v}, attempt=3, queue=q)
        assert not escalated and not q


class TestVisionHints:
    def test_contract_parses_hints_and_stays_number_free(self):
        fake = FakeProvider({"qA1": {
            "verdict": "abstain", "failure_mode": None, "reason": "presence",
            "feature_visible": True, "direction": "right",
            "sneaky_frequency": 5.1e9}})
        aud = Auditor({"provider": "fake"}, fake_provider=fake)
        av = aud.audit({"context": {"target": "qA1"}})
        assert av.feature_visible is True
        assert av.direction == "right"
        assert av.discarded_numeric is True          # the number was dropped
        d = av.as_dict()
        assert d["feature_visible"] is True and d["direction"] == "right"
        assert "sneaky_frequency" not in json.dumps(d)

    def test_invalid_hint_values_normalize_to_none(self):
        fake = FakeProvider({"qA1": {
            "verdict": "accept", "failure_mode": None, "reason": "ok",
            "feature_visible": "yes", "direction": "up"}})
        aud = Auditor({"provider": "fake"}, fake_provider=fake)
        av = aud.audit({"context": {"target": "qA1"}})
        assert av.feature_visible is None
        assert av.direction is None

    def test_presence_reading_splits_a_2d_node_failure(self, tmp_path):
        """rvp/qsvp have no deterministic localizer — vision's presence bit
        selects the ladder for an opaque node failure, without ever turning
        the fail into a pass."""
        plan = _plan([{"id": "rvp",
                       "family": "resonator_spectroscopy_vs_power"}])
        fake = FakeProvider({"qA1": {
            "verdict": "abstain", "failure_mode": None,
            "reason": "clear dip visible", "feature_visible": True,
            "direction": None}})
        eng, _c, _b, _w = _mk(tmp_path, plan,
                              auditor=Auditor({"provider": "fake"},
                                              fake_provider=fake))
        res = StepRunResult(status="done", run={
            "experiment_name": "resonator_spectroscopy_vs_power",
            "fit_results": {"qA1": {"success": False}},
            "outcomes": {"qA1": "failed"}, "parameters": {},
            "folder_path": str(tmp_path), "patches": []})
        v = eng._evaluate(eng.plan.steps[0], res, ["qA1"])["qA1"]
        assert v.verdict == "fail"                    # never un-failed
        assert v.failure_mode == "feature_present_fit_failed"
        assert v.feature_present is True

    def test_presence_reading_empty_window_takes_direction(self, tmp_path):
        plan = _plan([{"id": "rvp",
                       "family": "resonator_spectroscopy_vs_power"}])
        fake = FakeProvider({"qA1": {
            "verdict": "abstain", "failure_mode": None,
            "reason": "flat noise, hint of a tail at the top edge",
            "feature_visible": False, "direction": "right"}})
        eng, _c, _b, _w = _mk(tmp_path, plan,
                              auditor=Auditor({"provider": "fake"},
                                              fake_provider=fake))
        res = StepRunResult(status="done", run={
            "experiment_name": "resonator_spectroscopy_vs_power",
            "fit_results": {"qA1": {"success": False}},
            "outcomes": {"qA1": "failed"}, "parameters": {},
            "folder_path": str(tmp_path), "patches": []})
        v = eng._evaluate(eng.plan.steps[0], res, ["qA1"])["qA1"]
        assert v.verdict == "fail"
        assert v.failure_mode == "no_signal"
        assert v.direction_hint == "right"


class TestSeedLifecycleRobustness:
    """Reviewed MAJOR: a leaked scan seed must never linger on the chip —
    the try/finally sweep restores it on every exit (abort / escalation)."""

    def test_seed_restored_when_step_aborts_mid_loop(self, tmp_path):
        plan = _plan([{"id": "qs", "family": "qubit_spectroscopy"}])
        eng, chip, _b, _w = _mk(tmp_path, plan)
        step = eng.plan.steps[0]
        pre = chip.get("qubits.qA1.f_01")
        rung = Rung(kind="seed_shift", seed_paths=_XY, span_default=100.0)
        eng._seed_shift(step, rung, "qA1", {"frequency_span_in_mhz": 40.0},
                        "right")
        assert chip.get("qubits.qA1.f_01") != pytest.approx(pre)
        assert ("qs", "qA1") in eng._seeds
        # simulate the abort path: _run_step_inner returns early, finally sweeps
        eng.abort_event.set()
        eng._run_step(step, ["qA1"], deque())
        assert chip.get("qubits.qA1.f_01") == pytest.approx(pre)
        assert not eng._seeds

    def test_seed_restored_across_escalation_handoff(self, tmp_path):
        """The seed rung fires (rung 2), then the escalate rung (rung 3) hands
        off to a continuation — the failed-hypothesis window shift is undone
        BEFORE the continuation sweeps, not left on the chip (rail ③)."""
        plan = _plan([{"id": "qs", "family": "qubit_spectroscopy",
                       "retry_max": 4,
                       "params": {"frequency_span_in_mhz": 30}}])
        eng, chip, _b, _w = _mk(tmp_path, plan)
        t = chip.qubits["qA1"]
        # deep visibility collapse → ladder reaches seed (skipped, no
        # direction) then escalate; give the seed a direction so it WRITES
        chip.set("qubits.qA1.resonator.f_01", t.f_res + 20e6)
        chip.set("qubits.qA1.resonator.RF_frequency", t.f_res + 20e6)
        st = _run(eng)
        assert st["status"] == "done"
        # no seed left outstanding anywhere after the plan
        assert not eng._seeds


class TestForwardAppliedDiscoveryRevert:
    """Reviewed MAJOR: a discovery applied via the engine's forward rows (the
    node wrote nothing) must still be revertible when the wide verify refutes
    it — the capture uses the applied rows, not the empty node patch list.

    Unit-level over _decide, which is exactly where capture + revert live —
    avoids fighting the full gate pipeline to manufacture the rare
    node-writes-nothing case."""

    def test_forward_applied_write_is_captured_and_reverted(self, tmp_path):
        plan = _plan([{"id": "qs", "family": "qubit_spectroscopy"}])
        eng, chip, _b, _w = _mk(tmp_path, plan)
        step = eng.plan.steps[0]
        fam = fam_mod.FAMILIES["qubit_spectroscopy"]
        truth = chip.qubits["qA1"].f_01
        pre = chip.get("qubits.qA1.f_01")

        # a node run with NO patches but a fitted frequency → the engine's
        # forward-apply path stages f_01 + RF (the discovery write)
        run = {"experiment_name": "08_qubit_spectroscopy",
               "fit_results": {"qA1": {"frequency": truth, "success": True}},
               "outcomes": {"qA1": "successful"}, "parameters": {},
               "folder_path": None, "patches": []}
        res = StepRunResult(status="done", run=run)
        v_pass = GateVerdict(target="qA1", verdict="pass")
        dec = eng._decide(step, "qA1", v_pass, res, fam, attempt=1)
        assert dec == "applied"
        # the applied write was captured as replace-patches (NOT the empty
        # node patch list) so a later revert has something to undo
        assert eng._last_write and all(
            p["op"] == "replace" for p in eng._last_write)
        assert chip.get("qubits.qA1.f_01") == pytest.approx(truth)

        # stash it as a discovery and run a verify step that FAILS
        eng._discoveries[("qs", "qA1")] = {"patches": list(eng._last_write)}
        vstep = Step(id="qs__verify_wide", family="qubit_spectroscopy",
                     verify_of="qs", inserted_by="verify_wide",
                     retry_max=0, criticality="soft", only_targets=("qA1",))
        v_fail = GateVerdict(target="qA1", verdict="fail",
                             failure_mode="wrong_peak",
                             reasons=["wide scan refutes the peak"])
        fail_run = {"experiment_name": "08_qubit_spectroscopy",
                    "fit_results": {"qA1": {"frequency": truth,
                                            "success": False}},
                    "outcomes": {"qA1": "failed"}, "parameters": {},
                    "folder_path": None, "patches": []}
        eng._decide(vstep, "qA1", v_fail, StepRunResult(status="done",
                                                        run=fail_run),
                    fam, attempt=0)
        ev = _ledger(eng)
        rev = [e for e in ev
               if e["event"] == "verify_failed_original_reverted"]
        assert rev and rev[0]["ok"] and rev[0]["paths"], \
            "forward-applied discovery was not actually reverted"
        # chip is back at the pre-discovery value
        assert chip.get("qubits.qA1.f_01") == pytest.approx(pre)


class TestSeededDiscoveryRevert:
    """Reviewed (verifier-limited): when a discovery was reached via a SEED,
    a verify-fail revert must chain back to the PRE-SEED value, not stop at
    the seeded (wrong) window; and a defer must CAS-restore an outstanding
    seed rather than drop it."""

    def test_verify_fail_after_seeded_discovery_restores_pre_plan(self,
                                                                  tmp_path):
        plan = _plan([{"id": "qs", "family": "qubit_spectroscopy"}])
        eng, chip, _b, _w = _mk(tmp_path, plan)
        step = eng.plan.steps[0]
        fam = fam_mod.FAMILIES["qubit_spectroscopy"]
        pre = chip.get("qubits.qA1.f_01")
        # seed the window (records old=pre), then the node "finds" a value F in
        # the seeded window and writes it (old=seeded value, value=F)
        rung = Rung(kind="seed_shift", seed_paths=_XY, span_default=100.0)
        eng._seed_shift(step, rung, "qA1", {"frequency_span_in_mhz": 40.0},
                        "right")
        seeded = chip.get("qubits.qA1.f_01")
        assert seeded != pytest.approx(pre)
        F = seeded + 1e6
        # the node applies its OWN write to the live state (here: the chip)
        chip.set("qubits.qA1.f_01", F)
        chip.set("qubits.qA1.xy.RF_frequency", F)
        run = {"experiment_name": "08_qubit_spectroscopy",
               "fit_results": {"qA1": {"frequency": F, "success": True}},
               "outcomes": {"qA1": "successful"}, "parameters": {},
               "folder_path": None,
               "patches": [{"op": "replace", "path": "/quam/qubits/qA1/f_01",
                            "value": F, "old": seeded},
                           {"op": "replace",
                            "path": "/quam/qubits/qA1/xy/RF_frequency",
                            "value": F, "old": seeded}]}
        # emulate the _run_step discovery capture (seed consumed → chain old)
        eng._decide(step, "qA1", GateVerdict(target="qA1", verdict="pass"),
                    StepRunResult(status="done", run=run), fam, attempt=1)
        from quam_state_manager.core.autofit.synth import patch_path_to_dotted
        disc = [dict(p) for p in eng._last_write]
        for p in disc:
            dotted = patch_path_to_dotted(p["path"])
            if dotted in eng._last_seed_old:
                p["old"] = eng._last_seed_old[dotted]
        eng._discoveries[("qs", "qA1")] = {"patches": disc}
        # the node write left the chip at F
        assert chip.get("qubits.qA1.f_01") == pytest.approx(F)
        # a verify step fails → revert the discovery → back to PRE-PLAN (pre),
        # not the seeded window
        vstep = Step(id="qs__verify_wide", family="qubit_spectroscopy",
                     verify_of="qs", inserted_by="verify_wide", retry_max=0,
                     criticality="soft", only_targets=("qA1",))
        fail_run = {"experiment_name": "08_qubit_spectroscopy",
                    "fit_results": {"qA1": {"frequency": F, "success": False}},
                    "outcomes": {"qA1": "failed"}, "parameters": {},
                    "folder_path": None, "patches": []}
        eng._decide(vstep, "qA1",
                    GateVerdict(target="qA1", verdict="fail",
                                failure_mode="wrong_peak", reasons=["x"]),
                    StepRunResult(status="done", run=fail_run), fam, attempt=0)
        assert chip.get("qubits.qA1.f_01") == pytest.approx(pre)

    def test_defer_cas_restores_an_outstanding_seed(self, tmp_path):
        plan = _plan([{"id": "qs", "family": "qubit_spectroscopy"}])
        eng, chip, _b, _w = _mk(tmp_path, plan)
        step = eng.plan.steps[0]
        fam = fam_mod.FAMILIES["qubit_spectroscopy"]
        pre = chip.get("qubits.qA1.f_01")
        rung = Rung(kind="seed_shift", seed_paths=_XY, span_default=100.0)
        eng._seed_shift(step, rung, "qA1", {"frequency_span_in_mhz": 40.0},
                        "left")
        assert chip.get("qubits.qA1.f_01") != pytest.approx(pre)
        # a suspect that defers (abstain) and produced NO patch on the seed
        # path → the seed must be CAS-restored, not left shifted
        run = {"experiment_name": "08_qubit_spectroscopy",
               "fit_results": {"qA1": {"frequency": chip.get("qubits.qA1.f_01"),
                                       "success": True}},
               "outcomes": {"qA1": "successful"}, "parameters": {},
               "folder_path": None, "patches": []}
        eng._decide(step, "qA1",
                    GateVerdict(target="qA1", verdict="suspect",
                                failure_mode="noisy", reasons=["marginal"]),
                    StepRunResult(status="done", run=run), fam, attempt=0)
        assert chip.get("qubits.qA1.f_01") == pytest.approx(pre)
        assert ("qs", "qA1") not in eng._seeds


class TestVerifyNotSkippedOnEscalation:
    def test_converged_target_verified_even_when_sibling_escalates(
            self, tmp_path):
        """A step with two targets: one converges (discovery), the other rides
        the ladder to escalation. The converged one must still get its wide
        verification — the escalation return no longer skips it."""
        plan = _plan([{"id": "qs", "family": "qubit_spectroscopy",
                       "retry_max": 4,
                       "params": {"frequency_span_in_mhz": 30}}])
        chip = synth.make_sim_chip(("qA1", "qA2"), (), seed=7)
        backend = SimBackend(chip, tmp_path / "data", seed=3)
        eng = PlanEngine(tmp_path / "inst", plan, ["qA1", "qA2"], backend,
                         SimWriter(chip), Auditor({"provider": "off"}))
        t2 = chip.qubits["qA2"]
        # qA1: out of window by 22 MHz (widen finds it → discovery)
        chip.set("qubits.qA1.f_01", chip.qubits["qA1"].f_01 + 22e6)
        chip.set("qubits.qA1.xy.RF_frequency", chip.qubits["qA1"].f_01 + 22e6)
        # qA2: deep readout collapse (rides to escalation)
        chip.set("qubits.qA2.resonator.f_01", t2.f_res + 20e6)
        chip.set("qubits.qA2.resonator.RF_frequency", t2.f_res + 20e6)
        st = _run(eng)
        assert st["status"] == "done"
        ev = _ledger(eng)
        vw = [e for e in ev if e["event"] == "verify_wide_inserted"]
        # qA1's discovery got verified despite qA2 escalating in the same step
        assert any("qA1" in (e.get("targets") or []) for e in vw)


class TestStepModel:
    def test_inserted_step_fields_serialize(self):
        s = Step(id="x__verify_wide", family="qubit_spectroscopy",
                 only_targets=("qA1",), verify_of="x",
                 inserted_by="verify_wide", carry_window_failure=("qA1",))
        d = s.as_dict()
        assert d["only_targets"] == ["qA1"]
        assert d["carry_window_failure"] == ["qA1"]
        assert d["verify_of"] == "x" and d["inserted_by"] == "verify_wide"
        json.dumps(d)                                  # ledger-safe
