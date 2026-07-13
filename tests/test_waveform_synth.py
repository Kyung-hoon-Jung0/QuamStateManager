"""Unit tests for core/waveform_synth.py — the graceful payload layer and the
store-aware synthesis (pointer resolution, aliases, gate slots).

Formula correctness is pinned by tests/test_waveform_golden.py against the
real quam library; these tests cover what the golden suite can't: error
shaping, pointer handling, overrides, caps, and decimation.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest

from quam_state_manager.core.loader import QuamStore
from quam_state_manager.core.waveform_synth import (
    MAX_SAMPLES,
    decimate_minmax,
    synth_for_operation,
    synthesize,
)

_QC = "quam.components.pulses."


# ---------------------------------------------------------------------------
# synthesize() payload layer
# ---------------------------------------------------------------------------

class TestSynthesizePayload:
    def test_square_constant_expanded(self):
        p = synthesize("SquarePulse", {"length": 10, "amplitude": 0.25})
        assert p["ok"] and p["kind"] == "constant" and not p["iq"]
        assert p["length"] == 10 and len(p["i"]) == 10
        assert all(v == 0.25 for v in p["i"])
        assert p["q"] is None and p["constant_value"] == 0.25

    def test_square_axis_angle_none_vs_zero(self):
        # None → real; 0.0 → complex with zero Q (quam semantics)
        real = synthesize("SquarePulse", {"length": 4, "amplitude": 0.1})
        cplx = synthesize("SquarePulse", {"length": 4, "amplitude": 0.1,
                                          "axis_angle": 0.0})
        assert not real["iq"]
        assert cplx["iq"] and all(v == 0.0 for v in cplx["q"])
        assert cplx["constant_value"] == {"real": 0.1, "imag": 0.0}

    def test_gaussian_subtracted_endpoints(self):
        p = synthesize("GaussianPulse", {"length": 40, "amplitude": 0.1,
                                         "sigma": 8.0})
        assert p["ok"]
        assert math.isclose(p["i"][0], 0.0, abs_tol=1e-15)
        assert math.isclose(p["i"][-1], 0.0, abs_tol=1e-15)

    def test_drag_iq_components(self):
        p = synthesize("DragCosinePulse", {
            "length": 48, "axis_angle": 0.0, "amplitude": 0.3,
            "alpha": -0.34, "anharmonicity": -200e6})
        assert p["ok"] and p["iq"] and len(p["q"]) == 48
        # Q ∝ d(I)/dt: zero at the envelope peak (middle), extremal on slopes
        mid = len(p["q"]) // 2
        assert abs(p["q"][mid]) < max(abs(v) for v in p["q"]) / 10

    def test_string_numbers_coerced(self):
        p = synthesize("SquarePulse", {"length": "12", "amplitude": "0.5"})
        assert p["ok"] and p["length"] == 12 and p["i"][0] == 0.5

    def test_missing_required_param(self):
        # Missing required params are surfaced, never silently defaulted
        # (the legacy add-pulse default-fallback was a known gotcha).
        p = synthesize("GaussianPulse", {"length": 40, "amplitude": 0.1})
        assert not p["ok"] and "sigma" in p["param_errors"]
        p2 = synthesize("SquarePulse", {"length": 40})
        assert not p2["ok"] and "amplitude" in p2["param_errors"]

    def test_unparseable_param(self):
        p = synthesize("SquarePulse", {"length": 40, "amplitude": "abc"})
        assert not p["ok"] and "amplitude" in p2_errors(p)

    def test_unresolved_pointer_param(self):
        p = synthesize("DragCosinePulse", {
            "length": 48, "axis_angle": 0.0, "amplitude": 0.3, "alpha": -0.3,
            "anharmonicity": "#/qubits/qA1/anharmonicity"})
        assert not p["ok"] and "anharmonicity" in p["param_errors"]
        assert "pointer" in p["param_errors"]["anharmonicity"]

    def test_pointer_on_non_synth_param_is_fine(self):
        p = synthesize("SquareReadoutPulse", {
            "length": 100, "amplitude": 0.01,
            "integration_weights": "#./default_integration_weights"})
        assert p["ok"]

    def test_inferred_length(self):
        p = synthesize("SNZPulse", {"amplitude": 0.05, "flat_length": 20,
                                    "t_phi_eff": 2.0})
        assert p["ok"] and p["length"] == 24 and len(p["i"]) == 24

    def test_quam_validation_surfaces_as_error(self):
        p = synthesize("SNZPulse", {"amplitude": 0.05, "flat_length": 21})
        assert not p["ok"] and "even" in p["error"]

    def test_max_samples_cap(self):
        p = synthesize("SquarePulse", {"length": MAX_SAMPLES + 1,
                                       "amplitude": 0.1})
        assert not p["ok"] and "cap" in p["error"]

    def test_long_but_allowed(self):
        p = synthesize("SquarePulse", {"length": 20000, "amplitude": 0.004})
        assert p["ok"] and len(p["i"]) == 20000

    def test_negative_length(self):
        p = synthesize("SquarePulse", {"length": -4, "amplitude": 0.1})
        assert not p["ok"]

    def test_unknown_class(self):
        p = synthesize("quam_builder.custom.WeirdPulse", {"length": 10})
        assert not p["ok"] and "WeirdPulse" in p["error"]

    def test_waveform_pulse_derived_length(self):
        p = synthesize("WaveformPulse", {"waveform_I": [0.0, 0.1, 0.2, 0.0]})
        assert p["ok"] and p["length"] == 4 and not p["iq"]
        p2 = synthesize("WaveformPulse", {"waveform_I": [0.0, 0.1],
                                          "waveform_Q": [0.1, 0.0]})
        assert p2["ok"] and p2["iq"]

    def test_never_raises_on_garbage(self):
        for params in ({}, {"length": None}, {"length": {}},
                       {"length": [1, 2]}, {"amplitude": object()}):
            payload = synthesize("SquarePulse", params)
            assert payload["ok"] is False

    def test_unmodeled_fields_warn_but_never_flip_ok(self):
        p = synthesize("newstack.pulses.SquarePulse",
                       {"length": 10, "amplitude": 0.1, "brand_new_knob": 3})
        assert p["ok"] is True
        assert p["class_match"] == "leaf"
        assert p["unmodeled_fields"] == ["brand_new_knob"]
        assert any("brand_new_knob" in w for w in p["warnings"])

    def test_implicit_gate_slot_suppresses_unmodeled(self):
        # The app's own cz_flattop template writes flat_length/smoothing_length
        # with NO __class__; the guessed SquarePulse spec models neither. A
        # guess is not a class claim — the caution must stay silent, or every
        # flattop flux slot on every chip cries wolf.
        body = {"amplitude": 0.209, "flat_length": 48,
                "smoothing_length": 8, "length": 68}
        p = synthesize("SquarePulse", body, class_match="implicit")
        assert p["ok"] and p["class_match"] == "implicit"
        assert p["unmodeled_fields"] == [] and p["warnings"] == []
        # ...and the same body under an actual class CLAIM does flag them
        # (pins that the suppression above is the implicit gate, not a hole).
        claimed = synthesize("SquarePulse", body)
        assert claimed["unmodeled_fields"] == ["flat_length", "smoothing_length"]

    def test_unmodeled_survives_error_payloads(self):
        # Likeliest churn case: leaf match + missing required param. The
        # error payload must still carry the unmodeled/provenance signals.
        p = synthesize("newstack.pulses.GaussianPulse",
                       {"length": 40, "amplitude": 0.1, "new_knob": 1})
        assert not p["ok"] and "sigma" in p["param_errors"]
        assert p["class_match"] == "leaf"
        assert p["unmodeled_fields"] == ["new_knob"]
        assert any("new_knob" in w for w in p["warnings"])


def p2_errors(payload):
    errs = dict(payload["param_errors"])
    if payload["error"]:
        errs.setdefault(payload["error"].split(":")[0], payload["error"])
    return errs


# ---------------------------------------------------------------------------
# synth_for_operation() — store-aware
# ---------------------------------------------------------------------------

def _make_state():
    return {
        "qubits": {
            "qA1": {
                "anharmonicity": -200e6,
                "xy": {
                    "operations": {
                        "x180_DragCosine": {
                            "length": 48, "axis_angle": 0, "amplitude": 0.319,
                            "alpha": -0.34,
                            "anharmonicity": "#/qubits/qA1/anharmonicity",
                            "detuning": 0,
                            "__class__": _QC + "DragCosinePulse",
                        },
                        "x90_DragCosine": {
                            "length": "#../x180_DragCosine/length",
                            "axis_angle": 0, "amplitude": 0.159,
                            "alpha": "#../x180_DragCosine/alpha",
                            "anharmonicity": "#../x180_DragCosine/anharmonicity",
                            "__class__": _QC + "DragCosinePulse",
                        },
                        "x180": "#./x180_DragCosine",
                        "saturation": {
                            "length": 20000, "amplitude": 0.004,
                            "__class__": _QC + "SquarePulse",
                        },
                        "mystery": {
                            "length": 10, "amplitude": 0.1,
                            "__class__": "quam_builder.custom.WeirdPulse",
                        },
                        "dangling_alias": "#./does_not_exist",
                    },
                },
                "z": {
                    "operations": {
                        "snz": {
                            "length": "#./inferred_length",
                            "amplitude": 0.05, "flat_length": 20,
                            "t_phi_eff": 2.0, "padding": 0,
                            "__class__": _QC + "SNZPulse",
                        },
                    },
                },
                "resonator": {
                    "operations": {
                        "readout": {
                            "length": 1024, "amplitude": 0.01,
                            "integration_weights": "#./default_integration_weights",
                            "integration_weights_angle": 0.0,
                            "threshold": 0.002,
                            "__class__": _QC + "SquareReadoutPulse",
                        },
                    },
                },
            },
        },
        "qubit_pairs": {
            "qA1-qA2": {
                "macros": {
                    "cz_unipolar": {
                        "flux_pulse_qubit": {"amplitude": 0.05, "length": 100},
                        "coupler_flux_pulse": None,
                        "phase_shift_control": 0.0,
                        "phase_shift_target": 0.0,
                    },
                    "cz": "#./cz_unipolar",
                },
            },
        },
    }


def _make_wiring():
    return {
        "wiring": {"qubits": {"qA1": {"xy": {"opx_output": "MW-FEM/1/2"}}}},
        "network": {"host": "10.1.1.1", "cluster_name": "test"},
    }


@pytest.fixture
def store(tmp_path: Path) -> QuamStore:
    (tmp_path / "state.json").write_text(json.dumps(_make_state()),
                                         encoding="utf-8")
    (tmp_path / "wiring.json").write_text(json.dumps(_make_wiring()),
                                          encoding="utf-8")
    return QuamStore(tmp_path)


OPS = "qubits.qA1.xy.operations"


class TestSynthForOperation:
    def test_absolute_pointer_param_resolved(self, store):
        p = synth_for_operation(store, f"{OPS}.x180_DragCosine")
        assert p["ok"], p["error"]
        assert p["iq"] and p["length"] == 48
        assert p["pointer_fields"]["anharmonicity"]["resolved"]
        assert p["resolved_params"]["anharmonicity"] == -200e6

    def test_chained_relative_pointers(self, store):
        # x90.anharmonicity → #../x180/anharmonicity → #/qubits/qA1/anharmonicity
        p = synth_for_operation(store, f"{OPS}.x90_DragCosine")
        assert p["ok"], p["error"]
        assert p["resolved_params"]["anharmonicity"] == -200e6
        assert p["resolved_params"]["length"] == 48
        assert p["resolved_params"]["alpha"] == -0.34

    def test_alias_followed(self, store):
        p = synth_for_operation(store, f"{OPS}.x180")
        assert p["ok"], p["error"]
        assert p["alias_of"] == f"{OPS}.x180_DragCosine"
        assert p["length"] == 48

    def test_dangling_alias(self, store):
        p = synth_for_operation(store, f"{OPS}.dangling_alias")
        assert not p["ok"] and "unresolvable" in p["error"]

    def test_readout_dangling_runtime_pointer_ok(self, store):
        # integration_weights → #./default_integration_weights is a quam
        # runtime property: unresolvable by design, irrelevant to the shape.
        p = synth_for_operation(store, "qubits.qA1.resonator.operations.readout")
        assert p["ok"], p["error"]
        assert not p["pointer_fields"]["integration_weights"]["resolved"]
        assert p["length"] == 1024

    def test_inferred_length_self_ref(self, store):
        p = synth_for_operation(store, "qubits.qA1.z.operations.snz")
        assert p["ok"], p["error"]
        assert p["length"] == 24  # ceil((0+20+2+2)/4)*4

    def test_gate_flux_slot_implicit_square(self, store):
        p = synth_for_operation(
            store, "qubit_pairs.qA1-qA2.macros.cz_unipolar.flux_pulse_qubit")
        assert p["ok"], p["error"]
        assert p["kind"] == "constant" and p["length"] == 100

    def test_none_coupler_slot(self, store):
        p = synth_for_operation(
            store, "qubit_pairs.qA1-qA2.macros.cz_unipolar.coupler_flux_pulse")
        assert not p["ok"] and "not a pulse dict" in p["error"]

    def test_unknown_class_degrades(self, store):
        p = synth_for_operation(store, f"{OPS}.mystery")
        assert not p["ok"] and "WeirdPulse" in p["error"]
        assert p["resolved_params"]["amplitude"] == 0.1  # raw fields kept

    def test_missing_operation(self, store):
        p = synth_for_operation(store, f"{OPS}.nope")
        assert not p["ok"] and "not found" in p["error"]

    def test_overrides_substitute_uncommitted_values(self, store):
        base = synth_for_operation(store, f"{OPS}.saturation")
        ovr = synth_for_operation(store, f"{OPS}.saturation",
                                  overrides={"amplitude": 0.5, "length": 8})
        assert base["i"][0] == 0.004
        assert ovr["ok"] and ovr["length"] == 8 and ovr["i"][0] == 0.5

    def test_does_not_mutate_store(self, store):
        before = json.dumps(store.merged["qubits"]["qA1"]["xy"], sort_keys=True)
        synth_for_operation(store, f"{OPS}.x90_DragCosine",
                            overrides={"amplitude": 9.9})
        after = json.dumps(store.merged["qubits"]["qA1"]["xy"], sort_keys=True)
        assert before == after
        assert not store.change_log


# ---------------------------------------------------------------------------
# decimate_minmax
# ---------------------------------------------------------------------------

class TestDecimate:
    def test_short_input_untouched(self):
        xs, ys, dec = decimate_minmax([1.0, 2.0, 3.0], 10)
        assert not dec and ys == [1.0, 2.0, 3.0] and xs == [0, 1, 2]

    def test_preserves_spikes(self):
        values = [0.0] * 10_000
        values[5_000] = 1.0     # a single B-sample-like spike
        values[7_777] = -1.0
        xs, ys, dec = decimate_minmax(values, 200)
        assert dec and len(ys) <= 201
        assert 1.0 in ys and -1.0 in ys

    def test_monotone_x(self):
        values = list(np.sin(np.linspace(0, 30, 5000)))
        xs, _, _ = decimate_minmax(values, 100)
        assert xs == sorted(xs)
