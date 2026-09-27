"""leaf_patch.leaf_changes == the uncapped json_diff.flatten comparison.

w7/livewrite. ``_sync_patch`` compares the flattened documents before and
after a pull; for a chip over the flatten cap it now asks
``leaf_patch.leaf_changes`` instead. Pinned here against the reference
comparison itself -- the exact loop ``_sync_patch`` runs -- over randomized
documents and randomized edit sequences, including the leaves where ``==``
and ``!=`` disagree with intuition (the decoder's one shared NaN object,
``1`` / ``1.0`` / ``True``, empty containers, a dict turning into a list).
"""
import copy
import json
import random

import pytest

from quam_state_manager.core import json_diff
from quam_state_manager.core.leaf_patch import leaf_changes

NAN = json.loads("NaN")          # the decoder hands out ONE shared NaN object


def _reference(before: dict, after: dict):
    b, tb = json_diff.flatten(before, cap=10 ** 9)
    a, ta = json_diff.flatten(after, cap=10 ** 9)
    assert not tb and not ta
    structural = any(p not in a for p in b) or any(p not in b for p in a)
    changes = {p: v for p, v in a.items() if p in b and b[p] != v}
    return changes, structural


def _leaf(r):
    return r.choice([0, 1, 1.0, True, False, None, "x", "", 2.5, -0.0, NAN,
                     float("inf"), [], {}, "#/qubits/q1/xy", r.random()])


def _tree(r, depth):
    if depth <= 0 or r.random() < 0.3:
        return _leaf(r)
    if r.random() < 0.7:
        return {f"k{i}": _tree(r, depth - 1) for i in range(r.randint(0, 4))}
    return [_tree(r, depth - 1) for _ in range(r.randint(0, 4))]


def _doc(r):
    return {f"t{i}": _tree(r, 4) for i in range(r.randint(1, 5))} | {"anchor": 1}


def _containers(node, out, path=()):
    if isinstance(node, dict):
        out.append((node, path))
        for k, v in node.items():
            _containers(v, out, path + (k,))
    elif isinstance(node, list):
        out.append((node, path))
        for i, v in enumerate(node):
            _containers(v, out, path + (i,))
    return out


def _mutate(r, doc):
    cs = _containers(doc, [])
    c, _ = r.choice(cs)
    op = r.random()
    if isinstance(c, dict):
        if c and op < 0.5:
            k = r.choice(list(c))
            old = c[k]
            c[k] = (float(old) if type(old) is int else
                    (True if old == 1 and type(old) is not bool else _tree(r, 2)))
        elif c and op < 0.7:
            del c[r.choice(list(c))]
        else:
            c[f"n{r.randint(0, 9)}"] = _tree(r, 2)
    else:
        if c and op < 0.5:
            i = r.randrange(len(c))
            c[i] = _tree(r, 2)
        elif c and op < 0.7:
            c.pop()
        else:
            c.append(_leaf(r))


@pytest.mark.parametrize("seed", range(300))
def test_random_edit_sequences_match_the_flatten_comparison(seed):
    r = random.Random(seed)
    before = _doc(r)
    after = copy.deepcopy(before)
    # deepcopy keeps the ONE NaN object shared, as two parses would
    for _ in range(r.randint(0, 6)):
        _mutate(r, after)
    got = leaf_changes(before, after)
    assert got is not None
    changes, structural = got
    assert len({p for p, _ in changes}) == len(changes), "a path reported twice"
    ref_changes, ref_structural = _reference(before, after)
    assert structural == ref_structural, seed
    assert {p for p, _ in changes} == set(ref_changes), seed
    for p, v in changes:
        assert json.dumps(v) == json.dumps(ref_changes[p]), (seed, p)


def test_an_identical_document_is_no_change_except_its_nans():
    d = {"q": {"a": 1, "n": NAN, "l": [1.0, {"x": True}]}}
    changes, structural = leaf_changes(d, copy.deepcopy(d))
    assert not structural
    # NaN != NaN for the flatten loop too, even for the very same object
    assert [p for p, _ in changes] == ["q.n"] == list(_reference(d, copy.deepcopy(d))[0])


def test_int_float_bool_are_compared_like_the_loop():
    b = {"q": {"a": 1, "b": 1, "c": 0}}
    a = {"q": {"a": 1.0, "b": True, "c": False}}
    assert leaf_changes(b, a) == ([], False) and _reference(b, a) == ({}, False)


@pytest.mark.parametrize("bad", [{"a.b": 1}, {"": 1}, {1: 2}])
def test_an_ambiguous_path_grammar_is_refused(bad):
    b = {"q": {"x": 1, **bad}}
    a = {"q": {"x": 2, **bad}}
    assert leaf_changes(b, a) is None


def test_it_stops_past_max_changes():
    b = {"q": {f"k{i}": i for i in range(50)}}
    a = {"q": {f"k{i}": i + 1 for i in range(50)}}
    changes, structural = leaf_changes(b, a, max_changes=10)
    assert structural and len(changes) == 11
