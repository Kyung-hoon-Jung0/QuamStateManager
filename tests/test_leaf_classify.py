"""Unit tests for core.leaf_classify — the single source of truth that partitions
every flattened leaf into one policy kind. These pin the data-safety rules the
'All values' completeness surface stands on (no pointer/self-ref/list/membership
leaf is ever editable) and the structural ``in_list`` distinction that keeps
port-number dict keys editable instead of mis-read as list elements."""

from __future__ import annotations

from quam_state_manager.core.leaf_classify import (
    KIND_LIST,
    KIND_MEMBERSHIP,
    KIND_SCALAR,
    KIND_SELFREF,
    KIND_SKIP,
    KIND_XREF,
    classify_leaf,
    is_editable,
)


class TestClassifyLeaf:
    def test_plain_scalar(self):
        assert classify_leaf("qubits", "f_01", 5.0e9, False) == KIND_SCALAR
        assert classify_leaf("qubits", "time_of_flight", 376, False) == KIND_SCALAR
        assert classify_leaf("qubits", "flux_point", "joint", False) == KIND_SCALAR
        assert classify_leaf("qubits", "f_12", None, False) == KIND_SCALAR  # null is still settable

    def test_identity_and_type_keys_skip(self):
        assert classify_leaf("qubits", "__class__", "quam.components.Transmon", False) == KIND_SKIP
        assert classify_leaf("qubits", "id", "qA1", False) == KIND_SKIP

    def test_digital_marker_is_a_real_value_not_identity(self):
        # Moved OUT of SKIP_LEAVES (2026-07-18): real chips carry null / "ON" /
        # pointer digital_markers — it classifies by value and is editable.
        assert classify_leaf("qubits", "digital_marker", "ON", False) == KIND_SCALAR
        assert classify_leaf("qubits", "digital_marker", None, False) == KIND_SCALAR
        assert classify_leaf("qubits", "digital_marker",
                             "#../x180_DragCosine/digital_marker", False) == KIND_XREF

    def test_cross_ref_pointer_is_xref(self):
        assert classify_leaf("qubits", "opx_output", "#/wiring/qubits/qA1/z/opx_output", False) == KIND_XREF
        assert classify_leaf("qubits", "thing", "#../sibling/field", False) == KIND_XREF

    def test_self_ref_pointer_is_selfref(self):
        assert classify_leaf("qubits", "intermediate_frequency",
                             "#./inferred_intermediate_frequency", False) == KIND_SELFREF
        assert classify_leaf("macros", "duration", "#./inferred_duration", False) == KIND_SELFREF

    def test_list_element_via_in_list(self):
        # a leaf reached by descending through a real JSON list — read-only regardless of value
        assert classify_leaf("qubits", "0", 0.99, True) == KIND_LIST
        assert classify_leaf("qubits", "1", "#/whatever", True) == KIND_LIST  # in_list wins over pointer

    def test_port_number_dict_keys_stay_scalar(self):
        # THE structural fix: ports.mw_outputs.con1.1.2.band — "1"/"2" are FEM/port
        # NUMBER dict keys, NOT list indices, so in_list is False → editable scalar.
        assert classify_leaf("ports", "band", 2, False) == KIND_SCALAR
        assert classify_leaf("ports", "upconverter_frequency", 5.05e9, False) == KIND_SCALAR

    def test_membership_arrays_win_over_list(self):
        # active_* arrays flatten to <arr>.<idx> (in_list True) but get their own
        # read-only-with-warning policy, which must win over the generic list rule.
        assert classify_leaf("active_qubit_names", "0", "qA1", True) == KIND_MEMBERSHIP
        assert classify_leaf("active_qubit_pair_names", "3", "qA1-qA2", True) == KIND_MEMBERSHIP
        assert classify_leaf("active_twpa_names", "0", "twpaA", True) == KIND_MEMBERSHIP

    def test_only_scalar_is_editable(self):
        assert is_editable(KIND_SCALAR) is True
        for k in (KIND_XREF, KIND_SELFREF, KIND_LIST, KIND_SKIP, KIND_MEMBERSHIP):
            assert is_editable(k) is False, f"{k} must NOT be editable"

class TestTheClientGetsTheSameVocabulary:
    """``readonly_policy`` is what the Json Tree applies in the browser.

    The tree builds every row client-side, so it cannot ask
    ``editability_reason`` per leaf — it gets the vocabulary and reaches the
    same verdict. These pins exist so the payload can never drift from the
    sets and reasons the write doors actually enforce: a drift would put the
    tree back where it was, offering an edit box on a leaf the door refuses.
    """

    def test_the_payload_carries_the_real_sets(self):
        from quam_state_manager.core.leaf_classify import (
            MEMBERSHIP_TOPS, SKIP_LEAVES, readonly_policy)
        pol = readonly_policy()
        assert set(pol["membership_tops"]) == set(MEMBERSHIP_TOPS)
        assert set(pol["skip_leaves"]) == set(SKIP_LEAVES)

    def test_the_reasons_are_the_doors_own_words(self):
        # editability_reason is what /field/edit answers; _crud_policy_reason is
        # what /field/create and /field/delete answer. One spelling, three users.
        from quam_state_manager.core.edit_policy import editability_reason
        from quam_state_manager.core.leaf_classify import readonly_policy
        pol = readonly_policy()

        class _Store:
            merged = {}

        assert editability_reason(_Store(), "active_qubit_names.0") == pol["membership_reason"]
        assert editability_reason(_Store(), "qubits.q1.__class__") == pol["skip_reason"]

    def test_a_path_the_doors_allow_is_not_in_the_payload(self):
        # The payload must not over-refuse either — the tree would then hide an
        # editor the server would have accepted.
        from quam_state_manager.core.leaf_classify import readonly_policy
        pol = readonly_policy()
        assert "qubits" not in pol["membership_tops"]
        assert "f_01" not in pol["skip_leaves"]
