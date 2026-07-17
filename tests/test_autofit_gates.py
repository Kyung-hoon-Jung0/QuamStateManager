"""Autofit deterministic gates — the per-family accuracy ledger (docs/56 §6.2).

The docs/47 methodology, CI-enforced: manufacture wrong-fit runs per family ×
corruption mode and require the gate pipeline to reject them; require clean
runs to pass. The EXPECTATION MATRIX below is the honest published coverage —
``fail`` = hard reject, ``not_pass`` = fail-or-suspect (never accepted as-is),
``pass_allowed`` = a DOCUMENTED v1 blind spot (deliberately uncovered without a
node-faithful re-fit / history trend / LLM — see docs/56 §2c). Tightening a
cell is progress; silently downgrading one is a regression this file blocks.
"""
from __future__ import annotations

import pytest

from quam_state_manager.core.autofit import families, gates, synth


def _mk_chip():
    return synth.make_sim_chip(("qA1", "qA2"), ("qA2-qA1",), seed=7)


def _evaluate(sr, chip, run=None):
    """Gate a SynthRun the way the engine will: current = post-run sim state,
    pre-update = patches-first old values."""
    fam = families.family_for(sr.node_name)
    assert fam is not None, f"family_for missed {sr.node_name}"
    patched_old = {synth.patch_path_to_dotted(p["path"]): p["old"]
                   for p in sr.patches}

    def current_value_of(path):
        return chip.get(path)

    def pre_update_value_of(path):
        if path in patched_old:
            return patched_old[path]
        return chip.get(path)

    run_obj = run if run is not None else {
        "fit_results": sr.fit_results,
        "outcomes": {t: "successful" for t in sr.targets},
        "parameters": {"operation": "cz_unipolar"},
        "folder_path": sr.folder,
    }
    return gates.evaluate_run(run_obj, fam, sr.targets,
                              current_value_of=current_value_of,
                              pre_update_value_of=pre_update_value_of)


# --------------------------------------------------------------------------
# THE LEDGER — family × corruption expectations
# --------------------------------------------------------------------------
# fail          gate pipeline must HARD-fail the target
# not_pass      fail or suspect (never verdict == "pass")
# pass_allowed  documented v1 blind spot (value plausible; needs refit/LLM/history)
# n/a           the synth generator has no such corruption branch for the family

LEDGER = {
    "03_resonator_spectroscopy": {
        "wrong_peak": "fail", "no_signal": "fail", "noisy": "not_pass",
        "out_of_band": "fail", "drift": "fail",
    },
    "08_qubit_spectroscopy": {
        "wrong_peak": "fail", "no_signal": "fail", "noisy": "not_pass",
        "out_of_band": "fail", "drift": "fail",
    },
    "11_power_rabi": {
        "wrong_peak": "fail",        # ×3 harmonic ⇒ prefactor outside [0.5, 2]
        "no_signal": "fail",         # span check: flat trace
        "noisy": "pass_allowed",     # value ≈ truth, no quality metric in fit
        "out_of_band": "fail",
        "drift": "n/a",
    },
    "12_ramsey": {
        "wrong_peak": "n/a",
        "no_signal": "fail",
        "noisy": "not_pass",         # honest 40% error bar trips the ratio gate
        "out_of_band": "fail",
        "drift": "pass_allowed",     # 250 kHz offset drift needs history/LLM
    },
    "25_T1": {
        "wrong_peak": "n/a",
        "no_signal": "fail",
        "noisy": "not_pass",
        "out_of_band": "fail",
        "drift": "not_pass",         # ×3 jump vs pre-run state (rel-jump gate)
    },
    "26_echo": {
        "wrong_peak": "n/a",
        "no_signal": "fail",
        "noisy": "not_pass",
        "out_of_band": "fail",
        "drift": "not_pass",
    },
    "15a_readout_frequency_optimization": {
        "wrong_peak": "fail",
        "no_signal": "fail",
        "noisy": "pass_allowed",     # value ≈ truth; no r2 in this fit shape
        "out_of_band": "fail",
        "drift": "fail",
    },
    "16_iq_blobs": {
        "wrong_peak": "n/a",
        "no_signal": "not_pass",     # unseparable blobs ⇒ fidelity floor
        "noisy": "pass_allowed",
        "out_of_band": "fail",
        "drift": "pass_allowed",     # a wrong angle is invisible w/o a re-fit
    },
    "31_chevron_11_02": {
        "wrong_peak": "not_pass",    # cz_len ↔ 1/(2J) internal inconsistency
        "no_signal": "fail",
        "noisy": "pass_allowed",
        "out_of_band": "fail",
        "drift": "pass_allowed",     # small amp drift within plausibility
    },
    "32_cz_conditional_phase": {
        "wrong_peak": "pass_allowed",  # 6% amp error needs error-amp refit/LLM
        "no_signal": "fail",
        "noisy": "pass_allowed",
        "out_of_band": "fail",
        "drift": "pass_allowed",
    },
}


def _targets_for(node_name):
    _, kind = synth.GENERATORS[node_name]
    return (["qA2-qA1"] if kind == "qubit_pairs" else ["qA1"]), kind


class TestFamilyMatching:
    @pytest.mark.parametrize("node_name", list(synth.GENERATORS))
    def test_every_synth_node_resolves_a_family(self, node_name):
        assert families.family_for(node_name) is not None

    def test_graph_prefixed_and_alias_names_resolve(self):
        assert families.family_for("1Q_08_qubit_spectroscopy_new").key == \
            "qubit_spectroscopy"
        assert families.family_for("2Q_19_chevron_1102").key == "chevron_11_02"
        assert families.family_for(
            "2Q_20b_cz_conditional_phase_error_amp").key == "cz_conditional_phase"


class TestCleanRunsPass:
    @pytest.mark.parametrize("node_name", list(LEDGER))
    @pytest.mark.parametrize("seed", [11, 12, 13])
    def test_clean_run_passes(self, tmp_path, node_name, seed):
        chip = _mk_chip()
        targets, _ = _targets_for(node_name)
        sr = synth.synth_run(node_name, chip, targets, tmp_path, 700 + seed,
                             seed=seed)
        verdicts = _evaluate(sr, chip)
        for t, v in verdicts.items():
            assert v.verdict == "pass", (node_name, t, v.as_dict())


class TestCorruptionLedger:
    @pytest.mark.parametrize("node_name,mode,expected", [
        (n, m, e) for n, row in LEDGER.items() for m, e in row.items()
        if e != "n/a"
    ])
    def test_ledger_cell(self, tmp_path, node_name, mode, expected):
        chip = _mk_chip()
        targets, _ = _targets_for(node_name)
        sr = synth.synth_run(node_name, chip, targets, tmp_path, 800,
                             seed=21, corrupt=mode)
        verdicts = _evaluate(sr, chip)
        v = verdicts[targets[0]]
        if expected == "fail":
            assert v.verdict == "fail", (node_name, mode, v.as_dict())
        elif expected == "not_pass":
            assert v.verdict in ("fail", "suspect"), (node_name, mode, v.as_dict())
        elif expected == "pass_allowed":
            pass  # documented blind spot — no assertion on the verdict
        else:  # pragma: no cover
            raise AssertionError(expected)

    def test_node_failed_targets_hard_fail(self, tmp_path):
        chip = _mk_chip()
        sr = synth.synth_run("08_qubit_spectroscopy", chip, ["qA1"], tmp_path,
                             900, seed=31)
        run = {"fit_results": {"qA1": dict(sr.fit_results["qA1"],
                                           success=False)},
               "outcomes": {"qA1": "failed"}, "parameters": {},
               "folder_path": sr.folder}
        v = _evaluate(sr, chip, run=run)["qA1"]
        assert v.verdict == "fail" and v.failure_mode == "node_failed"

    def test_missing_ds_raw_is_never_silently_accepted(self, tmp_path):
        chip = _mk_chip()
        sr = synth.synth_run("08_qubit_spectroscopy", chip, ["qA1"], tmp_path,
                             901, seed=32)
        (sr.folder / "ds_raw.h5").unlink()
        v = _evaluate(sr, chip)["qA1"]
        assert v.verdict == "suspect"
        assert v.failure_mode == "unverifiable"


class TestUpdateResolution:
    def test_ramsey_subtract_and_chevron_ceil4(self, tmp_path):
        chip = _mk_chip()
        pre_f01 = chip.get("qubits.qA1.f_01")
        fam = families.family_for("12_ramsey")
        rows = families.resolve_updates(
            fam, "qA1", {"freq_offset": 2.5e5, "decay": 2e-5}, {},
            lambda p: {"qubits.qA1.f_01": pre_f01,
                       "qubits.qA1.xy.RF_frequency": pre_f01,
                       "qubits.qA1.T2ramsey": 1e-5}[p])
        by_path = {r["path"]: r for r in rows}
        assert by_path["qubits.qA1.f_01"]["value"] == pre_f01 - 2.5e5
        assert by_path["qubits.qA1.T2ramsey"]["value"] == 2e-5

        fam = families.family_for("31_chevron_11_02")
        rows = families.resolve_updates(
            fam, "qA2-qA1", {"cz_amp": 0.21, "cz_len": 46.3}, {},
            lambda p: 0.2)
        by_label = {r["label"]: r for r in rows}
        assert by_label["CZ length (ceil 4 ns)"]["value"] == 48

    def test_operation_placeholder_skips_when_missing(self):
        fam = families.family_for("32_cz_conditional_phase")
        rows = families.resolve_updates(fam, "qA2-qA1",
                                        {"optimal_amplitude": 0.2}, {},
                                        lambda p: 0.2)
        assert rows == []          # no run.parameters.operation ⇒ never guess
        rows = families.resolve_updates(fam, "qA2-qA1",
                                        {"optimal_amplitude": 0.2},
                                        {"operation": "cz_unipolar"},
                                        lambda p: 0.2)
        assert rows and "cz_unipolar" in rows[0]["path"]

    def test_pointer_current_blocks_subtract(self):
        fam = families.family_for("12_ramsey")
        rows = families.resolve_updates(
            fam, "qA1", {"freq_offset": 1e5}, {},
            lambda p: "#/qubits/qA1/f_01")   # pointer string current value
        assert all(r["op"] != "subtract_from_current" for r in rows)


class TestFitTargetsParity:
    def test_shared_paths_agree_with_fit_target_map(self):
        """Where FIT_TARGET_MAP covers a (family, fit_key), the autofit registry
        must write the SAME path — fit_targets stays the single source of truth
        for the UI, and the two must never drift (docs/56 §4)."""
        from quam_state_manager.core.fit_targets import FIT_TARGET_MAP
        pairs = {
            ("1Q_08_qubit_spectroscopy", "frequency", "qubit_spectroscopy"),
            ("1Q_11_power_rabi", "opt_amp", "power_rabi"),
            ("1Q_15a_readout_frequency_optimization", "optimal_frequency",
             "readout_frequency_optimization"),
            ("2Q_20b_cz_conditional_phase_error_amp", "optimal_amplitude",
             "cz_conditional_phase"),
        }
        for ft_prefix, fit_key, fam_key in pairs:
            ft_path = FIT_TARGET_MAP[ft_prefix][fit_key]["path"]
            fam = families.FAMILIES[fam_key]
            fam_paths = [u.path for u in fam.updates if u.fit_key == fit_key]
            assert ft_path in fam_paths, (ft_prefix, fit_key, fam_paths)

    def test_iq_blobs_is_verify_only(self):
        """The fitted iw_angle's sign convention is node-version-dependent
        (assign vs subtract-delta) — autofit must NEVER auto-write it
        (design-review physics #9). Gate-only family."""
        assert families.FAMILIES["iq_blobs"].updates == []
