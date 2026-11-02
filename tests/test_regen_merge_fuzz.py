"""Deterministic synthetic rebuilds; seed IDs make failures reproducible.

Self aliases are runtime expressions to the project resolver. Their concrete
targets are checked too; the direct resolver contract is pinned separately.
"""
from copy import deepcopy
import json
import random
import time
from typing import Any

import pytest

from quam_state_manager.core.pointer_resolver import is_pointer, resolve_pointer
from quam_state_manager.core.regen_merge import (
    _Cycle, _merged_doc, _resolved_leaves, graft_wiring_values, merge_states,
)


SEEDS = range(200)
KEEP = {"lab.x.Obj": {"bases": [], "fields": ["channels"]}}


def leaves(node, path=()):
    if isinstance(node, dict):
        for key, value in node.items():
            yield from leaves(value, path + (key,))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from leaves(value, path + (str(index),))
    else:
        yield path, node


def at(root, path):
    for key in path:
        root = root[int(key)] if isinstance(root, list) else root[key]
    return root


def resolved(root, path, value):
    if not is_pointer(value):
        return value
    if value.startswith("#./"):
        # The resolver intentionally leaves runtime self aliases opaque.
        value = "#/" + "/".join(path[:-1] + tuple(value[3:].split("/")))
    result = resolve_pointer(root, value, path)
    assert not is_pointer(result), (path, value, result)
    return result


def snapshot(root):
    # Flatten pointer-to-object values as well, so cabling references compare
    # concrete port calibration rather than just pointer spelling.
    return {path: resolved(root, path, value) for path, value in leaves(root)}


def chip(seed, count=None):
    rng = random.Random(seed)
    count = count or rng.randint(2, 8)
    old = {"qubits": {}, "qubit_pairs": {}, "ports": {}, "wiring": {"qubits": {}}}
    for kind, fem in (("analog_outputs", "5"), ("mw_outputs", "1")):
        old["ports"][kind] = {"con1": {fem: {}}}
    for i in range(count):
        q = f"q{i}"
        lines = {}
        wiring = {}
        for j, line in enumerate(("xy", "z", "resonator")):
            kind, fem = ("analog_outputs", "5") if line == "z" else ("mw_outputs", "1")
            port = i + 1 if line == "z" else 2 * i + (1 if line == "xy" else 2)
            old["ports"][kind]["con1"][fem][str(port)] = {
                "__class__": "quam.components.ports.Port",
                "controller_id": "con1", "fem_id": int(fem), "port_id": port,
                "delay": rng.randint(1, 100), "offset": rng.uniform(-0.4, 0.4)}
            wiring[line] = {"opx_output": f"#/ports/{kind}/con1/{fem}/{port}",
                            "dc_channel": i + 1}
            base = rng.uniform(0.1, 0.5)
            length = rng.randrange(16, 200, 4)
            lines[line] = {
                "opx_output": f"#/wiring/qubits/{q}/{line}/opx_output",
                "operations": {
                    "primary": {"amplitude": base, "length": length,
                                "alpha": rng.uniform(-1, -0.1)},
                    "secondary": {"amplitude": rng.uniform(0.5, 0.9),
                                  "length": "#../primary/length",
                                  "alpha": rng.uniform(0.1, 1)},
                    "readout": {"amplitude": base, "alias": "#./amplitude",
                                "length": f"#/qubits/{q}/{line}/operations/primary/length"}}}
        old["qubits"][q] = lines
        old["wiring"]["qubits"][q] = wiring
    names = list(old["qubits"])
    rng.shuffle(names)
    for a, b in zip(names, names[1:]):
        old["qubit_pairs"][f"{a}-{b}"] = {
            "qubit_control": f"#/qubits/{a}", "qubit_target": f"#/qubits/{b}",
            "macros": {"cz": {"amplitude": rng.uniform(0.1, 0.9),
                              "length": rng.randrange(20, 200, 4)}}}
    if rng.choice((True, False)):
        old["auxiliary"] = {"__class__": "lab.x.Obj", "channels": {
            q: {"channel": f"#/wiring/qubits/{q}/z/dc_channel", "gain": rng.uniform(1, 2)}
            for q in names}}
    new = deepcopy(old)
    for path, value in list(leaves(new)):
        if isinstance(value, (float, int)) and path[0] != "wiring" and path[-1] not in {
                "port_id", "fem_id"}:
            at(new, path[:-1])[path[-1]] = 0
    for q in names:
        for line in ("xy", "z", "resonator"):
            ops = new["qubits"][q][line]["operations"]
            for key in ("amplitude", "alpha"):
                ops["secondary"][key] = f"#../primary/{key}"
    if "auxiliary" in new:
        new["auxiliary"] = None
    return old, new


def merge(old, new, **kwargs):
    wiring = {"wiring": deepcopy(new["wiring"])}
    graft_wiring_values({"wiring": old["wiring"]}, wiring)
    start = time.perf_counter()
    # Production stores state and wiring separately; assemble their document
    # only for resolution, just as the merge's _merged_doc helper does.
    old_state = {key: value for key, value in old.items() if key != "wiring"}
    new_state = {key: value for key, value in new.items() if key != "wiring"}
    result = merge_states(old_state, new_state, old_wiring={"wiring": old["wiring"]},
                          new_wiring=wiring, keep_classes=KEEP, class_schemas={}, **kwargs)
    assert time.perf_counter() - start < 2.0
    result.merged["wiring"] = wiring["wiring"]
    return result


@pytest.mark.parametrize("seed", SEEDS)
def test_rebuild_properties(seed):
    old, new = chip(seed)
    before = deepcopy((old, new))
    identity = merge(old, new)
    assert identity.stats.residual_lost == []
    assert identity.stats.schema_dropped == []
    assert snapshot(identity.merged) == snapshot(old)
    assert identity.stats.links_not_taken
    again = merge(old, new)
    assert json.dumps(identity.merged, sort_keys=True) == json.dumps(again.merged, sort_keys=True)
    assert (old, new) == before

    rng = random.Random(seed)
    a, b = rng.sample(list(new["qubits"]), 2)
    swapped = deepcopy(new)
    wa = swapped["wiring"]["qubits"][a]["z"]
    wb = swapped["wiring"]["qubits"][b]["z"]
    wa["opx_output"], wb["opx_output"] = wb["opx_output"], wa["opx_output"]
    result = merge(old, swapped)
    expected = deepcopy(identity.merged)
    expected["wiring"] = deepcopy(swapped["wiring"])
    ports = expected["ports"]["analog_outputs"]["con1"]["5"]
    pa, pb = str(int(a[1:]) + 1), str(int(b[1:]) + 1)
    for key in ("delay", "offset"):
        ports[pa][key], ports[pb][key] = ports[pb][key], ports[pa][key]
    assert snapshot(result.merged) == snapshot(expected)
    assert len(result.stats.ports_moved) == 2

    removed = deepcopy(new)
    del removed["qubits"][a]
    del removed["wiring"]["qubits"][a]
    removed["qubit_pairs"] = {key: value for key, value in removed["qubit_pairs"].items()
                              if a not in key.split("-")}
    # Keep unused physical slots: removing an entity need not remove hardware.
    result = merge(old, removed)
    expected = deepcopy(identity.merged)
    del expected["qubits"][a]
    del expected["wiring"]["qubits"][a]
    expected["qubit_pairs"] = {key: value for key, value in expected["qubit_pairs"].items()
                               if a not in key.split("-")}
    if "auxiliary" in expected:
        del expected["auxiliary"]["channels"][a]
    assert snapshot(result.merged) == snapshot(expected)
    assert result.stats.residual_lost
    assert all(a in path.replace("-", ".").split(".") for path in result.stats.residual_lost)

    protected = deepcopy(new)
    paths = [("qubits", a, "xy", "operations", "primary", "amplitude"),
             ("qubits", b, "z", "operations", "secondary", "alpha"),
             ("ports", "analog_outputs", "con1", "5", pa, "offset")]
    for path in paths:
        at(protected, path[:-1])[path[-1]] = rng.uniform(10, 20)
    result = merge(old, protected, protect_paths={".".join(path) for path in paths})
    for path in paths:
        assert at(result.merged, path) == at(protected, path)
    snapshot(result.merged)  # Every absolute/relative pointer and self target resolves.


@pytest.mark.parametrize("seed", SEEDS)
def test_eight_qubit_performance(seed):
    old, new = chip(seed, count=8)
    merge(old, new)


def test_self_alias_preserves_source():
    old = {"qubits": {"q0": {"amplitude": 0.25, "alias": "#./amplitude"}}}
    assert merge_states(old, deepcopy(old)).merged == old


# Embedded wiring is excluded: production reads state.json and wiring.json separately.


# Frozen pre-optimization algorithm: compare both state and restoration order.
def _old_restore_source_forms(merged: dict, old: dict, wiring: dict | None,
                          protect: set[str] | None) -> list[str]:
    """Put back the SOURCE's own form (a lab's pointer direction, an inline object
    the rebuild replaced with a link, a pointer to a different but equal field)
    wherever doing so changes no value QUAM reads and closes no cycle.

    A candidate is a place where the source and the merged state hold a different
    raw form. They are tried together first, then one by one (objects before
    pointers, so a link can land on an object put back before it); each attempt is
    kept only if every leaf of the whole state resolves exactly as before (a
    ``__class__`` is a leaf too) and no pointer chain loops."""
    cands: list[tuple[str, tuple, Any]] = []

    def walk(o: Any, m: Any, path: tuple) -> None:
        if isinstance(o, dict) and isinstance(m, dict):
            for k, ov in o.items():
                if k in m:
                    walk(ov, m[k], path + (k,))
            return
        if o == m:
            return
        if not (is_pointer(o) or is_pointer(m)):
            return
        dot = ".".join(path)
        if protect and any(dot == p or dot.startswith(p + ".") or p.startswith(dot + ".")
                           for p in protect):
            return
        cands.append((dot, path, o))

    walk(old, merged, ())
    if not cands:
        return []
    cands.sort(key=lambda c: (is_pointer(c[2]), c[0]))

    def doc_of(state: dict) -> dict:
        return _merged_doc(state, wiring)

    def every_leaf(state: dict) -> dict[str, Any]:
        d = doc_of(state)
        out: dict[str, Any] = {}
        for top, v in state.items():
            if isinstance(v, dict):
                out.update(_resolved_leaves(d, top))
            else:
                out[top] = v
        return out

    try:
        before = every_leaf(merged)
    except _Cycle:
        return []

    def setp(root: dict, path: tuple, v: Any) -> None:
        node = root
        for k in path[:-1]:
            node = node[k]
        node[path[-1]] = deepcopy(v)

    def ok(state: dict) -> bool:
        try:
            return every_leaf(state) == before
        except _Cycle:
            return False

    def get(root: dict, path: tuple) -> Any:
        node = root
        for k in path:
            node = node[k]
        return node

    done: list[str] = []

    def attempt(group: list) -> None:
        """Put a group back; keep it if the state reads the same, else halve it."""
        if not group:
            return
        saved = [(path, deepcopy(get(merged, path))) for _, path, _ in group]
        for _, path, v in group:
            setp(merged, path, v)
        if ok(merged):
            done.extend(dot for dot, _, _ in group)
            return
        for path, v in reversed(saved):
            setp(merged, path, v)
        if len(group) == 1:
            return
        mid = len(group) // 2
        attempt(group[:mid])
        attempt(group[mid:])

    # all together first (forms that only work as a set, a link and the object it
    # lands on); else objects first, then pointers, each halved until it holds
    saved = deepcopy(merged)
    for _, path, v in cands:
        setp(merged, path, v)
    if ok(merged):
        return [c[0] for c in cands]
    merged.clear()
    merged.update(saved)
    attempt([c for c in cands if not is_pointer(c[2])])
    attempt([c for c in cands if is_pointer(c[2])])
    return done


@pytest.mark.parametrize("seed", range(50))
def test_source_forms_equal_old_algorithm(seed, monkeypatch):
    from quam_state_manager.core import regen_merge
    old, new = chip(seed)
    actual = merge(old, new)
    monkeypatch.setattr(regen_merge, "_restore_source_forms", _old_restore_source_forms)
    expected = merge(old, new)
    assert actual == expected

    # Exercise refused candidates, chains, object expansion, cycles, missing
    # targets, and protected subtrees with the identical original trial order.
    rng = random.Random(seed)
    state = {"data": {f"n{i}": {"value": rng.randrange(4), "alias": "#./value"}
                      for i in range(12)}}
    source = deepcopy(state)
    for i, node in enumerate(source["data"].values()):
        node["alias"] = rng.choice(["#./value", f"#/data/n{rng.randrange(12)}/alias",
                                    f"#/data/n{rng.randrange(12)}/value", "#/missing"])
        if i % 3 == 0:
            node["value"] = f"#/data/n{rng.randrange(12)}"
    protect = {f"data.n{seed % 12}.value"}
    a, b = deepcopy(state), deepcopy(state)
    # Retrieve the optimized function before replacing it for the second run.
    monkeypatch.undo()
    restored = regen_merge._restore_source_forms(a, source, None, protect)
    expected_restored = _old_restore_source_forms(b, source, None, protect)
    assert (a, restored) == (b, expected_restored)


def test_source_form_refusal_performance(monkeypatch):
    from quam_state_manager.core import regen_merge
    hop = regen_merge._hop
    hops = 0

    def counted_hop(*args):
        nonlocal hops
        hops += 1
        return hop(*args)

    monkeypatch.setattr(regen_merge, "_hop", counted_hop)
    def make():
        return {"qubits": {f"q{q}": {"ops": {
            f"op{i}": dict({f"p{j}": float(j) for j in range(49)}, b="#./p1")
            for i in range(40)}} for q in range(10)}}
    old, new = make(), make()
    for qubit in old["qubits"].values():
        for i in range(0, 40, 2):
            qubit["ops"][f"op{i}"]["b"] = "#./p2"
    start = time.perf_counter()
    result = merge_states(old, new)
    elapsed = time.perf_counter() - start
    assert result.merged == new
    assert not result.stats.source_forms_restored
    assert elapsed < 3.0, elapsed
    # Also pin the work bound so a fast host cannot hide whole-state rescans.
    assert hops < 20_000, hops
