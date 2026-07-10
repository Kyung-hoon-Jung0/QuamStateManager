"""Tests for the additional Tier-1 hardware value-specs in core/spec_constraints:
octave LO/gain, LF-FEM sampling_rate / output_mode / upsampling_mode, MW-input
lo_mode, gain_db dispatch-on-__class__, and the pulse-length upper bound.

Every check is verified to (a) flag a genuinely-bad value and (b) stay silent on
None / pointer / absent / in-range values — the no-false-positive contract.
"""

from __future__ import annotations

from quam_state_manager.core import spec_constraints as sc

LF_OUT = "quam.components.ports.analog_outputs.LFFEMAnalogOutputPort"
LF_IN = "quam.components.ports.analog_inputs.LFFEMAnalogInputPort"
MW_IN = "quam.components.ports.mw_inputs.MWFEMAnalogInputPort"
OPXP_IN = "quam.components.ports.analog_inputs.OPXPlusAnalogInputPort"


def _cats(root):
    return {f["category"] for f in sc.spec_findings(root)}


# ---------------------------------------------------------------------------
# New low-level checkers
# ---------------------------------------------------------------------------

class TestNewCheckers:
    def test_in_set_float_tolerant(self):
        assert sc._check_in_set(1e9, sc.SAMPLING_RATE_VALID) is None
        assert sc._check_in_set(1000000000.0, sc.SAMPLING_RATE_VALID) is None  # examplechip9q float
        assert sc._check_in_set(2e9, sc.SAMPLING_RATE_VALID) is None
        assert sc._check_in_set(5e8, sc.SAMPLING_RATE_VALID) is not None
        assert sc._check_in_set(None, sc.SAMPLING_RATE_VALID) is None     # variantb default
        assert sc._check_in_set("#./x", sc.SAMPLING_RATE_VALID) is None

    def test_str_enum_skips_none_and_pointer(self):
        assert sc._check_str_enum("direct", sc.OUTPUT_MODE_VALID) is None
        assert sc._check_str_enum("amplified", sc.OUTPUT_MODE_VALID) is None
        assert sc._check_str_enum("bogus", sc.OUTPUT_MODE_VALID) is not None
        assert sc._check_str_enum(None, sc.OUTPUT_MODE_VALID) is None     # absent → never flag
        assert sc._check_str_enum("#./x", sc.OUTPUT_MODE_VALID) is None
        assert sc._check_str_enum(123, sc.OUTPUT_MODE_VALID) is None

    def test_range_float(self):
        assert sc._check_range_float(5e9, *sc.OCTAVE_LO_RANGE_HZ) is None
        assert sc._check_range_float(1e9, *sc.OCTAVE_LO_RANGE_HZ) is not None
        assert sc._check_range_float(19e9, *sc.OCTAVE_LO_RANGE_HZ) is not None
        assert sc._check_range_float(3.5, *sc.OCTAVE_GAIN_RANGE_DB) is None
        assert sc._check_range_float(None, *sc.OCTAVE_GAIN_RANGE_DB) is None

    def test_pulse_length_upper_bound(self):
        assert sc._check_multiple_min(20000, mult=4, minimum=16,
                                      maximum=sc.PULSE_LENGTH_MAX_NS) is None
        r = sc._check_multiple_min(sc.PULSE_LENGTH_MAX_NS + 4, mult=4, minimum=16,
                                   maximum=sc.PULSE_LENGTH_MAX_NS)
        assert r is not None and "≤" in r

    def test_gain_dispatch_on_class(self):
        assert sc._input_gain_range({"__class__": MW_IN}, "mw_inputs") == sc.GAIN_DB_MW_INPUT
        assert sc._input_gain_range({"__class__": LF_IN}, "analog_inputs") == sc.GAIN_DB_LF_INPUT
        assert sc._input_gain_range({"__class__": OPXP_IN}, "analog_inputs") == sc.GAIN_DB_OPXPLUS_INPUT
        # class-less synthetic dicts fall back to the section name (back-compat)
        assert sc._input_gain_range({}, "analog_inputs") == sc.GAIN_DB_LF_INPUT
        assert sc._input_gain_range({}, "mw_inputs") == sc.GAIN_DB_MW_INPUT


# ---------------------------------------------------------------------------
# Octave (future-proofing — dead on the MW-FEM fleet, must work when present)
# ---------------------------------------------------------------------------

class TestOctave:
    def test_lo_and_gain_out_of_range_flag(self):
        root = {"octaves": {"oct1": {
            "RF_outputs": {"1": {"LO_frequency": 1.0e9, "gain": 25}},   # LO<2e9, gain>20
            "RF_inputs": {"1": {"LO_frequency": 20e9}},                 # LO>18e9
        }}}
        cats = _cats(root)
        assert "value_spec_octave_lo" in cats
        assert "value_spec_octave_gain" in cats

    def test_valid_octave_silent(self):
        root = {"octaves": {"o": {"RF_outputs": {"1": {"LO_frequency": 5e9, "gain": 3.5}},
                                  "RF_inputs": {"1": {"LO_frequency": 7e9}}}}}
        assert sc.spec_findings(root) == []

    def test_pointer_lo_skipped(self):
        root = {"octaves": {"o": {"RF_outputs": {"1": {"LO_frequency": "#/x/y", "gain": None}}}}}
        assert sc.spec_findings(root) == []


# ---------------------------------------------------------------------------
# LF-FEM port enums + sampling_rate, MW-input lo_mode
# ---------------------------------------------------------------------------

def _ports(**leaf):
    """One analog_outputs leaf (LF-FEM) at con1/5/6 with the given fields."""
    return {"ports": {"analog_outputs": {"con1": {"5": {"6":
            {"__class__": LF_OUT, **leaf}}}}}}


class TestLfPortFields:
    def test_bad_sampling_rate_flags(self):
        assert "value_spec_sampling_rate" in _cats(_ports(sampling_rate=5e8))

    def test_good_sampling_rate_silent(self):
        assert sc.spec_findings(_ports(sampling_rate=1e9)) == []
        assert sc.spec_findings(_ports(sampling_rate=1000000000.0)) == []
        assert sc.spec_findings(_ports(sampling_rate=None)) == []   # default

    def test_bad_output_and_upsampling_mode(self):
        cats = _cats(_ports(output_mode="bogus", upsampling_mode="nope"))
        assert "value_spec_output_mode" in cats
        assert "value_spec_upsampling_mode" in cats

    def test_good_modes_silent(self):
        assert sc.spec_findings(_ports(output_mode="amplified", upsampling_mode="pulse")) == []

    def test_lo_mode_enum(self):
        bad = {"ports": {"mw_inputs": {"con1": {"1": {"1":
               {"__class__": MW_IN, "lo_mode": "weird"}}}}}}
        assert "value_spec_lo_mode" in _cats(bad)
        ok = {"ports": {"mw_inputs": {"con1": {"1": {"1":
              {"__class__": MW_IN}}}}}}   # lo_mode absent (pre-QOP-3.7) → silent
        assert sc.spec_findings(ok) == []

    def test_lffem_input_gain_via_class(self):
        # LF-FEM input gain_db below -3 floor flags; -3..29 is clean
        bad = {"ports": {"analog_inputs": {"con1": {"5": {"1":
               {"__class__": LF_IN, "gain_db": -5}}}}}}
        assert "value_spec_gain" in _cats(bad)
        ok = {"ports": {"analog_inputs": {"con1": {"5": {"1":
              {"__class__": LF_IN, "gain_db": 10}}}}}}
        assert sc.spec_findings(ok) == []
