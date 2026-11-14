"""One interleaved-RB value per gate when several per-gate keys hold one.

The resolver (``query.pair_gate_fidelity``) and every surface reading it are
pinned in ``test_pair_gate_fidelity.py``. This file pins the counter-examples
around it: a non-number never outranks a number, two CZ variants of one pair
never borrow each other's keys, and StandardRB never joins the per-gate set.
"""
import math

import pytest

from quam_state_manager.core import query as Q
from tests.test_topology_pairs import _chip, _edge


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), "0.99", None])
def test_a_non_number_never_wins_over_a_number(bad):
    r = Q.pair_gate_fidelity({"InterleavedRB": bad, "IRB": 0.9434})
    assert r["key"] == "IRB" and r["value"] == pytest.approx(0.9434)
    assert r["keys"] == ["IRB"] and r["conflict"] == []


def test_a_nested_nan_never_wins_either():
    r = Q.pair_gate_fidelity({"InterleavedRB": {"value": float("nan")}, "IRB": 0.95})
    assert r["key"] == "IRB"


def test_only_non_numbers_is_no_value():
    assert Q.pair_gate_fidelity({"InterleavedRB": float("nan"), "IRB": "x"}) is None


def test_the_node_key_wins_without_a_recency_and_the_other_is_listed():
    r = Q.pair_gate_fidelity({"InterleavedRB": 0.9763, "IRB": 0.9434})
    assert (r["key"], r["value"]) == ("InterleavedRB", 0.9763)
    assert r["conflict"] == [("InterleavedRB", 0.9763), ("IRB", 0.9434)]


def test_the_newer_run_wins():
    r = Q.pair_gate_fidelity({"InterleavedRB": 0.9763, "InterleavedRB_load_id": 5,
                              "IRB": 0.9434, "IRB_load_id": 9})
    assert r["key"] == "IRB" and r["basis"] == "newest run"


def test_standard_rb_is_never_a_candidate():
    r = Q.pair_gate_fidelity({"StandardRB": 0.99, "IRB": 0.94})
    assert r["keys"] == ["IRB"]


def test_two_variants_on_one_pair_do_not_cross():
    a = Q.pair_gate_fidelity({"InterleavedRB": 0.97})
    b = Q.pair_gate_fidelity({"IRB": 0.93})
    assert (a["key"], a["value"]) == ("InterleavedRB", 0.97)
    assert (b["key"], b["value"]) == ("IRB", 0.93)


def test_the_edge_ignores_a_nan_key(tmp_path):
    e = _edge(_chip(tmp_path, {"InterleavedRB": float("nan"), "IRB": 0.9434}))
    assert e["cz_fidelity"] == pytest.approx(0.9434)
    assert e["cz_fidelity_path"].endswith(".fidelity.IRB")
    assert not math.isnan(e["cz_fidelity"])
