"""Tests for core.units — the single source of truth for unit display.

Guards the µ (U+00B5) -> Μ (U+039C) mangle, the field->unit canon, the SI
ladders shared by tables/inspector/exports/plots, and the export labeling.
"""

import pytest

from quam_state_manager.core import units


# ---------------------------------------------------------------------------
# Micro-sign identity — catch an editor swapping U+00B5 for U+03BC (Greek mu)
# ---------------------------------------------------------------------------
def test_micro_sign_is_u00b5_not_greek_mu():
    assert ord(units.MICRO) == 0xB5
    assert units.MICRO != "μ"  # NOT Greek small mu


# ---------------------------------------------------------------------------
# format_metric — auto-scale ladders (legacy dataset behavior preserved)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("val,unit,expected", [
    (8e-6, "s", "8.00 µs"),          # the test_history.py:2287 guard
    (2.4356e-5, "s", "24.36 µs"),
    (1.5e-3, "s", "1.50 ms"),
    (3.0, "s", "3.00 s"),
    (4e-7, "s", "400.0 ns"),
    (5.075e9, "Hz", "5.0750 GHz"),
    (-2.124e8, "Hz", "-212.40 MHz"),
    (5.0e5, "Hz", "500.0 kHz"),
    (250.0, "Hz", "250.0 Hz"),
    (-0.213, "V", "-213.00 mV"),
    (1.5, "V", "1.5000 V"),
    (4e-4, "V", "400.0 µV"),
    (0.9991, "%", "99.9%"),
    (95.0, "%", "95.0%"),
])
def test_format_metric_ladders(val, unit, expected):
    assert units.format_metric(val, unit) == expected


def test_format_metric_micro_is_canonical():
    out = units.format_metric(8e-6, "s")
    assert "µ" in out  # U+00B5
    assert "Μ" not in out  # never Greek capital Mu


# ---------------------------------------------------------------------------
# format_quantity — fixed per-field display
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("value,field,expected", [
    (2.4356e-5, "T1", ("24.36", "µs")),
    (2.241e-5, "T2ramsey", ("22.41", "µs")),
    (5.0752e9, "f_01", ("5.0752", "GHz")),
    (7.126e9, "readout_frequency", ("7.1260", "GHz")),
    (-2.124e8, "anharmonicity", ("-212.40", "MHz")),
    (-5.0e5, "chi", ("-0.50", "MHz")),
    (3.2e7, "detuning", ("32.00", "MHz")),
    (40, "x180_length", ("40", "ns")),
    (800, "readout_length", ("800", "ns")),
    (376, "time_of_flight", ("376", "ns")),
    (-0.213, "phi0_voltage", ("-0.2130", "V")),
])
def test_format_quantity_fixed(value, field, expected):
    assert units.format_quantity(value, field) == expected


@pytest.mark.parametrize("value,field", [
    (0.159, "x180_amplitude"),      # dimensionless
    (0.9991, "gate_fidelity_avg"),  # fraction, no unit here
    (None, "T1"),                   # missing value
    ("#/qubits/qA1/T1", "T1"),      # JSON pointer string
    ([1, 2], "T1"),                 # list
    (True, "T1"),                   # bool is not a measurement
])
def test_format_quantity_returns_none(value, field):
    assert units.format_quantity(value, field) is None


def test_duration_integer_no_decimals_no_scientific():
    # ns durations are integers; never 4.0e+01 / 40.0
    assert units.format_quantity(40, "x180_length") == ("40", "ns")
    assert units.format_quantity(20000, "x180_length") == ("20000", "ns")


def test_pattern_resolution_for_dynamic_fields():
    # Fields surfaced dynamically (not in the static map) resolve by pattern.
    assert units.format_quantity(64, "some_pulse_length") == ("64", "ns")
    fq = units.format_quantity(6.0e9, "drive_frequency")
    assert fq == ("6.0000", "GHz")
    fq = units.format_quantity(1.2e8, "intermediate_frequency")
    assert fq[1] == "MHz"
    # Unknown field -> no conversion.
    assert units.format_quantity(0.5, "mystery_param") is None


# ---------------------------------------------------------------------------
# Ladder consistency — format_metric and humanize must agree
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("unit,dim", [("Hz", "freq"), ("s", "time"), ("V", "volt")])
@pytest.mark.parametrize("mag", [1e10, 5e9, 3e6, 7e2, 0.5, 2e-4, 8e-6, 4e-7])
def test_humanize_matches_format_metric(unit, dim, mag):
    scaled, suffix = units.humanize(mag, dim)
    # The suffix humanize picks must be the one format_metric appends.
    assert units.format_metric(mag, unit).endswith(suffix)


def test_pick_axis_scale_returns_factor_and_suffix():
    factor, suffix = units.pick_axis_scale("freq", 5e9)
    assert suffix == "GHz" and factor == 1e-9
    factor, suffix = units.pick_axis_scale("time", 2e-5)
    assert suffix == "µs" and factor == 1e6


# ---------------------------------------------------------------------------
# Export labeling
# ---------------------------------------------------------------------------
def test_export_header_ascii_tokens():
    assert units.export_header("f_01") == "f_01_GHz"
    assert units.export_header("T1") == "T1_us"          # ASCII 'us', not µs
    assert units.export_header("anharmonicity") == "anharmonicity_MHz"
    assert units.export_header("x180_length") == "x180_length_ns"
    assert units.export_header("phi0_voltage") == "phi0_voltage_V"
    # Unitless / unknown -> bare name
    assert units.export_header("x180_amplitude") == "x180_amplitude"
    assert units.export_header("id") == "id"


def test_export_header_has_no_micro_sign():
    for field in ("T1", "T2ramsey", "T2echo"):
        assert "µ" not in units.export_header(field)


def test_export_value_converts():
    assert units.export_value("f_01", 6.25e9) == 6.25
    assert units.export_value("T1", 2.4356e-5) == pytest.approx(24.356)
    # ns durations stay as stored integers.
    assert units.export_value("x180_length", 40) == 40
    # Unitless / non-numeric pass through.
    assert units.export_value("x180_amplitude", 0.15) == 0.15
    assert units.export_value("T1", None) is None


# ---------------------------------------------------------------------------
# qty_filter — the Jinja entrypoint
# ---------------------------------------------------------------------------
def test_qty_filter_modes():
    assert units.qty_filter(2.4356e-5, "T1") == "24.36"            # num (default)
    assert units.qty_filter(2.4356e-5, "T1", "full") == "24.36 µs"
    assert units.qty_filter(2.4356e-5, "T1", "unit") == "µs"
    # None
    assert units.qty_filter(None, "T1") == "-"
    assert units.qty_filter(None, "T1", "full") == ""
    # Unknown unit: 'full' gates to empty (no preview badge), 'num' passes through.
    assert units.qty_filter(0.15, "x180_amplitude", "full") == ""
    assert units.qty_filter(0.15, "x180_amplitude") == "0.1500"


# ---------------------------------------------------------------------------
# group_digits — lossless full-digit + thousands-comma (Bulk Edit display)
# ---------------------------------------------------------------------------


class TestGroupDigits:
    def test_int_grouping(self):
        assert units.group_digits(5050000000) == "5,050,000,000"
        assert units.group_digits(800) == "800"
        assert units.group_digits(0) == "0"
        assert units.group_digits(-256900000) == "-256,900,000"

    def test_float_full_precision(self):
        # every stored digit shown — no e-notation, no precision loss
        assert units.group_digits(5075187484.52453) == "5,075,187,484.52453"
        assert units.group_digits(0.215) == "0.215"
        assert units.group_digits(-0.00014) == "-0.00014"
        assert units.group_digits(7460000000.0) == "7,460,000,000.0"

    def test_non_numeric_passthrough(self):
        assert units.group_digits(None) == ""
        assert units.group_digits(True) == "True"
        assert units.group_digits("direct") == "direct"
        assert units.group_digits("#/wiring/qubits/qA1/xy/opx_output") == "#/wiring/qubits/qA1/xy/opx_output"

    def test_non_finite_passthrough(self):
        assert units.group_digits(float("inf")) == "inf"
        assert units.group_digits(float("nan")) == "nan"
