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


def _reference_walk(merged: dict, path: list[str], int_index: bool = False) -> dict:
    """A deliberately naive walker for absolute pointers, as quam reads them.

    One flat list of keys still to descend. A pointer at the current node sends
    the walk back to the root with the pointer's keys put in front of that list.
    A cycle is the exact same full state met again: the node holding the
    pointer, the pointer, and every key still to descend. At most _LIMIT
    pointers are followed in the whole walk. A key is a whole string, never split
    or unescaped once read; a list is indexed only by a key of plain digits, as
    quam reads one (quam/utils/string_reference.py: ``next_attr.isdigit()``);
    *int_index* reads any text int() accepts instead, to count where the rules differ.
    A cycle "spans" when the repeated state was first met while keys of *path* were still to descend
    and is met again after all of them were descended.
    """
    todo = list(path)
    mine = len(todo)   # the keys of *path* not yet descended: always the last *mine* keys of todo
    phase: dict = {}   # state -> whether keys of *path* were still to descend when it was first met
    at: list[str] = []
    node = merged
    seen: set = set()
    hops: list[str] = []
    crossed = False   # a pointer met with keys of an earlier pointer still to descend
    while True:
        if isinstance(node, str) and node.startswith("#/"):
            state = (tuple(at), node, tuple(todo))
            if state in seen:
                return {"ok": False, "why": "cycle", "hops": hops, "spans": phase[state] and not mine}
            seen.add(state)
            phase[state] = mine > 0
            crossed = crossed or bool(hops and todo)
            if len(hops) == _LIMIT:
                return {"ok": False, "why": "limit", "hops": hops}
            hops.append(".".join(at))
            todo = [s for s in node[2:].split("/") if s] + todo
            at, node = [], merged
            continue
        if not todo:
            return {"ok": True, "why": "", "at": at, "value": node, "hops": hops, "crossed": crossed}
        if len(todo) == mine:
            mine -= 1
        key = todo.pop(0)
        if isinstance(node, dict) and key in node:
            at, node = at + [key], node[key]
            continue
        if isinstance(node, list) and (int_index or key.isdigit()):
            try:
                at, node = at + [key], node[int(key)]
                continue
            except (ValueError, IndexError):
                pass
        return {"ok": False, "why": "missing", "hops": hops}


def _agrees(merged: dict, path: list[str]) -> dict:
    ref = _reference_walk(merged, path)
    got = resolve_field_target(merged, path, cross=True)
    case = f"{merged} at {path}: reference {ref}, resolver {got}"
    assert got["resolvable"] is ref["ok"], case
    assert [h["from_path"] for h in got["chain"]] == ref["hops"], case
    if ref["ok"]:
        assert got["resolved_segments"] == ref["at"], case
        want = ref["value"] if not isinstance(ref["value"], (dict, list)) else None
        assert got["resolved_value"] == want, case
    return ref


def _random_graph(rng, roots="abc", keys="xyz", lists: bool = False, list_share: float = 0.2) -> dict:
    """Small random pointer graph; with *lists* a *list_share* of containers are lists, indexed by "0", "1", ..."""
    def path() -> str:
        return "#/" + "/".join([rng.choice(roots)] + [rng.choice(keys) for _ in range(rng.randint(0, 3))])

    def node(depth: int):
        roll = rng.random()
        if depth == 0 or roll < 0.3:
            return path() if rng.random() < 0.6 else 26
        if lists and rng.random() < list_share:
            return [node(depth - 1) for _ in range(rng.randint(1, 2))]
        return {k: node(depth - 1) for k in rng.sample(keys, rng.randint(1, 3))}

    m = {k: node(3) for k in roots}
    m["q"] = {"leaf": path()}
    return m


# Keys a joined-string cycle key or a pointer string could confuse. Pointers are not escaped (docs/01): a "/" in
# a key splits when the key is written into a pointer, so a "/" key is reached only from a list path; "~0", "~1",
# "#", "." and a space are plain keys; "0" is a dict key or, in a list, an index.
_TRICKY_KEYS = ("x", "y", "x.y", "x/y", "x|y", "x y", "~0", "~1", "#", "0", ".")
_TRICKY_ROOTS = ("a", "a.x", "a|x", "a x", "a/x")
_SEPARATORS = (".", "/", "|", " ")


def _regroup(rng, keys: list[str], sep: str) -> list[str]:
    """The same text as *keys* joined by *sep*, cut into keys at other *sep* boundaries (same text, other keys)."""
    parts = sep.join(keys).split(sep)
    out = [parts[0]]
    for part in parts[1:]:
        if rng.random() < 0.5:
            out[-1] += sep + part
        else:
            out.append(part)
    return out


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


def _nested_revisit_graph(rng, keys="xy", tricky: bool = False) -> tuple[dict, list[str]]:
    """One pointer met twice, each time inside one or two pending walks, the walks split another way.

    holder.p points at a root t. The first visit enters holder.p from q.leaf directly, or through a relay
    pointer u.s whose own keys stay pending outside; the walk then runs along t.<first>, where <first> is every
    key still to walk at that visit. Partway along it, with the rest of <first> still pending outside, a pointer
    re-enters holder.p, again directly or through a relay v.s. Still to walk at the second visit, innermost walk
    first: <second>. It may equal <first> (a true cycle with the walks split differently), or reorder, nest or
    split the same keys. What sits at t.<second> is random. Short keys over a two-letter alphabet make keys
    that lose frame order, key order or frame boundaries collide with the first visit often.

    With *tricky* the keys come from _TRICKY_KEYS, half the walks start from a list path (its keys are never
    split, so a "/" key is walked whole there and split once written into a pointer), and in 40 % of the rest
    the second visit has the same text as the first joined by one separator, cut into other keys: a cycle key
    that joins keys into a string calls that state a repeat. Returns the graph and the path to resolve.
    """
    def run() -> list[str]:
        return [rng.choice(keys) for _ in range(rng.randint(1, 2))]

    def enter(m: dict, relay: str, inner: list[str], outer: list[str]) -> list[str]:
        """The pointer keys that reach holder.p with *inner* inside and *outer* pending in a relay walk."""
        if not outer:
            return [holder, p] + inner
        m[relay] = {"s": "/".join(["#", holder, p] + inner)}
        return [relay, "s"] + outer

    holder, target = rng.sample("abc", 2)
    p = rng.choice("xyz")
    m = _random_graph(rng)
    m[holder] = {p: "#/" + target}
    m[target] = {}
    r1, r2 = run(), (run() if rng.random() < 0.5 else [])
    first = r1 + r2
    if len(first) < 2:
        first.append(rng.choice(keys))
        r1 = first[:len(first) - len(r2)]
    start = enter(m, "u", r1, r2)
    from_list = tricky and rng.random() < 0.5
    path = start if from_list else ["q", "leaf"]
    if not from_list:
        m["q"] = {"leaf": "/".join(["#"] + start)}
    j = rng.randint(1, len(first) - 1)
    at, rest = first[:j], first[j:]
    if tricky and rng.random() < 0.4:   # the same text, cut into other keys
        again = _regroup(rng, at, rng.choice(_SEPARATORS))
        cut = rng.randint(1, len(again))
        s1, s2 = again[:cut], again[cut:]
    elif rng.random() < 0.2:   # the same keys again, split another way: a cycle
        cut = rng.randint(0, len(at))
        s1, s2 = at[:cut] or at, at[cut:] if cut else []
    else:
        s1, s2 = run(), (run() if rng.random() < 0.5 else [])
    _put(m[target], at, "/".join(["#"] + enter(m, "v", s1, s2)))
    second = s1 + s2 + rest
    clash = second[:len(at)] == at or at[:len(second)] == second
    roll = rng.random()
    if not clash and roll < 0.7:
        _put(m[target], second, 26)
    elif not clash and roll < 0.9:
        _put(m[target], second, "/".join(["#", holder, p] + run()))
    return m, path


def _twin_holder_graph(rng) -> tuple[dict, list[str]]:
    """Two holders whose keys read the same once joined by one separator, holding the same pointer.

    One holder's keys are single letters, the other's the same text cut at other separator boundaries. The walk
    meets one of them with keys <rest> still to walk; its pointer leads to a pointer that names the other one
    with the same <rest>. Two states, so the walk goes on until the first holder's state repeats; a cycle key
    that joins the holder's keys stops a hop early. A holder whose keys hold a "/" is reached from a list path
    only. Returns the graph and the path to resolve.
    """
    sep = rng.choice(_SEPARATORS)
    letters = [rng.choice("xy") for _ in range(rng.randint(2, 4))]
    cut = letters
    while cut == letters:
        cut = _regroup(rng, letters, sep)
    first, second = (cut, letters) if sep == "/" or rng.random() < 0.5 else (letters, cut)
    m = _random_graph(rng)
    rest = [rng.choice("xy") for _ in range(rng.randint(0, 2))]
    inner = [rng.choice("xy") for _ in range(rng.randint(0, 1))]
    ptr = "/".join(["#", "t"] + inner)
    _put(m, first, ptr)
    _put(m, second, ptr)
    m["t"] = {}
    _put(m, ["t"] + inner + rest, "/".join(["#"] + second + rest))
    if sep == "/" or rng.random() < 0.5:
        return m, first + rest
    m["q"] = {"leaf": "/".join(["#"] + first + rest)}
    return m, ["q", "leaf"]


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
        # b.p met with x, y, x still to walk, then again with y, x inside and x outside (y, x, x innermost first;
        # outermost first it would read x, y, x again): lands on c.y.x.x = 26
        "same pointer, frames nested the other way": (
            {"b": {"p": "#/c"}, "c": {"x": {"y": "#/b/p/y/x"}, "y": {"x": {"x": 26}}}, "q": {"leaf": "#/b/p/x/y/x"}},
            26),
        # h.p met with the one key "b.c" still to walk, then with b, c: two states, lands on t.b.c = 26
        "a dotted key still to walk": (
            {"h": {"p": "#/t"}, "t": {"b.c": "#/h/p/b/c", "b": {"c": 26}}, "q": {"leaf": "#/h/p/b.c"}}, 26),
        # the same pointer held at a."b.c" and at "a.b".c: two holders, so the cycle is t met again, after a.b.c
        "dotted holders": (
            {"a": {"b.c": "#/t"}, "a.b": {"c": "#/t"}, "t": "#/a.b/c", "q": {"leaf": "#/a/b.c"}}, None),
    }


def _slashed_graphs() -> dict:
    """Graphs walked from a list path, whose keys may hold a "/" a pointer would split: (graph, path, value)."""
    return {
        # h.p met with the one key "x/y" still to walk, then with x, y: two states, lands on t.x.y = 26
        "a slashed key still to walk": (
            {"h": {"p": "#/t"}, "t": {"x/y": "#/h/p/x/y", "x": {"y": 26}}}, ["h", "p", "x/y"], 26),
        # the same pointer held at "a/b" and at a.b: two holders, so the cycle is t met again, after a.b
        "slashed holders": ({"a/b": "#/t", "a": {"b": "#/t"}, "t": "#/a/b"}, ["a/b"], None),
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


@pytest.mark.parametrize("name", sorted(_slashed_graphs()))
def test_cross_agrees_with_the_reference_on_slashed_keys(name):
    merged, path, value = _slashed_graphs()[name]
    ref = _agrees(merged, path)
    assert ref["ok"] is (value is not None) and (value is None or ref["value"] == value)


def test_cross_tells_relative_dotted_holders_apart():
    # a."b.c" and "a.b".c hold the same "#./z": two states, so the walk goes on to "a.b".z = 26
    merged = {"a": {"b.c": "#./z", "z": "#/a.b/c"}, "a.b": {"c": "#./z", "z": 26}, "q": {"leaf": "#/a/b.c"}}
    got = resolve_field_target(merged, ["q", "leaf"], cross=True)
    assert got["resolvable"] and got["resolved_value"] == 26 and got["resolved_segments"] == ["a.b", "z"]
    assert [h["from_path"] for h in got["chain"]] == ["q.leaf", "a.b.c", "a.z", "a.b.c"]


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


def test_cross_agrees_with_the_reference_on_nested_revisits():
    import random
    rng = random.Random(20261118)
    why: dict = {}
    for _ in range(1500):
        ref = _agrees(*_nested_revisit_graph(rng))
        kind = ref["why"] or "landed"
        why[kind] = why.get(kind, 0) + 1
    # both verdicts are common, so a cycle key that merges or splits states has walks to disagree on
    assert why.get("landed", 0) >= 300 and why.get("cycle", 0) >= 100, why


def test_cross_agrees_with_the_reference_on_tricky_keys():
    import random
    rng = random.Random(20261119)
    why: dict = {}
    for _ in range(3000):
        merged = _random_graph(rng, _TRICKY_ROOTS, _TRICKY_KEYS, lists=True)
        path = ["q", "leaf"]
        if rng.random() < 0.5:
            path = [rng.choice(_TRICKY_ROOTS)] + [rng.choice(_TRICKY_KEYS) for _ in range(rng.randint(0, 3))]
        ref = _agrees(merged, path)
        kind = ref["why"] or "landed"
        why[kind] = why.get(kind, 0) + 1
    # every verdict occurs (cycles are rarer over this many keys), so a key that merges two states can disagree
    assert why.get("cycle", 0) >= 20 and all(why.get(k, 0) >= 50 for k in ("missing", "landed", "limit")), why


def test_cross_agrees_with_the_reference_on_nested_revisits_with_tricky_keys():
    import random
    rng = random.Random(20261120)
    why: dict = {}
    for _ in range(1500):
        ref = _agrees(*_nested_revisit_graph(rng, ("x", "y", "x.y", "x/y", "x|y", "x y"), tricky=True))
        kind = ref["why"] or "landed"
        why[kind] = why.get(kind, 0) + 1
    # walks the same text cut into other keys must land or stop where the reference does
    assert why.get("landed", 0) >= 200 and why.get("cycle", 0) >= 100, why


def test_cross_agrees_with_the_reference_on_twin_holders():
    import random
    rng = random.Random(20261121)
    why: dict = {}
    for _ in range(600):
        ref = _agrees(*_twin_holder_graph(rng))
        kind = ref["why"] or "landed"
        why[kind] = why.get(kind, 0) + 1
    # nearly every walk reaches both holders and stops at the cycle, so a key that joins holders stops early
    assert why.get("cycle", 0) >= 500, why


def _walk_then_leaf_graph(rng) -> tuple[dict, list[str]]:
    """A pointer met while the path is walked, then met again in the leaf chain.

    The path starts at a holder (one or two keys) holding a pointer to a root t, and walks on along
    t.<inner>.<rest> to a leaf pointer. The leaf chain reaches a root u, directly or through one more pointer at
    a root w, and u names the holder with keys <again> still to walk. <again> is <rest> in most graphs: the state
    met in the walk comes back in the leaf chain, a cycle that spans both. Otherwise the walk lands or misses.
    Returns the graph and the path to resolve.
    """
    h, t, u, w = rng.sample("abcd", 4)
    holder = [h] + ([rng.choice("xy")] if rng.random() < 0.5 else [])
    inner = [rng.choice("xy") for _ in range(rng.randint(0, 1))]
    rest = [rng.choice("xy") for _ in range(rng.randint(1, 2))]
    m = _random_graph(rng)
    _put(m, holder, "/".join(["#", t] + inner))
    m[t] = {}
    again = rest if rng.random() < 0.6 else [rng.choice("xy") for _ in range(rng.randint(0, 2))]
    back = "/".join(["#", u] + again)
    if rng.random() < 0.5:
        m[w], back = back, "#/" + w
    _put(m[t], inner + rest, back)
    m[u] = "/".join(["#"] + holder)
    clash = again[:len(rest)] == rest or rest[:len(again)] == again
    if not clash and rng.random() < 0.7:
        _put(m[t], inner + again, 26)
    return m, holder + rest


def test_cross_stops_a_cycle_met_in_the_walk_and_again_in_the_leaf_chain():
    # a is met with k still to walk, then b.k leads through c back to a with k still to walk: stop there
    merged = {"a": "#/b", "b": {"k": "#/c/k"}, "c": "#/a"}
    ref = _agrees(merged, ["a", "k"])
    assert ref["why"] == "cycle" and ref["spans"] and ref["hops"] == ["a", "b.k", "c"]


def test_cross_agrees_with_the_reference_on_walk_then_leaf_cycles():
    import random
    rng = random.Random(20261122)
    why: dict = {}
    for _ in range(500):
        ref = _agrees(*_walk_then_leaf_graph(rng))
        kind = "spans" if ref.get("spans") else (ref["why"] or "landed")
        why[kind] = why.get(kind, 0) + 1
    # most walks stop at a state met in the walk and again in the leaf chain, so forgetting it in between is seen
    assert why.get("spans", 0) >= 150 and why.get("landed", 0) >= 30, why


# Text int() reads as an integer but quam does not index a list with (not plain digits).
_INT_ONLY_INDICES = ("-1", "+1", " 1", "0_1", "-0", " 0 ")


@pytest.mark.parametrize("index", ["-1", "+1", " 1", "0_1"])
def test_cross_reads_a_list_index_of_plain_digits_only(index):
    merged = {"a": [5, 26], "r": "#/a", "q": {"leaf": f"#/a/{index}"}, "p": {"leaf": f"#/r/{index}"}}
    for path in (["q", "leaf"], ["p", "leaf"]):
        got = resolve_field_target(merged, path, cross=True)
        assert got["resolvable"] is False and got["resolved_value"] == merged[path[0]]["leaf"], got
        assert _agrees(merged, path)["why"] == "missing"
    got = resolve_field_target(merged, ["a", index], cross=True)
    assert got["resolvable"] is False and got["chain"] == []
    assert _agrees(merged, ["a", index])["why"] == "missing"
    # the click-to-edit default (no cross) reads such a key with int(), as before
    assert resolve_field_target(merged, f"a.{index}")["resolved_value"] == 26


def test_cross_still_reads_plain_digit_list_indices():
    merged = {"a": list(range(13)), "r": "#/a", "q": {"leaf": "#/a/0"}, "p": {"leaf": "#/r/12"}}
    assert resolve_field_target(merged, ["q", "leaf"], cross=True)["resolved_value"] == 0
    assert resolve_field_target(merged, ["p", "leaf"], cross=True)["resolved_value"] == 12
    assert resolve_field_target(merged, ["a", "12"], cross=True)["resolved_value"] == 12
    for path in (["q", "leaf"], ["p", "leaf"], ["a", "12"]):
        assert _agrees(merged, path)["ok"]


# Digit text quam indexes a list with beyond plain ASCII digits: leading zeros and non-ASCII decimal digits
# (str.isdigit() then int()); "\u00b2" passes isdigit() but int() refuses it, so quam raises and nothing resolves.
_DIGIT_FORMS = (("007", 7), ("01", 1), ("\u0661", 1), ("\u0967", 1), ("\u0660\u0667", 7))


@pytest.mark.parametrize("index, item", _DIGIT_FORMS)
def test_cross_reads_every_digit_form_quam_indexes(index, item):
    merged = {"a": list(range(13)), "r": "#/a", "q": {"leaf": f"#/a/{index}"}, "p": {"leaf": f"#/r/{index}"}}
    for path in (["q", "leaf"], ["p", "leaf"], ["a", index]):
        assert resolve_field_target(merged, path, cross=True)["resolved_value"] == item, path
        assert _agrees(merged, path)["ok"]


def test_cross_does_not_index_with_a_superscript_digit():
    merged = {"a": list(range(13)), "q": {"leaf": "#/a/\u00b2"}}
    got = resolve_field_target(merged, ["q", "leaf"], cross=True)
    assert got["resolvable"] is False and got["resolved_value"] == "#/a/\u00b2", got
    assert _agrees(merged, ["q", "leaf"])["why"] == "missing"


def test_cross_agrees_with_the_reference_on_list_indices():
    import random
    rng = random.Random(20261123)
    keys = ("0", "1", "x", "01", "\u0661", "\u00b2") + _INT_ONLY_INDICES
    why: dict = {}
    for _ in range(1000):
        merged = _random_graph(rng, "abc", keys, lists=True, list_share=0.5)
        path = ["q", "leaf"]
        if rng.random() < 0.5:
            path = [rng.choice("abc")] + [rng.choice(keys) for _ in range(rng.randint(1, 3))]
        ref = _agrees(merged, path)
        other = _reference_walk(merged, path, int_index=True)
        kind = "rules differ" if (other["ok"], other["hops"]) != (ref["ok"], ref["hops"]) else ref["why"] or "landed"
        why[kind] = why.get(kind, 0) + 1
    # many walks read a list by such a key, so a walker that reads it with int() disagrees with the reference
    assert why.get("rules differ", 0) >= 80 and why.get("landed", 0) >= 50, why
