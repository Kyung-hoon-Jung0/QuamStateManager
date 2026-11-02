"""Deterministic synthetic rebuilds; seed IDs make failures reproducible.

Self aliases are runtime expressions to the project resolver. Their concrete
targets are checked too; the direct resolver contract is pinned separately.
"""
from copy import deepcopy
import json
import random
import time

import pytest

from quam_state_manager.core.pointer_resolver import is_pointer, resolve_pointer
from quam_state_manager.core.regen_merge import graft_wiring_values, merge_states


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


@pytest.mark.xfail(strict=True, reason="Project resolver intentionally returns #./ aliases unchanged")
def test_self_alias_resolves_directly_reproducer():
    old = {"qubits": {"q0": {"amplitude": 0.25, "alias": "#./amplitude"}}}
    result = merge_states(old, deepcopy(old))
    assert resolve_pointer(result.merged, "#./amplitude", ("qubits", "q0", "alias")) == 0.25


@pytest.mark.xfail(strict=True, reason="Source-form restoration undoes embedded wiring swaps when external wiring is also supplied")
def test_embedded_wiring_swap_reproducer():
    # External wiring masks embedded wiring during source-form validation.
    # The normal fuzz adapter above follows the separate-file production shape;
    # keep this failing combined-document case rather than losing the finding.
    old = {"qubits": {"q0": {"z": {"opx_output": "#/wiring/qubits/q0/z/opx_output"}}},
           "ports": {"analog_outputs": {"con1": {"5": {
               "1": {"__class__": "quam.components.ports.Port", "delay": 11},
               "2": {"__class__": "quam.components.ports.Port", "delay": 22}}}}},
           "wiring": {"qubits": {"q0": {"z": {"opx_output": "#/ports/analog_outputs/con1/5/1"}}}}}
    old["qubits"]["q1"] = {"z": {"opx_output": "#/wiring/qubits/q1/z/opx_output"}}
    old["wiring"]["qubits"]["q1"] = {"z": {"opx_output": "#/ports/analog_outputs/con1/5/2"}}
    new = deepcopy(old)
    new["wiring"]["qubits"]["q0"]["z"]["opx_output"] = "#/ports/analog_outputs/con1/5/2"
    new["wiring"]["qubits"]["q1"]["z"]["opx_output"] = "#/ports/analog_outputs/con1/5/1"
    result = merge_states(old, new, old_wiring={"wiring": old["wiring"]},
                          new_wiring={"wiring": new["wiring"]})
    path = ("wiring", "qubits", "q0", "z", "opx_output")
    assert at(result.merged, path) == at(new, path)
