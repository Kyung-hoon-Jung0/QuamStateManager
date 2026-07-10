"""Tests for core.fit_targets — the fit_result → state dot-path mapping that powers
the Dataset Results tab's "Apply to state" buttons."""
from __future__ import annotations

from quam_state_manager.core.fit_targets import FIT_TARGET_MAP, resolve_fit_targets


class _Run:
    def __init__(self, experiment_name, fit_results, parameters=None):
        self.experiment_name = experiment_name
        self.fit_results = fit_results
        self.parameters = parameters or {}


def test_resolve_maps_known_keys_and_skips_diagnostics():
    run = _Run("1Q_15b_readout_power_optimization", {"qA1": {
        "optimal_amplitude": 0.0197, "iw_angle": 6.13,
        "ge_threshold": 0.005,            # omitted from map (length-scaled)
        "readout_fidelity": 97.8,         # diagnostic, not in map
        "success": True,                  # bool
        "confusion_matrix": [[0.98, 0.02], [0.03, 0.97]],  # non-numeric
    }})
    out = resolve_fit_targets(run)
    assert set(out["qA1"]) == {"optimal_amplitude", "iw_angle"}
    amp = out["qA1"]["optimal_amplitude"]
    assert amp["path"] == "qubits.qA1.resonator.operations.readout.amplitude"
    assert amp["value"] == 0.0197 and amp["label"] == "Readout amplitude"


def test_resolve_substitutes_qubit_and_passes_value():
    run = _Run("1Q_08_qubit_spectroscopy_new", {"qA3": {"frequency": 5.07e9}})
    out = resolve_fit_targets(run)
    assert out["qA3"]["frequency"]["path"] == "qubits.qA3.f_01"
    assert out["qA3"]["frequency"]["value"] == 5.07e9


def test_resolve_x180_amp_path_uses_operation_alias():
    """opt_amp / x180_amp target operations.x180.* — resolved through the #./ alias
    at write time (matches the power_rabi/drag clickable paths)."""
    run = _Run("1Q_11_power_rabi", {"qA1": {"opt_amp": 0.318, "operation": "x180", "success": True}})
    out = resolve_fit_targets(run)
    assert out["qA1"]["opt_amp"]["path"] == "qubits.qA1.xy.operations.x180.amplitude"
    assert set(out["qA1"]) == {"opt_amp"}  # operation(str) + success(bool) skipped


def test_resolve_skips_nan_str_bool_none():
    run = _Run("1Q_11_power_rabi", {"qA1": {
        "opt_amp": float("nan"), "operation": "x180", "success": True,
        "best_readout_frequency": None,
    }})
    assert resolve_fit_targets(run) == {}


def test_resolve_unmapped_experiment_is_empty():
    run = _Run("2Q_24_Bell_State_Tomography", {"qA2-qA1": {"fidelity": 0.9, "purity": 0.8}})
    assert resolve_fit_targets(run) == {}


def test_resolve_15a_and_15b_do_not_collide():
    a = _Run("1Q_15a_readout_frequency_optimization", {"qA1": {"optimal_frequency": 7.1e9}})
    b = _Run("1Q_15b_readout_power_optimization", {"qA1": {"optimal_amplitude": 0.02}})
    assert resolve_fit_targets(a)["qA1"]["optimal_frequency"]["path"] == "qubits.qA1.resonator.f_01"
    assert "optimal_amplitude" in resolve_fit_targets(b)["qA1"]


def test_resolve_handles_missing_attrs_gracefully():
    assert resolve_fit_targets(_Run("", {})) == {}
    assert resolve_fit_targets(_Run("1Q_08_qubit_spectroscopy", {"qA1": "notadict"})) == {}


def test_resolve_accepts_dict_run():
    """DatasetStore.get_run returns a dict, not a RunInfo — must work too."""
    run = {"experiment_name": "1Q_15b_readout_power_optimization",
           "fit_results": {"qA1": {"optimal_amplitude": 0.02}}}
    out = resolve_fit_targets(run)
    assert out["qA1"]["optimal_amplitude"]["path"] == "qubits.qA1.resonator.operations.readout.amplitude"


def test_all_map_entries_are_well_formed():
    for prefix, spec in FIT_TARGET_MAP.items():
        assert isinstance(spec, dict) and spec, prefix
        for key, entry in spec.items():
            assert {"path", "scale", "label"} <= set(entry), f"{prefix}.{key}"
            path = entry["path"]
            assert "{q}" in path or "{pair}" in path, f"{prefix}.{key}: no placeholder"
            assert path.startswith(("qubits.", "qubit_pairs.")), path
            assert isinstance(entry["scale"], (int, float)) and not isinstance(entry["scale"], bool)
            stripped = path.replace("{q}", "").replace("{pair}", "").replace("{operation}", "")
            assert "{" not in stripped and "}" not in stripped, f"{prefix}.{key}: stray braces"


def test_resolve_cz_amp_is_operation_aware():
    """2Q CZ optimal_amplitude targets the macro the run actually calibrated,
    pulled from run.parameters['operation'] — not the default macros.cz alias."""
    run = _Run("2Q_20b_cz_conditional_phase_error_amp",
               {"qA2-qA1": {"optimal_amplitude": 0.209, "success": True}},
               parameters={"operation": "cz_flattop"})
    out = resolve_fit_targets(run)
    assert set(out["qA2-qA1"]) == {"optimal_amplitude"}  # success(bool) skipped
    tgt = out["qA2-qA1"]["optimal_amplitude"]
    assert tgt["path"] == "qubit_pairs.qA2-qA1.macros.cz_flattop.flux_pulse_qubit.amplitude"
    assert tgt["value"] == 0.209 and tgt["label"] == "CZ flux-pulse amplitude"


def test_resolve_cz_amp_skipped_without_operation():
    """No operation recorded → no Apply target (don't guess the macro)."""
    run = _Run("2Q_20b_cz_conditional_phase_error_amp",
               {"qA2-qA1": {"optimal_amplitude": 0.209}}, parameters={})
    assert resolve_fit_targets(run) == {}


def test_resolve_cz_amp_dict_run_with_parameters():
    """get_run hands resolve_fit_targets a dict — {operation} fill must work there too."""
    run = {"experiment_name": "2Q_20b_cz_conditional_phase_error_amp",
           "parameters": {"operation": "cz_unipolar"},
           "fit_results": {"qA2-qA1": {"optimal_amplitude": 0.18}}}
    out = resolve_fit_targets(run)
    assert out["qA2-qA1"]["optimal_amplitude"]["path"] == \
        "qubit_pairs.qA2-qA1.macros.cz_unipolar.flux_pulse_qubit.amplitude"
