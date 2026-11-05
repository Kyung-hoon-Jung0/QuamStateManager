import copy
from unittest.mock import patch
from quam_state_manager.core import regen_merge
from tests.test_leftover_minors_regen_pairkey import pair


def test_aligned_pairs_do_not_copy():
    source = {"qubit_pairs": {"q2-q1": pair("q2", "q1")}}
    fresh = copy.deepcopy(source)
    with patch.object(regen_merge.copy, "deepcopy", side_effect=AssertionError("unnecessary copy")):
        assert regen_merge._reconcile_pair_ids(source, fresh) is fresh


def test_alias_only_normalization_copies_without_mutating_input():
    source = {"qubit_pairs": {"q2-q1": pair("q2", "q1")}}
    source["qubit_pairs"]["q2-q1"]["macros"]["cz"] = "#./cz_SNZ"
    fresh = copy.deepcopy(source)
    fresh["qubit_pairs"]["q3-q2"] = pair("q3", "q2")
    result = regen_merge._reconcile_pair_ids(source, fresh)
    assert result is not fresh
    assert result["qubit_pairs"]["q3-q2"]["macros"]["cz"] == "#./cz_SNZ"
    assert "cz" not in fresh["qubit_pairs"]["q3-q2"]["macros"]
