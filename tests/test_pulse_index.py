"""Tests for core/pulse_index.py — enumeration, used_by reverse refs, and
pointer rewriting for duplicate/rename.

The fixture mirrors the shapes observed in the real LabA state file:
``#../`` family refs, ``#./`` aliases, absolute pointers to qubit props,
``#./default_integration_weights`` runtime refs, pair macros with implicit-
class flux pulses, ``None`` coupler slots, and gate-level ``cz`` aliases.
"""

from __future__ import annotations

import pytest

from quam_state_manager.core.pulse_index import (
    build_reverse_pointer_index,
    list_pulses,
    rewrite_referrer_pointer,
    rewrite_subtree_pointers,
    used_by,
)

QC = "quam.components.pulses."


@pytest.fixture
def merged() -> dict:
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
                            "__class__": QC + "DragCosinePulse",
                        },
                        "x90_DragCosine": {
                            "length": "#../x180_DragCosine/length",
                            "axis_angle": 0, "amplitude": 0.159,
                            "alpha": "#../x180_DragCosine/alpha",
                            "anharmonicity": "#../x180_DragCosine/anharmonicity",
                            "__class__": QC + "DragCosinePulse",
                        },
                        "x180": "#./x180_DragCosine",
                        "x90": "#./x90_DragCosine",
                        "saturation": {
                            "length": 20000, "amplitude": 0.004,
                            "__class__": QC + "SquarePulse",
                        },
                        "mystery": {
                            "length": 10, "amplitude": 0.1,
                            "__class__": "quam_builder.custom.WeirdPulse",
                        },
                    },
                },
                "z": {
                    "operations": {
                        "const": {"length": 100, "amplitude": 0.05,
                                  "axis_angle": 0.5,
                                  "__class__": QC + "SquarePulse"},
                    },
                },
                "resonator": {
                    "operations": {
                        "readout": {
                            "length": 1024, "amplitude": 0.01,
                            "integration_weights": "#./default_integration_weights",
                            "__class__": QC + "SquareReadoutPulse",
                        },
                    },
                },
            },
            "qA2": {
                "xy": {
                    "operations": {
                        # same op name as qA1's — must never cross-match
                        "x180_DragCosine": {
                            "length": 52, "axis_angle": 0, "amplitude": 0.2,
                            "alpha": -0.2, "anharmonicity": -210e6,
                            "__class__": QC + "DragCosinePulse",
                        },
                        "x180": "#./x180_DragCosine",
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
                    },
                    "cz_snz": {
                        "flux_pulse_qubit": {
                            "length": "#./inferred_length",
                            "amplitude": 0.06, "flat_length": 20,
                            "t_phi_eff": 2.0, "padding": 0,
                            "__class__": QC + "SNZPulse",
                        },
                        "coupler_flux_pulse": {"amplitude": 0.1, "length": 100},
                    },
                    "cz": "#./cz_unipolar",
                },
            },
        },
    }


XY = "qubits.qA1.xy.operations"


# ---------------------------------------------------------------------------
# Reverse index + used_by
# ---------------------------------------------------------------------------

class TestReverseIndex:
    def test_absolute_pointer_indexed(self, merged):
        index = build_reverse_pointer_index(merged)
        assert f"{XY}.x180_DragCosine.anharmonicity" in index[
            "qubits.qA1.anharmonicity"]

    def test_relative_family_pointer_indexed(self, merged):
        index = build_reverse_pointer_index(merged)
        assert f"{XY}.x90_DragCosine.length" in index[
            f"{XY}.x180_DragCosine.length"]

    def test_alias_pointer_indexed(self, merged):
        index = build_reverse_pointer_index(merged)
        assert f"{XY}.x180" in index[f"{XY}.x180_DragCosine"]

    def test_used_by_collects_direct_referrers(self, merged):
        refs = used_by(merged, f"{XY}.x180_DragCosine")
        assert f"{XY}.x180" in refs                      # alias op
        assert f"{XY}.x90_DragCosine.length" in refs     # field-level ref
        assert f"{XY}.x90_DragCosine.alpha" in refs
        assert f"{XY}.x90_DragCosine.anharmonicity" in refs

    def test_used_by_excludes_internal_self_refs(self, merged):
        refs = used_by(merged, "qubits.qA1.resonator.operations.readout")
        assert refs == []  # #./default_integration_weights is internal

    def test_used_by_segment_matching_not_substring(self, merged):
        # nothing points at x180 (the alias) itself; in particular the refs
        # to x180_DragCosine must NOT leak in via prefix-string matching
        assert used_by(merged, f"{XY}.x180") == []

    def test_used_by_does_not_cross_qubits(self, merged):
        refs = used_by(merged, "qubits.qA2.xy.operations.x180_DragCosine")
        assert refs == ["qubits.qA2.xy.operations.x180"]

    def test_gate_alias_counts_as_referrer(self, merged):
        refs = used_by(merged, "qubit_pairs.qA1-qA2.macros.cz_unipolar")
        assert "qubit_pairs.qA1-qA2.macros.cz" in refs

    def test_used_by_of_unreferenced_op(self, merged):
        assert used_by(merged, f"{XY}.saturation") == []


# ---------------------------------------------------------------------------
# Enumeration
# ---------------------------------------------------------------------------

class TestListPulses:
    def test_row_count_and_paths(self, merged):
        rows = list_pulses(merged)
        paths = {r["path"] for r in rows}
        # qubit ops (incl. aliases + unknown class) and pair slots; the None
        # coupler slot and the gate-level "cz" alias are NOT rows
        assert f"{XY}.x180_DragCosine" in paths
        assert f"{XY}.x180" in paths
        assert "qubits.qA1.z.operations.const" in paths
        assert "qubits.qA1.resonator.operations.readout" in paths
        assert "qubit_pairs.qA1-qA2.macros.cz_unipolar.flux_pulse_qubit" in paths
        assert "qubit_pairs.qA1-qA2.macros.cz_snz.flux_pulse_qubit" in paths
        assert "qubit_pairs.qA1-qA2.macros.cz_snz.coupler_flux_pulse" in paths
        assert "qubit_pairs.qA1-qA2.macros.cz_unipolar.coupler_flux_pulse" not in paths
        assert "qubit_pairs.qA1-qA2.macros.cz" not in paths

    def _row(self, merged, path):
        for row in list_pulses(merged):
            if row["path"] == path:
                return row
        raise AssertionError(path)

    def test_real_pulse_row(self, merged):
        row = self._row(merged, f"{XY}.x180_DragCosine")
        assert row["owner"] == "qA1" and row["channel"] == "xy"
        assert row["class_short"] == "DragCosinePulse" and row["known"]
        assert row["iq"] is True       # DragCosine is always-IQ
        assert row["length"] == 48 and row["amplitude"] == 0.319
        assert len(row["used_by"]) == 4

    def test_pointer_params_resolved_for_display(self, merged):
        row = self._row(merged, f"{XY}.x90_DragCosine")
        assert row["length"] == 48  # via #../x180_DragCosine/length

    def test_alias_row(self, merged):
        row = self._row(merged, f"{XY}.x180")
        assert row["is_alias"]
        assert row["alias_target"] == f"{XY}.x180_DragCosine"
        assert row["length"] == 48 and row["amplitude"] == 0.319

    def test_unknown_class_row_degrades(self, merged):
        row = self._row(merged, f"{XY}.mystery")
        assert not row["known"]
        assert row["class_short"] == "WeirdPulse"
        assert row["length"] == 10  # raw numeric length still shown

    def test_square_with_axis_angle_is_iq(self, merged):
        row = self._row(merged, "qubits.qA1.z.operations.const")
        assert row["iq"] is True  # optional-IQ + axis_angle present

    def test_readout_row(self, merged):
        row = self._row(merged, "qubits.qA1.resonator.operations.readout")
        assert row["readout"] is True

    def test_gate_slot_implicit_square(self, merged):
        row = self._row(merged,
                        "qubit_pairs.qA1-qA2.macros.cz_unipolar.flux_pulse_qubit")
        assert row["owner_kind"] == "pair" and row["gate"] == "cz_unipolar"
        assert row["known"] and row["class_short"] == "SquarePulse"
        assert row["length"] == 100

    def test_gate_slot_inferred_length(self, merged):
        row = self._row(merged,
                        "qubit_pairs.qA1-qA2.macros.cz_snz.flux_pulse_qubit")
        assert row["class_short"] == "SNZPulse"
        assert row["length"] == 24  # ceil((0+20+2+2)/4)*4

    def test_empty_merged(self):
        assert list_pulses({}) == []


# ---------------------------------------------------------------------------
# Pointer rewriting
# ---------------------------------------------------------------------------

class TestRewriteSubtreePointers:
    def test_internal_self_ref_kept(self, merged):
        body = merged["qubit_pairs"]["qA1-qA2"]["macros"]["cz_snz"]["flux_pulse_qubit"]
        out = rewrite_subtree_pointers(
            body,
            "qubit_pairs.qA1-qA2.macros.cz_snz.flux_pulse_qubit",
            "qubit_pairs.qA1-qA2.macros.cz_snz_copy.flux_pulse_qubit")
        assert out["length"] == "#./inferred_length"  # still self-relative

    def test_family_ref_to_other_op_kept(self, merged):
        body = merged["qubits"]["qA1"]["xy"]["operations"]["x90_DragCosine"]
        out = rewrite_subtree_pointers(body, f"{XY}.x90_DragCosine",
                                       f"{XY}.x90_copy")
        assert out["length"] == "#../x180_DragCosine/length"  # keeps tracking

    def test_absolute_external_ref_kept(self, merged):
        body = merged["qubits"]["qA1"]["xy"]["operations"]["x180_DragCosine"]
        out = rewrite_subtree_pointers(body, f"{XY}.x180_DragCosine",
                                       f"{XY}.x180_copy")
        assert out["anharmonicity"] == "#/qubits/qA1/anharmonicity"

    def test_relative_self_by_name_rewritten(self):
        # a pulse whose field points back into ITSELF via the family path
        body = {"a": 1.0, "b": "#../weird/a"}
        out = rewrite_subtree_pointers(body, "qubits.q.xy.operations.weird",
                                       "qubits.q.xy.operations.weird2")
        assert out["b"] == "#../weird2/a"

    def test_absolute_self_ref_rewritten(self):
        body = {"a": 1.0, "b": "#/qubits/q/xy/operations/weird/a"}
        out = rewrite_subtree_pointers(body, "qubits.q.xy.operations.weird",
                                       "qubits.q.xy.operations.weird2")
        assert out["b"] == "#/qubits/q/xy/operations/weird2/a"

    def test_substring_name_not_rewritten(self):
        # weird_v2 contains "weird" as a name prefix — segments must not match
        body = {"a": 1.0, "b": "#../weird_v2/a"}
        out = rewrite_subtree_pointers(body, "qubits.q.xy.operations.weird",
                                       "qubits.q.xy.operations.weird9")
        assert out["b"] == "#../weird_v2/a"

    def test_deepcopy_no_aliasing(self, merged):
        body = merged["qubits"]["qA1"]["xy"]["operations"]["saturation"]
        out = rewrite_subtree_pointers(body, f"{XY}.saturation", f"{XY}.sat2")
        out["amplitude"] = 999
        assert body["amplitude"] == 0.004

    def test_string_alias_value(self):
        out = rewrite_subtree_pointers("#./x180_DragCosine",
                                       f"{XY}.x180", f"{XY}.x180_b")
        assert out == "#./x180_DragCosine"  # outside the (scalar) subtree


class TestRewriteReferrerPointer:
    def test_relative_referrer_same_flavor(self):
        out = rewrite_referrer_pointer(
            "#../x180_DragCosine/length",
            f"{XY}.x90_DragCosine.length",
            f"{XY}.x180_DragCosine", f"{XY}.x180_v2")
        assert out == "#../x180_v2/length"

    def test_self_flavor_alias(self):
        out = rewrite_referrer_pointer(
            "#./x180_DragCosine", f"{XY}.x180",
            f"{XY}.x180_DragCosine", f"{XY}.x180_v2")
        assert out == "#./x180_v2"

    def test_absolute_referrer(self):
        out = rewrite_referrer_pointer(
            "#/qubits/qA1/xy/operations/x180_DragCosine/length",
            "qubits.qA2.xy.operations.borrow.length",
            f"{XY}.x180_DragCosine", f"{XY}.x180_v2")
        assert out == "#/qubits/qA1/xy/operations/x180_v2/length"

    def test_pointer_outside_target_returns_none(self):
        out = rewrite_referrer_pointer(
            "#../saturation/length", f"{XY}.x90_DragCosine.length",
            f"{XY}.x180_DragCosine", f"{XY}.x180_v2")
        assert out is None

    def test_exact_target_match(self):
        out = rewrite_referrer_pointer(
            "#./x180_DragCosine", f"{XY}.x180",
            f"{XY}.x180_DragCosine", f"{XY}.renamed")
        assert out == "#./renamed"
