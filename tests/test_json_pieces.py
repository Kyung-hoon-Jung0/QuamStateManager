"""json_pieces renders byte-identical text to the stdlib (w7/livewrite).

The composed output is compared with ``json.dumps`` itself -- for random
documents (every leaf type, unicode, NaN/inf, empty containers, 1/1.0/True
swaps between renders) and for real chips when they are on this machine --
and the cache is exercised across a randomized edit sequence, so a piece
served from RAM for a CHANGED subtree would show up as a mismatch.
"""
from __future__ import annotations

from tests.archive_roots import lab_path

import hashlib
import json
import os
import random

import pytest

from quam_state_manager.core import json_pieces as JP
from quam_state_manager.core import working_copy

LEAVES = [0, 1, 1.0, True, False, None, -0.0, 2.5, 1e-05, 1e300, -7, "x", "", "é⚡\n\"\\",
          "\u2028", float("inf"), float("-inf"), 10**30, [], {}]


def _leaf(r):
    if r.random() < 0.05:
        return float("nan")
    return r.choice(LEAVES)


def _tree(r, depth):
    t = r.random()
    if depth <= 0 or t < 0.3:
        return _leaf(r)
    if t < 0.7:
        return {r.choice(["q1", "q2", "q10", "é", "a b", "0", "T1", "__class__", ""]) + str(r.randint(0, 3)):
                _tree(r, depth - 1) for _ in range(r.randint(0, 5))}
    return [_tree(r, depth - 1) for _ in range(r.randint(0, 4))]


def _doc(r):
    return {f"k{r.randint(0, 9)}{c}": _tree(r, 4) for c in "abcdef"[: r.randint(0, 6)]}


def _mutate_leaf(r, doc):
    """Edit one leaf in place, sometimes to an equal value of another type."""
    node = doc
    path = []
    while isinstance(node, (dict, list)) and node:
        k = r.choice(list(node.keys())) if isinstance(node, dict) else r.randrange(len(node))
        path.append((node, k))
        node = node[k]
    if not path:
        doc["new"] = _leaf(r)
        return
    parent, k = path[-1]
    old = parent[k]
    if isinstance(old, bool):
        parent[k] = int(old)
    elif isinstance(old, int):
        parent[k] = float(old)
    else:
        parent[k] = _tree(r, 2)


@pytest.mark.parametrize("seed", range(150))
def test_random_documents_render_like_the_stdlib(seed):
    r = random.Random(seed)
    doc = _doc(r)
    for step in range(6):
        for ind in (4, 2, "\t"):
            assert JP.dumps_indent(doc, ind) == json.dumps(doc, indent=ind, ensure_ascii=False), (seed, step, ind)
            both = JP.dumps_indent_and_canonical(doc, ind)
            assert both == (json.dumps(doc, indent=ind, ensure_ascii=False),
                            json.dumps(doc, sort_keys=True, separators=(",", ":"))), (seed, step, ind)
        assert JP.canonical(doc) == json.dumps(doc, sort_keys=True, separators=(",", ":")), (seed, step)
        w = {"wiring": doc.get("k1a", 1)}
        assert JP.content_hash_pair(doc, w) == hashlib.sha256(
            json.dumps([doc, w], sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        _mutate_leaf(r, doc)          # the NEXT render must see this edit


def test_non_str_keys_and_subclasses_fall_back_to_the_stdlib():
    class D(dict):
        pass
    doc = {"a": {1: "x", "b": 2}, "c": D(z=1)}
    assert JP.dumps_indent(doc, 4) == json.dumps(doc, indent=4, ensure_ascii=False)
    assert JP.canonical({"a": {"b": D(z=1)}}) == json.dumps({"a": {"b": D(z=1)}}, sort_keys=True, separators=(",", ":"))


def test_content_hash_is_unchanged():
    """working_copy.content_hash keeps its value (persisted in every meta)."""
    s = {"qubits": {"q1": {"f": 5.1e9, "T1": 1e-05, "ok": True}}, "b": [1, 2.0, None]}
    w = {"wiring": {"q1": {"xy": "#/ports/1"}}}
    assert working_copy.content_hash(s, w) == hashlib.sha256(
        json.dumps([s, w], sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


_REAL = [p for p in (
    lab_path("lab-F") / "state.json",
    r"D:\work\sm_qa_rigs\_shared\bigstate\big20\state.json",
) if os.path.exists(p)]


@pytest.mark.skipif(not _REAL, reason="no real chip on this machine")
@pytest.mark.parametrize("path", _REAL)
def test_real_chip_renders_like_the_stdlib(path):
    doc = json.load(open(path, encoding="utf-8"))
    for ind in (4, 2):
        assert JP.dumps_indent(doc, ind) == json.dumps(doc, indent=ind, ensure_ascii=False)
    assert JP.canonical(doc) == json.dumps(doc, sort_keys=True, separators=(",", ":"))
    # an edit deep in one qubit: every other piece is served from RAM
    q = next(iter(doc["qubits"]))
    doc["qubits"][q]["__w7_probe"] = 1.25
    assert JP.dumps_indent(doc, 4) == json.dumps(doc, indent=4, ensure_ascii=False)
    assert JP.dumps_indent_and_canonical(doc, 4) == (
        json.dumps(doc, indent=4, ensure_ascii=False),
        json.dumps(doc, sort_keys=True, separators=(",", ":")))
    assert JP.canonical(doc) == json.dumps(doc, sort_keys=True, separators=(",", ":"))


def test_key_order_is_part_of_the_piece():
    """Same content, other key order: the indented text differs, and a piece
    keyed on order-free content would serve the first order for both."""
    a = {"qubits": {"q1": {"x": 1, "y": [2, 3]}, "q2": {"z": 1}}}
    b = {"qubits": {"q1": {"y": [2, 3], "x": 1}, "q2": {"z": 1}}}
    assert JP.dumps_indent(a, 4) == json.dumps(a, indent=4, ensure_ascii=False)
    assert JP.dumps_indent(b, 4) == json.dumps(b, indent=4, ensure_ascii=False)
    assert JP.canonical(a) == JP.canonical(b)


@pytest.mark.parametrize("seed", range(60))
def test_json_diff_leafcount_equals_the_walk(seed):
    """json_diff_leafcount == len(json_diff.flatten(doc, cap=inf)) -- the
    number the cap is compared against -- across edits (served from RAM)."""
    from quam_state_manager.core import json_diff
    r = random.Random(1000 + seed)
    doc = _doc(r)
    for _ in range(5):
        want = len(json_diff.flatten(doc, cap=10**9)[0])
        assert JP.json_diff_leafcount(doc) == want, seed
        _mutate_leaf(r, doc)
    assert JP.json_diff_leafcount({}) == 0


def test_a_written_chip_file_hashes_like_its_parse(tmp_path):
    """safe_io seeds the content cache with the canonical text it composed
    while writing: the hash of the bytes on disk must equal the hash of their
    parse, and a later edit of the written document must not leak into it."""
    from quam_state_manager.core import doc_cache, safe_io
    r = random.Random(7)
    state, wiring = _doc(r), {"wiring": {"q1": {"xy": "#/ports/1"}}, "network": {"h": 1}}
    safe_io.write_state_wiring(tmp_path, state, wiring)
    state["k_after_write"] = 99          # the store mutates its doc afterwards
    sb = (tmp_path / "state.json").read_bytes()
    wb = (tmp_path / "wiring.json").read_bytes()
    assert doc_cache.CANON.has(doc_cache.digest(sb)), "the write did not seed the cache"
    ref = hashlib.sha256(json.dumps([json.loads(sb), json.loads(wb)], sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()
    assert doc_cache.read_pair(tmp_path, mode="hash").content_hash() == ref
    assert working_copy.content_hash(json.loads(sb), json.loads(wb)) == ref
