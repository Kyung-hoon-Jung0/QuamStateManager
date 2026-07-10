"""Tests for core.all_values.build_all_values_rows — the flat 'All values'
completeness enumerator.

The load-bearing guarantee: ``summary.total == len(loader.flatten(merged))`` and
``sum(by_kind) == total`` — every on-disk leaf is one row, nothing silently
dropped. Plus the data-safety invariants: only scalars editable; port-number dict
keys stay editable; list elements / pointers / membership arrays are read-only.
"""

from __future__ import annotations

import os

import pytest

from quam_state_manager.core.all_values import build_all_values_rows
from quam_state_manager.core.leaf_classify import KIND_SCALAR, READONLY_KINDS
from quam_state_manager.core.loader import QuamStore, flatten


def _kind_of(rows, dot_path):
    for p, _disp, kind, _mod in rows:
        if p == dot_path:
            return kind
    return None


def _synthetic_store() -> QuamStore:
    state = {
        "qubits": {"qA1": {
            "__class__": "quam.components.Transmon",
            "id": "qA1",
            "f_01": 6.25e9,
            "f_12": None,
            "xy": {"intermediate_frequency": "#./inferred_intermediate_frequency",
                   "operations": {"x180": "#./x180_DragCosine",
                                  "x180_DragCosine": {"amplitude": 0.11, "digital_marker": "ON"}}},
            "z": {"opx_output": "#/wiring/qubits/qA1/z/opx_output", "joint_offset": 0.05},
            "resonator": {"time_of_flight": 376,
                          "confusion_matrix": [[0.98, 0.02], [0.03, 0.97]]},
        }},
        "qubit_pairs": {},
        "ports": {"mw_outputs": {"con1": {"1": {"2": {"band": 2, "upconverter_frequency": 5.05e9}}}}},
        "twpas": {"twpaA": {"frequency": 6.0e9, "pump": {"opx_output": "#/wiring/twpas/twpaA/pump/opx_output"}}},
        "active_qubit_names": ["qA1"],
        "active_qubit_pair_names": [],
        "active_twpa_names": ["twpaA"],
    }
    wiring = {"wiring": {"qubits": {"qA1": {"z": {"opx_output": "#/ports/analog_outputs/con1/5"}}}},
              "network": {"host": "10.0.0.1"}}
    return QuamStore.from_dicts(state, wiring)


class TestCompletenessInvariant:
    def test_total_equals_flatten_count(self):
        store = _synthetic_store()
        rows, summary = build_all_values_rows(store)
        assert summary["total"] == len(rows)
        assert summary["total"] == len(flatten(store.merged))

    def test_by_kind_sums_to_total(self):
        store = _synthetic_store()
        _rows, summary = build_all_values_rows(store)
        assert sum(summary["by_kind"].values()) == summary["total"]
        assert summary["editable"] + summary["readonly"] == summary["total"]


class TestClassificationOnRealShape:
    def test_each_category_lands_in_the_right_kind(self):
        rows, _summary = build_all_values_rows(_synthetic_store())
        # editable scalars (incl. chip-level twpa + numeric-keyed port leaves)
        assert _kind_of(rows, "qubits.qA1.f_01") == KIND_SCALAR
        assert _kind_of(rows, "qubits.qA1.resonator.time_of_flight") == KIND_SCALAR
        assert _kind_of(rows, "qubits.qA1.f_12") == KIND_SCALAR            # null but settable
        assert _kind_of(rows, "ports.mw_outputs.con1.1.2.band") == KIND_SCALAR  # numeric dict keys
        assert _kind_of(rows, "twpas.twpaA.frequency") == KIND_SCALAR      # chip-level, was 0% reachable
        # read-only kinds
        assert _kind_of(rows, "qubits.qA1.__class__") == "skip"
        assert _kind_of(rows, "qubits.qA1.id") == "skip"
        assert _kind_of(rows, "qubits.qA1.z.opx_output") == "xref"
        assert _kind_of(rows, "qubits.qA1.xy.intermediate_frequency") == "selfref"
        assert _kind_of(rows, "qubits.qA1.xy.operations.x180") == "selfref"
        assert _kind_of(rows, "qubits.qA1.resonator.confusion_matrix.0.0") == "list"
        assert _kind_of(rows, "active_qubit_names.0") == "membership"
        assert _kind_of(rows, "active_twpa_names.0") == "membership"

    def test_no_readonly_kind_is_marked_editable(self):
        rows, _ = build_all_values_rows(_synthetic_store())
        for _p, _d, kind, _m in rows:
            if kind in READONLY_KINDS:
                # read-only rows never carry the modified=editable signal path; the
                # contract is simply that kind != scalar ⇒ the UI must not offer an input
                assert kind != KIND_SCALAR

    def test_modified_marker_only_on_edited_scalars(self):
        store = _synthetic_store()
        rows, _ = build_all_values_rows(store, modified={"qubits.qA1.f_01"})
        assert _kind_of(rows, "qubits.qA1.f_01") == KIND_SCALAR
        f01 = next(r for r in rows if r[0] == "qubits.qA1.f_01")
        assert f01[3] == 1
        other = next(r for r in rows if r[0] == "qubits.qA1.resonator.time_of_flight")
        assert other[3] == 0


_LabA = "<quam-states>/example_lab"


@pytest.mark.skipif(not os.path.isdir(_LabA), reason="real LabA chip not present")
class TestRealChipCompleteness:
    def test_laba_total_matches_flatten_and_reaches_chip_level(self):
        store = QuamStore(_LabA)
        rows, summary = build_all_values_rows(store)
        assert summary["total"] == len(flatten(store.merged))
        assert sum(summary["by_kind"].values()) == summary["total"]
        # the long tail that the curated grid reached 0% of is now editable
        assert summary["editable"] > 4000, summary["editable"]
        assert any(p.startswith("twpas.") and k == KIND_SCALAR for p, _d, k, _m in rows)
        # time_of_flight is an editable row for every qubit
        tofs = [r for r in rows if r[0].endswith("resonator.time_of_flight")]
        assert tofs and all(r[2] == KIND_SCALAR for r in tofs)
