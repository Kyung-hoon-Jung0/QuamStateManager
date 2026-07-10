"""Tests for quam_state_manager.core.config_view.

Pure-function tests against a synthetic QM config dict that mirrors the
shape ``QuamRoot.generate_config()`` would produce. No subprocess, no QM
stack required.
"""

from __future__ import annotations

import pytest

from quam_state_manager.core import config_view


@pytest.fixture
def synth_config():
    """A 2-element QM config covering both waveform kinds."""
    return {
        "version": 1,
        "controllers": {"con1": {"analog_outputs": {"1": {"offset": 0.0}}}},
        "elements": {
            "qA1.xy": {
                "mixInputs": {"I": ("con1", 1), "Q": ("con1", 2), "mixer": "mixer_qA1_xy"},
                "intermediate_frequency": 100e6,
                "operations": {
                    "x180_DragCosine": "x180_DragCosine.pulse",
                    "x90_DragCosine": "x90_DragCosine.pulse",
                },
            },
            "qA1.z": {
                "singleInput": {"port": ("con1", 5)},
                "operations": {
                    "const_z": "const_z.pulse",
                },
            },
            "qA2.xy": {
                "mixInputs": {"I": ("con1", 3), "Q": ("con1", 4), "mixer": "mixer_qA2_xy"},
                "operations": {"x180_DragCosine": "x180_qA2.pulse"},
            },
        },
        "pulses": {
            "x180_DragCosine.pulse": {
                "operation": "control",
                "length": 40,
                "waveforms": {"I": "wf_qA1_x180_I", "Q": "wf_qA1_x180_Q"},
            },
            "x90_DragCosine.pulse": {
                "operation": "control",
                "length": 40,
                "waveforms": {"I": "wf_qA1_x90_I", "Q": "wf_qA1_x90_Q"},
            },
            "const_z.pulse": {
                "operation": "control",
                "length": 100,
                "waveforms": {"single": "wf_qA1_const_z"},
            },
            "x180_qA2.pulse": {
                "operation": "control",
                "length": 40,
                "waveforms": {"I": "wf_qA2_x180_I", "Q": "wf_qA2_x180_Q"},
            },
        },
        "waveforms": {
            "wf_qA1_x180_I": {"type": "arbitrary", "samples": [0.0, 0.05, 0.1, 0.05, 0.0]},
            "wf_qA1_x180_Q": {"type": "arbitrary", "samples": [0.0, 0.01, 0.02, 0.01, 0.0]},
            "wf_qA1_x90_I": {"type": "arbitrary", "samples": [0.0, 0.025, 0.05, 0.025, 0.0]},
            "wf_qA1_x90_Q": {"type": "arbitrary", "samples": [0.0, 0.005, 0.01, 0.005, 0.0]},
            "wf_qA1_const_z": {"type": "constant", "sample": 0.1},
            "wf_qA2_x180_I": {"type": "arbitrary", "samples": [0.0, 0.06, 0.12]},
            "wf_qA2_x180_Q": {"type": "arbitrary", "samples": [0.0, 0.012, 0.024]},
        },
        "integration_weights": {},
        "mixers": {
            "mixer_qA1_xy": [{"intermediate_frequency": 100e6, "lo_frequency": 5e9}],
            "mixer_qA2_xy": [{"intermediate_frequency": 100e6, "lo_frequency": 5e9}],
        },
    }


# ---------------------------------------------------------------------------
# top_level_keys
# ---------------------------------------------------------------------------


class TestTopLevelKeys:
    def test_ordering(self, synth_config):
        keys = config_view.top_level_keys(synth_config)
        # version first, then controllers, then elements
        assert keys[0] == "version"
        assert keys[1] == "controllers"
        assert keys[2] == "elements"
        assert "pulses" in keys
        assert "waveforms" in keys

    def test_unknown_keys_at_end(self):
        cfg = {"version": 1, "elements": {}, "custom_key": "x"}
        keys = config_view.top_level_keys(cfg)
        assert keys[-1] == "custom_key"
        assert keys[0] == "version"


# ---------------------------------------------------------------------------
# slice_for
# ---------------------------------------------------------------------------


class TestSliceFor:
    def test_qubit_slice_isolates_one_qubit(self, synth_config):
        sl = config_view.slice_for(synth_config, "qA1")
        assert set(sl["elements"].keys()) == {"qA1.xy", "qA1.z"}
        # No qA2 leaks
        assert all(k.startswith("qA1.") for k in sl["elements"])
        # Pulses follow elements
        assert "x180_DragCosine.pulse" in sl["pulses"]
        assert "const_z.pulse" in sl["pulses"]
        assert "x180_qA2.pulse" not in sl["pulses"]
        # Waveforms follow pulses
        assert "wf_qA1_x180_I" in sl["waveforms"]
        assert "wf_qA1_const_z" in sl["waveforms"]
        assert "wf_qA2_x180_I" not in sl["waveforms"]

    def test_qubit_slice_includes_mixers(self, synth_config):
        sl = config_view.slice_for(synth_config, "qA1")
        assert "mixer_qA1_xy" in sl["mixers"]
        assert "mixer_qA2_xy" not in sl["mixers"]

    def test_unknown_prefix_returns_empty_slice(self, synth_config):
        sl = config_view.slice_for(synth_config, "qNOPE")
        assert sl == {
            "elements": {}, "pulses": {}, "waveforms": {},
            "integration_weights": {}, "mixers": {},
        }


# ---------------------------------------------------------------------------
# resolve_waveform
# ---------------------------------------------------------------------------


class TestResolveWaveform:
    def test_arbitrary_passthrough(self, synth_config):
        out = config_view.resolve_waveform(synth_config, "wf_qA1_x180_I")
        assert out["kind"] == "arbitrary"
        assert out["y"] == [0.0, 0.05, 0.1, 0.05, 0.0]
        assert out["x"] == [0, 1, 2, 3, 4]
        assert out["length_ns"] == 5
        assert out["constant_value"] is None
        assert out["length_inferred"] is False

    def test_constant_expansion(self, synth_config):
        out = config_view.resolve_waveform(synth_config, "wf_qA1_const_z")
        assert out["kind"] == "constant"
        assert out["constant_value"] == 0.1
        # Length comes from the pulse that uses it (100 ns)
        assert out["length_ns"] == 100
        assert len(out["y"]) == 100
        assert all(y == 0.1 for y in out["y"])
        assert out["length_inferred"] is False

    def test_unknown_waveform_safe(self, synth_config):
        out = config_view.resolve_waveform(synth_config, "nonexistent")
        assert out["kind"] == "unknown"
        # No referencing pulse → 16-sample placeholder, honestly flagged.
        assert out["length_ns"] == 16
        assert out["length_inferred"] is True


# ---------------------------------------------------------------------------
# waveform_for_operation + operations_for
# ---------------------------------------------------------------------------


class TestWaveformForOperation:
    def test_iq_pulse_returns_I_then_Q_traces(self, synth_config):
        out = config_view.waveform_for_operation(synth_config, "qA1", "x180_DragCosine")
        assert out is not None
        assert out["element"] == "qA1.xy"
        assert out["operation"] == "x180_DragCosine"
        assert out["pulse"] == "x180_DragCosine.pulse"
        assert [t["label"] for t in out["traces"]] == ["I", "Q"]
        assert out["traces"][0]["name"] == "wf_qA1_x180_I"
        assert out["traces"][1]["name"] == "wf_qA1_x180_Q"
        assert all(t["length_inferred"] is False for t in out["traces"])

    def test_finds_single_pulse(self, synth_config):
        out = config_view.waveform_for_operation(synth_config, "qA1", "const_z")
        assert out is not None
        assert len(out["traces"]) == 1
        trace = out["traces"][0]
        assert trace["label"] == "single"
        assert trace["name"] == "wf_qA1_const_z"
        assert trace["kind"] == "constant"

    def test_plain_string_waveforms_one_trace(self):
        config = {
            "elements": {"qX.z": {"operations": {"bias": "bias.pulse"}}},
            "pulses": {"bias.pulse": {"length": 24, "waveforms": "wf_bias"}},
            "waveforms": {"wf_bias": {"type": "constant", "sample": 0.2}},
        }
        out = config_view.waveform_for_operation(config, "qX", "bias")
        assert out is not None
        assert [t["label"] for t in out["traces"]] == ["single"]
        assert out["traces"][0]["length_ns"] == 24
        assert out["traces"][0]["length_inferred"] is False

    def test_length_inferred_true_when_pulse_has_no_length(self):
        config = {
            "elements": {"qX.z": {"operations": {"bias": "bias.pulse"}}},
            "pulses": {"bias.pulse": {"waveforms": {"single": "wf_bias"}}},
            "waveforms": {"wf_bias": {"type": "constant", "sample": 0.2}},
        }
        out = config_view.waveform_for_operation(config, "qX", "bias")
        assert out is not None
        trace = out["traces"][0]
        assert trace["length_ns"] == 16
        assert trace["length_inferred"] is True

    def test_unknown_op_returns_none(self, synth_config):
        assert config_view.waveform_for_operation(synth_config, "qA1", "nope") is None

    def test_unknown_qubit_returns_none(self, synth_config):
        assert config_view.waveform_for_operation(synth_config, "qNOPE", "x180_DragCosine") is None

    def test_channel_filter(self, synth_config):
        # Restrict to xy only
        out = config_view.waveform_for_operation(
            synth_config, "qA1", "x180_DragCosine", channel="xy",
        )
        assert out is not None
        # Restrict to z (op doesn't exist there)
        assert (
            config_view.waveform_for_operation(
                synth_config, "qA1", "x180_DragCosine", channel="z",
            )
            is None
        )


class TestOperationsFor:
    def test_lists_all_qubit_operations(self, synth_config):
        ops = config_view.operations_for(synth_config, "qA1")
        op_names = sorted(o["op_name"] for o in ops)
        assert op_names == ["const_z", "x180_DragCosine", "x90_DragCosine"]

    def test_channel_extracted(self, synth_config):
        ops = config_view.operations_for(synth_config, "qA1")
        by_op = {o["op_name"]: o["channel"] for o in ops}
        assert by_op["x180_DragCosine"] == "xy"
        assert by_op["const_z"] == "z"

    def test_pulse_name_attached(self, synth_config):
        ops = config_view.operations_for(synth_config, "qA1")
        by_op = {o["op_name"]: o["pulse"] for o in ops}
        assert by_op["x180_DragCosine"] == "x180_DragCosine.pulse"


@pytest.fixture
def pair_config():
    """A config covering all three 2Q-gate naming families + a distractor pair.

    Mirrors real generate_config() output: dedicated cr_/coupler_ elements,
    flux-tunable CZ ops on the control qubit's z line, and a legacy
    ``<pair>.coupler`` element.
    """
    return {
        "elements": {
            # CR: dedicated elements, both directions, with the CR drive also
            # riding the control qubit's xy (referencing the partner).
            "cr_q0_q4": {"operations": {"square": "cr_sq_04.pulse"}},
            "cr_q4_q0": {"operations": {"square": "cr_sq_40.pulse"}},
            "q0.xy": {"operations": {"x180": "x180_q0.pulse",
                                      "cr_square_q4": "cr_drive_04.pulse"}},
            # coupler: dedicated element.
            "coupler_q1_q2": {"operations": {"const": "coupler_const.pulse"}},
            # flux-tunable CZ: ops on the control's z, one per partner.
            "q5.z": {"operations": {"cz_unipolar_pulse_q6": "cz_56.pulse",
                                     "cz_unipolar_pulse_q7": "cz_57.pulse"}},
            # legacy naming.
            "qA1-A2.coupler": {"operations": {"cz_pulse": "legacy_cz.pulse"}},
        },
        "pulses": {
            "cr_sq_04.pulse": {"length": 100, "waveforms": {"I": "wf_i", "Q": "wf_q"}},
            "cr_sq_40.pulse": {"length": 100, "waveforms": {"I": "wf_i", "Q": "wf_q"}},
            "coupler_const.pulse": {"length": 80, "waveforms": {"single": "wf_c"}},
            "cz_56.pulse": {"length": 48, "waveforms": {"single": "wf_cz"}},
            "cz_57.pulse": {"length": 48, "waveforms": {"single": "wf_cz"}},
            "legacy_cz.pulse": {"length": 120, "waveforms": {"single": "wf_c"}},
            "cr_drive_04.pulse": {"length": 100, "waveforms": {"single": "wf_c"}},
            "x180_q0.pulse": {"length": 40, "waveforms": {"I": "wf_i", "Q": "wf_q"}},
        },
        "waveforms": {
            "wf_i": {"type": "arbitrary", "samples": [0.0, 0.1, 0.0]},
            "wf_q": {"type": "arbitrary", "samples": [0.0, 0.02, 0.0]},
            "wf_c": {"type": "constant", "sample": 0.05},
            "wf_cz": {"type": "constant", "sample": 0.03},
        },
        "integration_weights": {},
    }


class TestPairResolution:
    def test_cross_resonance_dedicated_both_directions(self, pair_config):
        sl = config_view.pair_slice_for(pair_config, "q0", "q4")
        assert set(sl["elements"]) == {"cr_q0_q4", "cr_q4_q0"}
        ops = config_view.pair_operations_for(pair_config, "q0", "q4")
        # both dedicated CR squares + the CR drive on q0.xy referencing q4
        assert ("q0.xy", "cr_square_q4") in {(o["element"], o["op_name"]) for o in ops}
        assert sl["operation_count"] >= 3

    def test_coupler_dedicated_element(self, pair_config):
        sl = config_view.pair_slice_for(pair_config, "q1", "q2")
        assert set(sl["elements"]) == {"coupler_q1_q2"}
        assert sl["operation_count"] == 1

    def test_flux_cz_ops_on_control_z_disambiguated(self, pair_config):
        # q5 pairs with both q6 and q7 — q5-q6 must not leak the q7 op.
        ops = config_view.pair_operations_for(pair_config, "q5", "q6")
        names = {o["op_name"] for o in ops}
        assert names == {"cz_unipolar_pulse_q6"}
        assert "cz_unipolar_pulse_q7" not in names

    def test_legacy_pair_prefix_element(self, pair_config):
        sl = config_view.pair_slice_for(pair_config, "qA1", "qA2", "qA1-A2")
        assert "qA1-A2.coupler" in sl["elements"]
        assert sl["operation_count"] == 1

    def test_empty_pair_reports_zero(self, pair_config):
        sl = config_view.pair_slice_for(pair_config, "q8", "q9")
        assert sl["operation_count"] == 0
        assert sl["elements"] == {}

    def test_waveform_element_disambiguates_shared_op_name(self, pair_config):
        # "square" exists on both cr_q0_q4 and cr_q4_q0 — element selects which.
        wf = config_view.pair_waveform_for_operation(
            pair_config, "q0", "q4", "square", element="cr_q4_q0")
        assert wf is not None
        assert wf["element"] == "cr_q4_q0"
        assert [t["label"] for t in wf["traces"]] == ["I", "Q"]

    def test_waveform_flux_cz_resolves(self, pair_config):
        wf = config_view.pair_waveform_for_operation(
            pair_config, "q5", "q6", "cz_unipolar_pulse_q6", element="q5.z")
        assert wf is not None
        assert wf["traces"][0]["constant_value"] == 0.03
        assert len(wf["traces"][0]["y"]) == 48
