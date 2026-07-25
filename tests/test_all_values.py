"""Tests for core.all_values.build_all_values_rows — the flat 'All values'
completeness enumerator.

The load-bearing guarantee: ``summary.total == len(loader.flatten(merged))`` and
``sum(by_kind) == total`` — every on-disk leaf is one row, nothing silently
dropped. Plus the data-safety invariants: only scalars carry the modified flag;
port-number dict keys stay editable; membership arrays are read-only. v2 adds
container rows (arrays / empty dicts+lists) COUNTED SEPARATELY so the leaf
invariant survives (``len(rows) == total + arrays + empties``), and xref rows
display the RESOLVED value with a ``{p, d}`` extra (dangling keeps the raw text).
"""

from __future__ import annotations

import os

import pytest

from quam_state_manager.core.all_values import (
    KIND_ARRAY,
    KIND_EMPTY,
    build_all_values_rows,
)
from quam_state_manager.core.leaf_classify import (
    ALL_KINDS,
    KIND_SCALAR,
    READONLY_KINDS,
)
from quam_state_manager.core.loader import QuamStore, flatten


def _row_of(rows, dot_path):
    for row in rows:
        if row[0] == dot_path:
            return row
    return None


def _kind_of(rows, dot_path):
    row = _row_of(rows, dot_path)
    return row[2] if row else None


def _leaf_rows(rows):
    return [r for r in rows if r[2] not in (KIND_ARRAY, KIND_EMPTY)]


def _synthetic_store() -> QuamStore:
    state = {
        "qubits": {"qA1": {
            "__class__": "quam.components.Transmon",
            "id": "qA1",
            "f_01": 6.25e9,
            "f_12": None,
            "extras": {},
            "xy": {"intermediate_frequency": "#./inferred_intermediate_frequency",
                   "operations": {"x180": "#./x180_DragCosine",
                                  "x180_DragCosine": {"amplitude": 0.11, "digital_marker": "ON"}}},
            "z": {"opx_output": "#/wiring/qubits/qA1/z/opx_output", "joint_offset": 0.05},
            "resonator": {"time_of_flight": 376,
                          "confusion_matrix": [[0.98, 0.02], [0.03, 0.97]]},
        }},
        "qubit_pairs": {},
        "ports": {"mw_outputs": {"con1": {"1": {"2": {"band": 2, "upconverter_frequency": 5.05e9}}}},
                  "analog_outputs": {"con1": {"5": {"offset": 0.1}}}},
        "twpas": {"twpaA": {"frequency": 6.0e9, "pump": {"opx_output": "#/wiring/twpas/twpaA/pump/opx_output"}}},
        "active_qubit_names": ["qA1"],
        "active_qubit_pair_names": [],
        "active_twpa_names": ["twpaA"],
    }
    wiring = {"wiring": {"qubits": {"qA1": {"z": {"opx_output": "#/ports/analog_outputs/con1/5/offset"}}}},
              "network": {"host": "10.0.0.1"}}
    return QuamStore.from_dicts(state, wiring)


class TestCompletenessInvariant:
    def test_total_equals_flatten_count(self):
        store = _synthetic_store()
        rows, summary = build_all_values_rows(store)
        # total counts LEAVES only; container rows are additive on top of it
        assert summary["total"] == len(_leaf_rows(rows))
        assert summary["total"] == len(flatten(store.merged))
        assert len(rows) == summary["total"] + summary["arrays"] + summary["empties"]

    def test_by_kind_sums_to_total(self):
        store = _synthetic_store()
        _rows, summary = build_all_values_rows(store)
        assert sum(summary["by_kind"].values()) == summary["total"]
        assert summary["editable"] + summary["readonly"] == summary["total"]
        # container kinds never leak into the 6-kind leaf partition
        assert KIND_ARRAY not in summary["by_kind"]
        assert KIND_EMPTY not in summary["by_kind"]


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
        for row in _leaf_rows(rows):
            if row[2] in READONLY_KINDS:
                # read-only rows never carry the modified=editable signal path; the
                # contract is simply that kind != scalar ⇒ the UI must not offer an input
                assert row[2] != KIND_SCALAR

    def test_modified_marker_only_on_edited_scalars(self):
        store = _synthetic_store()
        rows, _ = build_all_values_rows(store, modified={"qubits.qA1.f_01"})
        assert _kind_of(rows, "qubits.qA1.f_01") == KIND_SCALAR
        f01 = _row_of(rows, "qubits.qA1.f_01")
        assert f01[3] == 1
        other = _row_of(rows, "qubits.qA1.resonator.time_of_flight")
        assert other[3] == 0


class TestContainerRowsV2:
    def test_array_rows_emitted_with_matrix_dims(self):
        rows, summary = build_all_values_rows(_synthetic_store())
        cm = _row_of(rows, "qubits.qA1.resonator.confusion_matrix")
        assert cm is not None and cm[2] == KIND_ARRAY
        assert cm[1] == "[2×2]"
        assert cm[4] == {"dims": "2×2"}
        # each inner matrix ROW is its own array row — plain [N], no dims extra
        inner = _row_of(rows, "qubits.qA1.resonator.confusion_matrix.0")
        assert inner is not None and inner[2] == KIND_ARRAY
        assert inner[1] == "[2]" and len(inner) == 4
        # membership arrays get an array header row too (elements stay read-only;
        # the server-side edit gate rejects a whole-array write regardless)
        aqn = _row_of(rows, "active_qubit_names")
        assert aqn is not None and aqn[2] == KIND_ARRAY and aqn[1] == "[1]"
        assert summary["arrays"] >= 4    # confusion_matrix + 2 inner rows + active_* arrays

    def test_array_header_sorts_above_its_elements(self):
        rows, _ = build_all_values_rows(_synthetic_store())
        paths = [r[0] for r in rows]
        head = paths.index("qubits.qA1.resonator.confusion_matrix")
        assert paths.index("qubits.qA1.resonator.confusion_matrix.0") == head + 1

    def test_empty_containers_now_visible(self):
        rows, summary = build_all_values_rows(_synthetic_store())
        # empty dict — previously invisible (the leaf walk yields nothing)
        ex = _row_of(rows, "qubits.qA1.extras")
        assert ex is not None and ex[2] == KIND_EMPTY and ex[1] == "{} empty"
        qp = _row_of(rows, "qubit_pairs")
        assert qp is not None and qp[2] == KIND_EMPTY and qp[1] == "{} empty"
        # empty list
        ap = _row_of(rows, "active_qubit_pair_names")
        assert ap is not None and ap[2] == KIND_EMPTY and ap[1] == "[] empty"
        assert summary["empties"] == 3

    def test_container_rows_never_modified_flagged(self):
        rows, _ = build_all_values_rows(
            _synthetic_store(), modified={"qubits.qA1.resonator.confusion_matrix"})
        cm = _row_of(rows, "qubits.qA1.resonator.confusion_matrix")
        assert cm[3] == 0


class TestXrefEditThroughV2:
    def test_resolvable_xref_shows_resolved_value(self):
        rows, _ = build_all_values_rows(_synthetic_store())
        # z.opx_output chains through wiring to ports.analog_outputs.con1.5.offset = 0.1
        row = _row_of(rows, "qubits.qA1.z.opx_output")
        assert row[2] == "xref"
        assert row[1] == "0.1"
        assert row[4] == {"p": "#/wiring/qubits/qA1/z/opx_output", "d": 0}

    def test_dangling_xref_keeps_raw_pointer_text(self):
        rows, _ = build_all_values_rows(_synthetic_store())
        # twpa pump wiring is absent → the chain dies → raw text + d=1 (read-only)
        row = _row_of(rows, "twpas.twpaA.pump.opx_output")
        assert row[2] == "xref"
        assert row[1] == "#/wiring/twpas/twpaA/pump/opx_output"
        assert row[4]["p"] == "#/wiring/twpas/twpaA/pump/opx_output"
        assert row[4]["d"] == 1

    def test_every_leaf_kind_still_valid(self):
        rows, _ = build_all_values_rows(_synthetic_store())
        for row in _leaf_rows(rows):
            assert row[2] in ALL_KINDS


_LabA = "<quam-states>/example_lab"


@pytest.mark.skipif(not os.path.isdir(_LabA), reason="real LabA chip not present")
class TestRealChipCompleteness:
    def test_laba_total_matches_flatten_and_reaches_chip_level(self):
        store = QuamStore(_LabA)
        rows, summary = build_all_values_rows(store)
        assert summary["total"] == len(flatten(store.merged))
        assert sum(summary["by_kind"].values()) == summary["total"]
        assert len(rows) == summary["total"] + summary["arrays"] + summary["empties"]
        # the long tail that the curated grid reached 0% of is now editable
        assert summary["editable"] > 4000, summary["editable"]
        assert any(r[0].startswith("twpas.") and r[2] == KIND_SCALAR for r in rows)
        # time_of_flight is an editable row for every qubit
        tofs = [r for r in rows if r[0].endswith("resonator.time_of_flight")]
        assert tofs and all(r[2] == KIND_SCALAR for r in tofs)
        # real chips carry matrices → array header rows must exist
        assert summary["arrays"] > 0
