"""RAM P10: SearchIndex.build was rewritten for speed (one pass, per-DISTINCT
string work, shared string copies). It must produce EXACTLY what the per-leaf
original produced -- entries field by field, path_to_idx, every prefix list
(duplicates and order included) and the inverted indexes -- and searches over
the two must agree. The original is kept here verbatim as the reference.
"""
from __future__ import annotations

import math
import random
from pathlib import Path

import pytest

from quam_state_manager.core import search_index as si
from quam_state_manager.core.loader import _walk


# ---------------------------------------------------------------- reference
def _ref_build_prefix_map(index) -> None:
    pm: dict[str, list[int]] = {}
    for idx, entry in enumerate(index.entries):
        for s in (entry.value_str, entry.leaf_key.lower(), entry.parent_id.lower()):
            for prefix in si._prefixes(s):
                if prefix not in pm:
                    pm[prefix] = []
                pm[prefix].append(idx)
    for key in pm:
        pm[key].sort()
    index.prefix_map = pm


def _ref_build(merged: dict, wiring_keys=None):
    if wiring_keys is None:
        wiring_keys = {"wiring", "network"}
    index = si.SearchIndex()
    for dot_path, value, _path_tuple in _walk(merged):
        category = si._categorize(dot_path)
        parent_id = si._extract_parent_id(dot_path, category)
        leaf_key = dot_path.rsplit(".", 1)[-1]
        top_key = dot_path.split(".", 1)[0]
        source = "wiring" if top_key in wiring_keys else "state"
        value_str = str(value).lower() if value is not None else "none"
        entry = si.IndexEntry(dot_path=dot_path, value_str=value_str, raw_value=value,
                              category=category, parent_id=parent_id,
                              leaf_key=leaf_key, source_file=source)
        idx = len(index.entries)
        index.entries.append(entry)
        index.path_to_idx[dot_path] = idx
    _ref_build_prefix_map(index)
    index._trigram_built = False
    si._build_inverted_indexes(index)
    return index


# ------------------------------------------------------------------ helpers
def _entry_tuple(e):
    rv = e.raw_value
    # nan != nan: compare its identity class instead of its value
    rv_key = ("nan",) if isinstance(rv, float) and math.isnan(rv) else (type(rv), rv)
    return (e.dot_path, e.value_str, rv_key, e.category, e.parent_id, e.leaf_key, e.source_file)


def _assert_same(new, ref):
    assert [_entry_tuple(e) for e in new.entries] == [_entry_tuple(e) for e in ref.entries]
    assert new.path_to_idx == ref.path_to_idx
    assert new.prefix_map == ref.prefix_map
    assert new.key_index == ref.key_index
    assert new.category_index == ref.category_index
    assert new.parent_index == ref.parent_index
    assert new._trigram_built == ref._trigram_built


_KEYS = ["qubits", "qubit_pairs", "twpas", "ports", "wiring", "network", "macros",
         "q1", "q10", "Q2", "qA1-2", "xy", "z", "resonator", "operations", "x180",
         "amplitude", "length", "a.b", "", "0", "RF_frequency", "mw_outputs", "con1", "1"]
_SCALARS = [0, 1, -1, 1.0, -0.0, 0.0, True, False, None, "True", "1", "none", "None",
            float("nan"), float("inf"), 1e-9, 123456789, 2**70, 4.5e9, "#/qubits/q1/xy",
            "Q1", "q1", "AmPlItUdE", "", "x" * 12, 0.1 + 0.2]


def _rand_doc(rng: random.Random, depth: int = 0):
    if depth >= 5 or rng.random() < 0.2:
        return rng.choice(_SCALARS)
    if rng.random() < 0.25:
        return [_rand_doc(rng, depth + 1) for _ in range(rng.randint(0, 4))]
    return {rng.choice(_KEYS): _rand_doc(rng, depth + 1) for _ in range(rng.randint(0, 5))}


# -------------------------------------------------------------------- pins
@pytest.mark.parametrize("seed", range(60))
def test_random_documents_build_identically(seed):
    rng = random.Random(seed)
    doc = {k: _rand_doc(rng, 1) for k in rng.sample(_KEYS, rng.randint(1, 8))}
    wk = set(rng.sample(list(doc), rng.randint(0, len(doc))))
    new, ref = si.SearchIndex.build(doc, wiring_keys=wk), _ref_build(doc, wiring_keys=wk)
    _assert_same(new, ref)
    # and the two indexes answer every query the same way, incremental edits included
    for q in ("q1", "qu", "amp", "none", "1", "0.0", "-0", "true", "wiring", "x180 amp",
              "q1 | q10", "nan", "inf", "#/q"):
        assert [(r.dot_path, r.score) for r in new.search(q, limit=500)] == \
               [(r.dot_path, r.score) for r in ref.search(q, limit=500)], q
    for dp in list(ref.path_to_idx)[:5]:
        new.update_entry(dp, "edited")
        ref.update_entry(dp, "edited")
    _assert_same(new, ref)


def test_float_zero_signs_and_equal_keys_of_different_types():
    """(1, 1.0, True) are equal dict keys and 0.0 == -0.0 -- a value-string
    memo keyed on the value alone would print the first one for all."""
    doc = {"a": [0.0, -0.0, 1, 1.0, True, 0, False, -0.0, 0.0, 1.0, True, 1]}
    new, ref = si.SearchIndex.build(doc), _ref_build(doc)
    _assert_same(new, ref)
    assert [e.value_str for e in new.entries] == \
        ["0.0", "-0.0", "1", "1.0", "true", "0", "false", "-0.0", "0.0", "1.0", "true", "1"]


def test_dotted_keys_and_deep_port_paths():
    doc = {"ports": {"mw_outputs": {"con1": {"1": {"2": {"x.y": 1, "z": {"w": 2}}}}}},
           "wiring": {"qubits": {"q1": {"xy": {"opx_output": "#/ports/x"}}}, "a.b": 3},
           "qubits.q9": {"f": 1}, "": {"": 5, "k": {"": 6}}}
    _assert_same(si.SearchIndex.build(doc, wiring_keys={"wiring"}),
                 _ref_build(doc, wiring_keys={"wiring"}))


_REAL = Path(r"D:\work\Customer_Codes\quam_states\260907_KRS_5Q")


@pytest.mark.skipif(not (_REAL / "state.json").exists(), reason="real chip absent")
def test_real_chip_builds_identically():
    from quam_state_manager.core.loader import QuamStore
    store = QuamStore(_REAL)
    wk = set(store.wiring.keys())
    _assert_same(si.SearchIndex.build(store.merged, wiring_keys=wk),
                 _ref_build(store.merged, wiring_keys=wk))
