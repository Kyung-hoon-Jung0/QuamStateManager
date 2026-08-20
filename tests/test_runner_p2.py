"""Runner+agent P2 (docs/78 §15): the flux/power families, the run-derived
update grammar, and the history gate that was dead in production.

Everything here is synthetic (tmp_path + the sim chip). The real-archive
accuracy ledger — 0 false rejects over 276 replayed targets — is recorded in
docs/78 §15 and reproduced by the job-side harness; committed tests never carry
customer paths (repo scrub doctrine, same rule as P0/P1).
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from quam_state_manager.core.autofit import families, gates, synth

NEW_FAMILIES = ["resonator_spectroscopy_vs_power",
                "resonator_spectroscopy_vs_flux",
                "resonator_spectroscopy_vs_coupler_flux",
                "qubit_spectroscopy_vs_power",
                "qubit_spectroscopy_vs_flux",
                "qubit_spectroscopy_vs_coupler_flux",
                "power_rabi"]


# ---------------------------------------------------------------------------
# The nine-family scope is actually registered and reachable by real names
# ---------------------------------------------------------------------------

class TestScopeRegistered:
    @pytest.mark.parametrize("node_name,fam_key", [
        ("05_resonator_spectroscopy_vs_power", "resonator_spectroscopy_vs_power"),
        ("1Q_05b_resonator_spectroscopy_vs_power_iq",
         "resonator_spectroscopy_vs_power"),
        ("06_resonator_spectroscopy_vs_flux", "resonator_spectroscopy_vs_flux"),
        ("2Q_07_resonator_spectroscopy_vs_coupler_flux",
         "resonator_spectroscopy_vs_coupler_flux"),
        ("08b_qubit_spectroscopy_vs_power", "qubit_spectroscopy_vs_power"),
        ("1Q_09_qubit_spectroscopy_vs_flux", "qubit_spectroscopy_vs_flux"),
        ("10_qubit_spectroscopy_vs_coupler_flux",
         "qubit_spectroscopy_vs_coupler_flux"),
        ("1Q_11_power_rabi", "power_rabi"),
    ])
    def test_archive_node_names_resolve(self, node_name, fam_key):
        fam = families.family_for(node_name)
        assert fam is not None and fam.key == fam_key

    @pytest.mark.parametrize("fam_key", ["resonator_spectroscopy_vs_coupler_flux",
                                         "qubit_spectroscopy_vs_coupler_flux"])
    def test_coupler_families_are_verify_only(self, fam_key):
        """Node 07's ``update_state`` is an empty stub and node 10 writes only a
        bookkeeping extras key. Inventing a calibration target for either would
        be the "figure axis is not the state value" trap (docs/78 D-1)."""
        assert families.FAMILIES[fam_key].updates == []

    def test_corpus_derived_floors_are_pinned(self):
        """These numbers came from replaying real runs and comparing the
        node-ACCEPTED side against the node-REJECTED side. Changing one is a
        claim about new data — it must move with docs/78 §15, not drift."""
        by_key = {f.key: {g.key: (g.min, g.max) for g in f.metric_gates}
                  for f in families.FAMILIES.values()}
        assert by_key["resonator_spectroscopy_vs_flux"]["ridge_amp_snr"] == (2.5, None)
        assert by_key["resonator_spectroscopy_vs_flux"]["ridge_coverage"] == (0.55, None)
        assert by_key["qubit_spectroscopy_vs_coupler_flux"]["num_crossings"] == (1, None)
        assert by_key["qubit_spectroscopy"]["peak_snr"] == (5.0, None)
        assert by_key["resonator_spectroscopy"]["dip_snr"] == (5.0, None)
        assert by_key["power_rabi"]["multipulse_fit_quality"] == (0.30, None)
        # the two families the corpus proved have NO separating numeric field
        assert by_key["qubit_spectroscopy_vs_flux"] == {}
        assert by_key["resonator_spectroscopy_vs_power"] == {}

    def test_jump_limits_clear_the_observed_accepted_moves(self):
        """Measured against the nodes' OWN accepted patches (docs/78 §15.2b).
        No node-rejected target emits a patch at all, so these limits have zero
        measured detection value — they are sanity envelopes, and tightening one
        below the observed accepted maximum is a pure false-alarm generator."""
        observed_max = {                    # largest ACCEPTED move in the corpus
            ("resonator_spectroscopy", "frequency"): 3.05e7,
            ("resonator_spectroscopy_vs_power", "resonator_frequency"): 4.5e6,
            ("qubit_spectroscopy", "frequency"): 1.392e8,
            ("qubit_spectroscopy_vs_power", "frequency"): 4.73e7,
            ("qubit_spectroscopy_vs_flux", "qubit_frequency"): 3.1e7,
            ("power_rabi", "opt_amp"): 0.538,
        }
        for (fam_key, key), seen in observed_max.items():
            lim = {p.key: p.max_abs_jump
                   for p in families.FAMILIES[fam_key].plausibility}[key]
            assert lim is not None and lim > seen, (fam_key, key, lim, seen)

    def test_qubit_spec_r2_floor_stays_below_the_observed_accepted_minimum(self):
        """The node itself accepted r² = 0.452; a floor at or above that is a
        production false-reject (this batch's measured regression)."""
        r2 = {g.key: g.min for g in
              families.FAMILIES["qubit_spectroscopy"].metric_gates}["r2"]
        assert r2 < 0.452


# ---------------------------------------------------------------------------
# Run-derived updates (docs/78 D-14): routing, ops, guards, {operation}
# ---------------------------------------------------------------------------

def _state(flux_point="independent", **over):
    base = {"qubits.qA1.z.flux_point": flux_point,
            "qubits.qA1.z.independent_offset": 0.0,
            "qubits.qA1.z.joint_offset": 0.11,
            "qubits.qA1.z.min_offset": 0.0,
            "qubits.qA1.f_01": 5.0e9,
            "qubits.qA1.xy.RF_frequency": 5.0e9,
            "qubits.qA1.resonator.f_01": 7.2e9,
            "qubits.qA1.resonator.RF_frequency": 7.2e9,
            "qubits.qA1.xy.operations.x180_DragCosine.amplitude": 0.3}
    base.update(over)
    return lambda p: base[p]


class TestRoutedUpdates:
    def test_node06_else_branch_writes_joint_for_any_other_flux_point(self):
        """06's analysis is if/else: anything that is not "independent" writes
        the JOINT offset. The else-branch must fire exactly once, and only when
        no exact branch did."""
        fam = families.FAMILIES["resonator_spectroscopy_vs_flux"]
        entry = {"idle_offset": 0.07, "frequency_shift": 1.0e6}
        rows = families.resolve_updates(fam, "qA1", entry, {},
                                        _state("joint"))
        paths = {r["path"]: r for r in rows}
        assert "qubits.qA1.z.joint_offset" in paths
        assert "qubits.qA1.z.independent_offset" not in paths
        assert paths["qubits.qA1.z.joint_offset"]["value"] == 0.07  # ASSIGN

        rows = families.resolve_updates(fam, "qA1", entry, {},
                                        _state("independent"))
        paths = {r["path"] for r in rows}
        assert "qubits.qA1.z.independent_offset" in paths
        assert "qubits.qA1.z.joint_offset" not in paths, \
            "the else-branch fired alongside an exact match"

    def test_node09_increments_joint_and_writes_nothing_when_unrecognised(self):
        """09 differs from 06 on the SAME fit key: independent ASSIGNS, joint
        INCREMENTS, and an unknown flux_point writes NO offset (its if/elif has
        no else). Collapsing the two families would corrupt one of them."""
        fam = families.FAMILIES["qubit_spectroscopy_vs_flux"]
        entry = {"idle_offset": 0.02, "qubit_frequency": 5.001e9}
        params = {"flux_offset_span_in_v": 0.5}

        rows = families.resolve_updates(fam, "qA1", entry, params,
                                        _state("joint"))
        by = {r["path"]: r for r in rows}
        assert by["qubits.qA1.z.joint_offset"]["value"] == pytest.approx(0.13)
        assert by["qubits.qA1.z.joint_offset"]["op"] == "add_to_current"

        rows = families.resolve_updates(fam, "qA1", entry, params,
                                        _state("independent"))
        by = {r["path"]: r for r in rows}
        assert by["qubits.qA1.z.independent_offset"]["value"] == 0.02

        rows = families.resolve_updates(fam, "qA1", entry, params,
                                        _state("some_new_scheme"))
        assert not [r for r in rows if ".z." in r["path"]]
        # the frequency writes still happen — only the offset is routed
        assert {r["path"] for r in rows} == {"qubits.qA1.f_01",
                                             "qubits.qA1.xy.RF_frequency"}

    def test_offset_outside_the_swept_span_writes_nothing(self):
        """Node 09's own pre-write condition: an idle offset beyond half the
        swept span is not a sweet spot the run actually saw."""
        fam = families.FAMILIES["qubit_spectroscopy_vs_flux"]
        entry = {"idle_offset": 0.9, "qubit_frequency": 5.001e9}
        rows = families.resolve_updates(fam, "qA1", entry,
                                        {"flux_offset_span_in_v": 0.5},
                                        _state())
        assert rows == []

    def test_min_offset_is_opt_in(self):
        fam = families.FAMILIES["resonator_spectroscopy_vs_flux"]
        entry = {"idle_offset": 0.05, "min_offset": -0.02}
        base = {"flux_offset_span_in_v": 0.5}
        rows = families.resolve_updates(fam, "qA1", entry, base, _state())
        assert "qubits.qA1.z.min_offset" not in {r["path"] for r in rows}
        rows = families.resolve_updates(fam, "qA1", entry,
                                        dict(base, update_flux_min=True),
                                        _state())
        assert "qubits.qA1.z.min_offset" in {r["path"] for r in rows}

    def test_resonator_shift_is_an_increment_not_an_assign(self):
        fam = families.FAMILIES["resonator_spectroscopy_vs_flux"]
        rows = families.resolve_updates(
            fam, "qA1", {"idle_offset": 0.05, "frequency_shift": 2.0e6},
            {}, _state())
        by = {r["path"]: r for r in rows}
        assert by["qubits.qA1.resonator.f_01"]["value"] == 7.2e9 + 2.0e6
        assert by["qubits.qA1.resonator.f_01"]["op"] == "add_to_current"

    def test_power_rabi_operation_comes_from_the_run(self):
        """This chip's pulse is `x180_DragCosine`; the old hardcoded `x180`
        addressed a field that does not exist on it (docs/78 D-14)."""
        fam = families.FAMILIES["power_rabi"]
        entry = {"opt_amp": 0.31}
        assert families.resolve_updates(fam, "qA1", entry, {}, _state()) == []
        rows = families.resolve_updates(
            fam, "qA1", entry, {"operation": "x180_DragCosine"}, _state())
        assert [r["path"] for r in rows] == \
            ["qubits.qA1.xy.operations.x180_DragCosine.amplitude"]


class TestLoopCanActuallyRunTheScope:
    """docs/78 §17 D2/D3 — the wiring that decides whether a plan step RUNS and
    whether a flagged target RETRIES. Both failed silently: a skipped step still
    reported `done`, and a family with no rung deferred instead of re-measuring
    while the ledger recorded it as an honest suspect."""

    def test_the_sim_can_run_every_scoped_family(self):
        from quam_state_manager.core.autofit.simbackend import FAMILY_TO_NODE
        for key in NEW_FAMILIES + ["resonator_spectroscopy",
                                   "qubit_spectroscopy"]:
            node = FAMILY_TO_NODE.get(key)
            assert node, f"sim cannot run {key} — a step for it is SKIPPED " \
                         f"while the plan still reports done"
            assert node in synth.GENERATORS, (key, node)

    @pytest.mark.parametrize("key", NEW_FAMILIES + ["resonator_spectroscopy",
                                                   "qubit_spectroscopy"])
    def test_a_family_that_can_be_flagged_wrong_peak_can_answer_it(self, key):
        """G2 emits `wrong_peak` for every consistency-check hit; with no rung
        `can_retry` is False and the target defers."""
        fam = families.FAMILIES[key]
        if not fam.consistency_checks:
            pytest.skip("cannot be flagged wrong_peak by a consistency check")
        assert "wrong_peak" in (fam.adaptations or {}), key

    def test_the_rabi_wrong_peak_rung_tightens_the_window(self):
        """A locked harmonic is answered by scanning tighter and finer around
        the parked amplitude — never by editing the fitted number."""
        rung = families.FAMILIES["power_rabi"].adaptations["wrong_peak"]
        out = rung({"min_amp_factor": 0.8, "max_amp_factor": 1.2,
                    "amp_factor_step": 0.002, "num_shots": 100})
        assert 0.8 < out["min_amp_factor"] < 1.0 < out["max_amp_factor"] < 1.2
        assert out["amp_factor_step"] < 0.002
        assert not any(k in out for k in ("opt_amp", "amplitude"))


class TestPowerCoupling:
    """The rvp node's update is ATOMIC across frequency + readout amplitude +
    the SHARED port FSP + every sibling amp on that feedline. The engine used to
    build only the frequency rows, which is a silent partial write (docs/56 §6G,
    r12 doctrine)."""

    def test_engine_adds_the_coupled_power_rows(self, tmp_path):
        from quam_state_manager.core.autofit import power_rows
        from quam_state_manager.core.autofit.auditor import Auditor
        from quam_state_manager.core.autofit.engine import PlanEngine
        from quam_state_manager.core.autofit.plan import validate_plan
        from quam_state_manager.core.autofit.simbackend import SimBackend, SimWriter

        chip = synth.make_sim_chip(("qA1", "qA2"), ("qA2-qA1",), seed=7)
        plan = validate_plan({"name": "p", "targets_kind": "qubits",
                              "targets": ["qA1"], "steps": [
                                  {"id": "s", "family": "power_rabi"}]})
        eng = PlanEngine(tmp_path / "inst", plan, ["qA1"],
                         SimBackend(chip, tmp_path / "d", seed=3),
                         SimWriter(chip), Auditor({"provider": "off"}))
        eng._ledger_dir.mkdir(parents=True, exist_ok=True)  # start() would
        fam = families.FAMILIES[power_rows.POWER_COUPLED_FAMILY]
        fresh = {"resonator_frequency": 7.2e9, "target_amplitude": 0.05,
                 "target_full_scale_power_dbm": 4.0, "readout_line": "line1"}
        run = {"fit_results": {"qA1": fresh}, "parameters": {}}
        rows = eng._forward_rows(fam, "qA1", run)
        # the sim chip carries no feedline wiring, so power coupling must
        # REFUSE — and say so in the ledger rather than writing a quiet partial
        assert [r["path"] for r in rows] == ["qubits.qA1.resonator.f_01",
                                             "qubits.qA1.resonator.RF_frequency"]
        led = (Path(eng.instance_path) / "autofit" / "runs" / eng.plan_run_id
               / "ledger.jsonl").read_text(encoding="utf-8")
        skipped = [json.loads(x) for x in led.splitlines()
                   if json.loads(x).get("event") == "power_rows_skipped"]
        assert skipped and skipped[0]["reason"]

    def test_only_the_rvp_family_is_power_coupled(self):
        """The plain FSP identity provably does NOT hold for the qubit node
        (+3.98 dB constant offset on the real archive) — it is a resonator
        convention, not a universal law."""
        from quam_state_manager.core.autofit import power_rows
        assert power_rows.POWER_COUPLED_FAMILY == "resonator_spectroscopy_vs_power"
        out = power_rows.coupled_power_rows(
            "qubit_spectroscopy_vs_power", "qA1",
            {"target_amplitude": 0.05, "target_full_scale_power_dbm": 4.0,
             "readout_line": "line1"}, {})
        assert out["rows"] == [] and out["skipped"]


class TestOperationAliases:
    """Real chips carry `operations.x180 = "#./x180_DragCosine"`. The RUN names
    the alias; the NODE patches the target (measured over three labs). A write
    to the alias path would store a field under a pointer string."""

    ALIASED = {
        "qubits.qA1.xy.operations": {
            "x180": "#./x180_DragCosine",
            "x90": "#./x90_DragCosine",
            "x180_DragCosine": {"amplitude": 0.32, "length": 48},
            "x90_DragCosine": {"amplitude": 0.16, "length": 48},
        },
    }

    def _vo(self, extra=None):
        flat = {
            "qubits.qA1.xy.operations.x180": "#./x180_DragCosine",
            "qubits.qA1.xy.operations.x90": "#./x90_DragCosine",
            "qubits.qA1.xy.operations.x180_DragCosine": {"amplitude": 0.32},
            "qubits.qA1.xy.operations.x180_DragCosine.amplitude": 0.32,
            "qubits.qA1.xy.operations.x90_DragCosine": {"amplitude": 0.16},
            "qubits.qA1.xy.operations.x90_DragCosine.amplitude": 0.16,
            "qubits.qA1": {}, "qubits": {},
            "qubits.qA1.xy": {}, "qubits.qA1.xy.operations": {},
        }
        flat.update(extra or {})

        def vo(p):
            if p not in flat:
                raise KeyError(p)
            return flat[p]
        return vo

    def test_alias_is_followed_to_the_real_pulse(self):
        fam = families.FAMILIES["power_rabi"]
        rows = families.resolve_updates(
            fam, "qA1", {"opt_amp": 0.25},
            {"operation": "x180", "update_x90": True}, self._vo())
        assert [r["path"] for r in rows] == [
            "qubits.qA1.xy.operations.x180_DragCosine.amplitude",
            "qubits.qA1.xy.operations.x90_DragCosine.amplitude"]
        assert [r["value"] for r in rows] == [0.25, 0.125]

    def test_x90_is_opt_in_via_the_run_flag(self):
        fam = families.FAMILIES["power_rabi"]
        rows = families.resolve_updates(fam, "qA1", {"opt_amp": 0.25},
                                        {"operation": "x180"}, self._vo())
        assert [r["path"] for r in rows] == \
            ["qubits.qA1.xy.operations.x180_DragCosine.amplitude"]

    def test_unresolvable_alias_refuses_rather_than_guessing(self):
        fam = families.FAMILIES["power_rabi"]
        vo = self._vo({"qubits.qA1.xy.operations.x180": "#./nope"})
        assert families.resolve_updates(fam, "qA1", {"opt_amp": 0.25},
                                        {"operation": "x180"}, vo) == []

    def test_absolute_and_parent_relative_forms(self):
        vo = self._vo({
            "qubits.qA1.xy.operations.abs": "#/qubits/qA1/xy/operations/"
                                            "x180_DragCosine",
            "qubits.qA1.xy.operations.up": "#../operations/x90_DragCosine",
        })
        assert families.resolve_alias_path(
            "qubits.qA1.xy.operations.abs.amplitude", vo) == \
            "qubits.qA1.xy.operations.x180_DragCosine.amplitude"
        assert families.resolve_alias_path(
            "qubits.qA1.xy.operations.up.amplitude", vo) == \
            "qubits.qA1.xy.operations.x90_DragCosine.amplitude"

    def test_plain_paths_and_absent_readers_pass_through(self):
        vo = self._vo()
        assert families.resolve_alias_path(
            "qubits.qA1.xy.operations.x180_DragCosine.amplitude", vo) == \
            "qubits.qA1.xy.operations.x180_DragCosine.amplitude"
        assert families.resolve_alias_path("a.b.c", None) == "a.b.c"

    def test_a_pointer_LEAF_is_never_followed(self):
        """A leaf holding a pointer is a different question with a different
        answer: the nodes replace it with a number (`/quam/qubits/q/f_01`), so
        following it would write where the node never writes."""
        vo = self._vo({"qubits.qA1.f_01": "#/qubits/qA1/xy/RF_frequency",
                       "qubits.qA1.xy.RF_frequency": 5e9})
        assert families.resolve_alias_path("qubits.qA1.f_01", vo) == \
            "qubits.qA1.f_01"

    def test_trend_anchor_follows_the_alias_too(self):
        fam = families.FAMILIES["power_rabi"]
        assert families.trend_path_for(fam, "opt_amp", "qA1",
                                       {"operation": "x180"}, self._vo()) == \
            "qubits.qA1.xy.operations.x180_DragCosine.amplitude"


# ---------------------------------------------------------------------------
# G5: the history trend anchor (P2d — it was constructed but never supplied)
# ---------------------------------------------------------------------------

class TestTrendAnchor:
    def test_routed_families_anchor_on_the_branch_that_will_fire(self):
        fam = families.FAMILIES["resonator_spectroscopy_vs_flux"]
        assert families.trend_path_for(fam, "idle_offset", "qA1", {},
                                       _state("independent")) == \
            "qubits.qA1.z.independent_offset"
        assert families.trend_path_for(fam, "idle_offset", "qA1", {},
                                       _state("joint")) == \
            "qubits.qA1.z.joint_offset"

    def test_no_reader_never_guesses_an_else_branch(self):
        """Without a state reader we cannot tell "else" from "unknown", and a
        trend compared against the WRONG offset field manufactures drift."""
        fam = families.FAMILIES["resonator_spectroscopy_vs_flux"]
        assert families.trend_path_for(fam, "idle_offset", "qA1", {}) is None

    def test_a_non_assign_write_has_no_honest_trend(self):
        """docs/78 §17 D1. Ramsey writes `f_01 -= freq_offset`: the FIELD holds
        a ~5 GHz frequency while the FIT KEY is a ~MHz offset, so anchoring on
        the written path made G5 report a 449,605-sigma drift on a clean run.
        An offset or scaled write has no history of its own — abstain."""
        fam = families.FAMILIES["ramsey"]
        assert fam.value_key == "freq_offset"
        assert any(u.op == "subtract_from_current" for u in fam.updates)
        assert families.trend_path_for(fam, "freq_offset", "qA1", {},
                                       _state()) is None

    def test_every_family_with_a_trend_anchors_on_an_assign(self):
        for key, fam in families.FAMILIES.items():
            path = families.trend_path_for(fam, fam.value_key, "qA1",
                                           {"operation": "x180"}, _state())
            if path is None:
                continue
            ups = [u for u in fam.updates if u.fit_key == fam.value_key]
            assert not ups or any(u.op == "assign" and u.factor == 1.0
                                  for u in ups), key

    def test_verify_only_families_have_no_trend(self):
        for key in ("resonator_spectroscopy_vs_coupler_flux",
                    "qubit_spectroscopy_vs_coupler_flux"):
            fam = families.FAMILIES[key]
            assert families.trend_path_for(fam, fam.value_key, "qA2-qA1", {},
                                           _state()) is None

    def test_operation_placeholder_is_filled_from_the_run(self):
        fam = families.FAMILIES["power_rabi"]
        assert families.trend_path_for(fam, "opt_amp", "qA1", {},
                                       _state()) is None
        assert families.trend_path_for(fam, "opt_amp", "qA1",
                                       {"operation": "x180_DragCosine"},
                                       _state()) == \
            "qubits.qA1.xy.operations.x180_DragCosine.amplitude"

    def test_history_catches_the_drift_the_other_gates_cannot(self, tmp_path):
        """The 06 `drift` ledger cell is a documented blind spot WITHOUT
        history: a 0.4 V offset step is inside every physical band and leaves
        the ridge metrics intact. With the chip's own trend supplied it becomes
        a suspect — which is exactly what wiring G5 buys (docs/78 P2d)."""
        chip = synth.make_sim_chip(("qA1", "qA2"), ("qA2-qA1",), seed=7)
        fam = families.FAMILIES["resonator_spectroscopy_vs_flux"]
        sr = synth.synth_run("06_resonator_spectroscopy_vs_flux", chip, ["qA1"],
                             tmp_path, 601, seed=5, corrupt="drift")
        patched = {synth.patch_path_to_dotted(p["path"]): p["old"]
                   for p in sr.patches}
        run = {"fit_results": sr.fit_results, "outcomes": {"qA1": "successful"},
               "parameters": {}, "folder_path": sr.folder}
        args = dict(current_value_of=chip.get,
                    pre_update_value_of=lambda p: patched.get(p, chip.get(p)))

        blind = gates.evaluate_run(run, fam, ["qA1"], **args)["qA1"]
        assert blind.verdict == "pass"

        sweet = chip.qubits["qA1"].flux_sweet_spot
        trend = [sweet + d for d in (0.001, -0.002, 0.0, 0.001, -0.001)]
        seen = gates.evaluate_run(run, fam, ["qA1"],
                                  history_points_of=lambda t: trend,
                                  **args)["qA1"]
        assert seen.verdict == "suspect"
        assert seen.failure_mode == "drifted"
        assert seen.checks["G5_history"] == "suspect"

    def test_history_never_flags_a_value_on_its_own_trend(self, tmp_path):
        chip = synth.make_sim_chip(("qA1", "qA2"), ("qA2-qA1",), seed=7)
        fam = families.FAMILIES["resonator_spectroscopy_vs_flux"]
        sr = synth.synth_run("06_resonator_spectroscopy_vs_flux", chip, ["qA1"],
                             tmp_path, 602, seed=5)
        patched = {synth.patch_path_to_dotted(p["path"]): p["old"]
                   for p in sr.patches}
        run = {"fit_results": sr.fit_results, "outcomes": {"qA1": "successful"},
               "parameters": {}, "folder_path": sr.folder}
        claim = sr.fit_results["qA1"]["idle_offset"]
        trend = [claim + d for d in (0.001, -0.002, 0.0, 0.0015, -0.001)]
        v = gates.evaluate_run(
            run, fam, ["qA1"], current_value_of=chip.get,
            pre_update_value_of=lambda p: patched.get(p, chip.get(p)),
            history_points_of=lambda t: trend)["qA1"]
        assert v.verdict == "pass"
        assert v.checks["G5_history"] == "ok"

    def test_engine_asks_for_resolved_paths_and_feeds_the_gate(self, tmp_path):
        """End-to-end through the REAL engine: the provider must be handed
        resolved dot-paths (never a bare fit key — guessing one is what left the
        gate dead), and what it returns must reach the verdict."""
        from quam_state_manager.core.autofit.auditor import Auditor
        from quam_state_manager.core.autofit.engine import PlanEngine
        from quam_state_manager.core.autofit.plan import validate_plan
        from quam_state_manager.core.autofit.simbackend import SimBackend, SimWriter

        chip = synth.make_sim_chip(("qA1", "qA2"), ("qA2-qA1",), seed=7)
        seen: list[dict] = []

        def provider(path_map):
            seen.append(dict(path_map))
            # a trend that is nowhere near any real f_01 ⇒ every target drifts
            return {t: [1.0e9, 1.0e9 + 1e3, 1.0e9 - 1e3, 1.0e9]
                    for t in path_map}

        plan = validate_plan({"name": "p", "targets_kind": "qubits",
                              "autonomy": "review", "targets": ["qA1"],
                              "steps": [{"id": "qs",
                                         "family": "qubit_spectroscopy",
                                         "retry_max": 0,
                                         "criticality": "soft"}]})
        eng = PlanEngine(tmp_path / "inst", plan, ["qA1"],
                         SimBackend(chip, tmp_path / "data", seed=3),
                         SimWriter(chip), Auditor({"provider": "off"}),
                         history_points_of=provider)
        eng.start()
        eng._thread.join(30.0)
        assert not eng.is_running()

        assert seen, "the engine never asked for a history trend"
        assert seen[0] == {"qA1": "qubits.qA1.f_01"}
        ledger = [json.loads(line) for line in
                  (Path(eng.instance_path) / "autofit" / "runs"
                   / eng.plan_run_id / "ledger.jsonl")
                  .read_text(encoding="utf-8").splitlines()]
        drifted = [e for e in ledger if e.get("event") == "verdict"
                   and e.get("failure_mode") == "drifted"]
        assert drifted, "the supplied trend never reached a verdict"
        assert drifted[0]["checks"]["G5_history"] == "suspect"

    def test_routes_wire_the_provider(self):
        """The gate is only alive if the ROUTE passes it — this is the exact
        omission P2d fixed."""
        import inspect

        from quam_state_manager.web import routes
        src = inspect.getsource(routes._autofit_start_real)
        assert "history_points_of=" in src
        # one snapshot pass for the whole target set, not one per qubit
        assert "column_history" in src


# ---------------------------------------------------------------------------
# The sim corpus stays readable by every SM reader (the P0/P8 contract)
# ---------------------------------------------------------------------------

class TestSimFidelity:
    @pytest.mark.parametrize("node_name", [
        "05_resonator_spectroscopy_vs_power",
        "06_resonator_spectroscopy_vs_flux",
        "07_resonator_spectroscopy_vs_coupler_flux",
        "08b_qubit_spectroscopy_vs_power",
        "09_qubit_spectroscopy_vs_flux",
        "10_qubit_spectroscopy_vs_coupler_flux",
    ])
    def test_run_carries_the_fields_its_family_gates_on(self, tmp_path,
                                                        node_name):
        chip = synth.make_sim_chip(("qA1", "qA2"), ("qA2-qA1",), seed=7)
        _, kind = synth.GENERATORS[node_name]
        targets = ["qA2-qA1"] if kind == "qubit_pairs" else ["qA1"]
        sr = synth.synth_run(node_name, chip, targets, tmp_path, 700, seed=3)
        fam = families.family_for(node_name)
        entry = sr.fit_results[targets[0]]
        for mg in fam.metric_gates:
            assert mg.key in entry, f"{node_name} omits gated metric {mg.key}"
        for pl in fam.plausibility:
            assert pl.key in entry, f"{node_name} omits gated value {pl.key}"

    def test_power_rabi_run_names_its_operation(self, tmp_path):
        """The `{operation}` in power_rabi's update path is filled from the
        RUN's parameters. A sim run that omitted it would resolve zero write
        rows — silently leaving the loop's most important write unexercised."""
        chip = synth.make_sim_chip(("qA1", "qA2"), ("qA2-qA1",), seed=7)
        sr = synth.synth_run("11_power_rabi", chip, ["qA1"], tmp_path, 500,
                             seed=3)
        node = json.loads((sr.folder / "node.json").read_text(encoding="utf-8"))
        params = node["data"]["parameters"]["model"]
        assert params.get("operation"), "run does not name its pulse"
        rows = families.resolve_updates(
            families.family_for("11_power_rabi"), "qA1",
            sr.fit_results["qA1"], params, chip.get)
        assert [r["path"] for r in rows] == \
            [f"qubits.qA1.xy.operations.{params['operation']}.amplitude"]

    def test_flux_cube_is_a_real_map_with_a_vertex(self, tmp_path):
        """The 2-D families exist because the ridge has a vertex; a sim that
        emitted a flat cube would make every gate meaningless."""
        import h5py

        chip = synth.make_sim_chip(("qA1", "qA2"), ("qA2-qA1",), seed=7)
        sr = synth.synth_run("06_resonator_spectroscopy_vs_flux", chip, ["qA1"],
                             tmp_path, 610, seed=3)
        with h5py.File(sr.folder / "ds_raw.h5", "r") as f:
            cube = np.asarray(f["IQ_abs"][0], dtype=float)
            flux = np.asarray(f["flux_bias"][()], dtype=float)
            detuning = np.asarray(f["detuning"][()], dtype=float)
        assert cube.shape == (flux.size, detuning.size)
        # dip frequency per flux column = the ridge; its apex is the sweet spot.
        # Recover it the way the node does — a parabola fit, not an argmax: the
        # arch is flat near its top and the frequency grid quantizes it, so the
        # raw extremum ties across many columns (which is precisely why these
        # families have no honest 1-D localizer, docs/47).
        ridge = detuning[np.argmin(cube, axis=1)]
        assert np.ptp(ridge) > 0.0, "flat cube — nothing for the node to fit"
        a, b, _ = np.polyfit(flux, ridge, 2)
        assert a < 0, "ridge does not curve downward — no sweet spot"
        vertex = float(-b / (2 * a))
        assert abs(vertex - chip.qubits["qA1"].flux_sweet_spot) < 0.03
