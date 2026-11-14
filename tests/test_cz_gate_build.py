"""The pure CZ gate build plan: rules, directions, copied layouts, Gaussian regression."""
from __future__ import annotations

import copy
import json

import pytest

from quam_state_manager.core import cz_gate_build as cgb
from quam_state_manager.core import gaussian_cz

SQUARE = "quam.components.pulses.SquarePulse"
G_UNI = "quam_builder.common.pulses.GaussianFilteredSquarePulse"
G_BI = ("quam_builder.architecture.superconducting.components.pulses."
        "GaussianFilteredSymmetricBipolarPulse")
LAB_GATE = "lab_pkg.TwoFluxGate"
LAB_PULSE = "lab_pkg.StepPulse"


@pytest.fixture(autouse=True)
def _plan_never_changes_merged(monkeypatch):
    """Every plan call in this module leaves its input deep-equal, and its
    creates share no object with the input."""
    real = cgb.plan

    def checked(merged, *a, **kw):
        before = copy.deepcopy(merged)
        res = real(merged, *a, **kw)
        assert merged == before
        ids = set()

        def walk(v):
            if isinstance(v, (dict, list)):
                ids.add(id(v))
                for x in (v.values() if isinstance(v, dict) else v):
                    walk(x)
        walk(merged)
        for _, value in res.get("creates", []):
            stack = [value]
            while stack:
                v = stack.pop()
                if isinstance(v, (dict, list)):
                    assert id(v) not in ids
                    stack.extend(v.values() if isinstance(v, dict) else v)
        # mutating the creates must not reach the input
        for _, value in res.get("creates", []):
            if isinstance(value, dict):
                value["__probe__"] = 1
        assert merged == before
        for _, value in res.get("creates", []):
            if isinstance(value, dict):
                value.pop("__probe__", None)
        return res
    monkeypatch.setattr(cgb, "plan", checked)


def _qubit(name):
    return {"id": name, "z": {"operations": {"const": {"amplitude": 0.1}}}}


def _pair(c, t, moving="control", coupler=True):
    p = {"id": f"{c}-{t}", "qubit_control": f"#/qubits/{c}",
         "qubit_target": f"#/qubits/{t}", "moving_qubit": moving, "macros": {}}
    if coupler:
        p["coupler"] = {"id": "c", "operations": {}}
    return p


def _chip(n=4):
    qs = [f"q{i}" for i in range(1, n + 1)]
    return {"qubits": {q: _qubit(q) for q in qs},
            "qubit_pairs": {f"q{i}-{i + 1}": _pair(f"q{i}", f"q{i + 1}")
                            for i in range(1, n)}}


def _macro_owned(state, pid, name, length=52, amp=0.2):
    """A gate in the default layout: the macro holds the pulse."""
    p = state["qubit_pairs"][pid]
    c, t = p["qubit_control"].split("/")[-1], p["qubit_target"].split("/")[-1]
    key = f"{name}_pulse_{c}_{t}"
    m = cgb._cz_skeleton()
    m["flux_pulse_qubit"] = {"id": key, "length": length, "amplitude": amp,
                             "axis_angle": None, "digital_marker": None,
                             "__class__": SQUARE}
    p["macros"][name] = m
    ref = f"#/qubit_pairs/{pid}/macros/{name}/flux_pulse_qubit"
    state["qubits"][c]["z"]["operations"][key] = {
        "id": None, "length": ref + "/length", "amplitude": ref + "/amplitude",
        "axis_angle": None, "digital_marker": None, "__class__": SQUARE}


def _op_owned(state, pid, name):
    """A gate in the reverse layout: the operation holds the pulse."""
    p = state["qubit_pairs"][pid]
    c, t = p["qubit_control"].split("/")[-1], p["qubit_target"].split("/")[-1]
    key = f"{name}_pulse_{c}_{t}"
    m = cgb._cz_skeleton()
    m["flux_pulse_qubit"] = key
    p["macros"][name] = m
    state["qubits"][c]["z"]["operations"][key] = {
        "id": key, "length": 40, "amplitude": 0.1, "__class__": SQUARE}


def _square(length=48, amp=0.3):
    return {"flux_pulse_qubit": {"class": SQUARE,
                                 "fields": {"length": length, "amplitude": amp}}}


def _plan(state, pid="q1-2", name="cz_new", **kw):
    kw.setdefault("gate_class", cgb.CZ_GATE)
    kw.setdefault("pulses", _square())
    return cgb.plan(state, pid, gate_name=name, **kw)


def _existing_paths(state):
    out = set()

    def walk(v, path):
        out.add(path)
        if isinstance(v, dict):
            for k, x in v.items():
                walk(x, f"{path}.{k}" if path else k)
    walk(state, "")
    return out


def _apply(state, creates):
    s = copy.deepcopy(state)
    for path, value in creates:
        parts = path.split(".")
        node = s
        for seg in parts[:-1]:
            node = node[seg]
        node[parts[-1]] = copy.deepcopy(value)
    return s


# --- name rule ---------------------------------------------------------

def test_name_must_start_with_cz_and_be_new():
    s = _chip()
    _macro_owned(s, "q1-2", "cz_old")
    assert "must start with cz_" in _plan(s, name="square")["error"]
    assert "already has a gate named cz_old" in _plan(s, name="cz_old")["error"]
    assert "creates" in _plan(s, name="cz_fresh")


def test_planning_the_same_name_twice_refuses_the_second():
    s = _chip()
    first = _plan(s)
    after = _apply(s, first["creates"])
    second = _plan(after)
    assert "error" in second and "creates" not in second


# --- direction ---------------------------------------------------------

def _dir_fixture(k):
    s = _chip(4)
    own, other = "q1-2", "q2-3"
    if k == 1:
        _macro_owned(s, own, "cz_a"); _macro_owned(s, own, "cz_b")
    elif k == 2:
        _op_owned(s, own, "cz_a"); _op_owned(s, own, "cz_b")
    elif k == 3:
        _macro_owned(s, own, "cz_a"); _op_owned(s, own, "cz_b")
    elif k == 4:
        _op_owned(s, other, "cz_a"); _op_owned(s, "q3-4", "cz_b")
    elif k == 5:
        _op_owned(s, other, "cz_a"); _macro_owned(s, "q3-4", "cz_b")
    elif k == 6:
        _macro_owned(s, other, "cz_a")
    elif k == 7:
        pass
    elif k == 8:
        _macro_owned(s, own, "cz_a"); _op_owned(s, other, "cz_b")
    elif k == 9:
        _op_owned(s, own, "cz_a"); _macro_owned(s, other, "cz_b")
    return s


@pytest.mark.parametrize("k,expected", [
    (1, cgb.MACRO_OWNS), (2, cgb.OPERATION_OWNS), (3, cgb.MACRO_OWNS),
    (4, cgb.OPERATION_OWNS), (5, cgb.MACRO_OWNS), (6, cgb.MACRO_OWNS),
    (7, cgb.MACRO_OWNS), (8, cgb.MACRO_OWNS), (9, cgb.OPERATION_OWNS)])
def test_nine_direction_fixtures(k, expected):
    s = _dir_fixture(k)
    assert cgb.choose_direction(s, "q1-2") == expected
    assert _plan(s)["direction"] == expected


def test_the_cz_alias_is_not_counted_as_a_gate():
    s = _chip()
    _op_owned(s, "q1-2", "cz_a")
    s["qubit_pairs"]["q1-2"]["macros"]["cz"] = "#./cz_a"
    assert cgb.choose_direction(s, "q1-2") == cgb.OPERATION_OWNS


def test_reverse_direction_op_holds_pulse_and_macro_names_it():
    s = _dir_fixture(2)
    res = _plan(s)
    by = dict(res["creates"])
    key = "cz_new_pulse_q1_q2"
    assert by["qubit_pairs.q1-2.macros.cz_new"]["flux_pulse_qubit"] == key
    op = by[f"qubits.q1.z.operations.{key}"]
    assert op["id"] == key and op["length"] == 48 and op["amplitude"] == 0.3
    assert not any(isinstance(v, str) and v.startswith("#/") for v in op.values())


# --- length link -------------------------------------------------------

def test_explicit_macro_length_is_referenced_by_the_operation():
    res = _plan(_chip(), pulses=_square(length=52))
    by = dict(res["creates"])
    macro = by["qubit_pairs.q1-2.macros.cz_new"]
    assert macro["flux_pulse_qubit"]["length"] == 52
    op = by["qubits.q1.z.operations.cz_new_pulse_q1_q2"]
    assert op["length"] == "#/qubit_pairs/q1-2/macros/cz_new/flux_pulse_qubit/length"


def test_inferred_macro_length_stays_inferred_on_the_operation():
    pulses = {"flux_pulse_qubit": {"class": G_UNI, "fields": {"pulse_length": 60}}}
    by = dict(_plan(_chip(), pulses=pulses)["creates"])
    assert by["qubits.q1.z.operations.cz_new_pulse_q1_q2"]["length"] == "#./inferred_length"


# --- names -------------------------------------------------------------

def test_pulse_id_equals_operation_key_for_both_roles():
    pulses = {**_square(), "coupler_flux_pulse": {"class": SQUARE,
                                                  "fields": {"length": 48}}}
    res = _plan(_chip(), pulses=pulses)
    by = dict(res["creates"])
    macro = by["qubit_pairs.q1-2.macros.cz_new"]
    assert macro["flux_pulse_qubit"]["id"] == "cz_new_pulse_q1_q2"
    assert macro["coupler_flux_pulse"]["id"] == "cz_new_coupler_pulse_q1_q2"
    assert "qubits.q1.z.operations.cz_new_pulse_q1_q2" in by
    assert "qubit_pairs.q1-2.coupler.operations.cz_new_coupler_pulse_q1_q2" in by


def test_coupler_only_class_uses_the_coupler_role():
    res = _plan(_chip(), gate_class=cgb.COUPLER_CZ_GATE,
                pulses={"coupler_flux_pulse": {"class": SQUARE, "fields": {"length": 48}}})
    paths = [p for p, _ in res["creates"]]
    assert paths == ["qubit_pairs.q1-2.macros.cz_new",
                     "qubit_pairs.q1-2.coupler.operations.cz_new_coupler_pulse_q1_q2"]


def test_naming_function_takes_every_slot_role_and_refuses_others():
    for role in ("control_pulse", "target_pulse", "coupler_pulse", "spectator_pulse"):
        assert cgb.operation_name("cz_a", role, "q1", "q2") == f"cz_a_{role}_q1_q2"
    with pytest.raises(ValueError):
        cgb.operation_name("cz_a", "other", "q1", "q2")


def test_existing_operation_is_never_overwritten():
    s = _chip()
    s["qubits"]["q1"]["z"]["operations"]["cz_new_pulse_q1_q2"] = {"amplitude": 1}
    res = _plan(s)
    assert "already exists" in res["error"]


def test_a_name_the_name_rule_rejects_is_refused_with_its_reason():
    res = _plan(_chip(), name="cz_" + "x" * 60)
    assert "is not a valid name" in res["error"]


# --- the alias and existing paths --------------------------------------

def test_no_create_touches_the_alias_or_an_existing_path():
    s = _dir_fixture(1)
    s["qubit_pairs"]["q1-2"]["macros"]["cz"] = "#./cz_a"
    before = copy.deepcopy(s)
    res = _plan(s)
    have = _existing_paths(s)
    for path, _ in res["creates"]:
        assert path not in have
        assert not path.endswith(".macros.cz")
    after = _apply(s, res["creates"])
    assert after["qubit_pairs"]["q1-2"]["macros"]["cz"] == "#./cz_a"
    assert s == before


# --- explicit length rule ----------------------------------------------

@pytest.mark.parametrize("length,ok", [(16, True), (20, True), (12, False), (18, False)])
def test_explicit_length_multiple_of_four_and_at_least_sixteen(length, ok):
    res = _plan(_chip(), pulses=_square(length=length))
    assert ("creates" in res) is ok
    if not ok:
        assert "multiple of 4 ns" in res["error"]


# --- copying initial values --------------------------------------------

def test_copy_takes_only_same_name_same_type_fields():
    s = _chip()
    _macro_owned(s, "q1-2", "cz_src", length=64, amp=0.25)
    res = _plan(s, copy_from="cz_src",
                pulses={"flux_pulse_qubit": {"class": SQUARE, "fields": {}}})
    pulse = dict(res["creates"])["qubit_pairs.q1-2.macros.cz_new"]["flux_pulse_qubit"]
    assert pulse["length"] == 64 and pulse["amplitude"] == 0.25
    prov = res["fields"]["flux_pulse_qubit"]
    assert prov["length"] == cgb.COPIED and prov["amplitude"] == cgb.COPIED
    assert prov["axis_angle"] == cgb.DEFAULT and pulse["axis_angle"] is None


def test_copy_never_takes_a_reference_and_fills_an_empty_default():
    s = _chip()
    _macro_owned(s, "q1-2", "cz_src", length=64)
    src = s["qubit_pairs"]["q1-2"]["macros"]["cz_src"]["flux_pulse_qubit"]
    src["axis_angle"] = 0.5
    src["amplitude"] = "#/qubits/q1/z/operations/const/amplitude"
    res = _plan(s, copy_from="cz_src",
                pulses={"flux_pulse_qubit": {"class": SQUARE, "fields": {}}})
    pulse = dict(res["creates"])["qubit_pairs.q1-2.macros.cz_new"]["flux_pulse_qubit"]
    prov = res["fields"]["flux_pulse_qubit"]
    assert pulse["axis_angle"] == 0.5 and prov["axis_angle"] == cgb.COPIED
    assert not str(pulse["amplitude"]).startswith("#") and prov["amplitude"] == cgb.DEFAULT


def test_copy_marks_given_values_and_skips_a_type_mismatch():
    s = _chip()
    _macro_owned(s, "q1-2", "cz_src", length=64)
    s["qubit_pairs"]["q1-2"]["macros"]["cz_src"]["flux_pulse_qubit"]["amplitude"] = "high"
    res = _plan(s, copy_from="cz_src",
                pulses={"flux_pulse_qubit": {"class": SQUARE, "fields": {"length": 40}}})
    prov = res["fields"]["flux_pulse_qubit"]
    assert prov["length"] == cgb.GIVEN and prov["amplitude"] == cgb.DEFAULT


# --- refusals ----------------------------------------------------------

def test_refuses_without_a_moving_qubit():
    s = _chip()
    s["qubit_pairs"]["q1-2"]["moving_qubit"] = None
    assert "no moving qubit" in _plan(s)["error"]


def test_refuses_without_a_macros_container_or_a_coupler():
    s = _chip()
    del s["qubit_pairs"]["q1-2"]["macros"]
    assert "has no macros" in _plan(s)["error"]
    s = _chip()
    del s["qubit_pairs"]["q1-2"]["coupler"]
    res = _plan(s, gate_class=cgb.COUPLER_CZ_GATE,
                pulses={"coupler_flux_pulse": {"class": SQUARE, "fields": {"length": 48}}})
    assert "no coupler" in res["error"]


def test_refuses_an_unknown_pulse_class_and_a_missing_required_slot():
    res = _plan(_chip(), pulses={"flux_pulse_qubit": {"class": "x.NoSuchPulse", "fields": {}}})
    assert "does not know the fields" in res["error"]
    assert "needs a pulse" in _plan(_chip(), pulses={})["error"]


# --- golden: square gate in the default layout ---------------------------

def _rename(value, old, new):
    return json.loads(json.dumps(value).replace(old, new))


def test_golden_square_gate_matches_an_existing_gate_with_names_changed():
    s = _chip()
    _macro_owned(s, "q1-2", "cz_sq", length=52, amp=0.2)
    res = _plan(s, name="cz_sq2", pulses={"flux_pulse_qubit": {
        "class": SQUARE, "fields": {"length": 52, "amplitude": 0.2}}})
    got = {p: v for p, v in res["creates"]}
    want = {
        "qubit_pairs.q1-2.macros.cz_sq": s["qubit_pairs"]["q1-2"]["macros"]["cz_sq"],
        "qubits.q1.z.operations.cz_sq_pulse_q1_q2":
            s["qubits"]["q1"]["z"]["operations"]["cz_sq_pulse_q1_q2"],
    }
    assert _rename(got, "cz_sq2", "cz_sq") == want


# --- copied layout -----------------------------------------------------

def _lab_macro_owned(s, pid, name):
    p = s["qubit_pairs"][pid]
    c, t = p["qubit_control"].split("/")[-1], p["qubit_target"].split("/")[-1]
    m = {"__class__": LAB_GATE, "phase_shift_control": 0.0, "mode": "fast"}
    for side, q in (("control", c), ("target", t)):
        key = f"{name}_{side}_pulse_{c}_{t}"
        m[f"flux_pulse_{side}"] = {"id": key, "length": 40, "amplitude": 0.2,
                                   "__class__": LAB_PULSE}
        ref = f"#/qubit_pairs/{pid}/macros/{name}/flux_pulse_{side}"
        s["qubits"][q]["z"]["operations"][key] = {
            "length": ref + "/length", "amplitude": ref + "/amplitude",
            "__class__": LAB_PULSE}
    p["macros"][name] = m


def _lab_op_owned(s, pid, name):
    p = s["qubit_pairs"][pid]
    c, t = p["qubit_control"].split("/")[-1], p["qubit_target"].split("/")[-1]
    m = {"__class__": LAB_GATE, "phase_shift_control": 0.0}
    for side, q in (("control", c), ("target", t)):
        key = f"{name}_{side}_pulse_{c}_{t}"
        m[f"flux_pulse_{side}"] = key
        s["qubits"][q]["z"]["operations"][key] = {
            "id": key, "length": 44, "amplitude": 0.3, "__class__": LAB_PULSE}
    p["macros"][name] = m


def _lab_chip():
    s = _chip(4)
    _lab_macro_owned(s, "q1-2", "cz_tf")
    _lab_op_owned(s, "q3-4", "cz_tb")
    _op_owned(s, "q2-3", "cz_rev")  # the target pair's other gates are reverse
    return s


def test_copied_layout_keeps_the_model_direction_and_renames():
    s = _lab_chip()
    res = cgb.plan(s, "q2-3", gate_name="cz_new", gate_class=LAB_GATE)
    assert res["model_gate"] == ("q1-2", "cz_tf")
    assert res["direction"] == cgb.MACRO_OWNS
    by = dict(res["creates"])
    macro = by["qubit_pairs.q2-3.macros.cz_new"]
    assert macro["flux_pulse_control"]["id"] == "cz_new_control_pulse_q2_q3"
    assert macro["mode"] == "fast"
    assert res["fields"]["macro"] == {"mode": cgb.FROM_MODEL}
    op = by["qubits.q3.z.operations.cz_new_target_pulse_q2_q3"]
    assert op["length"] == "#/qubit_pairs/q2-3/macros/cz_new/flux_pulse_target/length"
    assert "qubits.q2.z.operations.cz_new_control_pulse_q2_q3" in by


def test_copied_layout_golden_same_pair_is_the_model_with_names_changed():
    s = _chip(4)
    _lab_macro_owned(s, "q1-2", "cz_tf")
    res = cgb.plan(s, "q1-2", gate_name="cz_tg", gate_class=LAB_GATE)
    got = _rename({p: v for p, v in res["creates"]}, "cz_tg", "cz_tf")
    assert got == {
        "qubit_pairs.q1-2.macros.cz_tf": s["qubit_pairs"]["q1-2"]["macros"]["cz_tf"],
        "qubits.q1.z.operations.cz_tf_control_pulse_q1_q2":
            s["qubits"]["q1"]["z"]["operations"]["cz_tf_control_pulse_q1_q2"],
        "qubits.q2.z.operations.cz_tf_target_pulse_q1_q2":
            s["qubits"]["q2"]["z"]["operations"]["cz_tf_target_pulse_q1_q2"],
    }


def test_model_choice_decides_the_layout():
    s = _lab_chip()
    res = cgb.plan(s, "q2-3", gate_name="cz_new", gate_class=LAB_GATE,
                   model_gate=("q3-4", "cz_tb"))
    assert res["direction"] == cgb.OPERATION_OWNS
    by = dict(res["creates"])
    assert by["qubit_pairs.q2-3.macros.cz_new"]["flux_pulse_control"] == "cz_new_control_pulse_q2_q3"
    assert by["qubits.q2.z.operations.cz_new_control_pulse_q2_q3"]["length"] == 44
    # the same pair's gate is the default model
    assert cgb.default_model(s, "q3-4", LAB_GATE) == ("q3-4", "cz_tb")
    assert cgb.plan(s, "q3-4", gate_name="cz_new", gate_class=LAB_GATE)["direction"] == cgb.OPERATION_OWNS


def test_copied_layout_refuses_when_no_gate_of_the_class_exists():
    res = cgb.plan(_chip(), "q1-2", gate_name="cz_new", gate_class=LAB_GATE)
    assert "No gate of the class" in res["error"]


def test_copied_layout_given_values_override_the_model():
    s = _lab_chip()
    res = cgb.plan(s, "q2-3", gate_name="cz_new", gate_class=LAB_GATE,
                   pulses={"flux_pulse_control": {"fields": {"amplitude": 0.5}}})
    macro = dict(res["creates"])["qubit_pairs.q2-3.macros.cz_new"]
    assert macro["flux_pulse_control"]["amplitude"] == 0.5
    assert res["fields"]["flux_pulse_control"]["amplitude"] == cgb.GIVEN


# --- Gaussian regression -----------------------------------------------

def _gauss_chip():
    s = _chip(2)
    s["qubit_pairs"]["q1-2"]["coupler"] = {"id": "c12", "operations": {"const": {"amplitude": 0.2}}}
    s["qubit_pairs"]["q1-2"]["macros"]["cz_flattop"] = {
        "flux_pulse_qubit": {"amplitude": 0.31, "flat_length": 60},
        "coupler_flux_pulse": {"amplitude": 0.12}}
    return s


@pytest.mark.parametrize("gname,cls", gaussian_cz.VARIANTS)
def test_gaussian_regression_matches_the_old_plan(gname, cls):
    s = _gauss_chip()
    old = gaussian_cz.plan(s, "q1-2", padding_length=20, qubit_filter_mhz=20,
                           coupler_filter_mhz=50, suffix_ids=("q1", "q2"))
    want = [(p, v) for p, v in old["creates"] if f".{gname}" in p or p.endswith(gname)
            or f"{gname}_" in p.rsplit(".", 1)[-1]]
    assert len(want) == 3
    new = cgb.plan(s, "q1-2", gate_name="cz_g_new", gate_class=cgb.CZ_GATE, pulses={
        "flux_pulse_qubit": {"class": cls, "fields": {
            "pulse_length": 60, "padding_length": 20, "amplitude": 0.31,
            "gaussian_filter_frequency_mhz": 20}},
        "coupler_flux_pulse": {"class": cls, "fields": {
            "pulse_length": 60, "padding_length": 20, "amplitude": 0.12,
            "gaussian_filter_frequency_mhz": 50}}})
    got = _rename(new["creates"], "cz_g_new", gname)
    assert [p for p, _ in got] == [p for p, _ in want]
    for (gp, gv), (wp, wv) in zip(got, want):
        assert gv == wv, gp


# --- copied names and ids ------------------------------------------------

def _two_pulse_chip():
    """Model cz_a on q1-q2: control-line name ends _q1_q2, target-line _q2_q1,
    and each operation carries its own id equal to its key."""
    s = {"qubits": {q: _qubit(q) for q in ("q1", "q2", "q3", "q4")},
         "qubit_pairs": {"q1-q2": _pair("q1", "q2"), "q3-q4": _pair("q3", "q4"),
                         "q1-q3": _pair("q1", "q3")}}
    m = {"__class__": LAB_GATE}
    for field, q, key in (("flux_pulse_qubit", "q1", "cz_a_flux_pulse_q1_q2"),
                          ("flux_pulse_target", "q2", "cz_a_flux_pulse_q2_q1")):
        m[field] = {"id": key, "length": 40, "amplitude": 0.2, "__class__": LAB_PULSE}
        ref = f"#/qubit_pairs/q1-q2/macros/cz_a/{field}"
        s["qubits"][q]["z"]["operations"][key] = {
            "id": key, "length": ref + "/length", "amplitude": ref + "/amplitude",
            "__class__": LAB_PULSE}
    s["qubit_pairs"]["q1-q2"]["macros"]["cz_a"] = m
    return s


MODEL_NAMES = ("cz_a_flux_pulse_q1_q2", "cz_a_flux_pulse_q2_q1")


def _strings(v):
    if isinstance(v, dict):
        for x in v.values():
            yield from _strings(x)
    elif isinstance(v, (list, tuple)):
        for x in v:
            yield from _strings(x)
    elif isinstance(v, str):
        yield v


def test_same_pair_copy_renames_operation_ids_and_leaves_no_model_name():
    res = cgb.plan(_two_pulse_chip(), "q1-q2", gate_name="cz_b", gate_class=LAB_GATE)
    by = dict(res["creates"])
    for path in ("qubits.q1.z.operations.cz_b_flux_pulse_q1_q2",
                 "qubits.q2.z.operations.cz_b_flux_pulse_q2_q1"):
        assert by[path]["id"] == path.rsplit(".", 1)[-1]
    for s in _strings(res["creates"]):
        assert not any(n in s for n in MODEL_NAMES), s
        assert "macros/cz_a/" not in s, s  # pointers follow the new macro


def test_cross_pair_copy_names_after_the_new_pair_in_the_model_order():
    res = cgb.plan(_two_pulse_chip(), "q3-q4", gate_name="cz_b", gate_class=LAB_GATE)
    paths = [p for p, _ in res["creates"]]
    assert paths == ["qubit_pairs.q3-q4.macros.cz_b",
                     "qubits.q3.z.operations.cz_b_flux_pulse_q3_q4",
                     "qubits.q4.z.operations.cz_b_flux_pulse_q4_q3"]
    for s in _strings(res["creates"]):
        assert not any(n in s for n in MODEL_NAMES), s


def test_single_qubit_id_ending_is_renamed():
    s = _two_pulse_chip()
    s["qubit_pairs"]["q1-q2"]["macros"]["cz_a"] = {
        "__class__": LAB_GATE, "flux_pulse_qubit": {
            "id": "cz_a_pulse_q2", "length": 40, "__class__": LAB_PULSE}}
    s["qubits"]["q1"]["z"]["operations"]["cz_a_pulse_q2"] = {
        "id": "cz_a_pulse_q2", "length": 40, "__class__": LAB_PULSE}
    res = cgb.plan(s, "q1-q3", gate_name="cz_b", gate_class=LAB_GATE)
    assert "qubits.q1.z.operations.cz_b_pulse_q3" in dict(res["creates"])


def test_gate_name_with_a_qubit_token_is_used_verbatim():
    assert cgb.copied_name("cz_q1_x_pulse_q1_q2", "cz_q1_x", ("q1", "q2"),
                           "cz_q1_y", ("q2", "q1")) == "cz_q1_y_pulse_q2_q1"


def test_names_that_collide_after_renaming_are_refused():
    # A pair whose control and target are one qubit maps _q1_q2 and _q2_q1
    # onto the same new name.
    s = _two_pulse_chip()
    s["qubit_pairs"]["q3-q3"] = _pair("q3", "q3")
    res = cgb.plan(s, "q3-q3", gate_name="cz_b", gate_class=LAB_GATE)
    assert "same name" in res["error"]


# --- copies carry structure, not results ---------------------------------

def test_copy_resets_results_and_lists_kept_macro_keys():
    s = _two_pulse_chip()
    m = s["qubit_pairs"]["q1-q2"]["macros"]["cz_a"]
    m.update({"fidelity": {"rb": 0.9917}, "phase_shift_target": 0.3141,
              "extras": {"note": 7.77}, "spectator_qubits": {"q3": "#/qubits/q3"},
              "spectator_qubits_control": [1], "label": "fast"})
    res = cgb.plan(s, "q1-q2", gate_name="cz_b", gate_class=LAB_GATE)
    macro = dict(res["creates"])["qubit_pairs.q1-q2.macros.cz_b"]
    assert macro["fidelity"] == {} and macro["extras"] == {}
    assert macro["phase_shift_target"] == 0.0
    assert macro["spectator_qubits"] == {} and macro["spectator_qubits_control"] == []
    assert res["fields"]["macro"] == {"label": cgb.FROM_MODEL}
    text = json.dumps(res["creates"])
    for n in ("0.9917", "0.3141", "7.77"):
        assert n not in text


def _copy_with(**extra):
    s = _two_pulse_chip()
    s["qubit_pairs"]["q1-q2"]["macros"]["cz_a"].update(extra)
    res = cgb.plan(s, "q1-q2", gate_name="cz_b", gate_class=LAB_GATE)
    return dict(res["creates"])["qubit_pairs.q1-q2.macros.cz_b"], res["fields"]["macro"]


def test_copy_keeps_a_lab_duration_reference_silently():
    macro, kept = _copy_with(duration="#./total_duration")
    assert macro["duration"] == "#./total_duration" and "duration" not in kept


def test_copy_keeps_a_plain_duration_and_lists_it():
    macro, kept = _copy_with(duration_qubit=40)
    assert macro["duration_qubit"] == 40 and kept["duration_qubit"] == cgb.FROM_MODEL


def test_copy_keeps_a_none_without_listing_it():
    macro, kept = _copy_with(coupler_flux_pulse=None)
    assert macro["coupler_flux_pulse"] is None and "coupler_flux_pulse" not in kept


def test_cross_pair_copy_refuses_a_different_moving_qubit():
    s = _two_pulse_chip()
    s["qubit_pairs"]["q3-q4"]["moving_qubit"] = "target"
    res = cgb.plan(s, "q3-q4", gate_name="cz_b", gate_class=LAB_GATE)
    assert "same moving qubit" in res["error"]
    s["qubit_pairs"]["q3-q4"]["moving_qubit"] = "control"
    assert "creates" in cgb.plan(s, "q3-q4", gate_name="cz_b", gate_class=LAB_GATE)


def test_leftover_references_into_the_model_pair_are_refused():
    s = _two_pulse_chip()
    op = s["qubits"]["q1"]["z"]["operations"]["cz_a_flux_pulse_q1_q2"]
    op["digital_marker"] = "#/qubit_pairs/q1-q2/coupler/marker"
    res = cgb.plan(s, "q3-q4", gate_name="cz_b", gate_class=LAB_GATE)
    assert "#/qubit_pairs/q1-q2/coupler/marker" in res["error"]
    s = _two_pulse_chip()
    s["qubits"]["q1"]["z"]["operations"]["cz_a_flux_pulse_q1_q2"]["extra"] = \
        "#/qubits/q1/z/operations/missing_op"
    res = cgb.plan(s, "q1-q2", gate_name="cz_b", gate_class=LAB_GATE)
    assert "missing_op" in res["error"]


@pytest.mark.parametrize("bad", ["cz_x/y", "cz_a.b", "cz_" + "x" * 70])
def test_invalid_gate_names_are_refused_in_both_paths(bad):
    assert "not a valid name" in _plan(_chip(), name=bad)["error"]
    res = cgb.plan(_two_pulse_chip(), "q1-q2", gate_name=bad, gate_class=LAB_GATE)
    assert "not a valid name" in res["error"]


def test_a_model_with_no_pulse_field_is_refused():
    s = _two_pulse_chip()
    s["qubit_pairs"]["q1-q2"]["macros"]["cz_a"] = {"__class__": LAB_GATE,
                                                   "pulse_shape": "gaussian"}
    assert "no pulse SM can copy" in cgb.plan(
        s, "q1-q2", gate_name="cz_b", gate_class=LAB_GATE)["error"]


def test_copied_name_needs_the_gate_name_then_an_underscore():
    assert cgb.copied_name("cz_ab_pulse_q1_q2", "cz_a", ("q1", "q2"), "cz_n", ("q1", "q2")) is None
    s = _two_pulse_chip()
    m = s["qubit_pairs"]["q1-q2"]["macros"]["cz_a"]
    m["flux_pulse_qubit"]["id"] = "cz_ab_pulse_q1_q2"
    ops = s["qubits"]["q1"]["z"]["operations"]
    ops["cz_ab_pulse_q1_q2"] = ops.pop("cz_a_flux_pulse_q1_q2")
    res = cgb.plan(s, "q1-q2", gate_name="cz_n", gate_class=LAB_GATE)
    assert "does not start with cz_a" in res["error"]


def test_copied_name_matches_whole_ids_longest_first():
    assert cgb.copied_name("cz_a_pulse_qA_1_qA_10", "cz_a", ("qA_1", "qA_10"),
                           "cz_b", ("qB_2", "qB_20")) == "cz_b_pulse_qB_2_qB_20"
    assert cgb.copied_name("cz_a_pulse_q10_q1", "cz_a", ("q1", "q10"),
                           "cz_b", ("q2", "q3")) == "cz_b_pulse_q3_q2"
    assert cgb.copied_name("cz_a_pulse_q1x", "cz_a", ("q1", "q2"),
                           "cz_b", ("q3", "q4")) == "cz_b_pulse_q1x"


@pytest.mark.parametrize("length,ok,written", [
    (16, True, 16), (20, True, 20), (52.0, True, 52), (12, False, None),
    (18, False, None), (52.5, False, None), ("52", False, None)])
def test_length_must_be_a_whole_number_of_ns(length, ok, written):
    res = _plan(_chip(), pulses=_square(length=length))
    assert ("creates" in res) is ok, res.get("error")
    if ok:
        pulse = dict(res["creates"])["qubit_pairs.q1-2.macros.cz_new"]["flux_pulse_qubit"]
        assert pulse["length"] == written and type(pulse["length"]) is int


def test_a_plain_string_with_pulse_in_its_key_is_not_a_pulse_field():
    m = {"__class__": cgb.CZ_GATE, "pulse_shape": "gaussian",
         "flux_pulse_qubit": "cz_a_pulse_q1_q2"}
    assert cgb.pulse_fields(m, {"cz_a_pulse_q1_q2"}) == ["flux_pulse_qubit"]
    assert cgb.gate_direction({"pulse_shape": "gaussian"}) is None
    assert cgb.gate_direction({"pulse_shape": "gaussian",
                               "flux_pulse_qubit": {"amplitude": 1}}) == cgb.MACRO_OWNS


def test_operation_reference_is_built_from_segments():
    s = _two_pulse_chip()
    m = s["qubit_pairs"]["q1-q2"]["macros"]["cz_a"]
    for field, q, key in (("flux_pulse_qubit", "q1", "cz_a_flux_pulse_q1_q2"),
                          ("flux_pulse_target", "q2", "cz_a_flux_pulse_q2_q1")):
        s["qubits"][q]["z"]["operations"][key] = dict(m[field])
        m[field] = f"#/qubits/{q}/z/operations/{key}"
    res = cgb.plan(s, "q3-q4", gate_name="cz_b", gate_class=LAB_GATE)
    macro = dict(res["creates"])["qubit_pairs.q3-q4.macros.cz_b"]
    assert macro["flux_pulse_qubit"] == "#/qubits/q3/z/operations/cz_b_flux_pulse_q3_q4"
    assert res["direction"] == cgb.OPERATION_OWNS


def test_coupler_reference_is_followed_one_hop():
    s = _chip()
    s["couplers"] = {"c1": {"operations": {}}}
    s["qubit_pairs"]["q1-2"]["coupler"] = "#/couplers/c1"
    res = _plan(s, gate_class=cgb.COUPLER_CZ_GATE,
                pulses={"coupler_flux_pulse": {"class": SQUARE, "fields": {"length": 48}}})
    assert "couplers.c1.operations.cz_new_coupler_pulse_q1_q2" in dict(res["creates"])
    for ref in ("#/couplers/none", "#./c1"):
        s["qubit_pairs"]["q1-2"]["coupler"] = ref
        res = _plan(s, gate_class=cgb.COUPLER_CZ_GATE,
                    pulses={"coupler_flux_pulse": {"class": SQUARE, "fields": {"length": 48}}})
        assert "could not be followed" in res["error"]


def test_mixed_model_reports_direction_per_pulse():
    s = _two_pulse_chip()
    m = s["qubit_pairs"]["q1-q2"]["macros"]["cz_a"]
    key = "cz_a_flux_pulse_q2_q1"
    s["qubits"]["q2"]["z"]["operations"][key] = dict(m["flux_pulse_target"])
    m["flux_pulse_target"] = key
    res = cgb.plan(s, "q1-q2", gate_name="cz_b", gate_class=LAB_GATE)
    assert res["direction"] == cgb.MIXED
    assert res["pulse_directions"] == {"flux_pulse_qubit": cgb.MACRO_OWNS,
                                       "flux_pulse_target": cgb.OPERATION_OWNS}


# --- Gaussian golden from the original builder ---------------------------

def _gauss_pulse(cls, pid, amp, fmhz):
    return {"length": "#./inferred_length", "id": pid, "digital_marker": None,
            "pulse_length": 60, "padding_length": 20, "amplitude": amp,
            "gaussian_filter_frequency_mhz": fmhz, "sample_rate": 1000000000.0,
            "axis_angle": None, "__class__": cls}


def _gauss_op(cls, ref):
    return {"length": "#./inferred_length", "id": None, "digital_marker": None,
            "pulse_length": ref + "/pulse_length", "padding_length": ref + "/padding_length",
            "amplitude": ref + "/amplitude",
            "gaussian_filter_frequency_mhz": ref + "/gaussian_filter_frequency_mhz",
            "sample_rate": 1000000000.0, "axis_angle": None, "__class__": cls}


def _gauss_golden(gname, cls):
    """Transcribed from the original builder's output on _gauss_golden_chip."""
    ref = f"#/qubit_pairs/q1-q2/macros/{gname}"
    macro = {"id": "#./inferred_id", "fidelity": {}, "duration": "#./inferred_duration",
             "flux_pulse_qubit": _gauss_pulse(cls, f"{gname}_pulse_q1_q2", 0.31, 20),
             "coupler_flux_pulse": _gauss_pulse(cls, f"{gname}_coupler_pulse_q1_q2", 0.12, 50),
             "phase_shift_control": 0.0, "phase_shift_target": 0.0,
             "spectator_qubits": {}, "spectator_qubits_control": {},
             "spectator_qubits_phase_shift": {}, "extras": {}, "duration_qubit": None,
             "__class__": cgb.CZ_GATE}
    return [
        (f"qubit_pairs.q1-q2.macros.{gname}", macro),
        (f"qubits.q1.z.operations.{gname}_pulse_q1_q2",
         _gauss_op(cls, ref + "/flux_pulse_qubit")),
        (f"qubit_pairs.q1-q2.coupler.operations.{gname}_coupler_pulse_q1_q2",
         _gauss_op(cls, ref + "/coupler_flux_pulse")),
    ]


def _gauss_golden_chip():
    s = {"qubits": {"q1": _qubit("q1"), "q2": _qubit("q2")},
         "qubit_pairs": {"q1-q2": _pair("q1", "q2")}}
    s["qubit_pairs"]["q1-q2"]["coupler"] = {"id": "c12", "operations": {"const": {"amplitude": 0.2}}}
    s["qubit_pairs"]["q1-q2"]["macros"]["cz_flattop"] = {
        "flux_pulse_qubit": {"amplitude": 0.31, "flat_length": 60},
        "coupler_flux_pulse": {"amplitude": 0.12}}
    return s


@pytest.mark.parametrize("gname,cls", gaussian_cz.VARIANTS)
def test_gaussian_matches_the_original_builder_literal(gname, cls):
    s = _gauss_golden_chip()
    want = _gauss_golden(gname, cls)
    old = gaussian_cz.plan(s, "q1-q2", padding_length=20, qubit_filter_mhz=20,
                           coupler_filter_mhz=50, suffix_ids=("q1", "q2"))
    assert [c for c in old["creates"] if gname in c[0]] == want
    new = cgb.plan(s, "q1-q2", gate_name=gname, gate_class=cgb.CZ_GATE, pulses={
        "flux_pulse_qubit": {"class": cls, "fields": {
            "pulse_length": 60, "padding_length": 20, "amplitude": 0.31,
            "gaussian_filter_frequency_mhz": 20}},
        "coupler_flux_pulse": {"class": cls, "fields": {
            "pulse_length": 60, "padding_length": 20, "amplitude": 0.12,
            "gaussian_filter_frequency_mhz": 50}}})
    assert new["creates"] == want


def test_gaussian_builder_never_changes_its_input():
    s = _gauss_golden_chip()
    before = copy.deepcopy(s)
    gaussian_cz.plan(s, "q1-q2", suffix_ids=("q1", "q2"))
    gaussian_cz.plan(s, "q1-q2", own_names=True)
    gaussian_cz.plan(s, "q1-q2")
    assert s == before


# --- copied layouts: SM refuses where it would have to guess -------------

def test_copy_refuses_another_pulse_class():
    res = cgb.plan(_two_pulse_chip(), "q1-q2", gate_name="cz_b", gate_class=LAB_GATE,
                   pulses={"flux_pulse_qubit": {"class": SQUARE, "fields": {}}})
    assert "keeps its pulse classes" in res["error"]


def test_given_value_reaches_a_plain_operation_copy():
    s = _two_pulse_chip()
    op = s["qubits"]["q1"]["z"]["operations"]["cz_a_flux_pulse_q1_q2"]
    op["amplitude"] = 0.2  # a plain copy, not a reference
    res = cgb.plan(s, "q1-q2", gate_name="cz_b", gate_class=LAB_GATE,
                   pulses={"flux_pulse_qubit": {"fields": {"amplitude": 0.5}}})
    by = dict(res["creates"])
    assert by["qubit_pairs.q1-q2.macros.cz_b"]["flux_pulse_qubit"]["amplitude"] == 0.5
    assert by["qubits.q1.z.operations.cz_b_flux_pulse_q1_q2"]["amplitude"] == 0.5


def test_moving_rule_applies_per_line():
    s = _two_pulse_chip()
    del s["qubit_pairs"]["q1-q2"]["moving_qubit"]
    res = cgb.plan(s, "q3-q4", gate_name="cz_b", gate_class=LAB_GATE)
    assert "same moving qubit" in res["error"]
    assert "creates" in cgb.plan(s, "q1-q2", gate_name="cz_b", gate_class=LAB_GATE)
    # a coupler-only model needs no moving-qubit check
    c = _chip(4)
    c["qubit_pairs"]["q1-2"]["coupler"]["operations"]["cz_c_cpl_q1_q2"] = {
        "id": "cz_c_cpl_q1_q2", "length": 40, "__class__": LAB_PULSE}
    c["qubit_pairs"]["q1-2"]["macros"]["cz_c"] = {"__class__": LAB_GATE,
                                                  "coupler_pulse": "cz_c_cpl_q1_q2"}
    c["qubit_pairs"]["q3-4"]["moving_qubit"] = "target"
    res = cgb.plan(c, "q3-4", gate_name="cz_d", gate_class=LAB_GATE)
    assert "qubit_pairs.q3-4.coupler.operations.cz_d_cpl_q3_q4" in dict(res["creates"])


def test_default_model_prefers_one_the_moving_rule_accepts():
    s = _two_pulse_chip()
    s["qubit_pairs"]["q0-q9"] = _pair("q1", "q2", moving="target")
    s["qubit_pairs"]["q0-q9"]["macros"]["cz_z"] = copy.deepcopy(
        s["qubit_pairs"]["q1-q2"]["macros"]["cz_a"])
    assert cgb.default_model(s, "q3-q4", LAB_GATE) == ("q1-q2", "cz_a")


def test_a_reference_through_a_coupler_reference_is_followed():
    s = _chip(2)
    s["couplers"] = {"c1": {"operations": {"cz_c_cpl_q1_q2": {
        "id": "cz_c_cpl_q1_q2", "length": 40, "__class__": LAB_PULSE}}}}
    s["qubit_pairs"]["q1-2"]["coupler"] = "#/couplers/c1"
    s["qubit_pairs"]["q1-2"]["macros"]["cz_c"] = {
        "__class__": LAB_GATE,
        "coupler_pulse": "#/qubit_pairs/q1-2/coupler/operations/cz_c_cpl_q1_q2"}
    res = cgb.plan(s, "q1-2", gate_name="cz_d", gate_class=LAB_GATE)
    by = dict(res["creates"])
    assert by["qubit_pairs.q1-2.macros.cz_d"]["coupler_pulse"] == \
        "#/qubit_pairs/q1-2/coupler/operations/cz_d_cpl_q1_q2"
    assert "couplers.c1.operations.cz_d_cpl_q1_q2" in by


def test_a_reference_inside_a_longer_string_is_scanned():
    s = _two_pulse_chip()
    s["qubits"]["q1"]["z"]["operations"]["cz_a_flux_pulse_q1_q2"]["note"] = \
        "see #/qubit_pairs/q1-q2/macros/cz_a/flux_pulse_qubit"
    res = cgb.plan(s, "q3-q4", gate_name="cz_b", gate_class=LAB_GATE)
    assert "#/qubit_pairs/q1-q2" in res["error"]


def test_a_qubit_that_changes_role_is_renamed_and_its_reference_refused():
    s = _two_pulse_chip()
    s["qubit_pairs"]["q3-q1"] = _pair("q3", "q1")
    res = cgb.plan(s, "q3-q1", gate_name="cz_b", gate_class=LAB_GATE)
    paths = [p for p, _ in res["creates"]]
    assert "qubits.q3.z.operations.cz_b_flux_pulse_q3_q1" in paths
    assert "qubits.q1.z.operations.cz_b_flux_pulse_q1_q3" in paths
    s["qubits"]["q2"]["z"]["operations"]["cz_a_flux_pulse_q2_q1"]["src"] = "#/qubits/q1/xy"
    res = cgb.plan(s, "q3-q1", gate_name="cz_b", gate_class=LAB_GATE)
    assert "#/qubits/q1/xy" in res["error"]


def test_a_kept_plain_value_naming_the_model_gate_is_refused():
    s = _two_pulse_chip()
    s["qubit_pairs"]["q1-q2"]["macros"]["cz_a"]["id"] = "cz_a"
    res = cgb.plan(s, "q1-q2", gate_name="cz_b", gate_class=LAB_GATE)
    assert "names the model" in res["error"] and ".id" in res["error"]


def test_two_pulses_naming_one_operation_are_refused():
    s = _two_pulse_chip()
    m = s["qubit_pairs"]["q1-q2"]["macros"]["cz_a"]
    key = "cz_a_flux_pulse_q1_q2"
    s["qubits"]["q1"]["z"]["operations"][key] = {"id": key, "length": 40,
                                                 "__class__": LAB_PULSE}
    m["flux_pulse_qubit"] = key
    m["flux_pulse_target"] = key
    res = cgb.plan(s, "q1-q2", gate_name="cz_b", gate_class=LAB_GATE)
    assert "twice" in res["error"]


def test_model_values_are_labelled_as_from_the_model():
    res = cgb.plan(_two_pulse_chip(), "q1-q2", gate_name="cz_b", gate_class=LAB_GATE)
    assert res["fields"]["flux_pulse_qubit"]["amplitude"] == cgb.FROM_MODEL


def test_a_model_pulse_without_a_class_is_refused():
    s = _two_pulse_chip()
    del s["qubit_pairs"]["q1-q2"]["macros"]["cz_a"]["flux_pulse_qubit"]["__class__"]
    res = cgb.plan(s, "q1-q2", gate_name="cz_b", gate_class=LAB_GATE)
    assert "names no pulse class" in res["error"]


def test_pulses_must_be_slots_of_the_class_or_the_model():
    assert "no pulse field" in _plan(_chip(), pulses={
        **_square(), "flux_pulse_x": {"class": SQUARE, "fields": {}}})["error"]
    res = cgb.plan(_two_pulse_chip(), "q1-q2", gate_name="cz_b", gate_class=LAB_GATE,
                   pulses={"flux_pulse_x": {"fields": {}}})
    assert "builds exactly the model's pulses" in res["error"]


def _bad_qubit_entry():
    s = _chip()
    s["qubits"]["q1"] = "not a qubit"
    return s


@pytest.mark.parametrize("state,kw", [
    (_chip, {"pulses": "abc"}),
    (_chip, {"pulses": {"flux_pulse_qubit": "abc"}}),
    (_chip, {"pulses": {"flux_pulse_qubit": {"class": SQUARE, "fields": [1]}}}),
    (_chip, {"gate_class": LAB_GATE, "model_gate": "q1-2"}),
    (_chip, {"gate_class": LAB_GATE, "model_gate": ("q1-2",)}),
    (_bad_qubit_entry, {}),
    (_chip, {"copy_from": "cz_missing"}),
    (_chip, {"copy_from": 5}),
    (_chip, {"pulses": {"flux_pulse_qubit": {"class": SQUARE, "fields": {"length": True}}}}),
])
def test_malformed_arguments_are_refusals(state, kw):
    res = _plan(state(), **kw)
    assert "error" in res and isinstance(res["error"], str)


def test_copy_from_a_gate_whose_pulse_is_an_operation_name():
    s = _dir_fixture(2)
    res = _plan(s, copy_from="cz_a", pulses={"flux_pulse_qubit": {"class": SQUARE, "fields": {}}})
    assert res["fields"]["flux_pulse_qubit"]["amplitude"] == cgb.COPIED
