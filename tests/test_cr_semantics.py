"""Unit tests for ``core.cr_semantics`` — the flavor-tolerant CR/ZZ accessor
layer (docs/54_cr_integration.md).

The three schema flavors and the phantom-"Cr" regression are pinned here
against the shared synthetic corpus in ``tests/cr_fixtures.py``; every
downstream surface test builds on the same fixtures.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from quam_state_manager.core import cr_semantics as crs
from quam_state_manager.core.loader import QuamStore
from quam_state_manager.core.query import QueryEngine

sys.path.insert(0, str(Path(__file__).parent))
from cr_fixtures import (  # noqa: E402
    B_ACTIVE_PAIRS,
    B_EXPECTED_IF,
    make_cz_reference,
    make_flavor_a,
    make_flavor_b,
    make_flavor_c,
)

_GATES_FIX = ("quam_builder.architecture.superconducting.custom_gates."
              "fixed_transmon_pair.two_qubit_gates.")
_COMP = "quam_builder.architecture.superconducting.components."


def _store(state, wiring) -> QuamStore:
    return QuamStore.from_dicts(state, wiring)


# ── classify_class ───────────────────────────────────────────────────────────

class TestClassifyClass:
    def test_exact_hits(self):
        assert crs.classify_class(_COMP + "cross_resonance.CrossResonanceMW") == \
            ("cr_channel_mw", "exact")
        assert crs.classify_class(
            _COMP + "cross_resonance_drive.CrossResonanceDriveIQ") == \
            ("cr_channel_iq", "exact")
        assert crs.classify_class(_COMP + "zz_drive.ZZDriveMW") == \
            ("zz_channel_mw", "exact")
        assert crs.classify_class(_GATES_FIX + "CRGate") == ("cr_gate", "exact")
        assert crs.classify_class(_GATES_FIX + "StarkInducedCZGate") == \
            ("stark_cz_gate", "exact")

    def test_leaf_fallback_on_foreign_module(self):
        # Module churn tolerance: unknown prefix, known leaf → leaf match.
        assert crs.classify_class("newstack.gates.CRGate") == ("cr_gate", "leaf")
        assert crs.classify_class("x.y.CrossResonanceMW") == ("cr_channel_mw", "leaf")
        assert crs.classify_class("x.y.ZZDriveIQ") == ("zz_channel_iq", "leaf")

    def test_stark_cz_not_swallowed_by_cz(self):
        # 'StarkInducedCZGate' contains 'CZGate' — must classify as stark.
        assert crs.classify_class("a.b.StarkInducedCZGate")[0] == "stark_cz_gate"

    def test_cz_gate_and_unknowns(self):
        assert crs.classify_class("a.b.CZGate") == ("cz_gate", "leaf")
        assert crs.classify_class("a.b.SomethingElse") == (None, None)
        assert crs.classify_class(None) == (None, None)
        assert crs.classify_class("") == (None, None)


# ── channel / macro accessors ────────────────────────────────────────────────

class TestChannelAccessors:
    def test_cr_channel_present_null_missing(self):
        state, _ = make_flavor_b()
        pair = state["qubit_pairs"]["q0-1"]
        assert crs.cr_channel(pair) is pair["cross_resonance"]
        assert crs.cr_channel({"cross_resonance": None}) is None
        assert crs.cr_channel({}) is None
        assert crs.cr_channel(None) is None

    def test_zz_channel_key_resolution(self):
        state, _ = make_flavor_b(with_zz=True)
        key, chan = crs.zz_channel(state["qubit_pairs"]["q0-1"])
        assert key == "zz_drive" and chan["detuning"] == -30e6
        # tip rename: `zz`
        state_c, _ = make_flavor_c()
        assert crs.zz_channel(state_c["qubit_pairs"]["q0-1"]) is None  # null
        assert crs.zz_channel({"zz": {"detuning": 1}}) == ("zz", {"detuning": 1})
        # explicit null on both spellings → None
        assert crs.zz_channel({"zz_drive": None, "zz": None}) is None

    def test_xy_detuned(self):
        assert crs.xy_detuned_channel({"xy_detuned": {"a": 1}}) == {"a": 1}
        assert crs.xy_detuned_channel({"xy_detuned": None}) is None


class TestCrGateMacro:
    def test_prefers_cr_name(self):
        state, _ = make_flavor_b()
        name, gate = crs.cr_gate_macro(state["qubit_pairs"]["q0-1"])
        assert name == "cr" and "CRGate" in gate["__class__"]

    def test_alias_hop(self):
        pair = {"macros": {"cr": "#./cr_echoed",
                           "cr_echoed": {"__class__": _GATES_FIX + "CRGate"}}}
        name, gate = crs.cr_gate_macro(pair)
        assert name == "cr_echoed"

    def test_class_based_fallback(self):
        pair = {"macros": {"my_gate": {"__class__": _GATES_FIX + "CRGate"}}}
        assert crs.cr_gate_macro(pair)[0] == "my_gate"

    def test_cz_named_cr_rejected(self):
        # A macro literally named "cr" but CZ-shaped is NOT a CR gate.
        pair = {"macros": {"cr": {
            "flux_pulse_qubit": {"amplitude": 0.1},
            "__class__": "x.y.CZGate",
        }}}
        assert crs.cr_gate_macro(pair) is None

    def test_none_cases(self):
        assert crs.cr_gate_macro({"macros": {}}) is None
        assert crs.cr_gate_macro({}) is None
        assert crs.cr_gate_macro(None) is None


class TestIsCzShapedMacro:
    def test_cz_with_null_values_still_renders(self):
        # Presence, not value: an uncalibrated CZ gate must render.
        assert crs.is_cz_shaped_macro({"flux_pulse_qubit": None}) is True
        assert crs.is_cz_shaped_macro({"phase_shift_control": None}) is True

    def test_modern_crgate_with_fidelity_is_not_cz(self):
        # THE phantom-"Cr" regression: fidelity (null) used to trip the check.
        gate = {"id": "#./inferred_id", "fidelity": None,
                "duration": "#./inferred_duration",
                "qc_correction_phase": None, "qt_correction_phase": None,
                "__class__": _GATES_FIX + "CRGate"}
        assert crs.is_cz_shaped_macro(gate) is False

    def test_bare_crgate_stub(self):
        assert crs.is_cz_shaped_macro({"__class__": _GATES_FIX + "CRGate"}) is False

    def test_cr_class_wins_over_stray_flux_keys(self):
        gate = {"flux_pulse_qubit": {"amplitude": 1},
                "__class__": _GATES_FIX + "CRGate"}
        assert crs.is_cz_shaped_macro(gate) is False

    def test_non_dict(self):
        assert crs.is_cz_shaped_macro(None) is False
        assert crs.is_cz_shaped_macro("#./cz_unipolar") is False


# ── flavor detection ─────────────────────────────────────────────────────────

class TestFlavorDetection:
    def test_flavor_a_is_lo_if(self):
        state, _ = make_flavor_a()
        report = crs.detect_flavor(state)
        assert report.flavor == crs.FLAVOR_LO_IF and not report.mixed
        assert set(report.per_pair.values()) == {crs.FLAVOR_LO_IF}

    def test_flavor_b_is_rf(self):
        state, _ = make_flavor_b()
        report = crs.detect_flavor(state)
        assert report.flavor == crs.FLAVOR_RF and not report.mixed

    def test_sparse_b_is_rf_not_rf_drive(self):
        # Sparse serialization strips lever keys — must NOT read as the tip.
        state, _ = make_flavor_b(sparse=True)
        flavor, _sig = crs.detect_pair_flavor(state["qubit_pairs"]["q0-1"])
        assert flavor == crs.FLAVOR_RF

    def test_flavor_c_is_rf_drive_by_class_only(self):
        state, _ = make_flavor_c()
        flavor, signals = crs.detect_pair_flavor(state["qubit_pairs"]["q0-1"])
        assert flavor == crs.FLAVOR_RF_DRIVE
        assert signals[0][0] == "drive_class"

    def test_macro_only_pair_is_unknown(self):
        pair = {"macros": {"cr": {"__class__": _GATES_FIX + "CRGate"}}}
        assert crs.detect_pair_flavor(pair)[0] == crs.FLAVOR_UNKNOWN

    def test_cz_chip_is_none(self):
        state, _ = make_cz_reference()
        assert crs.detect_flavor(state).flavor == crs.FLAVOR_NONE
        assert crs.detect_pair_flavor(state["qubit_pairs"]["q0-q1"])[0] == \
            crs.FLAVOR_NONE

    def test_mixed_chip(self):
        state, _ = make_flavor_b()
        state_a, _ = make_flavor_a()
        state["qubit_pairs"]["mix"] = state_a["qubit_pairs"]["q0-1"]
        report = crs.detect_flavor(state)
        assert report.mixed is True
        assert report.per_pair["mix"] == crs.FLAVOR_LO_IF

    def test_empty(self):
        assert crs.detect_flavor({}).flavor == crs.FLAVOR_NONE
        assert crs.detect_flavor({"qubit_pairs": {}}).flavor == crs.FLAVOR_NONE


class TestIsCrChip:
    def test_true_for_all_flavors(self):
        for maker in (make_flavor_a, make_flavor_b, make_flavor_c):
            state, _ = maker()
            assert crs.is_cr_chip(state) is True

    def test_false_for_cz_and_empty(self):
        state, _ = make_cz_reference()
        assert crs.is_cr_chip(state) is False
        assert crs.is_cr_chip({}) is False
        assert crs.is_cr_chip(None) is False


# ── fidelity ─────────────────────────────────────────────────────────────────

class TestFidelity:
    def test_macro_standard_rb_nested(self):
        pair = {"macros": {"cr": {
            "fidelity": {"StandardRB": {"average_gate_fidelity": 0.99}},
            "__class__": _GATES_FIX + "CRGate"}}}
        out = crs.fidelity(pair)
        assert out["value"] == 0.99 and out["source"] == "macro"
        assert out["clifford"] is False
        assert out["path_suffix"].endswith("average_gate_fidelity")

    def test_macro_bare_standard_rb_is_clifford(self):
        pair = {"macros": {"cr": {"fidelity": {"StandardRB": 0.95},
                                  "__class__": _GATES_FIX + "CRGate"}}}
        out = crs.fidelity(pair)
        assert out["value"] == 0.95 and out["clifford"] is True

    def test_macro_bell_state(self):
        pair = {"macros": {"cr": {
            "fidelity": {"Bell_State": {"Fidelity": 0.9}},
            "__class__": _GATES_FIX + "CRGate"}}}
        assert crs.fidelity(pair)["value"] == 0.9

    def test_macro_bare_float(self):
        pair = {"macros": {"cr": {"fidelity": 0.88,
                                  "__class__": _GATES_FIX + "CRGate"}}}
        out = crs.fidelity(pair)
        assert out["value"] == 0.88 and out["path_suffix"] == "macros.cr.fidelity"

    def test_channel_bell_fallback(self):
        state, _ = make_flavor_a()
        out = crs.fidelity(state["qubit_pairs"]["q1-2"])
        assert out == {"value": 0.93, "source": "channel", "gate": None,
                       "clifford": False,
                       "path_suffix": "cross_resonance.bell_state_fidelity"}

    def test_none_when_nothing(self):
        state, _ = make_flavor_b()          # macro fidelity null, no channel bell
        assert crs.fidelity(state["qubit_pairs"]["q0-1"]) is None


# ── lever map ────────────────────────────────────────────────────────────────

class TestLeverMap:
    def test_flavor_b_channel_levers(self):
        state, _ = make_flavor_b()
        levers = crs.lever_map(state["qubit_pairs"]["q0-1"])
        assert levers["drive_amplitude_scaling"] == \
            "cross_resonance.drive_amplitude_scaling"
        assert levers["drive_phase"] == "cross_resonance.drive_phase"
        # channel wins the bare name; the macro copy is macro_-prefixed
        assert levers["qc_correction_phase"] == \
            "cross_resonance.qc_correction_phase"
        assert levers["macro_qc_correction_phase"] == \
            "macros.cr.qc_correction_phase"
        assert levers["upconverter"] == "cross_resonance.upconverter"
        assert "bell_state_fidelity" not in levers      # key absent in flavor B

    def test_sparse_b_never_grows_phantom_rows(self):
        state, _ = make_flavor_b(sparse=True)
        levers = crs.lever_map(state["qubit_pairs"]["q0-1"])
        assert "drive_amplitude_scaling" not in levers
        assert "qc_correction_phase" not in levers
        assert levers["upconverter"] == "cross_resonance.upconverter"

    def test_flavor_a_includes_bell(self):
        state, _ = make_flavor_a()
        levers = crs.lever_map(state["qubit_pairs"]["q1-2"])
        assert levers["bell_state_fidelity"] == \
            "cross_resonance.bell_state_fidelity"
        assert levers["cancel_phase"] == "cross_resonance.cancel_phase"

    def test_flavor_c_macro_levers_bare_names(self):
        state, _ = make_flavor_c()
        levers = crs.lever_map(state["qubit_pairs"]["q0-1"])
        # channel has no levers at the tip → macro paths win the bare names
        assert levers["drive_amplitude_scaling"] == \
            "macros.cr.drive_amplitude_scaling"

    def test_zz_levers_use_real_key(self):
        state, _ = make_flavor_b(with_zz=True)
        levers = crs.lever_map(state["qubit_pairs"]["q0-1"])
        assert levers["zz_detuning"] == "zz_drive.detuning"
        pair = {"zz": {"detuning": -1e6}}
        assert crs.lever_map(pair)["zz_detuning"] == "zz.detuning"

    def test_non_cr_pair_empty(self):
        state, _ = make_cz_reference()
        assert crs.lever_map(state["qubit_pairs"]["q0-q1"]) == {}


# ── effective frequencies ────────────────────────────────────────────────────

class TestEffectiveFrequencies:
    def test_flavor_b_all_pairs_exact(self):
        store = _store(*make_flavor_b())
        for pid, expected_if in B_EXPECTED_IF.items():
            eff = crs.effective_frequencies(store, pid)
            assert eff is not None, pid
            assert eff.if_hz == pytest.approx(expected_if), pid
            assert eff.formula == "rf-lo"
            assert eff.upconverter == 2

    def test_invalid_pair_flagged(self):
        store = _store(*make_flavor_b())
        eff = crs.effective_frequencies(store, "q2-1")
        assert eff.valid is False
        assert any("exceeds" in p for p in eff.problems)

    def test_valid_pair(self):
        store = _store(*make_flavor_b())
        eff = crs.effective_frequencies(store, "q0-1")
        assert eff.valid is True and eff.problems == ()
        assert eff.lo_hz == pytest.approx(5.0e9)
        assert eff.target_rf_hz == pytest.approx(5.2e9)
        assert eff.rf_hz == pytest.approx(5.2e9)

    def test_flavor_a_scalar_port_lo(self):
        # LO_frequency == "#./upconverter_frequency" → port scalar (dedicated
        # CR port, upconverter 1); target RF = LO+IF literals.
        store = _store(*make_flavor_a())
        eff = crs.effective_frequencies(store, "q0-1")
        assert eff.lo_hz == pytest.approx(5.0e9)
        assert eff.target_rf_hz == pytest.approx(5.2e9)
        assert eff.if_hz == pytest.approx(200e6)
        assert eff.formula == "lo+if-lo"
        assert eff.valid is True

    def test_zz_channel_with_detuning(self):
        store = _store(*make_flavor_b(with_zz=True))
        eff = crs.effective_frequencies(store, "q0-1", channel="zz")
        # target RF = 5.3e9 − 100e6 = 5.2e9; LO = 5.0e9; det −30e6 → 170 MHz
        assert eff.if_hz == pytest.approx(170e6)
        assert eff.formula == "rf-lo+det"

    def test_dangling_pointer_is_problem_not_exception(self):
        state, wiring = make_flavor_b()
        state["qubit_pairs"]["q0-1"]["cross_resonance"][
            "target_qubit_RF_frequency"] = "#/qubits/missing/xy/RF_frequency"
        store = _store(state, wiring)
        eff = crs.effective_frequencies(store, "q0-1")
        assert eff.if_hz is None and eff.valid is False
        assert any("target_qubit_RF_frequency" in p for p in eff.problems)

    def test_none_for_non_cr_pair_and_missing(self):
        store = _store(*make_cz_reference())
        assert crs.effective_frequencies(store, "q0-q1") is None
        assert crs.effective_frequencies(store, "nope") is None


# ── directed-pair helpers ────────────────────────────────────────────────────

class TestDirectedHelpers:
    def test_endpoints_from_pointers_never_id_split(self):
        state, _ = make_flavor_b()
        # id "q0-1" would split to ("q0", "1") — pointers give the truth.
        assert crs.pair_endpoints(state["qubit_pairs"]["q0-1"]) == ("q0", "q1")
        assert crs.pair_endpoints({}) == (None, None)

    def test_directed_partner(self):
        state, _ = make_flavor_b()
        assert crs.directed_partner(state, "q0-1") == "q1-0"
        assert crs.directed_partner(state, "q1-0") == "q0-1"

    def test_physical_edge_key_shared(self):
        state, _ = make_flavor_b()
        k1 = crs.physical_edge_key(state["qubit_pairs"]["q0-1"])
        k2 = crs.physical_edge_key(state["qubit_pairs"]["q1-0"])
        assert k1 == k2 == ("q0", "q1")

    def test_is_active_membership(self):
        state, _ = make_flavor_b()
        for pid in B_ACTIVE_PAIRS:
            assert crs.is_active(state, pid) is True
        assert crs.is_active(state, "q1-0") is False

    def test_absent_list_means_all_active(self):
        state, _ = make_cz_reference()      # no active_qubit_pair_names key
        assert crs.is_active(state, "q0-q1") is True
        state2, _ = make_flavor_b()
        state2["active_qubit_pair_names"] = []
        assert crs.is_active(state2, "q1-0") is True


# ── gate class evidence ──────────────────────────────────────────────────────

class TestGateClassEvidence:
    def test_majority_verbatim_class(self):
        state, _ = make_flavor_b()
        assert crs.gate_class_evidence(state, "CRGate") == _GATES_FIX + "CRGate"

    def test_none_without_evidence(self):
        state, _ = make_cz_reference()
        assert crs.gate_class_evidence(state, "CRGate") is None
        assert crs.gate_class_evidence(state, "CZGate") is not None


# ── the phantom-"Cr" fix, end to end through QueryEngine ─────────────────────

class TestPhantomCrRegression:
    def test_no_phantom_cz_keys_for_modern_crgate(self):
        engine = QueryEngine(_store(*make_flavor_b()))
        pair = engine.get_pair("q0-1")
        # the CZ extractor must not have fired for the CRGate macro
        assert "cr_amplitude" not in pair
        assert "cr_coupler_amplitude" not in pair
        assert "cr_phase_shift_control" not in pair
        # the CR channel block still surfaces
        assert pair["cr_upconverter"] == 2
        assert pair["cr_operations"] == "square, flattop"

    def test_cz_reference_unchanged(self):
        engine = QueryEngine(_store(*make_cz_reference()))
        pair = engine.get_pair("q0-q1")
        # uncalibrated CZ (null values, keys present) must still render
        assert "cz_unipolar_amplitude" in pair
        assert pair["cz_unipolar_amplitude"] == 0.1
        assert "cz_unipolar_phase_shift_control" in pair
