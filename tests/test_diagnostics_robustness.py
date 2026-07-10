"""Robustness regressions from the value-spec red-team: a lint must NEVER raise
on malformed/partial input, and the waveform check must not over-report
invalid-waveform for shapes generate_config() actually accepts (NaN-emitting
Blackman len<2, too-long-for-preview pulses, and unrecognized custom classes).
"""

from __future__ import annotations

from quam_state_manager.core import diagnostics
from quam_state_manager.core.loader import QuamStore

SQUARE = "quam.components.pulses.SquarePulse"
DRAGCOS = "quam.components.pulses.DragCosinePulse"
BLACKMAN = "quam.components.pulses.BlackmanIntegralPulse"
DRAGGAUSS = "quam.components.pulses.DragGaussianPulse"
MWFEM_OUT = "quam.components.ports.mw_outputs.MWFEMAnalogOutputPort"


def _lint(state, wiring=None):
    """lint_state on a from_dicts store — returns findings, must not raise."""
    return diagnostics.lint_state(QuamStore.from_dicts(state, wiring or {"wiring": {}}))


# ---------------------------------------------------------------------------
# Crashes — a malformed chip must lint, not 500
# ---------------------------------------------------------------------------

class TestLintNeverRaises:
    def test_negative_index_pointer_segment(self):
        # a hand-edited pointer with a negative list index must not IndexError
        state = {
            "qubits": {"q0": {"id": "q0", "resonator": {
                "RF_frequency": 7.0e9,
                "opx_output": "#/wiring/qubits/q0/rr/opx_output", "operations": {}}}},
            "qubit_pairs": {}, "somelist": [1, 2], "ports": {},
        }
        wiring = {"wiring": {"qubits": {"q0": {"rr": {"opx_output": "#/somelist/-9"}}}}}
        out = _lint(state, wiring)
        assert not [f for f in out if f.category == "value_spec_if_floor"]

    def test_truthy_nondict_wiring_entry(self):
        state = {"qubits": {"q0": {"id": "q0", "xy": {"operations": {
            "x180": {"__class__": SQUARE, "amplitude": 0.1, "length": 40}}}}},
            "qubit_pairs": {}, "ports": {}}
        wiring = {"wiring": {"qubits": {"q0": "broken"}}}   # truthy non-dict
        _lint(state, wiring)   # must not raise

    def test_truthy_nondict_pair_wiring_entry(self):
        state = {"qubits": {}, "qubit_pairs": {"p0": {"macros": {"cz": {
            "coupler_flux_pulse": {"__class__": SQUARE, "amplitude": 0.1, "length": 40}}}}},
            "ports": {}}
        wiring = {"wiring": {"qubit_pairs": {"p0": "broken"}}}
        _lint(state, wiring)   # must not raise

    def test_nonstring_class_on_pulse(self):
        state = {"qubits": {"q0": {"id": "q0", "xy": {"operations": {
            "x180": {"__class__": 12345, "amplitude": 0.1, "length": 40}}}}},
            "qubit_pairs": {}, "ports": {}}
        out = _lint(state)   # must not raise (AttributeError on .endswith)
        # a non-string class is unrecognized, not "invalid" → no waveform finding
        assert not [f for f in out if f.category.startswith("waveform")]

    def test_list_valued_integration_weights_in_lint_config(self):
        # regression: the old lint_config key-iterated this; the new pass must not
        # call .items() on a list
        cfg = {"elements": {}, "pulses": {}, "waveforms": {},
               "integration_weights": ["w1", "w2"], "mixers": {}, "controllers": {}}
        diagnostics.lint_config(cfg)   # must not raise

    def test_nondict_config_sections(self):
        for bad in ({"mixers": []}, {"elements": "x"}, {"integration_weights": 3}):
            diagnostics.lint_config(bad)   # must not raise


# ---------------------------------------------------------------------------
# Waveform check must not over-report invalid-waveform
# ---------------------------------------------------------------------------

def _pulse_store(op):
    state = {"qubits": {"q0": {"id": "q0", "xy": {"operations": {"p": op}}}},
             "qubit_pairs": {}, "ports": {}}
    return QuamStore.from_dicts(state, {"wiring": {"qubits": {}}})


def _invalid(store):
    return [f for f in diagnostics.lint_state(store) if f.category == "waveform_invalid"]


class TestWaveformInvalidScope:
    def test_blackman_len1_emits_nan_not_invalid(self):
        # generate_config() emits NaN (does NOT raise) for a len<2 Blackman → no finding
        op = {"__class__": BLACKMAN, "length": 1, "v_start": 0.0, "v_end": 0.1}
        assert _invalid(_pulse_store(op)) == []

    def test_dragcosine_len1_still_invalid(self):
        # generate_config() genuinely raises (ZeroDivision) for a len<2 DragCosine
        op = {"__class__": DRAGCOS, "length": 1, "amplitude": 0.1,
              "alpha": 0.5, "anharmonicity": -2e8, "axis_angle": 0.0}
        assert len(_invalid(_pulse_store(op))) == 1

    def test_too_long_pulse_not_invalid(self):
        # a legal-but-huge waveform exceeds the in-process preview cap; that is not
        # a generate_config crash → must not be reported
        op = {"__class__": DRAGGAUSS, "length": 300000, "amplitude": 0.1,
              "sigma": 1000, "alpha": 0.0, "anharmonicity": -2e8, "axis_angle": 0.0}
        assert _invalid(_pulse_store(op)) == []

    def test_unrecognized_custom_class_not_invalid(self):
        op = {"__class__": "quam_builder.custom.WeirdPulse", "length": 40, "amplitude": 0.1}
        assert _invalid(_pulse_store(op)) == []
