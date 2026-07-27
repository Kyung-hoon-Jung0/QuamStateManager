"""Natural (numeric-aware) ordering of qubit/pair ids.

THE bug: labs numbering qubits q1…q11 got lexicographic listings — q1, q10,
q11, q2, … — both in the DEFAULT order (loader.qubit_names was a plain
sorted()) and when pressing a table sort header (plain string comparators in
app.js / bulk-edit.js / pair-edit.js). Chips with letter+single-digit ids
(qA1…qD2) masked it, since natural == lexicographic there.
"""

from __future__ import annotations

import json
from pathlib import Path

from quam_state_manager.core.loader import QuamStore, natural_key
from quam_state_manager.web import routes as routes_mod

_STATIC = Path(routes_mod.__file__).resolve().parent / "static"


class TestNaturalKey:
    def test_double_digit_qubits_order_numerically(self):
        ids = ["q1", "q10", "q11", "q2", "q3", "q9"]
        assert sorted(ids, key=natural_key) == ["q1", "q2", "q3", "q9", "q10", "q11"]

    def test_lettered_ids_keep_their_order(self):
        ids = ["qA1", "qA2", "qB1", "qA10"]
        assert sorted(ids, key=natural_key) == ["qA1", "qA2", "qA10", "qB1"]

    def test_pair_ids(self):
        ids = ["q1-q2", "q10-q11", "q2-q3"]
        assert sorted(ids, key=natural_key) == ["q1-q2", "q2-q3", "q10-q11"]

    def test_mixed_shapes_never_raise(self):
        # digit-leading, empty, case drift — tuple positions stay type-aligned
        ids = ["1a", "a1", "", "Q2", "q10"]
        out = sorted(ids, key=natural_key)
        assert set(out) == set(ids)
        assert out.index("Q2") < out.index("q10")


class TestLoaderOrdering:
    def test_qubit_names_are_naturally_sorted(self, tmp_path):
        qubits = {f"q{i}": {"id": f"q{i}", "f_01": 5e9 + i} for i in (1, 2, 3, 10, 11)}
        (tmp_path / "state.json").write_text(json.dumps(
            {"qubits": qubits,
             "qubit_pairs": {"q10-q11": {}, "q1-q2": {}, "q2-q3": {}}}), encoding="utf-8")
        (tmp_path / "wiring.json").write_text(json.dumps({"wiring": {}}), encoding="utf-8")
        store = QuamStore(tmp_path)
        assert store.qubit_names == ["q1", "q2", "q3", "q10", "q11"]
        assert store.qubit_pair_names == ["q1-q2", "q2-q3", "q10-q11"]


class TestClientComparatorsPinned:
    """The three client sorters must use localeCompare numeric:true — a plain
    string comparator regressing here re-introduces q1, q10, q11, q2."""

    def test_generic_sortable_tables(self):
        js = (_STATIC / "app.js").read_text(encoding="utf-8")
        assert "numeric: true" in js

    def test_bulk_grid_id_sort(self):
        js = (_STATIC / "bulk-edit.js").read_text(encoding="utf-8")
        assert "numeric: true" in js

    def test_pair_grid_id_sort(self):
        js = (_STATIC / "pair-edit.js").read_text(encoding="utf-8")
        assert "numeric: true" in js
