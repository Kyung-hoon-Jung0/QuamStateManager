"""Unit tests for the pure hardware-constraint checkers in core/spec_constraints.

These guard the numeric edge cases (bool vs int, integral floats, pointer
strings / None skipped) that keep the linter free of false positives.
"""

from __future__ import annotations

from quam_state_manager.core import spec_constraints as sc


class TestSkips:
    # Non-numbers (pointers, None) and bool must never produce a reason.
    def test_pointer_string_skipped(self):
        assert sc._check_multiple_min("#./tof", mult=4, minimum=24) is None
        assert sc._check_int_range("#/x/y", -11, 18) is None
        assert sc._check_abs_max("#./if", 400e6) is None
        assert sc._check_band("#./b") is None

    def test_none_skipped(self):
        assert sc._check_multiple_min(None, mult=4, minimum=24) is None
        assert sc._check_band(None) is None

    def test_bool_skipped(self):
        # bool is a subclass of int; must be treated as non-applicable
        assert sc._num(True) is None
        assert sc._check_int_range(True, 0, 32) is None


class TestMultipleMin:
    def test_ok(self):
        assert sc._check_multiple_min(280, mult=4, minimum=24) is None

    def test_integral_float_ok(self):
        assert sc._check_multiple_min(280.0, mult=4, minimum=24) is None

    def test_not_multiple(self):
        assert "multiple of 4" in sc._check_multiple_min(421, mult=4, minimum=24)

    def test_below_min(self):
        assert "≥ 24" in sc._check_multiple_min(12, mult=4, minimum=24)

    def test_non_integral_float(self):
        assert "integer" in sc._check_multiple_min(28.5, mult=4, minimum=24)


class TestIntRange:
    def test_ok_endpoints(self):
        assert sc._check_int_range(-11, -11, 18) is None
        assert sc._check_int_range(18, -11, 18) is None

    def test_out_of_range(self):
        assert sc._check_int_range(25, -11, 18) is not None
        assert sc._check_int_range(-12, -11, 18) is not None


class TestAbsMax:
    def test_within(self):
        assert sc._check_abs_max(-430e6, 440e6) is None  # negative IF is fine

    def test_exceeds(self):
        assert sc._check_abs_max(500e6, 400e6) is not None


class TestBand:
    def test_valid(self):
        for b in (1, 2, 3):
            assert sc._check_band(b) is None

    def test_invalid(self):
        assert sc._check_band(4) is not None
        assert sc._check_band(0) is not None


class TestSmearing:
    def test_within_bound(self):
        assert sc._check_smearing(20, 100) is None

    def test_exceeds_bound(self):
        assert "time_of_flight" in sc._check_smearing(96, 100)

    def test_negative(self):
        assert "≥ 0" in sc._check_smearing(-1, 100)

    def test_tof_unknown_only_checks_floor(self):
        # tof is a pointer/None → only the ≥0 + int checks apply, no upper bound
        assert sc._check_smearing(10_000, "#./tof") is None
        assert sc._check_smearing(-5, None) is not None


def test_spec_findings_empty_on_minimal_root():
    assert sc.spec_findings({}) == []
    assert sc.spec_findings({"qubits": {}, "ports": {}}) == []
