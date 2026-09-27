"""Differ.diff's tree walk returns exactly what the flatten-everything
reference returned (w7/livewrite, design F7 + "output parity").

The reference below is the pre-w7 ``Differ.diff`` body verbatim (flatten both,
natural-sort each bucket, final natural sort). Parity is checked list-equal,
ORDER INCLUDED, over randomized document pairs that exercise every trap the
tree walk has to respect: int vs float vs bool with equal values (``1 == 1.0
== True`` in Python), the json decoder's shared NaN object, +-inf, -0.0,
dict-vs-list and leaf-vs-container swaps, empty containers, list growth and
shrink, ignore keys, and keys that collide in dotted-path space.
"""
from __future__ import annotations

import json
import math
import random

import pytest

from quam_state_manager.core import differ as D
from quam_state_manager.core.differ import DiffEntry, Differ, _leaf_key, _values_equal
from quam_state_manager.core.loader import flatten, natural_key


def reference_diff(a, b, *, float_tolerance=1e-12, ignore_keys=None):
    """The pre-w7 implementation, verbatim."""
    flat_a = Differ._flatten_side(a)
    flat_b = Differ._flatten_side(b)
    ignore = ignore_keys if ignore_keys is not None else D._DEFAULT_IGNORE
    keys_a = set(flat_a.keys())
    keys_b = set(flat_b.keys())
    entries = []
    for key in sorted(keys_b - keys_a, key=natural_key):
        if _leaf_key(key) in ignore:
            continue
        entries.append(DiffEntry(key, None, flat_b[key], "added"))
    for key in sorted(keys_a - keys_b, key=natural_key):
        if _leaf_key(key) in ignore:
            continue
        entries.append(DiffEntry(key, flat_a[key], None, "removed"))
    for key in sorted(keys_a & keys_b, key=natural_key):
        if _leaf_key(key) in ignore:
            continue
        va, vb = flat_a[key], flat_b[key]
        if _values_equal(va, vb, float_tolerance):
            continue
        entries.append(DiffEntry(key, va, vb, "modified"))
    entries.sort(key=lambda e: natural_key(e.dot_path))
    return entries


def _same(x, y):
    """Entry lists equal, with NaN == NaN for the payload values only."""
    if len(x) != len(y):
        return False
    for e, f in zip(x, y):
        if (e.dot_path, e.change_type) != (f.dot_path, f.change_type):
            return False
        for u, v in ((e.old_value, f.old_value), (e.new_value, f.new_value)):
            if type(u) is not type(v):
                return False
            if isinstance(u, float) and math.isnan(u) and math.isnan(v):
                continue
            if u != v:
                return False
    return True


LEAVES = [0, 1, 1.0, True, False, None, -0.0, 0.0, 2.5, 2.5000000000001, "x", "1", "",
          float("inf"), float("-inf"), "NaN", 10**20, 1e-300]


def _leaf(r):
    if r.random() < 0.08:
        return json.loads("NaN")          # the decoder's SHARED nan object
    return r.choice(LEAVES)


def _key(r, dotted):
    ks = ["q1", "q2", "q10", "a", "b", "T1", "__class__", "0", "1", "x_1"]
    if dotted and r.random() < 0.05:
        return r.choice(["a.b", "q1.T1", "", "0.1"])
    return r.choice(ks)


def _tree(r, depth, dotted):
    t = r.random()
    if depth <= 0 or t < 0.3:
        return _leaf(r)
    if t < 0.65:
        return {_key(r, dotted): _tree(r, depth - 1, dotted) for _ in range(r.randint(0, 5))}
    return [_tree(r, depth - 1, dotted) for _ in range(r.randint(0, 4))]


def _mutate(r, obj, depth=0):
    """A copy of *obj* with a few random edits (value/type swaps, adds, drops)."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            u = r.random()
            if u < 0.05:
                continue                                  # drop
            out[k] = _mutate(r, v, depth + 1) if u < 0.6 else json.loads(json.dumps(v))
        if r.random() < 0.1:
            out[_key(r, True)] = _tree(r, 2, True)        # add
        return out
    if isinstance(obj, list):
        out = [_mutate(r, v, depth + 1) for v in obj]
        u = r.random()
        if u < 0.1 and out:
            out.pop()
        elif u < 0.2:
            out.append(_tree(r, 1, True))
        return out
    u = r.random()
    if u < 0.15:
        # same value, other type -- the ``1 == 1.0 == True`` trap
        if isinstance(obj, bool):
            return int(obj)
        if isinstance(obj, int):
            return float(obj)
        if isinstance(obj, float) and obj.is_integer() and not math.isinf(obj):
            return int(obj)
    if u < 0.3:
        return _leaf(r)
    if u < 0.35:
        return {"k": _leaf(r)} if r.random() < 0.5 else [_leaf(r)]   # leaf -> container
    return obj


@pytest.mark.parametrize("seed", range(400))
def test_tree_diff_equals_reference(seed):
    r = random.Random(seed)
    dotted = seed % 5 == 0
    a = {_key(r, dotted): _tree(r, 4, dotted) for _ in range(r.randint(1, 6))}
    # parse both sides from JSON like the app does (fresh objects, shared NaN)
    a = json.loads(json.dumps(a))
    b = json.loads(json.dumps(_mutate(r, a)))
    wa = {"wiring_" + str(seed % 3): _tree(r, 2, False)}
    for ign in (None, set(), {"T1"}):
        got = Differ().diff((a, wa), (b, wa), ignore_keys=ign)
        want = reference_diff((a, wa), (b, wa), ignore_keys=ign)
        assert _same(got, want), (seed, ign)


def test_shared_nan_object_is_still_reported_like_the_reference():
    a = json.loads('{"q": {"T1": NaN, "x": [1, NaN]}}')
    b = json.loads('{"q": {"T1": NaN, "x": [1, NaN]}}')
    assert a == b          # Python's == hides it (one shared nan object)
    got = Differ().diff((a, {}), (b, {}))
    assert _same(got, reference_diff((a, {}), (b, {})))
    assert [e.dot_path for e in got] == ["q.T1", "q.x.1"]


def test_int_float_bool_with_equal_values_are_modified_like_the_reference():
    a = {"q": {"a": 1, "b": 1.0, "c": True, "d": [0]}}
    b = {"q": {"a": 1.0, "b": True, "c": 1, "d": [False]}}
    assert a == b
    got = Differ().diff((a, {}), (b, {}))
    assert _same(got, reference_diff((a, {}), (b, {})))
    assert len(got) == 4


def test_a_dotted_key_falls_back_to_the_reference(monkeypatch):
    calls = []
    real = Differ._diff_flat
    monkeypatch.setattr(Differ, "_diff_flat", staticmethod(
        lambda *a: calls.append(1) or real(*a)))
    a = {"q": {"a.b": 1}, "r": 2}
    b = {"q": {"a": {"b": 1}}, "r": 3}
    got = Differ().diff((a, {}), (b, {}))
    assert calls, "a dotted key must route to the reference algorithm"
    assert _same(got, reference_diff((a, {}), (b, {})))


def test_the_walk_skips_identical_subtrees():
    """The point of it: a one-leaf change never flattens the rest."""
    big = {f"q{i}": {"x": list(range(50)), "f": float(i)} for i in range(200)}
    a = json.loads(json.dumps(big))
    b = json.loads(json.dumps(big))
    b["q7"]["f"] = 7.5
    seen = []
    real = D._flat_under
    D._flat_under = lambda o, p: seen.append(p) or real(o, p)
    try:
        got = Differ().diff((a, {}), (b, {}))
    finally:
        D._flat_under = real
    assert [e.dot_path for e in got] == ["q7.f"]
    assert seen == []
