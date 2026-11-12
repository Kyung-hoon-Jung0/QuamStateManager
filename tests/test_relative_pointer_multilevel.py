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


def test_a_warm_cache_without_str_ok_reads_the_annotation():
    old = {"base": "component", "class": "quam.components.pulses.Pulse",
           "raw": "typing.Union[quam.components.pulses.Pulse, str]"}
    assert judge("x180", old)[0] is True
    other = {"base": "component", "class": "x.Pulse",
             "raw": "typing.Union[x.Pulse, typing.List[str]]"}
    assert judge("x180", other)[0] is False
    assert judge("x180", {"base": "component", "raw": "x.Pulse"})[0] is False
