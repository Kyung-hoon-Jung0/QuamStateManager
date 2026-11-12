"""Multi-level relative pointers (``#../../x``) and Union[Component, str].

quam reads ``#../../gate_fidelity/averaged`` on ``qubits.q1.macros.x.fidelity``
as ``qubits.q1.gate_fidelity.averaged``: the pointer is relative to the object
holding the leaf (``x``), and each ``../`` climbs one level.
"""
from quam_state_manager.core.pointer_resolver import relative_parts, resolve_pointer as resolve_value
from quam_state_manager.core.pointer_path import pointer_to_abs
from quam_state_manager.core.state_env_validate import judge
from quam_state_manager.core import store_revs


def _state():
    return {"qubits": {"q1": {
        "gate_fidelity": {"averaged": 0.9878},
        "macros": {"x": {"fidelity": "#../../gate_fidelity/averaged",
                         "pulse": "x180"},
                   "y": {"fidelity": "#../x/fidelity"}},
    }}}


LEAF = ("qubits", "q1", "macros", "x", "fidelity")


def test_relative_parts_climbs_one_level_per_step():
    assert relative_parts("#../../a/b", LEAF) == (("qubits", "q1"), ["a", "b"])
    assert relative_parts("#../a", LEAF) == (("qubits", "q1", "macros"), ["a"])
    assert relative_parts("#../../../../../a", LEAF) == (None, None)


def test_resolver_reads_the_quam_target():
    st = _state()
    assert resolve_value(st, "#../../gate_fidelity/averaged", LEAF) == 0.9878
    # chained: y.fidelity -> x.fidelity -> gate_fidelity.averaged
    y = ("qubits", "q1", "macros", "y", "fidelity")
    assert resolve_value(st, "#../x/fidelity", y) == 0.9878


def test_every_python_interpreter_agrees():
    want = ["qubits", "q1", "gate_fidelity", "averaged"]
    p = "#../../gate_fidelity/averaged"
    assert pointer_to_abs(p, list(LEAF)) == want
    assert store_revs.pointer_target_segs(list(LEAF), p) == want


def test_union_component_str_accepts_a_name_but_plain_component_does_not():
    union_ts = {"base": "component", "class": "quam.components.pulses.Pulse",
                "str_ok": True}
    plain_ts = {"base": "component", "class": "quam.components.pulses.Pulse"}
    assert judge("x180", union_ts)[0] is True
    assert judge("x180", plain_ts)[0] is False
    assert judge(3, union_ts)[0] is False


def test_probe_marks_union_component_str(monkeypatch):
    import typing
    from quam_state_manager.generator import probe_state_schema as p

    class Comp:  # stands in for a quam component class
        pass

    monkeypatch.setattr(p, "_is_component_class", lambda t: t is Comp)
    spec = p._map_type(typing.Union[Comp, str], "Union[Comp, str]")
    assert spec["base"] == "component" and spec.get("str_ok") is True
    plain = p._map_type(Comp, "Comp")
    assert plain["base"] == "component" and not plain.get("str_ok")


def test_a_mid_path_dotdot_climbs_from_the_object_a_reference_reached():
    # quam (get_relative_reference_value) steps attribute by attribute; the
    # ".." after opx_output climbs from the PORT it points at, not from xy.
    # A decoy xy["1"] shows textual normalisation would read the wrong value.
    st = {"ports": {"mw_outputs": {"con1": {"1": {
              "1": {"upconverter_frequency": 7.4e9},
              "2": {"upconverter_frequency": 7.6e9}}}}},
          "qubits": {"q": {"xy": {
              "opx_output": "#/ports/mw_outputs/con1/1/2",
              "1": {"upconverter_frequency": 999.0},
              "operations": {"p": {"f": "#../../opx_output/../1/upconverter_frequency"}}}}}}
    leaf = ("qubits", "q", "xy", "operations", "p", "f")
    assert resolve_value(st, st["qubits"]["q"]["xy"]["operations"]["p"]["f"], leaf) == 7.4e9


def _op_state(xy_as_pointer=False):
    chan = {"operations": {"x180": {"amplitude": 0.1}}}
    st = {"channels": {"c1": chan}, "qubits": {"q1": {}, "q2": {"xy": {"operations": {}}}},
          "qubit_pairs": {"p": {"qubit_control": "#/qubits/q1", "qubit_target": "#/qubits/q2",
                                "macros": {"cz": {"flux_pulse_qubit": "x180"}}}}}
    st["qubits"]["q1"]["xy"] = "#/channels/c1" if xy_as_pointer else chan
    return st


def test_a_pointer_channel_counts_as_its_target():
    from quam_state_manager.core.state_env_validate import operation_name_problem as onp
    st = _op_state(xy_as_pointer=True)
    assert onp(st, "qubits.q1.macros.x.pulse", "x180") is None
    assert "does not exist" in onp(st, "qubits.q1.macros.x.pulse", "nope")


def test_a_pair_name_is_looked_up_on_the_moving_qubit_only():
    from quam_state_manager.core.state_env_validate import operation_name_problem as onp
    st = _op_state()
    path = "qubit_pairs.p.macros.cz.flux_pulse_qubit"
    assert onp(st, path, "x180") is None                     # default moving = control = q1
    st["qubit_pairs"]["p"]["moving_qubit"] = "target"        # q2 has no x180
    assert "qubits.q2" in onp(st, path, "x180")
    st["qubit_pairs"]["p"]["moving_qubit"] = "control"
    st["qubits"]["q1"]["z"] = {"operations": {"x180": {}}}   # two on the moving qubit
    assert "not unique" in onp(st, path, "x180")


def test_a_pair_verdict_is_not_memoised_past_a_change_on_the_qubit():
    import copy
    from quam_state_manager.core import state_env_validate as sev
    from tests.test_type_policy import MANIFEST, _ts, _field
    man = copy.deepcopy(MANIFEST)
    man["classes"]["q.Root"]["fields"]["qubit_pairs"] = _field(
        _ts("dict", item=_ts("component", **{"class": "q.Pair"})))
    man["classes"]["q.Pair"] = {"importable": True, "canonical": "q.Pair", "bases": [],
                                "is_dataclass": True, "error": None, "fields": {
        "qubit_control": _field(_ts("component", **{"class": "q.Transmon"})),
        "qubit_target": _field(_ts("component", **{"class": "q.Transmon"})),
        "macros": _field(_ts("dict", item=_ts("component", **{"class": "q.CZ"})))}}
    man["classes"]["q.CZ"] = {"importable": True, "canonical": "q.CZ", "bases": [],
                              "is_dataclass": True, "error": None, "fields": {
        "flux_pulse_qubit": _field(_ts("component", **{"class": "q.Pulse"},
                                       raw="typing.Union[q.Pulse, str]"))}}
    st = {"__class__": "q.Root", "qubits": {
              "q1": {"__class__": "q.Transmon", "xy": {"__class__": "q.Channel", "operations": {
                  "x180": {"__class__": "q.Pulse", "amplitude": 0.1}}}},
              "q2": {"__class__": "q.Transmon"}},
          "qubit_pairs": {"p": {"__class__": "q.Pair", "qubit_control": "#/qubits/q1",
                                "qubit_target": "#/qubits/q2", "macros": {"cz": {
                                    "__class__": "q.CZ", "flux_pulse_qubit": "x180"}}}}}

    class Memo:   # a memo whose tokens only see a chunk's OWN section
        def __init__(self):
            self.d = {}

        def get(self, k1, k2):
            return self.d.get((k1, k2))

        def put(self, k1, k2, v):
            self.d[(k1, k2)] = v

    def flagged(memo):
        res = sev.analyze_state(st, man, _chunks=memo)["findings"]
        return [f for f in res if f.get("field") == "flux_pulse_qubit"]

    memo = Memo()
    assert flagged(memo) == []
    # rename the op on q1 (another section): the pair's verdict must follow
    ops = st["qubits"]["q1"]["xy"]["operations"]
    ops["x180_new"] = ops.pop("x180")
    del memo.d[("qubits", "q1")]          # q1's own token moved
    got = flagged(memo)
    assert len(got) == 1 and got[0]["code"] == "missing_operation", got


def test_a_warm_cache_without_str_ok_reads_the_annotation():
    old = {"base": "component", "class": "quam.components.pulses.Pulse",
           "raw": "typing.Union[quam.components.pulses.Pulse, str]"}
    assert judge("x180", old)[0] is True
    other = {"base": "component", "class": "x.Pulse",
             "raw": "typing.Union[x.Pulse, typing.List[str]]"}
    assert judge("x180", other)[0] is False
    assert judge("x180", {"base": "component", "raw": "x.Pulse"})[0] is False


def test_a_dotdot_in_the_middle_climbs_too():
    # holder object x; ../../ -> q1; b/../gate_fidelity -> q1.gate_fidelity
    p = "#../../b/../gate_fidelity/averaged"
    # the text alone cannot tell where a mid-path ".." lands
    assert relative_parts(p, LEAF) == (None, None)
    assert pointer_to_abs(p, list(LEAF)) is None
    # the resolver steps like quam: "b" must exist before ".." climbs from it
    assert resolve_value(_state(), p, LEAF) == p
    assert pointer_to_abs(p, list(LEAF), _state()) is None
    ok = "#../../macros/../gate_fidelity/averaged"
    assert resolve_value(_state(), ok, LEAF) == 0.9878
    assert pointer_to_abs(ok, list(LEAF), _state()) == ["qubits", "q1", "gate_fidelity", "averaged"]


def test_store_revs_and_regen_merge_share_the_rule():
    from quam_state_manager.core.regen_merge import _one_hop
    p = "#../../gate_fidelity/averaged"
    assert _one_hop(_state(), p, LEAF) == 0.9878
    mid = "#../../macros/../gate_fidelity/averaged"
    assert _one_hop(_state(), mid, LEAF) == 0.9878
    assert store_revs.pointer_target_segs(list(LEAF), mid, _state()) \
        == ["qubits", "q1", "gate_fidelity", "averaged"]


def test_the_edit_target_is_the_leaf_the_value_is_read_from():
    from quam_state_manager.core.pointer_path import resolve_field_target
    st = {"a": {"ref": "#/b/c", "d": 1, "h": {"f": "#../ref/../d"}}, "b": {"c": {}, "d": 2}}
    assert resolve_value(st, "#../ref/../d", ("a", "h", "f")) == 2
    ft = resolve_field_target(st, "a.h.f")
    assert ft["resolved_path"] == "b.d" and ft["resolved_value"] == 2, ft
    assert store_revs.pointer_target_segs(["a", "h", "f"], "#../ref/../d", st) == ["b", "d"]
    assert pointer_to_abs("#../ref/../d", ["a", "h", "f"], st) == ["b", "d"]


def test_a_dotdot_from_a_scalar_does_not_resolve():
    # quam asks the reached object for .parent; an int has none
    st = {"ops": {"x90": {"length": "#../x180/length", "amplitude": 0.25},
                  "x180": {"length": 40, "amplitude": 0.5,
                           "detuning": "#../x90/length/../amplitude"}}}
    leaf = ("ops", "x180", "detuning")
    p = st["ops"]["x180"]["detuning"]
    assert resolve_value(st, p, leaf) == p
    assert pointer_to_abs(p, list(leaf), st) is None
    st2 = {"a": {"v": 5, "d": 1, "h": {"f": "#../v/../d"}}}
    assert resolve_value(st2, "#../v/../d", ("a", "h", "f")) == "#../v/../d"


def test_a_self_alias_crossed_mid_path_is_followed():
    # r = "#./q" names a.q; ".." from a.q climbs to a, so w is a.w (quam: 9)
    st = {"a": {"r": "#./q", "q": {"w": 1}, "w": 9, "h": {"f": "#../r/../w"}}}
    leaf = ("a", "h", "f")
    assert resolve_value(st, "#../r/../w", leaf) == 9
    assert pointer_to_abs("#../r/../w", list(leaf), st) == ["a", "w"]


def test_a_self_ref_that_climbs_is_a_parent_ref():
    # "#./" is the holding object, so "#./../1/delay" on port 2 is port 1's
    holder = ["ports", "ao", "con1", "5", "2", "delay"]
    want = ["ports", "ao", "con1", "5", "1", "delay"]
    assert pointer_to_abs("#./../1/delay", holder) == want
    st = {"ports": {"ao": {"con1": {"5": {"1": {"delay": 20}, "2": {"x": {"y": 1}}}}}}}
    h2 = ["ports", "ao", "con1", "5", "2", "z"]
    assert pointer_to_abs("#./x/../../1/delay", h2, st) == want
    assert pointer_to_abs("#./x/../../1/delay", h2) is None


def test_store_revs_and_one_hop_walk_a_self_ref_through_objects():
    from quam_state_manager.core.regen_merge import _one_hop
    st = {"a": {"h": {"ref": "#/b/c", "f": "#./ref/../d", "d": 1}}, "b": {"c": {}, "d": 2}}
    leaf = ("a", "h", "f")
    assert store_revs.pointer_target_segs(list(leaf), "#./ref/../d", st) == ["b", "d"]
    assert store_revs.pointer_target_segs(list(leaf), "#./ref/../d") is None
    assert _one_hop(st, "#./ref/../d", leaf) == 2


_R3 = {"a": {"ref": "#/b/c", "d": 1, "h": {"f": "#../ref/../d"}}, "b": {"c": {}, "d": 2}}


def test_regen_and_diagram_walkers_use_the_one_rule():
    from quam_state_manager.core.regen_merge import _hop, _pointer_target, _land
    from quam_state_manager.core.diagram_add import _abs_target
    leaf = ("a", "h", "f")
    assert _hop(_R3, "#../ref/../d", leaf) == (2, ("b", "d"))
    assert _pointer_target("a.h.f", "#../ref/../d", _R3) == "b.d"
    assert _land(_R3, "a.h.f") == "b.d"
    assert _abs_target("#../ref/../d", leaf, _R3) == ("b", "d")
    # without the document the answer is unknown, never a text guess (a.d)
    assert _pointer_target("a.h.f", "#../ref/../d") is None
    assert _abs_target("#../ref/../d", leaf) == ()


def test_rename_rewrites_and_pulse_refs_read_with_the_document():
    from quam_state_manager.core.pulse_index import rewrite_referrer_pointer
    from quam_state_manager.core.pulse_refs import read
    new = rewrite_referrer_pointer("#../ref/../d", "a.h.f", "b.d", "b.e", _R3)
    assert new is not None and new.endswith("e"), new
    assert rewrite_referrer_pointer("#../ref/../d", "a.h.f", "b.d", "b.e") is None
    r = read(_R3, ["a", "h"], "#../ref/../d")
    assert (r.ok, r.value) == (True, 2), r


def test_the_autofit_alias_walk_follows_a_multi_level_alias():
    from quam_state_manager.core.autofit.families import resolve_alias_path
    st = {"qubits": {"q1": {"xy": {"operations": {
        "x180_D": {"amplitude": 0.1}}},
        "macros": {"x": {"op": "#../../xy/operations/x180_D"}}}}}

    def value_of(dot):
        node = st
        for s in dot.split("."):
            node = node[s]
        return node

    got = resolve_alias_path("qubits.q1.macros.x.op.amplitude", value_of)
    assert got == "qubits.q1.xy.operations.x180_D.amplitude"


def test_a_copied_pulse_keeps_the_authors_level_count():
    from quam_state_manager.core.pulse_index import _derive_pointer, _flavor
    holder = ["qubits", "q2", "macros", "x", "fidelity"]
    target = ["qubits", "q2", "gate_fidelity", "averaged"]
    f = _flavor("#../../gate_fidelity/averaged")
    assert f == "#../../"
    assert _derive_pointer(f, holder, target) == "#../../gate_fidelity/averaged"
    # one level still reads as before
    assert _derive_pointer("#../", holder, ["qubits", "q2", "macros", "y"]) == "#../y"
