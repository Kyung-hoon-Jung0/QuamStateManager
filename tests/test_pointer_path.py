"""Tests for core/pointer_path.py — the mid-path QUAM-pointer follower used by
pointer-aware click-to-edit. Synthetic tests always run; real-data tests
auto-skip when the quam_states folder is absent.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from quam_state_manager.core.pointer_path import find_shared_by, resolve_field_target


def _merged() -> dict:
    return {
        "qubits": {
            "qA1": {
                "f_01": 6.25e9,
                "xy": {
                    "intermediate_frequency": "#./inferred_if",  # runtime alias, no sibling
                    "operations": {
                        "x180": "#./x180_DragCosine",
                        "x90": "#./x90_DragCosine",
                        "y180": "#./y180_DragCosine",
                        "x180_DragCosine": {"amplitude": 0.115, "length": 40},
                        "x90_DragCosine": {
                            "amplitude": 0.057,
                            "length": "#../x180_DragCosine/length",
                        },
                        "y180_DragCosine": {
                            "amplitude": "#../x180_DragCosine/amplitude",
                            "length": 40,
                        },
                    },
                },
            },
        },
    }


def test_resolves_self_ref_mid_path():
    ft = resolve_field_target(_merged(), "qubits.qA1.xy.operations.x180.amplitude")
    assert ft["resolved_path"] == "qubits.qA1.xy.operations.x180_DragCosine.amplitude"
    assert ft["resolved_value"] == 0.115
    assert ft["is_pointer"] is True and ft["resolvable"] is True
    assert len(ft["chain"]) == 1
    assert ft["chain"][0]["pointer"] == "#./x180_DragCosine"
    assert ft["candidates"][-1]["path"] == ft["resolved_path"]


def test_resolves_leaf_pointer_chain():
    # y180 (mid pointer) → y180_DragCosine; its .amplitude (leaf pointer) → x180.
    ft = resolve_field_target(_merged(), "qubits.qA1.xy.operations.y180.amplitude")
    assert ft["resolved_path"] == "qubits.qA1.xy.operations.x180_DragCosine.amplitude"
    assert ft["resolved_value"] == 0.115
    # candidates: the y180_DragCosine.amplitude (pointer) and the final literal.
    paths = [c["path"] for c in ft["candidates"]]
    assert paths == [
        "qubits.qA1.xy.operations.y180_DragCosine.amplitude",
        "qubits.qA1.xy.operations.x180_DragCosine.amplitude",
    ]
    assert ft["candidates"][0]["is_pointer"] is True
    assert ft["candidates"][-1]["is_pointer"] is False
    assert len(ft["chain"]) == 2


def test_leaf_relative_pointer():
    ft = resolve_field_target(_merged(), "qubits.qA1.xy.operations.x90_DragCosine.length")
    assert ft["resolved_path"] == "qubits.qA1.xy.operations.x180_DragCosine.length"
    assert ft["resolved_value"] == 40


def test_runtime_alias_degrades_gracefully():
    ft = resolve_field_target(_merged(), "qubits.qA1.xy.intermediate_frequency")
    assert ft["is_pointer"] is True
    assert ft["resolvable"] is False  # sibling 'inferred_if' does not exist
    assert ft["candidates"]  # best-effort, non-empty
    # No exception; the deepest real path is still offered as a target.
    assert ft["resolved_path"] == "qubits.qA1.xy.intermediate_frequency"


def test_non_pointer_path_is_trivial():
    ft = resolve_field_target(_merged(), "qubits.qA1.f_01")
    assert ft["is_pointer"] is False and ft["resolvable"] is True
    assert ft["chain"] == []
    assert len(ft["candidates"]) == 1
    assert ft["candidates"][0]["path"] == "qubits.qA1.f_01"
    assert ft["resolved_value"] == 6.25e9


def test_cycle_guard():
    merged = {"a": {"x": "#./y", "y": "#./x"}}
    ft = resolve_field_target(merged, "a.x")
    assert ft["resolvable"] is False  # a.x → a.y → a.x cycle
    assert ft["is_pointer"] is True   # terminates without RecursionError


def test_list_index_traversal():
    merged = {"qubits": {"qA1": {"arr": [{"v": 1}, {"v": 2}]}}}
    ft = resolve_field_target(merged, "qubits.qA1.arr.1.v")
    assert ft["resolvable"] is True
    assert ft["resolved_value"] == 2


def test_square_pulse_label_not_hardcoded():
    merged = {
        "qubits": {"qA1": {"xy": {"operations": {
            "x180": "#./x180_Square",
            "x180_Square": {"amplitude": 0.2},
        }}}}
    }
    ft = resolve_field_target(merged, "qubits.qA1.xy.operations.x180.amplitude")
    assert ft["resolved_path"].endswith("x180_Square.amplitude")
    assert ft["candidates"][-1]["label"] == "x180_Square.amplitude"


def test_find_shared_by():
    merged = _merged()
    resolved = "qubits.qA1.xy.operations.x180_DragCosine.amplitude"
    shared = find_shared_by(merged, resolved, scope_qubit="qA1", input_op="x180")
    assert "y180" in shared          # y180.amplitude → x180_DragCosine.amplitude
    assert "x180" not in shared      # the clicked alias is excluded
    assert "x90" not in shared       # x90 has its own literal amplitude


# --- real-data ---------------------------------------------------------
_QS = Path("<quam-states>/example_lab")
skip_no_real = pytest.mark.skipif(
    not (_QS / "state.json").exists(), reason="real quam_states folder not found")


@skip_no_real
def test_real_x180_resolution():
    from quam_state_manager.core.loader import QuamStore
    store = QuamStore(str(_QS))
    q = store.qubit_names[0]
    ft = resolve_field_target(store.merged, f"qubits.{q}.xy.operations.x180.amplitude")
    assert ft["is_pointer"] and ft["resolvable"]
    assert ft["resolved_path"].endswith("_DragCosine.amplitude")
    assert isinstance(ft["resolved_value"], (int, float))
    assert any(h["pointer"].startswith("#./") for h in ft["chain"])


@skip_no_real
def test_real_shared_by_excludes_self():
    from quam_state_manager.core.loader import QuamStore
    store = QuamStore(str(_QS))
    q = store.qubit_names[0]
    ft = resolve_field_target(store.merged, f"qubits.{q}.xy.operations.x180.amplitude")
    shared = find_shared_by(store.merged, ft["resolved_path"], scope_qubit=q, input_op="x180")
    assert "x180" not in shared
    assert "y180" in shared


# ---------------------------------------------------------------------------
# cross: a pointer whose target path passes through another pointer
# ---------------------------------------------------------------------------

def _crossed() -> dict:
    return {"a": {"ref": "#/b"}, "b": {"x": "#./y", "y": 5},
            "q": {"leaf": "#/a/ref/x"}}


def test_a_target_path_through_a_pointer_is_a_dead_end_by_default():
    r = resolve_field_target(_crossed(), "q.leaf")
    assert r["resolvable"] is False and r["resolved_value"] == "#/a/ref/x"


def test_cross_follows_the_crossed_pointer_and_its_self_reference():
    # the #./ at b.x is relative to b, the object really reached
    r = resolve_field_target(_crossed(), "q.leaf", cross=True)
    assert r["resolvable"] is True
    assert r["resolved_path"] == "b.y" and r["resolved_value"] == 5
    assert [c["path"] for c in r["candidates"]] == ["q.leaf", "b.x", "b.y"]


def test_cross_keeps_a_crossed_dangling_or_looping_path_a_dead_end():
    m = _crossed()
    m["a"]["ref"] = "#/nowhere"
    assert resolve_field_target(m, "q.leaf", cross=True)["resolvable"] is False
    m["a"]["ref"] = "#/a/ref/x"      # the crossed pointer names a path through itself
    assert resolve_field_target(m, "q.leaf", cross=True)["resolvable"] is False


def test_cross_stops_a_cycle_at_the_cycle_not_at_the_pointer_limit():
    # the cycle and the limit give the same result keys; the chain length tells them apart
    m = _crossed()
    m["b"]["x"] = "#/a/ref/x"        # b.x crosses a.ref back to itself
    r = resolve_field_target(m, "q.leaf", cross=True)
    assert r["resolvable"] is False
    assert [h["from_path"] for h in r["chain"]] == ["q.leaf", "a.ref", "b.x"]
    plain = {"a": "#/b", "b": "#/a", "q": {"leaf": "#/a"}}
    r = resolve_field_target(plain, "q.leaf", cross=True)
    assert r["resolvable"] is False and len(r["chain"]) == 3


def test_cross_forgets_a_finished_crossing():
    # a.ref is met first with y/x still to walk, later with y only: not a cycle
    m = {"a": {"ref": "#/b"}, "b": {"y": {"x": "#/a/ref/y"}}, "q": {"leaf": "#/a/ref/y/x"}}
    r = resolve_field_target(m, "q.leaf", cross=True)
    assert r["resolvable"] is True and r["resolved_path"] == "b.y"
    assert [h["from_path"] for h in r["chain"]] == ["q.leaf", "a.ref", "b.y.x", "a.ref"]


def test_cross_tells_nested_crossings_apart_by_the_whole_path_still_to_walk():
    # c.p is met twice with w still to walk inside it, but with z and then k still
    # to walk in the outer crossing: not a cycle, the walk lands on d.w.k = 26
    m = {"a": {"ref": "#/c/p/w"}, "c": {"p": "#/d"}, "d": {"w": {"z": "#/e/r/k", "k": 26}},
         "e": {"r": "#/c/p/w"}, "q": {"leaf": "#/a/ref/z"}}
    r = resolve_field_target(m, "q.leaf", cross=True)
    assert r["resolvable"] is True
    assert r["resolved_path"] == "d.w.k" and r["resolved_value"] == 26
    assert [h["from_path"] for h in r["chain"]] == ["q.leaf", "a.ref", "c.p", "d.w.z", "e.r", "c.p"]


def _chain(hops: int) -> dict:
    """q.leaf -> h0 -> ... -> h{hops-2} -> number: exactly *hops* pointers followed."""
    m = {"h": {f"h{i}": f"#/h/h{i + 1}" for i in range(hops - 1)}, "q": {"leaf": "#/h/h0"}}
    m["h"][f"h{hops - 1}"] = 26
    return m


@pytest.mark.parametrize("cross", [False, True])
def test_the_hop_limit_boundary(cross):
    from quam_state_manager.core.pointer_path import _MAX_HOPS
    ok = resolve_field_target(_chain(_MAX_HOPS), "q.leaf", cross=cross)
    assert ok["resolvable"] is True and ok["resolved_value"] == 26
    cut = resolve_field_target(_chain(_MAX_HOPS + 1), "q.leaf", cross=cross)
    assert cut["resolvable"] is False and cut["resolved_value"].startswith("#/h/")


def test_cross_names_leaves_by_their_real_keys():
    m = {"s": {"a.b": "#/s/c.d", "c.d": 26}, "q.1": {"leaf": "#/s/a.b"}}
    r = resolve_field_target(m, ["q.1", "leaf"], cross=True)
    assert r["resolvable"] is True and r["resolved_value"] == 26
    assert r["resolved_segments"] == ["s", "c.d"]
    assert [h["to_segments"] for h in r["chain"]] == [["s", "a.b"], ["s", "c.d"]]
    # the default result keeps its keys
    plain = resolve_field_target(_crossed(), "q.leaf")
    assert "resolved_segments" not in plain and all("to_segments" not in h for h in plain["chain"])


def test_cross_counts_every_pointer_of_the_walk_once():
    # a crossed run of 63 pointers plus the leaf: 64 resolve; one more is cut
    from quam_state_manager.core.pointer_path import _MAX_HOPS
    for extra, ok in ((0, True), (1, False)):
        n = _MAX_HOPS - 1 + extra
        m = {"h": {f"h{i}": f"#/h/h{i + 1}" for i in range(n - 1)}, "q": {"leaf": "#/h/h0/x"}}
        m["h"][f"h{n - 1}"] = "#/h/end"
        m["h"]["end"] = {"x": 26}
        r = resolve_field_target(m, "q.leaf", cross=True)
        assert r["resolvable"] is ok and len(r["chain"]) == min(n + 1, _MAX_HOPS)


# ---------------------------------------------------------------------------
# cross: the walk against an independent reference, over many small graphs
# ---------------------------------------------------------------------------

_LIMIT = 64   # the documented whole-walk pointer limit (docs/27); pinned against _MAX_HOPS below


def _reference_walk(merged: dict, path: list[str]) -> dict:
    """A deliberately naive walker for absolute pointers, as quam reads them.

    One flat list of keys still to descend. A pointer at the current node sends
    the walk back to the root with the pointer's keys put in front of that list.
    A cycle is the exact same full state met again: the node holding the
    pointer, the pointer, and every key still to descend. At most _LIMIT
    pointers are followed in the whole walk.
    """
    todo = list(path)
    at: list[str] = []
    node = merged
    seen: set = set()
    hops: list[str] = []
    crossed = False   # a pointer met with keys of an earlier pointer still to descend
    while True:
        if isinstance(node, str) and node.startswith("#/"):
            state = (tuple(at), node, tuple(todo))
            if state in seen:
                return {"ok": False, "why": "cycle", "hops": hops}
            seen.add(state)
            crossed = crossed or bool(hops and todo)
            if len(hops) == _LIMIT:
                return {"ok": False, "why": "limit", "hops": hops}
            hops.append(".".join(at))
            todo = [s for s in node[2:].split("/") if s] + todo
            at, node = [], merged
            continue
        if not todo:
            return {"ok": True, "why": "", "at": at, "value": node, "hops": hops, "crossed": crossed}
        key = todo.pop(0)
        if not isinstance(node, dict) or key not in node:
            return {"ok": False, "why": "missing", "hops": hops}
        at, node = at + [key], node[key]


def _agrees(merged: dict, path: list[str]) -> dict:
    ref = _reference_walk(merged, path)
    got = resolve_field_target(merged, path, cross=True)
    case = f"{merged} at {path}: reference {ref}, resolver {got}"
    assert got["resolvable"] is ref["ok"], case
    assert [h["from_path"] for h in got["chain"]] == ref["hops"], case
    if ref["ok"]:
        assert got["resolved_segments"] == ref["at"], case
        want = ref["value"] if not isinstance(ref["value"], dict) else None
        assert got["resolved_value"] == want, case
    return ref


def _random_graph(rng) -> dict:
    keys = "xyz"

    def path() -> str:
        return "#/" + "/".join([rng.choice("abc")] + [rng.choice(keys) for _ in range(rng.randint(0, 3))])

    def node(depth: int):
        roll = rng.random()
        if depth == 0 or roll < 0.3:
            return path() if rng.random() < 0.6 else 26
        return {k: node(depth - 1) for k in rng.sample(keys, rng.randint(1, 3))}

    m = {k: node(3) for k in "abc"}
    m["q"] = {"leaf": path()}
    return m


def _put(m: dict, keys: list[str], value) -> None:
    for k in keys[:-1]:
        if not isinstance(m.get(k), dict):
            m[k] = {}
        m = m[k]
    m[keys[-1]] = value


def _reordered_revisit_graph(rng) -> dict:
    """One pointer met twice with the same keys still to walk, in another order.

    holder.p points at a root t; q.leaf enters holder.p with keys k; at t.k a
    pointer re-enters holder.p with a reordering of k; what sits at t.<reordered>
    is random (a value, a pointer, or nothing).
    """
    keys = "xyz"
    holder, target = rng.sample("abc", 2)
    p = rng.choice(keys)
    first = rng.sample(keys, rng.randint(2, 3))
    again = first[:]
    while again == first:
        rng.shuffle(again)
    m = _random_graph(rng)
    m[holder] = {p: "#/" + target}
    m[target] = {}
    m["q"] = {"leaf": "/".join(["#", holder, p] + first)}
    _put(m[target], first, "/".join(["#", holder, p] + again))
    roll = rng.random()
    if roll < 0.7:
        _put(m[target], again, 26)
    elif roll < 0.9:
        _put(m[target], again, "#/" + "/".join([rng.choice("abc")] + rng.sample(keys, rng.randint(0, 2))))
    return m


def _named_graphs() -> dict:
    run = {f"h{i}": f"#/h/h{i + 1}" for i in range(_LIMIT - 2)}
    run[f"h{_LIMIT - 2}"] = "#/h/h0"
    return {
        # c.p met twice with w still to walk inside it, z then k outside: lands on d.w.k = 26
        "inner frames differ": (
            {"a": {"ref": "#/c/p/w"}, "c": {"p": "#/d"}, "d": {"w": {"z": "#/e/r/k", "k": 26}},
             "e": {"r": "#/c/p/w"}, "q": {"leaf": "#/a/ref/z"}}, 26),
        # b.z.z met twice with the same outermost remainder (y), a different inner one: lands on b.z.y = 26
        "outer frames agree, inner differ": (
            {"b": {"y": "#/b/z/z/z", "z": {"x": "#/b/z", "y": 26, "z": "#/b/z/x"}}, "q": {"leaf": "#/b/y/y"}}, 26),
        "same pointer twice": ({"a": {"ref": "#/b"}, "b": {"x": {"y": 26}}, "q": {"leaf": "#/a/ref/x/y"},
                                "s": "#/a/ref"}, 26),
        "finished crossing forgotten": (
            {"a": {"ref": "#/b"}, "b": {"y": {"x": "#/a/ref/y"}}, "q": {"leaf": "#/a/ref/y/x"}}, None),
        "true cycle": ({"a": "#/b", "b": "#/a", "q": {"leaf": "#/a"}}, None),
        "crossed cycle": ({"a": {"ref": "#/b"}, "b": {"x": "#/a/ref/x"}, "q": {"leaf": "#/a/ref/x"}}, None),
        "cycle near the limit": ({"h": run, "q": {"leaf": "#/h/h0"}}, None),
        # b.p met twice, the keys still to walk x, y the first time and y, x the second: lands on c.y.x = 26
        "same pointer, remainder reordered": (
            {"b": {"p": "#/c"}, "c": {"x": {"y": "#/b/p/y/x"}, "y": {"x": 26}}, "q": {"leaf": "#/b/p/x/y"}}, 26),
    }


def test_the_reference_uses_the_documented_limit():
    from quam_state_manager.core.pointer_path import _MAX_HOPS
    assert _MAX_HOPS == _LIMIT


@pytest.mark.parametrize("name", sorted(_named_graphs()))
def test_cross_agrees_with_the_reference_on_named_graphs(name):
    merged, value = _named_graphs()[name]
    ref = _agrees(merged, ["q", "leaf"])
    if value is not None:
        assert ref["ok"] and ref["value"] == value


def test_cross_agrees_with_the_reference_near_the_limit():
    for n in range(_LIMIT - 4, _LIMIT + 3):
        plain = _chain(n)
        _agrees(plain, ["q", "leaf"])
        crossed = {"h": {f"h{i}": f"#/h/h{i + 1}" for i in range(n - 1)}, "q": {"leaf": "#/h/h0/x"}}
        crossed["h"][f"h{n - 1}"] = "#/h/end"
        crossed["h"]["end"] = {"x": 26}
        _agrees(crossed, ["q", "leaf"])
        looped = _chain(n)
        looped["h"][f"h{n - 1}"] = "#/h/h0"
        _agrees(looped, ["q", "leaf"])


def test_cross_agrees_with_the_reference_on_random_graphs():
    import random
    rng = random.Random(20261116)
    why: dict = {}
    for _ in range(4000):
        ref = _agrees(_random_graph(rng), ["q", "leaf"])
        kind = ref["why"] or ("crossed" if ref["crossed"] else "landed")
        why[kind] = why.get(kind, 0) + 1
    # the corpus reaches every outcome, so a mutant has something to disagree on
    assert all(why.get(k, 0) >= 50 for k in ("cycle", "limit", "missing", "landed", "crossed")), why


def test_cross_agrees_with_the_reference_on_reordered_revisits():
    import random
    rng = random.Random(20261117)
    landed = 0
    for _ in range(600):
        ref = _agrees(_reordered_revisit_graph(rng), ["q", "leaf"])
        landed += ref["ok"]
    # most of these walks land, so a cycle key blind to key order has walks to refuse
    assert landed >= 300, landed
