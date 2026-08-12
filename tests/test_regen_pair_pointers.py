"""A pair's control/target is a POINTER, and it can be two hops (docs/118).

Found by the two-chip audit on a real 10-qubit / 9-tunable-coupler chip built
by a modern quam_builder. It stores the pair's membership as::

    qubit_pairs.coupler_q1_q2.qubit_control
        -> "#/wiring/qubit_pairs/q1-2/c/control_qubit"
        -> "#/qubits/q2"

Both `regen_spec` and `regen_merge` read that with `str(ref).split("/")[-1]`,
which is right only for a ONE-hop `#/qubits/qX`. On this chip the last segment
is the literal field name `control_qubit`, so:

  * every pair was dropped from the reconstructed spec with the false message
    "references qubit(s) not on this chip: control_qubit, target_qubit",
  * `populate.pairs` came back empty,
  * pair-id reconciliation matched nothing, so the merge orphaned every pair's
    calibration.

And the build still SUCCEEDED — measured on the real chip: 1,878 pair leaves in
the source, 774 in the rebuild, reported as `residual_lost: 200` because the
list is capped at 200 with no total. Silent degradation is the worst failure
mode a rebuild can have, which is why both halves are pinned here.
"""

from __future__ import annotations

from quam_state_manager.core import regen_merge, regen_spec

# The shape the real chip uses.
_STATE = {
    "qubits": {
        "q1": {"id": "q1"}, "q2": {"id": "q2"}, "q3": {"id": "q3"},
    },
    "qubit_pairs": {
        "coupler_q1_q2": {
            "id": "coupler_q1_q2",
            "qubit_control": "#/wiring/qubit_pairs/q1-2/c/control_qubit",
            "qubit_target": "#/wiring/qubit_pairs/q1-2/c/target_qubit",
        },
        # a one-hop pair, to prove the old shape is untouched
        "q2-3": {
            "id": "q2-3",
            "qubit_control": "#/qubits/q2",
            "qubit_target": "#/qubits/q3",
        },
    },
}
_WIRING = {
    "network": {"host": "1.1.1.1", "cluster_name": "C1"},
    "wiring": {
        "qubit_pairs": {
            "q1-2": {"c": {"control_qubit": "#/qubits/q2",
                           "target_qubit": "#/qubits/q1"}},
        },
    },
}


def _root():
    doc = dict(_STATE)
    doc["wiring"] = _WIRING["wiring"]
    return doc


class TestQubitRefName:
    def test_two_hop_pointer_names_the_qubit(self):
        root = _root()
        p = _STATE["qubit_pairs"]["coupler_q1_q2"]
        assert regen_spec.qubit_ref_name(root, p["qubit_control"]) == "q2"
        assert regen_spec.qubit_ref_name(root, p["qubit_target"]) == "q1"

    def test_one_hop_is_unchanged(self):
        assert regen_spec.qubit_ref_name(_root(), "#/qubits/q3") == "q3"

    def test_a_plain_name_passes_through(self):
        assert regen_spec.qubit_ref_name(_root(), "q7") == "q7"

    def test_nothing_is_invented(self):
        assert regen_spec.qubit_ref_name(_root(), None) == ""
        assert regen_spec.qubit_ref_name(_root(), "") == ""

    def test_a_cycle_cannot_hang(self):
        root = dict(_STATE)
        root["wiring"] = {"a": "#/wiring/b", "b": "#/wiring/a"}
        # terminates and answers with the last segment it reached
        assert regen_spec.qubit_ref_name(root, "#/wiring/a") in {"a", "b"}


class TestReconstruct:
    def test_every_pair_survives_reconstruction(self):
        spec = regen_spec.reconstruct_spec(_STATE, _WIRING)
        d = spec.spec if hasattr(spec, "spec") else spec
        pairs = d.get("qubit_pairs") or []
        assert len(pairs) == 2, pairs
        assert ["q2", "q1"] in pairs, pairs      # the TWO-hop pair, resolved
        assert ["q2", "q3"] in pairs, pairs      # the one-hop pair, unchanged

    def test_no_false_missing_qubit_note(self):
        spec = regen_spec.reconstruct_spec(_STATE, _WIRING)
        notes = " ".join(str(n) for n in (getattr(spec, "notes", None) or []))
        assert "control_qubit" not in notes, notes
        assert "target_qubit" not in notes, notes


class TestPairIdReconciliation:
    """The merge must recognise the SAME pair under a different id."""

    def test_membership_follows_two_hop_refs(self):
        doc = _root()
        m = regen_merge._pair_membership(_STATE["qubit_pairs"]["coupler_q1_q2"], doc)
        assert m == ("q2", "q1")

    def test_without_a_document_it_degrades_to_the_old_answer(self):
        m = regen_merge._pair_membership(_STATE["qubit_pairs"]["coupler_q1_q2"])
        assert m == ("control_qubit", "target_qubit")

    def test_the_new_build_id_is_renamed_onto_the_source_id(self):
        new_state = {
            "qubits": {"q1": {}, "q2": {}},
            "qubit_pairs": {
                "q2-1": {"id": "q2-1",
                         "qubit_control": "#/qubits/q2",
                         "qubit_target": "#/qubits/q1",
                         "macros": {"cz_unipolar": {}}},
            },
        }
        merged = regen_merge.merge_states(
            _STATE, new_state, old_wiring=_WIRING, new_wiring=None)
        ids = set((merged.merged.get("qubit_pairs") or {}).keys())
        assert "coupler_q1_q2" in ids, ids
        assert "q2-1" not in ids, ids


class TestCappedListsAreHonest:
    def test_the_report_carries_the_true_totals(self):
        import inspect

        from quam_state_manager.core import regenerate
        src = inspect.getsource(regenerate)
        for key in ("residual_lost_total", "dangling_grafts_total",
                    "superseded_paths_total", "schema_dropped_paths_total"):
            assert key in src, key

    def test_the_panel_prefers_the_total_over_the_capped_list(self):
        from pathlib import Path
        js = (Path(__file__).resolve().parent.parent / "quam_state_manager" / "web"
              / "static" / "generate.js").read_text(encoding="utf-8")
        assert "m.residual_lost_total" in js
        assert "m.dangling_grafts_total" in js
