"""Line identity, rather than hardware address, is the calibration oracle.

No merge internals are used by the oracle. Physical identity fields are checked
against the destination address; EVERY other port leaf (including unknown future
fields, lists, and pointer-to-object fields) must follow its source line. An
explicit Populate edit is the sole value exception. Crosstalk is a directed
line-to-line relation, including self terms, independent of key spelling/type.

Unreferenced source ports must retain their values if kept, or have every lost
leaf reported. A removed line is outside the carry contract, but its crosstalk
label remains meaningful when a retained coefficient names its former address.
"""
from copy import deepcopy
import inspect
import random
import time

import pytest

from quam_state_manager.core import regen_merge


SEEDS = range(100)
MOVE_KINDS = (
    "swap", "cycle", "permutation", "empty", "fem", "controller",
    "xy_readout", "input_swap", "removal", "rename", "populate", "stale",
    "pointer_delay", "digital_swap", "pair_swap", "pump_swap", "shared_swap",
    "rename_strings", "analog_input_swap",
)
IDENTITY = {"controller_id", "fem_id", "port_id", "__class__"}


def at(root, path):
    for key in path:
        if isinstance(root, list):
            root = root[int(key)]
        else:
            root = root[key if key in root else int(key)]
    return root


def leaves(node, path=()):
    if isinstance(node, dict):
        for key, value in node.items():
            yield from leaves(value, path + (str(key),))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from leaves(value, path + (str(index),))
    else:
        yield path, node


def land(doc, path, seen=()):
    """Independent QUAM pointer interpreter, including intermediate aliases."""
    assert path not in seen, f"pointer cycle: {path}"
    node = doc
    prefix = ()
    for key in path:
        if isinstance(node, str) and node.startswith(("#/", "#./", "#../")):
            _, prefix = land(doc, prefix, seen + (path,))
            node = at(doc, prefix)
        node = at(node, (key,))
        prefix += (key,)
    if isinstance(node, str) and node.startswith(("#/", "#./", "#../")):
        target = () if node.startswith("#/") else prefix[:-1]
        for seg in node[1:].split("/"):
            if seg == "..":
                target = target[:-1]
            elif seg and seg != ".":
                target += (seg,)
        return land(doc, target, seen + (path,))
    return node, prefix


def concrete(doc, path, seen=()):
    node, target = land(doc, path)
    if isinstance(node, (dict, list)):
        assert target not in seen, f"object pointer cycle: {target}"
        if isinstance(node, dict):
            return {str(k): concrete(doc, target + (str(k),), seen + (target,))
                    for k in node}
        return [concrete(doc, target + (str(i),), seen + (target,))
                for i in range(len(node))]
    return node


def ports(state):
    return {("ports", kind, con, fem, str(number)): port
            for kind, controllers in state.get("ports", {}).items()
            for con, fems in controllers.items()
            for fem, slots in fems.items() for number, port in slots.items()}


def line_ports(doc):
    result = {}
    for path, value in leaves(doc.get("wiring", {})):
        if isinstance(value, str) and value.startswith("#"):
            _, target = land(doc, ("wiring",) + path)
            if target[:1] == ("ports",) and len(target) >= 5:
                result[path] = target[:5]
    return result


def line_identity_oracle(old_state, old_wiring, merged_state, new_wiring, *,
                         qubit_sources=None, protected=None, reported=(), dropped=(), rebuilt=None, schemas=None, stats=None):
    """Pure assertion over the two documents; inputs are never modified.

    ``protected`` maps destination leaf paths to deliberate new values.
    ``reported`` contains loss paths from MergeStats, used only for unused ports.
    ``qubit_sources`` is the wizard's new-id -> source-id identity record.
    """
    old = dict(old_state, wiring=old_wiring.get("wiring", old_wiring))
    new = dict(merged_state, wiring=new_wiring.get("wiring", new_wiring))
    op, np = ports(old), ports(new)
    old_lines, new_lines = line_ports(old), line_ports(new)
    sources = qubit_sources or {}
    protected = protected or {}
    errors = []

    def identity(line):
        if line[:1] == ("qubits",):
            return (line[0], sources.get(line[1], line[1]), *line[2:])
        return line

    def owners(lines, normalize=False):
        out = {}
        for line, port in lines.items():
            out.setdefault(port, set()).add(identity(line) if normalize else line)
        return out

    oo, no = owners(old_lines), owners(new_lines, True)
    for port, lines in no.items():
        if len(lines) > 1 and not (port[1] == "mw_outputs" and
                len(np[port].get("upconverters", {})) >= len(lines)):
            assert all(line in old_lines and
                       concrete(old, old_lines[line] + ("shareable",)) is True
                       for line in lines), ("unshareable collision", port, lines)

    # Resolve every element pointer, even ones not represented in wiring.
    for top in merged_state:
        if top in ("ports", "wiring"):
            continue
        for path, value in leaves(merged_state[top], (top,)):
            if isinstance(value, str) and value.startswith(("#/", "#./", "#../")):
                concrete(new, path)
                _, target = land(new, path)
                if target[:1] == ("ports",):
                    assert target[:5] in np, ("missing element port", path, target)
                    assert target[:5] in no, ("element port absent from wiring", path)
                    if path in new_lines:
                        assert target[:5] == new_lines[path], ("wrong element port", path)

    def relation(port, row, current_owners, self_labels=None):
        out = {}
        for number, value in row.items():
            address = port[:-1] + (str(number),)
            labels = self_labels if address == port and self_labels else current_owners.get(address)
            if not labels:
                # A retained coefficient may name a removed line/unused slot.
                former = oo.get(address, set())
                surviving = {identity(line) for line in new_lines}
                labels = (former if former and former.isdisjoint(surviving)
                          else {("unused", *address)})
            out[tuple(sorted(labels))] = value
        return out

    for line, dest in new_lines.items():
        source_line = identity(line)
        if source_line not in old_lines:
            if rebuilt is not None and dest in oo and not any(l in old_lines for l in no[dest]):
                assert concrete(new, dest) == at(rebuilt, dest), ("fresh defaults", dest)
                assert stats and any(p == ".".join(dest) for p, _, _ in stats.ports_fresh)
            continue  # A genuinely new line has no calibration to carry.
        src = old_lines[source_line]
        before, after = concrete(old, src), concrete(new, dest)
        for key, expected in zip(("controller_id", "fem_id", "port_id"), dest[2:]):
            assert str(after[key]) == expected, ("physical identity", line, key)
        a = {k: v for k, v in before.items() if k not in IDENTITY}
        b = {k: v for k, v in after.items() if k not in IDENTITY}
        if rebuilt is not None:
            built = at(rebuilt, dest)
            a = {**{k: deepcopy(v) for k, v in built.items() if k not in IDENTITY}, **a}
            source_ports = {old_lines[l] for l in no[dest] if l in old_lines}
            if len(source_ports) > 1:
                inputs = [concrete(old, p) for p in source_ports]
                expected = {k: deepcopy(v) for k, v in built.items() if k not in IDENTITY}
                for key in set().union(*(p.keys() for p in inputs)) - IDENTITY:
                    values = [p.get(key, "<absent>") for p in inputs]
                    if all(v == values[0] for v in values) and values[0] != "<absent>":
                        expected[key] = values[0]
                    else:
                        assert stats and any(c["port"] == ".".join(dest) and c["field"] == key
                                             for c in stats.ports_conflict), ("unreported conflict", key)
                a = expected
            elif schemas and built.get("__class__") in schemas:
                for key in list(a):
                    if key not in built and key not in schemas[built["__class__"]]:
                        a.pop(key)
                        assert stats and {"port": ".".join(dest), "field": key,
                            "owner": ".".join(line[1:-1])} in stats.port_fields_dropped
            if "upconverters" in before and "upconverter_frequency" in built:
                index = at(old, ("qubits", source_line[1], source_line[2])).get("upconverter", 1)
                freq = before["upconverters"][str(index)]["frequency"]
                a.pop("upconverters", None)
                a["upconverter_frequency"] = freq
                assert at(merged_state, ("qubits", line[1], line[2], "upconverter")) == 1
        for field, value in list(leaves(a)):
            full = dest + field
            if full in protected:
                at(a, field[:-1])[field[-1]] = deepcopy(protected[full])
        if "crosstalk" in a:
            retained = {}
            for number, value in a["crosstalk"].items():
                column = src[:-1] + (str(number),)
                old_labels = oo.get(column, set())
                targets = ({dest} if column == src else
                           {new_lines[l] for l in new_lines if identity(l) in old_labels})
                reason = None
                if targets and any(t[:-1] != dest[:-1] for t in targets):
                    reason = "lines on different FEMs"
                elif not targets and no.get(dest[:-1] + (str(number),), set()) - old_labels:
                    reason = "column port now drives another line"
                if reason:
                    dot = ".".join(src + ("crosstalk", str(number)))
                    assert {"path": dot, "reason": reason} in dropped, ("unreported drop", dot)
                else:
                    retained[number] = value
            a["crosstalk"] = retained
        for values, address, own in ((a, src, oo), (b, dest, no)):
            if "crosstalk" in values:
                values["crosstalk"] = relation(address, values["crosstalk"], own, no.get(dest))
        if a != b:
            changed = [k for k in a.keys() | b.keys() if a.get(k) != b.get(k)]
            errors.append((source_line, src, dest, changed, a, b))

    for path in op.keys() - oo.keys() - no.keys():
        if path in np:
            assert concrete(old, path) == concrete(new, path), ("unused port changed", path)
        else:
            for leaf, _ in leaves(op[path], path):
                if leaf[-1] != "__class__":
                    dot = ".".join(leaf)
                    assert any(dot == p or dot.startswith(p + ".") for p in reported), (
                        "unused port silently dropped", dot)
    if errors:
        changed = sorted({field for error in errors for field in error[3]})
        raise AssertionError(f"calibrated fields {changed} differ: {errors}")


def port_defaults(address, value=0):
    _, kind, con, fem, number = address
    p = {"__class__": "quam.components.ports.Port", "controller_id": con,
         "fem_id": int(fem), "port_id": int(number), "delay": value,
         "shareable": False}
    if kind in ("analog_outputs", "mw_outputs"):
        p.update(offset=value / 1000, feedforward_filter=[value / 100, .2],
                 exponential_filter=[[.1, value + 1]],
                 iir_filter={"feedforward": [.1, value / 10], "feedback": [.3]})
    if kind == "analog_outputs":
        p["crosstalk"] = {}
    if kind == "mw_outputs":
        p.update(band=2 if value else 1, full_scale_power_dbm=-value / 10,
                 upconverters={"1": {"frequency": 5e9 + value * 1e6}})
    if kind.endswith("inputs"):
        p.update(gain_db=value / 10, offset=value / 1000)
    if kind == "digital_outputs":
        p.update(buffer=value + 2)
    return p


def set_port(state, address, port):
    node = state
    for seg in address[:-1]:
        node = node.setdefault(seg, {})
    node[address[-1]] = port


def synthetic_chip(seed, count=8):
    """Extend the fuzz chip shape with all cabling kinds and fresh rebuilds."""
    rng = random.Random(seed)
    state = {"qubits": {}, "qubit_pairs": {}, "twpas": {}, "ports": {}}
    wiring = {"wiring": {}}

    def add(collection, name, line, endpoint, kind, con, fem, number):
        path = (collection, name, line, endpoint)
        address = ("ports", kind, con, str(fem), str(number))
        node = wiring["wiring"]
        for seg in path[:-1]:
            node = node.setdefault(seg, {})
        node[endpoint] = "#/" + "/".join(address)
        channel = state[collection].setdefault(name, {}).setdefault(line, {})
        channel[endpoint] = "#/wiring/" + "/".join(path)
        set_port(state, address, port_defaults(address, rng.randint(10, 500)))

    for i in range(count):
        q = f"q{i}"
        add("qubits", q, "z", "opx_output", "analog_outputs", "con1", 5, i + 1)
        for line, number in (("xy", 2*i+1), ("resonator", 2*i+2)):
            add("qubits", q, line, "opx_output", "mw_outputs", "con1", 1, number)
        add("qubits", q, "resonator", "opx_input", "mw_inputs", "con1", 1, i+1)
        add("qubits", q, "acquire", "opx_input", "analog_inputs", "con2", 5, i+1)
        add("qubits", q, "trigger", "opx_output", "digital_outputs", "con2", 5, i+1)
        # The two MW outputs share a literal LO source through a pointer chain.
        xy = ("ports", "mw_outputs", "con1", "1", str(2*i+1))
        rr = xy[:-1] + (str(2*i+2),)
        at(state, xy)["upconverters"]["1"]["frequency"] = 5e9 + i * 1e8 + seed * 1e4
        at(state, rr)["upconverters"]["1"]["frequency"] = (
            "#/" + "/".join(xy) + "/upconverters/1/frequency")
    for line, kind, fem, number in (("coupler", "analog_outputs", 6, 1),
                                     ("cross_resonance", "mw_outputs", 2, 1)):
        add("qubit_pairs", "p0", line, "opx_output", kind, "con2", fem, number)
    state["qubit_pairs"]["p0"].update(qubit_control="#/qubits/q0",
                                         qubit_target="#/qubits/q1")
    for i in range(2):
        add("twpas", f"t{i}", "pump", "opx_output", "mw_outputs", "con2", 2, i+3)
    # An unreferenced spare on a retained FEM and one on a removed FEM.
    for fem in (5, 9):
        address = ("ports", "analog_outputs", "con1", str(fem), "99")
        set_port(state, address, port_defaults(address, 93))
    lf = state["ports"]["analog_outputs"]["con1"]["5"]
    for i in range(count):
        lf[str(i+1)]["crosstalk"] = {
            (str(j+1) if (i+j+seed) % 2 else j+1): (i+1)*.01+(j+1)*.0001
            for j in range(count)}
    # Relative pointers stay local when a whole calibrated port moves.
    p = lf["1"]
    p["delay"] = "#./offset"
    return state, wiring


def build_fresh(old, wiring):
    new = {k: deepcopy(v) for k, v in old.items() if k != "ports"}
    new["ports"] = {}
    # Only allocated slots appear, as in the builder; no copied calibration.
    addresses = {tuple(v[2:].split("/")) for _, v in leaves(wiring["wiring"])
                 if isinstance(v, str) and v.startswith("#/ports/")}
    for address in addresses:
        set_port(new, address, port_defaults(address))
    return new


def make_case(kind, seed, count=8):
    old, ow = synthetic_chip(seed, count)
    nw = deepcopy(ow)
    rng = random.Random(seed)
    kwargs, oracle = {}, {}
    w = nw["wiring"]

    def rotate(paths):
        values = [at(w, p) for p in paths]
        for p, value in zip(paths, values[1:] + values[:1]):
            at(w, p[:-1])[p[-1]] = value

    zs = [("qubits", f"q{i}", "z", "opx_output") for i in range(count)]
    chosen = rng.sample(zs, 2)
    if kind in ("swap", "removal", "rename", "rename_strings", "populate", "pointer_delay"):
        if kind == "pointer_delay":
            chosen = [zs[0], zs[1]]
            p = old["ports"]["analog_outputs"]["con1"]["5"]["1"]
            p["delay"] = "#/ports/analog_outputs/con1/5/2/delay"
        rotate(chosen)
    elif kind == "cycle":
        rotate(rng.sample(zs, 3 + seed % 4))
    elif kind == "permutation":
        paths = rng.sample(zs, len(zs))
        rotate(paths)  # One full, nontrivial permutation; never an accidental no-op.
    elif kind in ("empty", "fem", "controller", "stale"):
        p = chosen[0]
        address = at(w, p).split("/")
        if kind == "fem":
            address[-2] = "6"
        elif kind == "controller":
            address[-3] = "con2"
        else:
            address[-1] = "70"
        at(w, p[:-1])[p[-1]] = "/".join(address)
        if kind == "stale":
            address = tuple(address[1:])
            set_port(old, address, port_defaults(address, 999))
    elif kind == "xy_readout":
        for line in ("xy", "resonator"):
            rotate([("qubits", f"q{i}", line, "opx_output") for i in (0, 1)])
    elif kind in ("input_swap", "digital_swap", "analog_input_swap"):
        line, endpoint = ("resonator", "opx_input") if kind == "input_swap" else (
            "trigger", "opx_output")
        if kind == "analog_input_swap":
            line, endpoint = "acquire", "opx_input"
        rotate([("qubits", f"q{i}", line, endpoint) for i in (0, 1)])
    elif kind == "pair_swap":
        rotate([("qubit_pairs", "p0", "coupler", "opx_output"), zs[0]])
    elif kind == "pump_swap":
        rotate([("twpas", f"t{i}", "pump", "opx_output") for i in (0, 1)])
    elif kind == "shared_swap":
        # Two readout outputs share each feedline; the whole groups swap.
        for wiring in (ow, nw):
            for a, b in ((0, 1), (2, 3)):
                wiring["wiring"]["qubits"][f"q{b}"]["resonator"]["opx_output"] = (
                    wiring["wiring"]["qubits"][f"q{a}"]["resonator"]["opx_output"])
        for i in (0, 2):
            at(old, ("ports", "mw_outputs", "con1", "1", str(2*i+2)))["shareable"] = True
        for a, b in ((0, 2), (1, 3)):
            rotate([("qubits", f"q{i}", "resonator", "opx_output") for i in (a, b)])
    else:
        raise AssertionError(kind)
    if kind == "rename_strings":
        for port in old["ports"]["analog_outputs"]["con1"]["5"].values():
            port["crosstalk"] = {str(k): v for k, v in port["crosstalk"].items()}
    template = deepcopy(old)
    if kind == "removal":
        del template["qubits"]["q7"]
        del w["qubits"]["q7"]
    if kind in ("rename", "rename_strings"):
        template["qubits"]["q8"] = template["qubits"].pop("q0")
        w["qubits"]["q8"] = w["qubits"].pop("q0")
        # Fresh builder references are emitted under the new id.
        def rewrite(node):
            if isinstance(node, dict):
                return {k: rewrite(v) for k, v in node.items()}
            return node.replace("/q0/", "/q8/").replace("#/qubits/q0", "#/qubits/q8") if isinstance(node, str) else node
        template = rewrite(template)
        oracle["qubit_sources"] = {"q8": "q0"}
    new = build_fresh(template, nw)
    if kind == "populate":
        dest = tuple(at(w, chosen[0])[2:].split("/")) + ("offset",)
        at(new, dest[:-1])[dest[-1]] = .789
        kwargs["protect_paths"] = {".".join(dest)}
        oracle["protected"] = {dest: .789}
    return old, ow, new, nw, kwargs, oracle


def run_case(case):
    old, ow, new, nw, kwargs, oracle = case
    before = deepcopy(case)
    source, sw = old, ow
    if oracle.get("qubit_sources"):
        renames = regen_merge.source_renames(oracle["qubit_sources"], old, new["qubits"])
        source, sw, _, _ = regen_merge.rename_source_qubits(old, ow, renames, new, nw)
    start = time.perf_counter()
    result = regen_merge.merge_states(source, new, old_wiring=sw, new_wiring=nw, **kwargs)
    elapsed = time.perf_counter() - start
    assert case == before, "merge/oracle must not mutate the source or builder inputs"
    line_identity_oracle(old, ow, result.merged, nw,
                         reported=result.stats.residual_lost, dropped=result.stats.crosstalk_dropped,
                         rebuilt=new, stats=result.stats, **oracle)
    assert case == before
    return result, elapsed


def known_finding(kind, seed):
    """Static scope of known failures; never classify by catching oracle errors."""
    if kind in ('fem', 'controller', 'pair_swap', 'rename', 'removal', 'xy_readout', 'pointer_delay', 'populate'):
        return None
    if kind in ("fem", "controller", "pair_swap"):
        return "F1: crosstalk cannot follow a line across hardware scopes"
    if kind == "xy_readout":
        return "F2: shared LO pointer retains the source hardware address"
    if kind == "pointer_delay":
        return "F3: moved absolute delay pointer becomes a self-reference"
    if kind == "rename":
        return "F4: qubit_sources preprocessing raises on integer crosstalk keys"
    selected = random.Random(seed).sample(range(8), 2)
    if kind == "removal" and 7 in selected:
        return "F5: removed crosstalk target collides with a moved column"
    if kind == "populate" and selected[0] == 0:
        return "F6: protecting a pointer target drops an unprotected delay"
    return None



CASES = []
for _kind in MOVE_KINDS:
    # 100 independent seeds for EACH cycle length 3, 4, 5, and 6.
    for _seed in range(400) if _kind == "cycle" else SEEDS:
        _finding = known_finding(_kind, _seed)
        _marks = (pytest.mark.xfail(strict=True, reason=_finding,
                  raises=TypeError if _kind == "rename" else AssertionError),) if _finding else ()
        CASES.append(pytest.param(_kind, _seed, id=f"{_kind}-{_seed}", marks=_marks))


@pytest.mark.parametrize("kind,seed", CASES)
def test_seeded_port_moves(kind, seed):
    run_case(make_case(kind, seed))


def test_thirty_qubit_full_lf_permutation():
    old, ow, _, nw, kwargs, oracle = make_case("permutation", 314159, count=30)
    # Allocate exactly the 30 used LF outputs: no fixed spare or single-port
    # coupler scope can make "all LF ports" a partial permutation.
    del old["ports"]["analog_outputs"]["con1"]["5"]["99"]
    del old["ports"]["analog_outputs"]["con1"]["9"]
    del old["ports"]["analog_outputs"]["con2"]
    del old["qubit_pairs"]["p0"]["coupler"]
    for wiring in (ow, nw):
        del wiring["wiring"]["qubit_pairs"]["p0"]["coupler"]
    # Permute LF input and digital slots as well as the entire LF output FEM.
    for line, endpoint in (("acquire", "opx_input"), ("trigger", "opx_output")):
        slots = [nw["wiring"]["qubits"][f"q{i}"][line] for i in range(30)]
        values = [slot[endpoint] for slot in slots]
        for slot, value in zip(slots, values[1:] + values[:1]):
            slot[endpoint] = value
    new = build_fresh(old, nw)
    result, elapsed = run_case((old, ow, new, nw, kwargs, oracle))
    lf_moves = [p for p, _, _ in result.stats.ports_moved if ".analog_outputs." in p]
    assert len(lf_moves) == 30
    assert elapsed < 5.0, elapsed
    print(f"30-qubit full LF permutation: {elapsed:.3f}s")


def minimal_chip(assignments, rows=None):
    """One endpoint per named line; no unrelated chip structure."""
    state, wiring = {"ports": {}}, {"wiring": {}}
    for path, address in assignments.items():
        node, wire = state, wiring["wiring"]
        for seg in path[:-1]:
            node, wire = node.setdefault(seg, {}), wire.setdefault(seg, {})
        node[path[-1]] = "#/wiring/" + "/".join(path)
        wire[path[-1]] = "#/" + "/".join(address)
        port = port_defaults(address, 10 * int(address[-1]))
        if rows and address in rows:
            port["crosstalk"] = rows[address]
        set_port(state, address, port)
    return state, wiring


Z0 = ("qubits", "q0", "z", "opx_output")
Z1 = ("qubits", "q1", "z", "opx_output")
Z2 = ("qubits", "q2", "z", "opx_output")
P1 = ("ports", "analog_outputs", "con1", "5", "1")
P2 = P1[:-1] + ("2",)
P3 = P1[:-1] + ("3",)


def small_case(assignments, destinations, rows=None):
    old, ow = minimal_chip(assignments, rows)
    template, nw = minimal_chip(destinations)
    new = build_fresh(template, nw)
    return old, ow, new, nw, {}, {}


@pytest.mark.parametrize("destination", [P1[:3] + ("6", "1"),
                                        P1[:2] + ("con2", "5", "1")], ids=["fem", "controller"])
def test_repro_cross_scope_crosstalk(destination):
    run_case(small_case({Z0: P1, Z1: P2}, {Z0: destination, Z1: P2},
                        {P1: {"1": .11, 2: .12}, P2: {"1": .21}}))


def test_repro_shared_lo_pointer():
    paths = [("qubits", f"q{i}", line, "opx_output")
             for i in range(2) for line in ("xy", "resonator")]
    addresses = [("ports", "mw_outputs", "con1", "1", str(i+1)) for i in range(4)]
    case = small_case(dict(zip(paths, addresses)),
                      dict(zip(paths, addresses[2:] + addresses[:2])))
    for xy, rr in zip(addresses[::2], addresses[1::2]):
        at(case[0], rr)["upconverters"]["1"]["frequency"] = (
            "#/" + "/".join(xy) + "/upconverters/1/frequency")
    run_case(case)


def test_repro_pointer_delay_swap():
    case = small_case({Z0: P1, Z1: P2}, {Z0: P2, Z1: P1})
    at(case[0], P1)["delay"] = "#/" + "/".join(P2) + "/delay"
    run_case(case)


def test_repro_rename_integer_crosstalk():
    renamed = ("qubits", "q8", "z", "opx_output")
    case = small_case({Z0: P1}, {renamed: P1}, {P1: {1: .11}})
    case[-1]["qubit_sources"] = {"q8": "q0"}
    run_case(case)


def test_repro_removed_target_collision():
    run_case(small_case({Z0: P1, Z1: P2, Z2: P3}, {Z0: P1, Z1: P3},
                        {P1: {"2": .12, "3": .13}}))


def test_repro_populate_pointer_target():
    case = small_case({Z0: P1}, {Z0: P2})
    at(case[0], P1)["delay"] = "#./offset"
    at(case[2], P2)["offset"] = .789
    case[-2]["protect_paths"] = {".".join(P2 + ("offset",))}
    case[-1]["protected"] = {P2 + ("offset",): .789}
    run_case(case)


@pytest.mark.xfail(strict=True, reason="F7: source-only pointer target loses delay", raises=AssertionError)
def test_repro_source_only_pointer_target():
    case = small_case({Z0: P1}, {Z0: P2})
    at(case[0], P1).update(delay="#./saved_delay", saved_delay=37)
    run_case(case)


def test_oracle_control():
    run_case(small_case({Z0: P1, Z1: P2}, {Z0: P2, Z1: P1},
                        {P1: {1: .11, "2": .12}, P2: {"1": .21}}))


@pytest.mark.parametrize("mutation", ["crosstalk", "delay"])
def test_oracle_rejects_merge_mutations(monkeypatch, mutation):
    test_oracle_control()  # Establish that the unmutated merge is green.
    if mutation == "crosstalk":
        source = inspect.getsource(regen_merge._carry_crosstalk)
        needle = 'number = target.rsplit(".", 1)[1]'
        assert needle in source
        namespace = dict(vars(regen_merge))
        # Leave row carry intact and break ONLY column renumbering.
        exec(source.replace(needle, "number = str(key)"), namespace)
        monkeypatch.setattr(regen_merge, "_carry_crosstalk", namespace["_carry_crosstalk"])
    else:
        original = regen_merge._ports_view

        def broken_delay(old, new, moves, fresh):
            view = original(old, new, moves, fresh)
            for dest in moves:
                path = tuple(dest.split("."))
                at(view, path)["delay"] = at(new, path)["delay"]
            return view

        monkeypatch.setattr(regen_merge, "_ports_view", broken_delay)
    with pytest.raises(AssertionError, match=mutation):
        test_oracle_control()


def test_oracle_rejects_unshareable_collision():
    case = small_case({Z0: P1, Z1: P2}, {Z0: P1, Z1: P1})
    with pytest.raises(AssertionError, match="unshareable collision"):
        run_case(case)


from tests.port_audit_cases import AUDIT_CASES












def test_multi_source_consensus_resolves_pointer_values():
    case = small_case({Z0: P1, Z1: P2}, {Z0: P3, Z1: P3})
    old, ow, new, nw, _, _ = case
    for path in (P1, P2):
        at(old, path)["shareable"] = True
    at(old, P1)["delay"] = "#./saved_delay"
    at(old, P1)["saved_delay"] = 37
    at(old, P2)["delay"] = 37
    at(new, P3)["shareable"] = True
    r = regen_merge.merge_states(old, new, old_wiring=ow, new_wiring=nw)
    assert at(r.merged, P3)["delay"] == 37
    assert not any(c["field"] == "delay" for c in r.stats.ports_conflict)
    line_identity_oracle(old, ow, r.merged, nw, rebuilt=new, stats=r.stats,
        reported=r.stats.residual_lost, dropped=r.stats.crosstalk_dropped)




def test_shared_source_self_crosstalk_stays_self_on_split():
    old, ow = minimal_chip({Z0: P1, Z1: P1}, {P1: {1: .11}})
    at(old, P1)["shareable"] = True
    template, nw = minimal_chip({Z0: P1, Z1: P2})
    new = build_fresh(template, nw)
    r = regen_merge.merge_states(old, new, old_wiring=ow, new_wiring=nw)
    assert at(r.merged, P1)["crosstalk"] == {1: .11}
    assert at(r.merged, P2)["crosstalk"] == {2: .11}
    assert not r.stats.crosstalk_dropped

    line_identity_oracle(old, ow, r.merged, nw, rebuilt=new, stats=r.stats,
        reported=r.stats.residual_lost, dropped=r.stats.crosstalk_dropped)
