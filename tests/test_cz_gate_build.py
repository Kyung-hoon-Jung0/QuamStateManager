"""The pure CZ gate build plan: rules, directions, copied layouts, Gaussian regression."""
from __future__ import annotations

import copy
import hashlib
import json

import pytest

from quam_state_manager.core import cz_gate_build as cgb

SQUARE = "quam.components.pulses.SquarePulse"
G_UNI = "quam_builder.common.pulses.GaussianFilteredSquarePulse"
G_BI = ("quam_builder.architecture.superconducting.components.pulses."
        "GaussianFilteredSymmetricBipolarPulse")
GAUSS_VARIANTS = (("cz_gaussian_unipolar", G_UNI), ("cz_gaussian_bipolar", G_BI))
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
    assert "longer than 64 characters. Pick a shorter gate name." in res["error"]
    s = _chip()
    s["qubits"]["q-1"] = s["qubits"].pop("q1")
    s["qubit_pairs"]["q1-2"]["qubit_control"] = "#/qubits/q-1"
    assert "The qubit id q-1 contains a character" in _plan(s)["error"]


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


def test_copy_keeps_a_lab_duration_reference_as_structure():
    macro, kept = _copy_with(duration="#./total_duration")
    assert macro["duration"] == "#./total_duration" and kept["duration"] == cgb.KEPT_REF


def test_copy_keeps_a_plain_duration_and_lists_it():
    macro, kept = _copy_with(duration_qubit=40)
    assert macro["duration_qubit"] == 40 and kept["duration_qubit"] == cgb.FROM_MODEL


def test_copy_keeps_a_none_without_listing_it():
    macro, kept = _copy_with(coupler_flux_pulse=None)
    assert macro["coupler_flux_pulse"] is None and "coupler_flux_pulse" not in kept


def test_cross_pair_copy_refuses_a_different_moving_qubit():
    s = _one_line_chip()
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


def test_a_model_whose_pulse_fields_climb_out_of_the_macro_states_that_reason():
    s = _two_pulse_chip()
    ref = "#../../../../qubits/q1/z/operations/cz_a_pulse"
    s["qubit_pairs"]["q1-q2"]["macros"]["cz_a"] = {"__class__": LAB_GATE,
                                                   "flux_pulse_qubit": ref}
    err = cgb.plan(s, "q1-q2", gate_name="cz_b", gate_class=LAB_GATE)["error"]
    assert ref in err and "flux_pulse_qubit" in err
    assert "points outside its own macro" in err
    assert "no pulse SM can copy" not in err


def test_copied_name_needs_the_gate_name_then_an_underscore():
    assert cgb.copied_name("cz_ab_pulse_q1_q2", "cz_a", ("q1", "q2"), "cz_n", ("q1", "q2")) is None
    s = _two_pulse_chip()
    m = s["qubit_pairs"]["q1-q2"]["macros"]["cz_a"]
    m["flux_pulse_qubit"]["id"] = "cz_ab_pulse_q1_q2"
    ops = s["qubits"]["q1"]["z"]["operations"]
    ops["cz_ab_pulse_q1_q2"] = ops.pop("cz_a_flux_pulse_q1_q2")
    res = cgb.plan(s, "q1-q2", gate_name="cz_n", gate_class=LAB_GATE)
    assert "is not named after cz_a" in res["error"]


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


@pytest.mark.parametrize("gname,cls", GAUSS_VARIANTS)
def test_gaussian_matches_the_original_builder_literal(gname, cls):
    s = _gauss_golden_chip()
    want = _gauss_golden(gname, cls)
    new = cgb.plan(s, "q1-q2", gate_name=gname, gate_class=cgb.CZ_GATE, pulses={
        "flux_pulse_qubit": {"class": cls, "fields": {
            "pulse_length": 60, "padding_length": 20, "amplitude": 0.31,
            "gaussian_filter_frequency_mhz": 20}},
        "coupler_flux_pulse": {"class": cls, "fields": {
            "pulse_length": 60, "padding_length": 20, "amplitude": 0.12,
            "gaussian_filter_frequency_mhz": 50}}})
    assert new["creates"] == want


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


def _one_line_chip():
    """Model cz_a on q1-q2 that plays only the control z line."""
    s = _two_pulse_chip()
    del s["qubit_pairs"]["q1-q2"]["macros"]["cz_a"]["flux_pulse_target"]
    del s["qubits"]["q2"]["z"]["operations"]["cz_a_flux_pulse_q2_q1"]
    return s


def test_moving_rule_applies_per_line():
    s = _one_line_chip()
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
    s = _one_line_chip()
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


def test_a_kept_plain_value_equal_to_the_model_gate_name_is_renamed():
    s = _two_pulse_chip()
    s["qubit_pairs"]["q1-q2"]["macros"]["cz_a"]["id"] = "cz_a"
    res = cgb.plan(s, "q1-q2", gate_name="cz_b", gate_class=LAB_GATE)
    assert dict(res["creates"])["qubit_pairs.q1-q2.macros.cz_b"]["id"] == "cz_b"


def test_same_gate_name_on_another_pair_is_planned():
    res = cgb.plan(_two_pulse_chip(), "q3-q4", gate_name="cz_a", gate_class=LAB_GATE)
    paths = [p for p, _ in res["creates"]]
    assert paths == ["qubit_pairs.q3-q4.macros.cz_a",
                     "qubits.q3.z.operations.cz_a_flux_pulse_q3_q4",
                     "qubits.q4.z.operations.cz_a_flux_pulse_q4_q3"]
    for s in _strings(res["creates"]):
        assert "q1" not in s.split("_") and "q2" not in s.split("_")
        assert "q1-q2" not in s


def test_a_name_extending_the_model_name_is_planned():
    res = cgb.plan(_two_pulse_chip(), "q1-q2", gate_name="cz_a_v2", gate_class=LAB_GATE)
    assert "qubits.q1.z.operations.cz_a_v2_flux_pulse_q1_q2" in dict(res["creates"])


def test_a_name_with_a_model_qubit_token_is_planned():
    res = cgb.plan(_two_pulse_chip(), "q3-q4", gate_name="cz_q1_fast", gate_class=LAB_GATE)
    assert "qubits.q3.z.operations.cz_q1_fast_flux_pulse_q3_q4" in dict(res["creates"])


def test_free_text_is_neither_renamed_nor_refused():
    s = _two_pulse_chip()
    s["qubit_pairs"]["q1-q2"]["macros"]["cz_a"]["flux_pulse_qubit"]["note"] = "calibrated on q1"
    s["qubit_pairs"]["q1-q2"]["macros"]["cz_a"]["label"] = "q2"
    res = cgb.plan(s, "q3-q4", gate_name="cz_b", gate_class=LAB_GATE)
    macro = dict(res["creates"])["qubit_pairs.q3-q4.macros.cz_b"]
    assert macro["flux_pulse_qubit"]["note"] == "calibrated on q1"
    assert macro["label"] == "q4"  # an exact model qubit id maps by role


def test_a_gate_sm_builds_needs_both_qubits_of_the_pair():
    """Both classes SM builds align the channels of both qubits when the gate
    runs (seen on a real chip: the lab's code fails on a pair whose target is
    not on the chip), so the plan refuses instead of proposing such a gate."""
    s = _chip()
    del s["qubits"]["q2"]
    res = _plan(s)  # official class; the pulse plays on the control line only
    assert "points at the qubit q2, which this chip does not have" in res["error"]
    assert "qubit_target" in res["error"] and "creates" not in res
    res = cgb.plan(s, "q1-2", gate_name="cz_c", gate_class=cgb.COUPLER_CZ_GATE,
                   pulses={"coupler_flux_pulse": {"class": SQUARE, "fields": {"length": 48}}})
    assert "points at the qubit q2" in res["error"]
    s = _chip()
    del s["qubits"]["q1"]
    assert "qubit_control" in _plan(s)["error"]
    assert "creates" in _plan(_chip())  # both qubits present: planned


def test_a_copy_that_never_touches_the_missing_qubit_is_planned():
    s = _one_line_chip()
    s["qubit_pairs"]["q3-q9"] = _pair("q3", "q9")
    res = cgb.plan(s, "q3-q9", gate_name="cz_b", gate_class=LAB_GATE)
    assert "qubits.q3.z.operations.cz_b_flux_pulse_q3_q9" in dict(res["creates"])


def test_two_line_model_crosses_pairs_with_different_moving_qubits():
    s = _two_pulse_chip()
    s["qubit_pairs"]["q3-q4"]["moving_qubit"] = "target"
    assert "creates" in cgb.plan(s, "q3-q4", gate_name="cz_b", gate_class=LAB_GATE)
    s = _one_line_chip()
    s["qubit_pairs"]["q3-q4"]["moving_qubit"] = "target"
    assert "same moving qubit" in cgb.plan(
        s, "q3-q4", gate_name="cz_b", gate_class=LAB_GATE)["error"]


def test_copy_from_the_cz_alias_follows_it():
    s = _chip()
    _macro_owned(s, "q1-2", "cz_src", length=64, amp=0.25)
    s["qubit_pairs"]["q1-2"]["macros"]["cz"] = "#./cz_src"
    res = _plan(s, copy_from="cz", pulses={"flux_pulse_qubit": {"class": SQUARE, "fields": {}}})
    assert res["fields"]["flux_pulse_qubit"]["amplitude"] == cgb.COPIED
    s["qubit_pairs"]["q1-2"]["macros"]["cz"] = "#./nothing"
    assert "is an alias" in _plan(s, copy_from="cz")["error"]


def test_table_class_lists_the_macro_keys_it_writes():
    res = _plan(_chip())
    assert res["fields"]["macro"]["phase_shift_control"] == cgb.DEFAULT
    assert "flux_pulse_qubit" not in res["fields"]["macro"]


def test_two_pulses_naming_one_operation_are_refused():
    s = _two_pulse_chip()
    m = s["qubit_pairs"]["q1-q2"]["macros"]["cz_a"]
    key = "cz_a_flux_pulse_q1_q2"
    s["qubits"]["q1"]["z"]["operations"][key] = {"id": key, "length": 40,
                                                 "__class__": LAB_PULSE}
    m["flux_pulse_qubit"] = key
    m["flux_pulse_target"] = key
    res = cgb.plan(s, "q1-q2", gate_name="cz_b", gate_class=LAB_GATE)
    assert "would both be written as cz_b_flux_pulse_q1_q2" in res["error"]


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


def _bad_pair_entry():
    s = _chip()
    s["qubit_pairs"]["q0-q0"] = "not a pair"
    return s


def _bad_macros_entry():
    s = _chip()
    s["qubit_pairs"]["q2-3"]["macros"] = ["not", "a", "dict"]
    return s


@pytest.mark.parametrize("state,kw", [
    (_chip, {"pulses": "abc"}),
    (_chip, {"pulses": {"flux_pulse_qubit": "abc"}}),
    (_chip, {"pulses": {"flux_pulse_qubit": {"class": SQUARE, "fields": [1]}}}),
    (_chip, {"gate_class": LAB_GATE, "model_gate": "q1-2"}),
    (_chip, {"gate_class": LAB_GATE, "model_gate": ("q1-2",)}),
    (_bad_qubit_entry, {}),
    (_bad_pair_entry, {"gate_class": LAB_GATE}),
    (_bad_macros_entry, {"gate_class": LAB_GATE}),
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


# --- plan never raises, and copies follow the model exactly ---------------

def _target_line_chip():
    """Model cz_a on q1-q2 that plays only the target z line."""
    s = _two_pulse_chip()
    del s["qubit_pairs"]["q1-q2"]["macros"]["cz_a"]["flux_pulse_qubit"]
    del s["qubits"]["q1"]["z"]["operations"]["cz_a_flux_pulse_q1_q2"]
    return s


@pytest.mark.parametrize("control", [None, "#./q1"])
def test_model_pair_with_an_unresolved_qubit(control):
    s = _target_line_chip()
    s["qubit_pairs"]["q1-q2"]["qubit_control"] = control
    res = cgb.plan(s, "q1-q2", gate_name="cz_b", gate_class=LAB_GATE)
    assert "qubits.q2.z.operations.cz_b_flux_pulse_q2_q1" in dict(res["creates"])
    res = cgb.plan(s, "q3-q4", gate_name="cz_b", gate_class=LAB_GATE)
    assert "The control of the model pair q1-q2" in res["error"]


@pytest.mark.parametrize("kw", [
    {"pair_id": "q1-q2", "gate_name": 5, "gate_class": "some.Class"},
    {"pair_id": "q1-q2", "gate_name": "cz_b", "gate_class": ["x"]},
    {"pair_id": 7, "gate_name": "cz_b", "gate_class": LAB_GATE},
])
def test_non_string_arguments_are_refusals(kw):
    res = cgb.plan(_two_pulse_chip(), kw.pop("pair_id"), **kw)
    assert isinstance(res["error"], str)


def test_an_internal_error_becomes_a_refusal(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("forced")
    monkeypatch.setattr(cgb, "_lines", boom)
    res = cgb.plan(_chip(), "q1-2", gate_name="cz_new", gate_class=cgb.CZ_GATE,
                   pulses=_square())
    assert res["error"].startswith("SM could not plan this gate (RuntimeError). Nothing was changed.")


def _op_owned_lab_chip(op_id):
    s = _two_pulse_chip()
    m = s["qubit_pairs"]["q1-q2"]["macros"]["cz_a"]
    for field, q, key in (("flux_pulse_qubit", "q1", "cz_a_flux_pulse_q1_q2"),
                          ("flux_pulse_target", "q2", "cz_a_flux_pulse_q2_q1")):
        s["qubits"][q]["z"]["operations"][key] = {
            "id": op_id(key), "length": 40, "amplitude": 0.2, "__class__": LAB_PULSE}
        m[field] = key
    return s


def test_operation_owned_copy_keeps_the_model_id_shape():
    res = cgb.plan(_op_owned_lab_chip(lambda k: None), "q1-q2", gate_name="cz_b",
                   gate_class=LAB_GATE)
    assert dict(res["creates"])["qubits.q1.z.operations.cz_b_flux_pulse_q1_q2"]["id"] is None
    res = cgb.plan(_op_owned_lab_chip(lambda k: k), "q1-q2", gate_name="cz_b",
                   gate_class=LAB_GATE)
    assert dict(res["creates"])["qubits.q1.z.operations.cz_b_flux_pulse_q1_q2"]["id"] == \
        "cz_b_flux_pulse_q1_q2"


def test_a_plain_operation_copy_holds_what_the_macro_pulse_holds():
    s = _two_pulse_chip()
    op = s["qubits"]["q1"]["z"]["operations"]["cz_a_flux_pulse_q1_q2"]
    op["amplitude"] = 0.2
    s["qubits"]["q1"]["z"]["operations"]["cz_a_flux_pulse_q1_q2"]["length"] = 40
    s["qubit_pairs"]["q1-q2"]["macros"]["cz_src"] = {
        "__class__": "other.Gate", "flux_pulse_qubit": {
            "id": "x", "amplitude": 0.33, "length": 44, "__class__": LAB_PULSE}}
    res = cgb.plan(s, "q1-q2", gate_name="cz_b", gate_class=LAB_GATE, copy_from="cz_src")
    by = dict(res["creates"])
    pulse = by["qubit_pairs.q1-q2.macros.cz_b"]["flux_pulse_qubit"]
    op = by["qubits.q1.z.operations.cz_b_flux_pulse_q1_q2"]
    assert pulse["amplitude"] == op["amplitude"] == 0.33
    assert pulse["length"] == op["length"] == 44


def test_a_reference_through_the_cz_alias_is_a_reference_to_the_model():
    s = _two_pulse_chip()
    s["qubit_pairs"]["q1-q2"]["macros"]["cz"] = "#./cz_a"
    op = s["qubits"]["q1"]["z"]["operations"]["cz_a_flux_pulse_q1_q2"]
    op["amplitude"] = "#/qubit_pairs/q1-q2/macros/cz/flux_pulse_qubit/amplitude"
    res = cgb.plan(s, "q1-q2", gate_name="cz_b", gate_class=LAB_GATE)
    assert dict(res["creates"])["qubits.q1.z.operations.cz_b_flux_pulse_q1_q2"]["amplitude"] == \
        "#/qubit_pairs/q1-q2/macros/cz_b/flux_pulse_qubit/amplitude"


def test_a_pulse_id_that_differs_from_its_operation_key_is_named():
    s = _two_pulse_chip()
    s["qubit_pairs"]["q1-q2"]["macros"]["cz_a"]["flux_pulse_qubit"]["id"] = "cz_a_other"
    res = cgb.plan(s, "q1-q2", gate_name="cz_b", gate_class=LAB_GATE)
    assert ("The pulse flux_pulse_qubit of cz_a is named cz_a_other but its "
            "operation is cz_a_flux_pulse_q1_q2") in res["error"]


@pytest.mark.parametrize("key", ["qubits", "qubit_pairs"])
def test_a_container_that_is_not_a_dict_is_a_refusal(key):
    s = _chip()
    s[key] = ["not", "a", "dict"]
    assert isinstance(_plan(s)["error"], str)


def test_a_reference_to_the_model_pair_itself_is_refused_across_pairs():
    s = _two_pulse_chip()
    s["qubit_pairs"]["q1-q2"]["macros"]["cz_a"]["pair"] = "#/qubit_pairs/q1-q2"
    res = cgb.plan(s, "q3-q4", gate_name="cz_b", gate_class=LAB_GATE)
    assert "#/qubit_pairs/q1-q2 " in res["error"]
    assert "creates" in cgb.plan(s, "q1-q2", gate_name="cz_b", gate_class=LAB_GATE)


def test_refresh_copies_the_rewritten_value_and_keeps_plain_over_relative():
    s = _two_pulse_chip()
    m = s["qubit_pairs"]["q1-q2"]["macros"]["cz_a"]["flux_pulse_qubit"]
    m["amplitude"] = "#/qubit_pairs/q1-q2/macros/cz_a/flux_pulse_target/amplitude"
    m["detuning"] = "#./amplitude"
    op = s["qubits"]["q1"]["z"]["operations"]["cz_a_flux_pulse_q1_q2"]
    op["amplitude"] = 0.2
    op["detuning"] = 0.0
    res = cgb.plan(s, "q1-q2", gate_name="cz_b", gate_class=LAB_GATE)
    op = dict(res["creates"])["qubits.q1.z.operations.cz_b_flux_pulse_q1_q2"]
    assert op["amplitude"] == "#/qubit_pairs/q1-q2/macros/cz_b/flux_pulse_target/amplitude"
    assert op["detuning"] == 0.0


def test_a_given_explicit_length_is_linked_from_an_inferred_operation():
    s = _two_pulse_chip()
    m = s["qubit_pairs"]["q1-q2"]["macros"]["cz_a"]["flux_pulse_qubit"]
    m["length"] = "#./inferred_length"
    s["qubits"]["q1"]["z"]["operations"]["cz_a_flux_pulse_q1_q2"]["length"] = "#./inferred_length"
    res = cgb.plan(s, "q1-q2", gate_name="cz_b", gate_class=LAB_GATE,
                   pulses={"flux_pulse_qubit": {"fields": {"length": 60}}})
    by = dict(res["creates"])
    assert by["qubit_pairs.q1-q2.macros.cz_b"]["flux_pulse_qubit"]["length"] == 60
    assert by["qubits.q1.z.operations.cz_b_flux_pulse_q1_q2"]["length"] == \
        "#/qubit_pairs/q1-q2/macros/cz_b/flux_pulse_qubit/length"


def test_a_spectator_operation_name_is_not_kept():
    s = _two_pulse_chip()
    s["qubits"]["q2"]["z"]["operations"]["cz_a_spec_q3"] = {"length": 40}
    s["qubit_pairs"]["q1-q2"]["macros"]["cz_a"]["spectator_pulse"] = "cz_a_spec_q3"
    res = cgb.plan(s, "q1-q2", gate_name="cz_b", gate_class=LAB_GATE)
    assert dict(res["creates"])["qubit_pairs.q1-q2.macros.cz_b"]["spectator_pulse"] is None


def test_a_coupler_only_copy_without_qubit_ids_needs_no_pair_qubits():
    c = _chip(4)
    c["qubit_pairs"]["q1-2"]["coupler"]["operations"]["cz_c_cpl"] = {
        "id": "cz_c_cpl", "length": 40, "__class__": LAB_PULSE}
    c["qubit_pairs"]["q1-2"]["macros"]["cz_c"] = {"__class__": LAB_GATE,
                                                  "coupler_pulse": "cz_c_cpl"}
    c["qubit_pairs"]["q3-4"]["qubit_control"] = None
    res = cgb.plan(c, "q3-4", gate_name="cz_d", gate_class=LAB_GATE)
    assert "qubit_pairs.q3-4.coupler.operations.cz_d_cpl" in dict(res["creates"])


def test_a_kept_macro_reference_into_the_model_pair_is_refused_across_pairs():
    s = _two_pulse_chip()
    s["qubit_pairs"]["q1-q2"]["macros"]["cz_a"]["mode"] = "#/qubit_pairs/q1-q2/extras/mode"
    res = cgb.plan(s, "q3-q4", gate_name="cz_b", gate_class=LAB_GATE)
    assert "#/qubit_pairs/q1-q2/extras/mode" in res["error"]


def _third_pair_chip():
    s = _two_pulse_chip()
    s["qubit_pairs"]["q9-q8"] = _pair("q9", "q8")
    s["qubit_pairs"]["q9-q8"]["macros"]["cz_a"] = {"extras": {}}
    return s


def test_a_climbing_reference_that_dangles_in_the_model_is_kept_and_listed():
    s = _two_pulse_chip()
    s["qubit_pairs"]["q1-q2"]["macros"]["cz_a"]["flux_pulse_qubit"]["src"] = "#../nowhere/x"
    res = cgb.plan(s, "q1-q2", gate_name="cz_b", gate_class=LAB_GATE)
    assert dict(res["creates"])["qubit_pairs.q1-q2.macros.cz_b"]["flux_pulse_qubit"]["src"] == \
        "#../nowhere/x"
    assert any("#../nowhere/x" in u for u in res["unresolved_references"])


# --- climbing relative references stay inside their own object -----------

def _macro_pulse(s):
    return s["qubit_pairs"]["q1-q2"]["macros"]["cz_a"]["flux_pulse_qubit"]


def _model_op(s):
    return s["qubits"]["q1"]["z"]["operations"]["cz_a_flux_pulse_q1_q2"]


OUTSIDE = ("points outside its own macro or operation. SM copies only relative "
           "references that stay inside their own object.")


def test_a_reference_that_returns_through_an_alias_is_refused():
    s = _two_pulse_chip()
    s["qubit_pairs"]["q1-q2"]["macros"]["cz"] = "#./cz_a"
    _macro_pulse(s)["src"] = "#../../cz/flux_pulse_target/amplitude"
    res = cgb.plan(s, "q1-q2", gate_name="cz_b", gate_class=LAB_GATE)
    assert OUTSIDE in res["error"]


def test_a_mid_path_reference_string_inside_the_object_is_refused():
    s = _two_pulse_chip()
    m = s["qubit_pairs"]["q1-q2"]["macros"]["cz_a"]
    m["a"] = "#/qubits/q1"
    _macro_pulse(s)["src"] = "#../a/z"
    assert OUTSIDE in cgb.plan(s, "q1-q2", gate_name="cz_b", gate_class=LAB_GATE)["error"]


def test_a_popped_step_that_is_a_reference_string_is_refused():
    s = _two_pulse_chip()
    s["qubits"]["q9"] = _qubit("q9")
    s["qubits"]["q9"]["xy"] = {"operations": {"x180": {"amp": 1}, "x90": {"amp": 2}}}
    m = s["qubit_pairs"]["q1-q2"]["macros"]["cz_a"]
    m["link"] = "#/qubits/q9/xy/operations/x180"
    m["x90"] = {"amp": 3}
    _macro_pulse(s)["src"] = "#../link/../x90"
    assert OUTSIDE in cgb.plan(s, "q1-q2", gate_name="cz_b", gate_class=LAB_GATE)["error"]


def test_a_node_missing_in_the_copy_is_refused():
    # the model resolves through a spectator entry that the copy empties
    s = _two_pulse_chip()
    s["qubit_pairs"]["q1-q2"]["macros"]["cz_a"]["spectator_qubits"] = {"q5": {"amp": 1}}
    _macro_pulse(s)["src"] = "#../spectator_qubits/q5/../../flux_pulse_target/amplitude"
    assert OUTSIDE in cgb.plan(s, "q1-q2", gate_name="cz_b", gate_class=LAB_GATE)["error"]


def test_a_list_index_out_of_range_does_not_resolve_and_is_listed():
    s = _two_pulse_chip()
    s["qubit_pairs"]["q1-q2"]["macros"]["cz_a"]["steps"] = [10, 20]
    _macro_pulse(s)["first"] = "#../steps/5"
    res = cgb.plan(s, "q1-q2", gate_name="cz_b", gate_class=LAB_GATE)
    assert any("#../steps/5" in u for u in res["unresolved_references"])


def test_a_non_ascii_digit_step_is_an_explicit_refusal():
    s = _two_pulse_chip()
    s["qubit_pairs"]["q1-q2"]["macros"]["cz_a"]["steps"] = [10, 20]
    _macro_pulse(s)["first"] = "#../steps/²"
    res = cgb.plan(s, "q1-q2", gate_name="cz_b", gate_class=LAB_GATE)
    assert "looks like a number but is not one" in res["error"]


def test_a_same_object_form_with_dotdot_is_checked_like_a_climb():
    s = _two_pulse_chip()
    _macro_pulse(s)["inner"] = {"k": 1}
    _macro_pulse(s)["src"] = "#./inner/../../../cz_x"
    s["qubit_pairs"]["q1-q2"]["macros"]["cz_x"] = {"y": 1}
    assert OUTSIDE in cgb.plan(s, "q1-q2", gate_name="cz_b", gate_class=LAB_GATE)["error"]
    s = _two_pulse_chip()
    _macro_pulse(s)["inner"] = {"k": 1}
    _macro_pulse(s)["src"] = "#./inner/../../flux_pulse_target/amplitude"
    res = cgb.plan(s, "q1-q2", gate_name="cz_b", gate_class=LAB_GATE)
    assert dict(res["creates"])["qubit_pairs.q1-q2.macros.cz_b"]["flux_pulse_qubit"]["src"] == \
        "#./inner/../../flux_pulse_target/amplitude"
    assert res["unresolved_references"] == []


@pytest.mark.parametrize("pair", ["q1-q2", "q3-q4"])
def test_a_reference_to_a_sibling_field_of_the_same_macro_is_kept(pair):
    s = _two_pulse_chip()
    _macro_pulse(s)["src"] = "#../flux_pulse_target/amplitude"
    res = cgb.plan(s, pair, gate_name="cz_b", gate_class=LAB_GATE)
    macro = dict(res["creates"])[f"qubit_pairs.{pair}.macros.cz_b"]
    assert macro["flux_pulse_qubit"]["src"] == "#../flux_pulse_target/amplitude"


def test_a_reference_into_a_reset_key_of_the_macro_is_refused():
    s = _two_pulse_chip()
    s["qubit_pairs"]["q1-q2"]["macros"]["cz_a"]["extras"] = {"gain": 2}
    _macro_pulse(s)["src"] = "#../extras/gain"
    res = cgb.plan(s, "q1-q2", gate_name="cz_b", gate_class=LAB_GATE)
    assert "whose target extras is not copied" in res["error"]


def test_a_reference_inside_the_same_operation_is_kept():
    s = _op_owned_lab_chip(lambda k: k)
    _model_op(s)["twin"] = "#./amplitude"
    _model_op(s)["deep"] = {"x": "#../amplitude"}
    res = cgb.plan(s, "q1-q2", gate_name="cz_b", gate_class=LAB_GATE)
    op = dict(res["creates"])["qubits.q1.z.operations.cz_b_flux_pulse_q1_q2"]
    assert op["deep"] == {"x": "#../amplitude"} and op["twin"] == "#./amplitude"


def test_a_reference_to_a_sibling_operation_is_refused():
    s = _op_owned_lab_chip(lambda k: k)
    s["qubits"]["q1"]["z"]["operations"]["shared_op"] = {"amp": 0.1}
    _model_op(s)["amp"] = "#../shared_op/amp"
    res = cgb.plan(s, "q1-q2", gate_name="cz_b", gate_class=LAB_GATE)
    assert "The model gate cz_a uses the relative reference #../shared_op/amp" in res["error"]
    assert OUTSIDE in res["error"]


def test_a_reference_to_the_operations_channel_is_refused():
    s = _op_owned_lab_chip(lambda k: k)
    s["qubits"]["q1"]["z"]["offset"] = 0.0
    _model_op(s)["base"] = "#../../offset"
    res = cgb.plan(s, "q1-q2", gate_name="cz_b", gate_class=LAB_GATE)
    assert OUTSIDE in res["error"]


def test_a_reference_through_a_reference_string_is_resolved_and_refused():
    s = _two_pulse_chip()
    _macro_pulse(s)["zline"] = "#../../../qubit_control/z"
    res = cgb.plan(s, "q1-q2", gate_name="cz_b", gate_class=LAB_GATE)
    assert OUTSIDE in res["error"] and "#../../../qubit_control/z" in res["error"]


def test_a_reference_through_a_list_index_in_the_same_macro_is_kept():
    s = _two_pulse_chip()
    s["qubit_pairs"]["q1-q2"]["macros"]["cz_a"]["steps"] = [10, 20]
    _macro_pulse(s)["first"] = "#../steps/0"
    res = cgb.plan(s, "q1-q2", gate_name="cz_b", gate_class=LAB_GATE)
    macro = dict(res["creates"])["qubit_pairs.q1-q2.macros.cz_b"]
    assert macro["flux_pulse_qubit"]["first"] == "#../steps/0"
    assert res["unresolved_references"] == []


def test_a_reference_into_a_third_pair_is_refused():
    s = _third_pair_chip()
    _macro_pulse(s)["src"] = "#../../../../q9-q8/macros/cz_a/extras"
    for pair in ("q1-q2", "q3-q4"):
        res = cgb.plan(s, pair, gate_name="cz_b", gate_class=LAB_GATE)
        assert OUTSIDE in res["error"]


@pytest.mark.parametrize("pair", ["q1-q2", "q3-q4"])
def test_the_real_model_shape_is_planned_as_before(pair):
    s = _two_pulse_chip()
    _macro_pulse(s)["length"] = "#./inferred_length"
    _model_op(s)["length"] = "#./inferred_length"
    res = cgb.plan(s, pair, gate_name="cz_b", gate_class=LAB_GATE)
    assert "creates" in res and res["unresolved_references"] == []


def test_renamed_has_no_identity_entries_and_only_used_qubits():
    s = _target_line_chip()
    res = cgb.plan(s, "q3-q4", gate_name="cz_a", gate_class=LAB_GATE)
    assert "cz_a" not in res["renamed"]
    assert res["renamed"] == {"cz_a_flux_pulse_q2_q1": "cz_a_flux_pulse_q4_q3",
                              "q1": "q3", "q2": "q4"}
    s = _chip(4)
    s["qubit_pairs"]["q1-2"]["coupler"]["operations"]["cz_a_cpl"] = {
        "id": "cz_a_cpl", "length": 40, "__class__": LAB_PULSE}
    s["qubit_pairs"]["q1-2"]["macros"]["cz_a"] = {"__class__": LAB_GATE, "coupler_pulse": "cz_a_cpl"}
    res = cgb.plan(s, "q3-4", gate_name="cz_b", gate_class=LAB_GATE)
    assert res["renamed"] == {"cz_a_cpl": "cz_b_cpl", "cz_a": "cz_b"}


def test_a_kept_value_changed_by_the_rename_is_labelled_renamed():
    s = _two_pulse_chip()
    s["qubit_pairs"]["q1-q2"]["macros"]["cz_a"]["label"] = "q1"
    res = cgb.plan(s, "q3-q4", gate_name="cz_b", gate_class=LAB_GATE)
    assert res["fields"]["macro"]["label"] == cgb.FROM_MODEL_RENAMED


def test_a_gate_name_with_a_qubit_token_is_not_a_stray_id():
    s = _target_line_chip()
    m = s["qubit_pairs"]["q1-q2"]["macros"].pop("cz_a")
    ops = s["qubits"]["q2"]["z"]["operations"]
    ops.pop("cz_a_flux_pulse_q2_q1")
    ops["cz_q7_pulse_q2"] = {"id": "cz_q7_pulse_q2", "length": 40, "amplitude": 0.2,
                             "__class__": LAB_PULSE}
    m["flux_pulse_target"]["id"] = "cz_q7_pulse_q2"
    s["qubit_pairs"]["q1-q2"]["macros"]["cz_q7"] = m
    s["qubits"]["q7"] = _qubit("q7")
    s["qubit_pairs"]["q1-q2"]["qubit_control"] = None
    s["qubit_pairs"]["q1-q2"]["moving_qubit"] = "target"
    s["qubit_pairs"]["q3-q4"]["moving_qubit"] = "target"
    res = cgb.plan(s, "q3-q4", gate_name="cz_b", gate_class=LAB_GATE)
    assert "qubits.q4.z.operations.cz_b_pulse_q4" in dict(res["creates"])


def test_a_plain_raw_model_qubit_text_in_a_name_blocks_the_copy():
    c = _chip(4)
    c["qubit_pairs"]["q1-2"]["coupler"]["operations"]["cz_a_cpl_qX_q2"] = {
        "id": "cz_a_cpl_qX_q2", "length": 40, "__class__": LAB_PULSE}
    c["qubit_pairs"]["q1-2"]["macros"]["cz_a"] = {"__class__": LAB_GATE,
                                                  "coupler_pulse": "cz_a_cpl_qX_q2"}
    c["qubit_pairs"]["q1-2"]["qubit_control"] = "qX"
    res = cgb.plan(c, "q3-4", gate_name="cz_b", gate_class=LAB_GATE)
    assert "The control of the model pair q1-2" in res["error"]


def test_spectator_settings_are_kept_and_resets_are_listed():
    s = _two_pulse_chip()
    s["qubits"]["q1"]["z"]["operations"]["cz_a_spec_q5"] = {"length": 40}
    s["qubit_pairs"]["q1-q2"]["macros"]["cz_a"].update({
        "spectator_mode": "static", "spectator_enabled": True,
        "spectator_duration": 40, "spectator_qubits": {"q5": "#/qubits/q5"},
        "spectator_pulse": "cz_a_spec_q5", "fidelity": {"rb": 0.99},
        "phase_shift_control": 0.1, "extras": {}})
    res = cgb.plan(s, "q1-q2", gate_name="cz_b", gate_class=LAB_GATE)
    macro = dict(res["creates"])["qubit_pairs.q1-q2.macros.cz_b"]
    assert (macro["spectator_mode"], macro["spectator_enabled"],
            macro["spectator_duration"]) == ("static", True, 40)
    for k in ("spectator_mode", "spectator_enabled", "spectator_duration"):
        assert res["fields"]["macro"][k] == cgb.FROM_MODEL
    assert macro["spectator_qubits"] == {} and macro["spectator_pulse"] is None
    assert sorted(res["reset"]) == sorted(["spectator_qubits", "spectator_pulse",
                                           "fidelity", "phase_shift_control", "extras"])


def test_layout_is_reported_in_both_paths():
    assert _plan(_chip())["layout"] == "known"
    s = _two_pulse_chip()
    s["qubit_pairs"]["q3-q4"]["moving_qubit"] = "target"
    res = cgb.plan(s, "q3-q4", gate_name="cz_b", gate_class=LAB_GATE)
    assert res["layout"] == "copied" and "creates" in res


def test_only_the_role_a_name_needs_must_have_a_qubit():
    c = _chip(4)
    c["qubit_pairs"]["q1-2"]["coupler"]["operations"]["cz_a_coupler_pulse_q2"] = {
        "id": "cz_a_coupler_pulse_q2", "length": 40, "__class__": LAB_PULSE}
    c["qubit_pairs"]["q1-2"]["macros"]["cz_a"] = {"__class__": LAB_GATE,
                                                  "coupler_pulse": "cz_a_coupler_pulse_q2"}
    c["qubit_pairs"]["q3-4"]["qubit_control"] = None
    res = cgb.plan(c, "q3-4", gate_name="cz_b", gate_class=LAB_GATE)
    assert "qubit_pairs.q3-4.coupler.operations.cz_b_coupler_pulse_q4" in dict(res["creates"])


def test_a_spectator_operation_on_a_third_qubit_is_reset():
    s = _two_pulse_chip()
    s["qubits"]["q5"] = _qubit("q5")
    s["qubits"]["q5"]["z"]["operations"]["cz_a_spectator_pulse_q5"] = {"length": 40}
    s["qubit_pairs"]["q1-q2"]["macros"]["cz_a"].update({
        "spectator_qubits": {"q5": "#/qubits/q5"},
        "spectator_pulse": "cz_a_spectator_pulse_q5", "spectator_mode": "static"})
    res = cgb.plan(s, "q1-q2", gate_name="cz_b", gate_class=LAB_GATE)
    macro = dict(res["creates"])["qubit_pairs.q1-q2.macros.cz_b"]
    assert macro["spectator_pulse"] is None and macro["spectator_mode"] == "static"
    assert {"spectator_pulse", "spectator_qubits"} <= set(res["reset"])
    assert res["fields"]["macro"]["spectator_mode"] == cgb.FROM_MODEL
    assert not any("cz_a_spectator_pulse_q5" in x for x in _strings(res["creates"]))


def test_a_table_class_plan_resets_nothing_and_renames_nothing():
    res = _plan(_chip())
    assert res["reset"] == [] and res["renamed"] == {}
    assert res["operations"] == {"flux_pulse_qubit": {
        "path": "qubits.q1.z.operations.cz_new_pulse_q1_q2",
        "key": "cz_new_pulse_q1_q2", "channel": "q1.z", "created": True}}


def test_a_coupler_only_model_on_a_pair_with_a_null_control_copies_across():
    c = _chip(4)
    c["qubit_pairs"]["q1-2"]["coupler"]["operations"]["cz_a_coupler_pulse_q2"] = {
        "id": "cz_a_coupler_pulse_q2", "length": 40, "__class__": LAB_PULSE}
    c["qubit_pairs"]["q1-2"]["macros"]["cz_a"] = {"__class__": LAB_GATE,
                                                  "coupler_pulse": "cz_a_coupler_pulse_q2"}
    c["qubit_pairs"]["q1-2"]["qubit_control"] = None
    res = cgb.plan(c, "q3-4", gate_name="cz_b", gate_class=LAB_GATE)
    assert "qubit_pairs.q3-4.coupler.operations.cz_b_coupler_pulse_q4" in dict(res["creates"])


def test_a_relative_reference_to_a_same_named_gate_on_a_third_pair_is_kept():
    s = _two_pulse_chip()
    ref = "#../../../../qubit_pairs/q9-q8/macros/cz_a/extras"
    s["qubit_pairs"]["q1-q2"]["macros"]["cz_a"]["flux_pulse_qubit"]["src"] = ref
    res = cgb.plan(s, "q1-q2", gate_name="cz_b", gate_class=LAB_GATE)
    macro = dict(res["creates"])["qubit_pairs.q1-q2.macros.cz_b"]
    assert macro["flux_pulse_qubit"]["src"] == ref


def test_a_cross_pair_copy_reports_operations_and_renames():
    res = cgb.plan(_two_pulse_chip(), "q3-q4", gate_name="cz_b", gate_class=LAB_GATE)
    assert res["operations"]["flux_pulse_target"] == {
        "path": "qubits.q4.z.operations.cz_b_flux_pulse_q4_q3",
        "key": "cz_b_flux_pulse_q4_q3", "channel": "q4.z", "created": True}
    assert res["renamed"] == {"cz_a": "cz_b",
                              "cz_a_flux_pulse_q1_q2": "cz_b_flux_pulse_q3_q4",
                              "cz_a_flux_pulse_q2_q1": "cz_b_flux_pulse_q4_q3",
                              "q1": "q3", "q2": "q4"}


@pytest.mark.parametrize("copied", [False, True])
def test_every_macro_key_written_is_accounted_for(copied):
    if copied:
        s = _two_pulse_chip()
        s["qubit_pairs"]["q1-q2"]["macros"]["cz_a"].update(
            {"duration": "#./total", "fidelity": {"x": 1}, "label": "fast"})
        res = cgb.plan(s, "q1-q2", gate_name="cz_b", gate_class=LAB_GATE)
        macro = dict(res["creates"])["qubit_pairs.q1-q2.macros.cz_b"]
    else:
        res = _plan(_chip())
        macro = dict(res["creates"])["qubit_pairs.q1-2.macros.cz_new"]
    pulse_fields = set(res["pulse_directions"]) | {"coupler_flux_pulse"}
    for k, v in macro.items():
        if k == "__class__" or k in pulse_fields or v is None:
            continue
        assert k in res["fields"]["macro"] or k in res["reset"], k


# --- reverse direction writes references; copy fills slots; no flux line ---

def _op_owned_ref(state, pid, name, coupler=True):
    """A reverse-layout gate whose macro fields reference the operations."""
    p = state["qubit_pairs"][pid]
    c, t = p["qubit_control"].split("/")[-1], p["qubit_target"].split("/")[-1]
    m = cgb._cz_skeleton()
    key = f"{name}_flux_pulse_{c}_{t}"
    state["qubits"][c]["z"]["operations"][key] = {
        "id": key, "length": 40, "amplitude": 0.15, "__class__": SQUARE}
    m["flux_pulse_qubit"] = f"#/qubits/{c}/z/operations/{key}"
    if coupler:
        ck = f"{name}_coupler_pulse_{c}_{t}"
        p["coupler"]["operations"][ck] = {
            "id": ck, "length": 40, "amplitude": 0.25, "__class__": SQUARE}
        m["coupler_flux_pulse"] = f"#/qubit_pairs/{pid}/coupler/operations/{ck}"
    p["macros"][name] = m


def _both():
    return {**_square(), "coupler_flux_pulse": {"class": SQUARE, "fields": {"length": 48}}}


def test_reverse_reference_chip_gets_references_in_both_fields():
    s = _chip()
    _op_owned_ref(s, "q1-2", "cz_old")
    res = _plan(s, pulses=_both())
    macro = dict(res["creates"])["qubit_pairs.q1-2.macros.cz_new"]
    assert macro["flux_pulse_qubit"] == "#/qubits/q1/z/operations/cz_new_pulse_q1_q2"
    assert macro["coupler_flux_pulse"] == (
        "#/qubit_pairs/q1-2/coupler/operations/cz_new_coupler_pulse_q1_q2")
    assert res["operations"]["flux_pulse_qubit"]["macro_holds"] == "reference"
    paths = [p for p, _ in res["creates"]]
    for f in ("flux_pulse_qubit", "coupler_flux_pulse"):
        assert macro[f][2:].replace("/", ".") in paths


def test_coupler_only_class_on_fixed_qubits_gets_a_reference():
    s = _chip()
    for q in s["qubits"].values():
        del q["z"]
    p = s["qubit_pairs"]["q1-2"]
    p["coupler"]["operations"]["cz_old_coupler_pulse_q1_q2"] = {
        "id": "cz_old_coupler_pulse_q1_q2", "length": 40, "amplitude": 0.2,
        "__class__": SQUARE}
    m = cgb._coupler_cz_skeleton()
    m["coupler_flux_pulse"] = "#/qubit_pairs/q1-2/coupler/operations/cz_old_coupler_pulse_q1_q2"
    p["macros"]["cz_old"] = m
    res = _plan(s, gate_class=cgb.COUPLER_CZ_GATE,
                pulses={"coupler_flux_pulse": {"class": SQUARE, "fields": {"length": 48}}})
    assert res["direction"] == cgb.OPERATION_OWNS
    macro = dict(res["creates"])["qubit_pairs.q1-2.macros.cz_new"]
    assert macro["coupler_flux_pulse"] == (
        "#/qubit_pairs/q1-2/coupler/operations/cz_new_coupler_pulse_q1_q2")


def test_name_form_chip_keeps_names_where_every_gate_has_one():
    s = _dir_fixture(2)  # two gates with a plain name in flux_pulse_qubit only
    res = _plan(s, pulses=_both())
    macro = dict(res["creates"])["qubit_pairs.q1-2.macros.cz_new"]
    assert macro["flux_pulse_qubit"] == "cz_new_pulse_q1_q2"
    assert res["operations"]["flux_pulse_qubit"]["macro_holds"] == "name"
    assert macro["coupler_flux_pulse"].startswith("#/qubit_pairs/q1-2/coupler/operations/")
    assert res["operations"]["coupler_flux_pulse"]["macro_holds"] == "reference"


def test_mixed_name_and_reference_gates_get_a_reference():
    s = _chip()
    _op_owned(s, "q1-2", "cz_a")
    _op_owned_ref(s, "q1-2", "cz_b", coupler=False)
    res = _plan(s)
    macro = dict(res["creates"])["qubit_pairs.q1-2.macros.cz_new"]
    assert macro["flux_pulse_qubit"] == "#/qubits/q1/z/operations/cz_new_pulse_q1_q2"


def _copy_chip(form):
    s = _chip()
    _op_owned_ref(s, "q1-2", "cz_src")
    m = s["qubit_pairs"]["q1-2"]["macros"]["cz_src"]
    ops = s["qubits"]["q1"]["z"]["operations"]
    key = "cz_src_flux_pulse_q1_q2"
    if form == "inline":
        m["flux_pulse_qubit"] = dict(ops.pop(key))
    elif form == "name":
        m["flux_pulse_qubit"] = key
    elif form == "elsewhere":  # a reference to an operation off the pair's lines
        s["qubits"]["q3"]["z"]["operations"]["shared"] = ops.pop(key)
        m["flux_pulse_qubit"] = "#/qubits/q3/z/operations/shared"
    elif form == "relative":
        m["flux_pulse_qubit"] = f"#../../../../qubits/q1/z/operations/{key}"
    return s


@pytest.mark.parametrize("form", ["inline", "absolute", "elsewhere", "relative", "name"])
def test_copy_from_without_pulses_takes_the_source_class_and_values(form):
    s = _copy_chip(form)
    res = _plan(s, copy_from="cz_src", pulses=None)
    assert "error" not in res, res
    by = dict(res["creates"])
    op = by["qubit_pairs.q1-2.macros.cz_new"]["flux_pulse_qubit"]
    if not isinstance(op, dict):  # operation-owned: the operation holds it
        op = by["qubits.q1.z.operations.cz_new_pulse_q1_q2"]
    assert op["__class__"] == SQUARE and op["amplitude"] == 0.15 and op["length"] == 40
    assert res["fields"]["flux_pulse_qubit"]["amplitude"] == cgb.COPIED
    cop = by["qubit_pairs.q1-2.macros.cz_new"]["coupler_flux_pulse"]
    if not isinstance(cop, dict):
        cop = by["qubit_pairs.q1-2.coupler.operations.cz_new_coupler_pulse_q1_q2"]
    assert cop["amplitude"] == 0.25
    assert "cz_src" not in json.dumps(res["creates"])


def test_copy_from_a_given_value_wins_over_the_copied_one():
    s = _copy_chip("absolute")
    res = _plan(s, copy_from="cz_src",
                pulses={"flux_pulse_qubit": {"fields": {"amplitude": 0.5}}})
    op = dict(res["creates"])["qubits.q1.z.operations.cz_new_pulse_q1_q2"]
    assert op["amplitude"] == 0.5 and op["length"] == 40
    assert res["fields"]["flux_pulse_qubit"]["amplitude"] == cgb.GIVEN


def test_copy_from_leaves_an_optional_slot_the_source_does_not_fill_empty():
    s = _chip()
    _op_owned_ref(s, "q1-2", "cz_src", coupler=False)
    res = _plan(s, copy_from="cz_src", pulses=None)
    assert "error" not in res, res
    assert "coupler_flux_pulse" not in res["operations"]
    s2 = _chip()
    s2["qubit_pairs"]["q1-2"]["macros"]["cz_src"] = cgb._cz_skeleton()
    assert "holds no pulse in flux_pulse_qubit" in _plan(
        s2, copy_from="cz_src", pulses=None)["error"]
    assert "needs a pulse" in _plan(s2, pulses={})["error"]


def test_copy_from_a_relative_reference_off_the_pair_lines_is_walked():
    s = _copy_chip("elsewhere")
    m = s["qubit_pairs"]["q1-2"]["macros"]["cz_src"]
    m["flux_pulse_qubit"] = "#../../../../qubits/q3/z/operations/shared"
    res = _plan(s, copy_from="cz_src", pulses=None)
    assert "error" not in res, res
    op = dict(res["creates"])["qubits.q1.z.operations.cz_new_pulse_q1_q2"]
    assert op["__class__"] == SQUARE and op["amplitude"] == 0.15
    assert "shared" not in json.dumps(res["creates"])


@pytest.mark.parametrize("held", [None, [], 5, {}, "#/qubits/q9/z/operations/gone",
                                  "#../../../../../../../above_root", "no_such_op"])
def test_copy_from_a_source_without_a_usable_pulse_names_the_cause(held):
    s = _chip()
    _op_owned_ref(s, "q1-2", "cz_src")
    s["qubit_pairs"]["q1-2"]["macros"]["cz_src"]["flux_pulse_qubit"] = held
    res = _plan(s, copy_from="cz_src", pulses=None)
    err = " ".join(res["refusals"])
    assert "cz_src" in err and "flux_pulse_qubit" in err
    assert "start from another gate" in err
    assert "coupler-only" not in err


def test_copy_from_a_source_without_a_usable_optional_pulse_leaves_it_empty():
    s = _chip()
    _op_owned_ref(s, "q1-2", "cz_src")
    s["qubit_pairs"]["q1-2"]["macros"]["cz_src"]["coupler_flux_pulse"] = "#/nowhere"
    res = _plan(s, copy_from="cz_src", pulses=None)
    assert "error" not in res, res
    assert "coupler_flux_pulse" not in res["operations"]


def test_a_dangling_plain_name_does_not_count_as_name_form():
    s = _dir_fixture(2)
    for m in s["qubit_pairs"]["q1-2"]["macros"].values():
        m["coupler_flux_pulse"] = "dangling_name"
    res = _plan(s, pulses=_both())
    macro = dict(res["creates"])["qubit_pairs.q1-2.macros.cz_new"]
    assert macro["flux_pulse_qubit"] == "cz_new_pulse_q1_q2"
    assert macro["coupler_flux_pulse"].startswith("#/qubit_pairs/q1-2/coupler/operations/")


def _copy_fail_chip(path):
    s = _chip()
    _op_owned_ref(s, "q1-2", "cz_src")
    macros = s["qubit_pairs"]["q1-2"]["macros"]
    if path == "alias":
        macros["cz"] = "#./cz_gone"
        return s, "cz"
    if path == "not_a_gate":
        macros["cz_num"] = 5
        return s, "cz_num"
    if path == "other_class":
        m = cgb._coupler_cz_skeleton()
        m["coupler_flux_pulse"] = macros["cz_src"]["coupler_flux_pulse"]
        macros["cz_cpl"] = m
        return s, "cz_cpl"
    return s, "cz_missing"


@pytest.mark.parametrize("path,expected", [
    ("missing", ["Pair q1-2 has no gate cz_missing to copy values from. "
                 "Pick a gate of this pair, or leave copying off."]),
    ("not_a_gate", ["Pair q1-2 has no gate cz_num to copy values from. "
                    "Pick a gate of this pair, or leave copying off."]),
    ("alias", ["cz on pair q1-2 is an alias (#./cz_gone) that SM could not follow. "
               "Name the gate itself to copy values from."]),
    ("other_class", ["The gate cz_cpl holds no pulse in flux_pulse_qubit to copy. "
                     "Pick a pulse class for flux_pulse_qubit, or start from another gate."]),
])
def test_copy_from_failures_carry_only_refusals_that_fit(path, expected):
    s, src = _copy_fail_chip(path)
    res = _plan(s, copy_from=src, pulses=None)
    assert res["refusals"] == expected
    assert "coupler-only" not in json.dumps(res)


@pytest.mark.parametrize("ref", ["#./", "#../cz_src", "#/qubit_pairs/q1-2/macros/cz_src",
                                 "#/qubits/q1/z", "#/qubits/q1"])
def test_a_reference_to_a_gate_or_channel_is_not_a_pulse(ref):
    s = _chip()
    _op_owned_ref(s, "q1-2", "cz_src")
    s["qubit_pairs"]["q1-2"]["macros"]["cz_src"]["flux_pulse_qubit"] = ref
    s["qubits"]["q1"]["__class__"] = "lab_pkg.Transmon"
    s["qubits"]["q1"]["z"]["__class__"] = "lab_pkg.FluxLine"
    res = _plan(s, copy_from="cz_src", pulses=None)
    assert res["refusals"] == [
        f"The gate cz_src holds the reference {ref} in flux_pulse_qubit, which does "
        "not lead to a pulse. Pick a pulse class for flux_pulse_qubit, or start "
        "from another gate."]


def test_an_unknown_pulse_class_in_an_operations_entry_keeps_its_refusal():
    s = _chip()
    _op_owned_ref(s, "q1-2", "cz_src")
    s["qubits"]["q1"]["z"]["operations"]["cz_src_flux_pulse_q1_q2"]["__class__"] = LAB_PULSE
    res = _plan(s, copy_from="cz_src", pulses=None)
    assert f"does not know the fields of the pulse class {LAB_PULSE}" in res["error"]


def _list_chip(ref):
    s = _chip()
    _op_owned_ref(s, "q1-2", "cz_src")
    ops = s["qubits"]["q1"]["z"]["operations"]
    ops["lst"] = [ops.pop("cz_src_flux_pulse_q1_q2")]
    m = s["qubit_pairs"]["q1-2"]["macros"]["cz_src"]
    m["pulse_lst"] = [copy.deepcopy(ops["lst"][0])]
    m["flux_pulse_qubit"] = ref
    return s


@pytest.mark.parametrize("ref,ok", [
    ("#/qubits/q1/z/operations/lst/0", True), ("#./pulse_lst/0", True),
    ("#/qubits/q1/z/operations/lst/00", False), ("#/qubits/q1/z/operations/lst/-1", False),
    ("#/qubits/q1/z/operations/lst/ 0", False), ("#/qubits/q1/z/operations/lst/0x", False),
    ("#./pulse_lst/-1", False), ("#./pulse_lst/1", False)])
def test_list_indices_in_references_are_plain_decimals(ref, ok):
    res = _plan(_list_chip(ref), copy_from="cz_src", pulses=None)
    if ok:
        assert "error" not in res, res
        op = dict(res["creates"])["qubits.q1.z.operations.cz_new_pulse_q1_q2"]
        assert op["amplitude"] == 0.15
    else:
        assert "does not lead to a pulse" in res["error"]


@pytest.mark.parametrize("held", [None, "#/elsewhere"])
def test_a_name_whose_operation_is_not_a_dict_is_written_as_a_reference(held):
    s = _dir_fixture(2)
    for m in s["qubit_pairs"]["q1-2"]["macros"].values():
        m["coupler_flux_pulse"] = "opname"
    s["qubit_pairs"]["q1-2"]["coupler"]["operations"]["opname"] = held
    res = _plan(s, pulses=_both())
    macro = dict(res["creates"])["qubit_pairs.q1-2.macros.cz_new"]
    assert macro["coupler_flux_pulse"].startswith("#/qubit_pairs/q1-2/coupler/operations/")
    assert macro["flux_pulse_qubit"] == "cz_new_pulse_q1_q2"


def test_qubits_without_flux_lines_point_at_the_coupler_only_class():
    s = _chip()
    for q in s["qubits"].values():
        del q["z"]
    for moving in (None, "control"):
        s["qubit_pairs"]["q1-2"]["moving_qubit"] = moving
        err = _plan(s)["error"]
        assert "have no flux line" in err and "coupler-only gate class" in err
        assert "moving_qubit" not in err


# ---------------------------------------------------------------- gate settings (macro_fields)

def _lab_with_settings():
    s = _lab_chip()
    m = s["qubit_pairs"]["q1-2"]["macros"]["cz_tf"]
    m["duration_ns"] = 40
    m["link"] = "#./flux_pulse_control"
    return s


def test_macro_fields_change_a_copied_setting_and_report_given():
    s = _lab_with_settings()
    res = cgb.plan(s, "q2-3", gate_name="cz_new", gate_class=LAB_GATE,
                   macro_fields={"mode": "slow", "duration_ns": 48})
    macro = dict(res["creates"])["qubit_pairs.q2-3.macros.cz_new"]
    assert macro["mode"] == "slow" and macro["duration_ns"] == 48
    assert res["fields"]["macro"]["mode"] == cgb.GIVEN
    assert res["fields"]["macro"]["duration_ns"] == cgb.GIVEN


@pytest.mark.parametrize("key,word", [
    ("flux_pulse_control", "pulse"), ("phase_shift_control", "not copied"),
    ("unknown_key", "no setting named unknown_key"), ("link", "reference"),
    ("__class__", "no setting")])
def test_macro_fields_refuse_naming_the_key(key, word):
    res = cgb.plan(_lab_with_settings(), "q2-3", gate_name="cz_new", gate_class=LAB_GATE,
                   macro_fields={key: 1})
    assert "error" in res and any(key in r and word in r for r in res["refusals"]), res


def test_macro_fields_on_a_known_class_are_refused():
    res = cgb.plan(_chip(), "q1-2", gate_name="cz_new", gate_class=cgb.CZ_GATE,
                   pulses=_square(), macro_fields={"mode": "x"})
    assert "error" in res and any("no gate settings" in r for r in res["refusals"])


def test_a_whole_number_for_a_float_field_stays_a_float():
    s = _lab_chip()
    res = cgb.plan(s, "q2-3", gate_name="cz_new", gate_class=LAB_GATE,
                   pulses={"flux_pulse_control": {"fields": {"amplitude": 1, "length": 48}}})
    pulse = dict(res["creates"])["qubit_pairs.q2-3.macros.cz_new"]["flux_pulse_control"]
    assert pulse["amplitude"] == 1.0 and isinstance(pulse["amplitude"], float)
    assert pulse["length"] == 48 and isinstance(pulse["length"], int)
    s = _lab_with_settings()
    s["qubit_pairs"]["q1-2"]["macros"]["cz_tf"]["neg_offset_v"] = 0.0
    res = cgb.plan(s, "q2-3", gate_name="cz_new", gate_class=LAB_GATE, macro_fields={"neg_offset_v": 0.01})
    assert dict(res["creates"])["qubit_pairs.q2-3.macros.cz_new"]["neg_offset_v"] == 0.01
    res = cgb.plan(s, "q2-3", gate_name="cz_new", gate_class=LAB_GATE, macro_fields={"neg_offset_v": 2})
    assert isinstance(dict(res["creates"])["qubit_pairs.q2-3.macros.cz_new"]["neg_offset_v"], float)



def test_refusals_cap_every_echoed_long_name():
    long_cls = "lab_pkg." + "X" * 120
    res = cgb.plan(_chip(), "q1-2", gate_name="cz_new", gate_class=long_cls)
    assert "error" in res
    assert all(long_cls not in r for r in res["refusals"])
    assert any(cgb.cap_name(long_cls) in r for r in res["refusals"]) and len(cgb.cap_name(long_cls)) == cgb.ECHO_CAP


# --- a class described by the caller ------------------------------------

DESCRIBED_GATE = "lab_pkg.TwoLineGate.TwoLineGate"


def _described():
    def skeleton():
        return {"control_flux_pulse": None, "coupler_flux_pulse": None,
                "duration": None, "__class__": DESCRIBED_GATE}
    return {"skeleton": skeleton,
            "slots": (cgb.Slot("control_flux_pulse", "control_pulse", "control", True),
                      cgb.Slot("coupler_flux_pulse", "coupler_pulse", "coupler", True))}


def _described_pulses():
    return {f: {"class": SQUARE, "fields": {"length": 48, "amplitude": 0.3}}
            for f in ("control_flux_pulse", "coupler_flux_pulse")}


def _plan_described(state, pid="q1-2", **kw):
    kw.setdefault("gate_desc", _described())
    kw.setdefault("pulses", _described_pulses())
    return cgb.plan(state, pid, gate_name="cz_new", gate_class=DESCRIBED_GATE, **kw)


def _no_desc_cases():
    lab = _lab_chip()
    return [
        (_chip(), "q1-2", dict(gate_name="cz_new", gate_class=cgb.CZ_GATE, pulses=_square())),
        (_chip(), "q1-2", dict(gate_name="cz_new", gate_class=cgb.COUPLER_CZ_GATE,
                               pulses={"coupler_flux_pulse": _square()["flux_pulse_qubit"]})),
        (_chip(), "q1-2", dict(gate_name="cz_new", gate_class=cgb.CZ_GATE)),
        (_chip(), "q1-2", dict(gate_name="bad", gate_class=cgb.CZ_GATE, pulses=_square())),
        (_chip(), "q1-2", dict(gate_name="cz_new", gate_class=cgb.CZ_GATE, pulses=_square(),
                               macro_fields={"mode": "x"})),
        (_chip(), "q1-2", dict(gate_name="cz_new", gate_class=cgb.CZ_GATE, pulses=_square(),
                               spectators=[{"qubit": "q3", "length": 48, "level": 0.05}])),
        (lab, "q2-3", dict(gate_name="cz_new", gate_class=LAB_GATE)),
        (_chip(), "q1-2", dict(gate_name="cz_new", gate_class=LAB_GATE)),
        (_chip(), "q1-2", dict(gate_name="cz_new", gate_class=DESCRIBED_GATE,
                               pulses=_described_pulses())),
    ]


def _dump(res):
    return json.dumps(res, sort_keys=True, default=repr)


def test_no_descriptor_answers_exactly_as_a_call_without_the_keyword():
    kinds = set()
    for state, pid, kw in _no_desc_cases():
        plain = cgb.plan(copy.deepcopy(state), pid, **kw)
        kinds.add("error" in plain)
        assert _dump(cgb.plan(copy.deepcopy(state), pid, gate_desc=None, **kw)) == _dump(plain)
        cls = kw["gate_class"]
        assert (cgb.class_problem(state, pid, cls, gate_desc=None)
                == cgb.class_problem(state, pid, cls))
    assert kinds == {True, False}


def test_a_descriptor_for_a_table_class_is_ignored():
    for state, pid, kw in _no_desc_cases():
        if kw["gate_class"] not in cgb.GATE_CLASSES:
            continue
        plain = cgb.plan(copy.deepcopy(state), pid, **kw)
        assert _dump(cgb.plan(copy.deepcopy(state), pid, gate_desc=_described(), **kw)) == _dump(plain)
    assert cgb._desc(cgb.CZ_GATE, _described()) is cgb.GATE_CLASSES[cgb.CZ_GATE]
    # the table class asks for no pulse on the coupler-less pair's control line
    # only; the handed-in descriptor would require the coupler
    s = _chip()
    s["qubit_pairs"]["q1-2"] = _pair("q1", "q2", coupler=False)
    assert cgb.class_problem(s, "q1-2", cgb.CZ_GATE, gate_desc=_described()) is None
    assert "no coupler" in cgb.class_problem(s, "q1-2", DESCRIBED_GATE, gate_desc=_described())


def test_a_described_class_is_built_like_a_table_class():
    s = _chip()
    res = _plan_described(s)
    assert "error" not in res, res
    creates = dict(res["creates"])
    macro = creates["qubit_pairs.q1-2.macros.cz_new"]
    assert macro["__class__"] == DESCRIBED_GATE and macro["duration"] is None
    assert set(macro) == {"control_flux_pulse", "coupler_flux_pulse", "duration", "__class__"}
    assert macro["control_flux_pulse"]["id"] == "cz_new_control_pulse_q1_q2"
    assert macro["coupler_flux_pulse"]["id"] == "cz_new_coupler_pulse_q1_q2"
    assert set(creates) == {
        "qubit_pairs.q1-2.macros.cz_new",
        "qubits.q1.z.operations.cz_new_control_pulse_q1_q2",
        "qubit_pairs.q1-2.coupler.operations.cz_new_coupler_pulse_q1_q2"}
    assert res.get("layout") != "copied" and not res.get("model_gate")
    # the same request without the descriptor has no model gate to copy
    bare = _plan_described(s, gate_desc=None)
    assert "error" in bare


def test_a_described_class_takes_the_table_class_refusals():
    res = _plan_described(_chip(), macro_fields={"mode": "x"})
    assert any("no gate settings" in r for r in res["refusals"])
    res = _plan_described(_chip(), pulses={"control_flux_pulse": _described_pulses()["control_flux_pulse"]})
    assert any("needs a pulse for coupler_flux_pulse" in r for r in res["refusals"])
    res = _plan_described(_chip(), pulses={**_described_pulses(), "flux_pulse_qubit": _square()["flux_pulse_qubit"]})
    assert any("no pulse field flux_pulse_qubit" in r for r in res["refusals"])
    s = _chip()
    s["qubit_pairs"]["q1-2"]["qubit_target"] = "#/qubits/q9"
    res = _plan_described(s)
    assert any("uses both qubits of the pair" in r for r in res["refusals"])
    s = _chip()
    s["qubit_pairs"]["q1-2"]["qubit_control"] = None
    res = _plan_described(s)
    assert any("so SM cannot name the gate's pulses" in r for r in res["refusals"])


def test_class_problem_reads_the_descriptor_only_for_an_unlisted_class():
    s = _chip()
    assert cgb.class_problem(s, "q1-2", DESCRIBED_GATE, gate_desc=_described()) is None
    s["qubits"]["q1"]["z"] = {}
    why = cgb.class_problem(s, "q1-2", DESCRIBED_GATE, gate_desc=_described())
    assert why and "q1.z has no operations" in why
    assert cgb.class_problem(s, "q1-2", DESCRIBED_GATE) is None
    assert _plan_described(s)["refusals"] == [why]


# --- answers without a descriptor, frozen from the module before the keyword ---

def _coupler_only():
    return {"coupler_flux_pulse": _square()["flux_pulse_qubit"]}


def _frozen_plan_cases():
    """label -> (chip, pair, plan keywords); no call passes a descriptor."""
    spect = [{"qubit": "q3", "length": 48, "level": 0.05}]
    both = {**_square(), **_coupler_only()}
    rev = _chip()
    _op_owned(rev, "q1-2", "cz_rev")
    own = _chip()
    _macro_owned(own, "q1-2", "cz_old")
    bare = _chip()
    bare["qubit_pairs"]["q1-2"] = _pair("q1", "q2", coupler=False)
    no_z = _chip()
    no_z["qubits"]["q1"]["z"] = {}
    cz, cc = cgb.CZ_GATE, cgb.COUPLER_CZ_GATE
    return {
        "cz": (_chip(), "q1-2", dict(gate_name="cz_new", gate_class=cz, pulses=_square())),
        "cz both pulses": (_chip(), "q1-2", dict(gate_name="cz_new", gate_class=cz, pulses=both)),
        "cz operation owns": (rev, "q1-2", dict(gate_name="cz_new", gate_class=cz, pulses=_square())),
        "cz copied values": (own, "q1-2", dict(gate_name="cz_new", gate_class=cz, pulses=None,
                                              copy_from="cz_old")),
        "cz spectator": (_chip(), "q1-2", dict(gate_name="cz_new", gate_class=cz, pulses=_square(),
                                              spectators=spect)),
        "cz no pulse": (_chip(), "q1-2", dict(gate_name="cz_new", gate_class=cz)),
        "cz bad name": (_chip(), "q1-2", dict(gate_name="bad", gate_class=cz, pulses=_square())),
        "cz settings": (_chip(), "q1-2", dict(gate_name="cz_new", gate_class=cz, pulses=_square(),
                                             macro_fields={"mode": "x"})),
        "cz no coupler": (bare, "q1-2", dict(gate_name="cz_new", gate_class=cz, pulses=both)),
        "cz no flux line": (no_z, "q1-2", dict(gate_name="cz_new", gate_class=cz, pulses=_square())),
        "cz unknown field": (_chip(), "q1-2", dict(gate_name="cz_new", gate_class=cz,
                                                  pulses={**_square(), "control_flux_pulse":
                                                          _square()["flux_pulse_qubit"]})),
        "coupler": (_chip(), "q1-2", dict(gate_name="cz_new", gate_class=cc, pulses=_coupler_only())),
        "coupler operation owns": (rev, "q1-2", dict(gate_name="cz_new", gate_class=cc,
                                                    pulses=_coupler_only())),
        "coupler no pulse": (_chip(), "q1-2", dict(gate_name="cz_new", gate_class=cc)),
        "coupler no coupler": (bare, "q1-2", dict(gate_name="cz_new", gate_class=cc,
                                                 pulses=_coupler_only())),
        "coupler spectator": (_chip(), "q1-2", dict(gate_name="cz_new", gate_class=cc,
                                                   pulses=_coupler_only(), spectators=spect)),
        "coupler settings": (_chip(), "q1-2", dict(gate_name="cz_new", gate_class=cc,
                                                  pulses=_coupler_only(), macro_fields={"mode": "x"})),
        "copied": (_lab_chip(), "q2-3", dict(gate_name="cz_new", gate_class=LAB_GATE)),
        "copied no model": (_chip(), "q1-2", dict(gate_name="cz_new", gate_class=LAB_GATE)),
        "copied spectator": (_lab_chip(), "q2-3", dict(gate_name="cz_new", gate_class=LAB_GATE,
                                                      spectators=spect)),
        "unlisted class": (_chip(), "q1-2", dict(gate_name="cz_new", gate_class=DESCRIBED_GATE,
                                                pulses=_described_pulses())),
    }


def _frozen_class_cases():
    """label -> (chip, pair, gate class) for ``class_problem``."""
    bare = _chip()
    bare["qubit_pairs"]["q1-2"] = _pair("q1", "q2", coupler=False)
    no_z = _chip()
    no_z["qubits"]["q1"]["z"] = {}
    moved = _chip()
    moved["qubit_pairs"]["q1-2"] = _pair("q1", "q2", moving="target")
    moved["qubits"]["q2"]["z"] = {}
    out = {}
    for chip_name, chip in (("plain", _chip()), ("no coupler", bare), ("no flux line", no_z),
                            ("target moves", moved)):
        for cls_name, cls in (("cz", cgb.CZ_GATE), ("coupler", cgb.COUPLER_CZ_GATE),
                              ("copied", LAB_GATE), ("unlisted", DESCRIBED_GATE)):
            out[f"{cls_name}, {chip_name}"] = (chip, "q1-2", cls)
    out["cz, no such pair"] = (_chip(), "q8-9", cgb.CZ_GATE)
    return out


def _digest(value):
    return hashlib.sha256(_dump(value).encode("utf-8")).hexdigest()[:16]


def _frozen_answers():
    plans = {k: _digest(cgb.plan(copy.deepcopy(s), pid, **kw))
             for k, (s, pid, kw) in _frozen_plan_cases().items()}
    problems = {k: cgb.class_problem(copy.deepcopy(s), pid, cls)
                for k, (s, pid, cls) in _frozen_class_cases().items()}
    return plans, problems


# Taken once by running _frozen_answers() against the module as it was before
# plan() and class_problem() gained the descriptor keyword.
FROZEN_PLANS = {
    "cz": "e17476cc45ca340d",
    "cz both pulses": "79cc350da5c81859",
    "cz operation owns": "9415da0c7180ea06",
    "cz copied values": "1559bdff4714aba6",
    "cz spectator": "f2d31d8e76039f49",
    "cz no pulse": "62c39c7216e72e61",
    "cz bad name": "14218b4aeba99fd0",
    "cz settings": "298442bf758aa231",
    "cz no coupler": "adf9d687c8d8d796",
    "cz no flux line": "514a6697f0b8effd",
    "cz unknown field": "c13abe543252f5ee",
    "coupler": "4ab696a35cbab410",
    "coupler operation owns": "524503da4dcc1574",
    "coupler no pulse": "02872c5ee697ef3a",
    "coupler no coupler": "adf9d687c8d8d796",
    "coupler spectator": "2c0eb8e99a046dfc",
    "coupler settings": "298442bf758aa231",
    "copied": "d9e17eabd3467787",
    "copied no model": "2e3c0fb3803c1316",
    "copied spectator": "271ad18786fe78fb",
    "unlisted class": "84e7a6374b4fc00d",
}
FROZEN_PROBLEMS = {
    "cz, plain": None,
    "coupler, plain": None,
    "copied, plain": None,
    "unlisted, plain": None,
    "cz, no coupler": None,
    "coupler, no coupler": (
        "Pair q1-2 has no coupler that can hold operations. Remove the coupler pulse, or pick "
        "a gate class that does not play on a coupler."),
    "copied, no coupler": None,
    "unlisted, no coupler": None,
    "cz, no flux line": (
        "q1.z has no operations to hold the pulse (a DC-biased flux line cannot play pulses). "
        "Pick a gate that does not play on q1.z, or give q1 a pulsed flux line."),
    "coupler, no flux line": None,
    "copied, no flux line": None,
    "unlisted, no flux line": None,
    "cz, target moves": (
        "q2.z has no operations to hold the pulse (a DC-biased flux line cannot play pulses). "
        "Pick a gate that does not play on q2.z, or give q2 a pulsed flux line."),
    "coupler, target moves": None,
    "copied, target moves": None,
    "unlisted, target moves": None,
    "cz, no such pair": None,
}


def test_answers_without_a_descriptor_are_the_frozen_ones():
    plans, problems = _frozen_answers()
    assert plans == FROZEN_PLANS
    assert problems == FROZEN_PROBLEMS
    # the frozen list reaches a built gate and a refusal of every table class
    cases = _frozen_plan_cases()
    for cls in cgb.GATE_CLASSES:
        kinds = {"error" in cgb.plan(copy.deepcopy(s), pid, **kw)
                 for s, pid, kw in cases.values() if kw["gate_class"] == cls}
        assert kinds == {True, False}, cls
    assert {v is None for v in problems.values()} == {True, False}


# --- a descriptor SM cannot build a gate from ----------------------------

def _bad_descriptors():
    good = _described()
    sk, slots = good["skeleton"], good["slots"]
    return {
        "empty": {},
        "a string": "x",
        "a list": [sk, slots],
        "no skeleton": {"slots": slots},
        "no slots": {"skeleton": sk},
        "skeleton None": {"skeleton": None, "slots": slots},
        "skeleton a dict": {"skeleton": sk(), "slots": slots},
        "slots a generator": {"skeleton": sk, "slots": (s for s in slots)},
        "slots empty": {"skeleton": sk, "slots": ()},
        "slots a dict": {"skeleton": sk, "slots": {s.field: s for s in slots}},
        "rows not slots": {"skeleton": sk, "slots": tuple(
            (s.field, s.role, s.line, s.required) for s in slots)},
        "unknown line": {"skeleton": sk, "slots": (
            cgb.Slot("control_flux_pulse", "control_pulse", "readout", True),)},
        "unknown role": {"skeleton": sk, "slots": (
            cgb.Slot("control_flux_pulse", "main_pulse", "control", True),)},
        "nameless field": {"skeleton": sk, "slots": (
            cgb.Slot("", "control_pulse", "control", True),)},
        "field not a string": {"skeleton": sk, "slots": (
            cgb.Slot(5, "control_pulse", "control", True),)},
        "field named __class__": {"skeleton": sk, "slots": (
            cgb.Slot("__class__", "control_pulse", "control", True),)},
        "required not a boolean": {"skeleton": sk, "slots": (
            slots[0], cgb.Slot("coupler_flux_pulse", "coupler_pulse", "coupler", "no"))},
        # each repeat alone: the other two columns of the two rows differ
        "same field twice": {"skeleton": sk, "slots": (
            slots[0], cgb.Slot("control_flux_pulse", "coupler_pulse", "coupler", True))},
        "same role twice": {"skeleton": sk, "slots": (
            slots[0], cgb.Slot("coupler_flux_pulse", "control_pulse", "coupler", True))},
        "same line twice": {"skeleton": sk, "slots": (
            slots[0], cgb.Slot("coupler_flux_pulse", "coupler_pulse", "control", True))},
        "same role and line twice": {"skeleton": sk, "slots": (
            slots[0], cgb.Slot("coupler_flux_pulse", "control_pulse", "control", True))},
        "same row twice": {"skeleton": sk, "slots": (slots[0], slots[0])},
        "nothing required": {"skeleton": sk, "slots": tuple(
            cgb.Slot(s.field, s.role, s.line, False) for s in slots)},
    }


def test_a_bad_descriptor_gets_one_sentence_from_the_plan_and_the_class_check():
    assert cgb.BAD_DESCRIPTION == (
        "SM's own description of this gate class is not one it can build a gate "
        "from, so it plans no gate of this class. Nothing was changed. Report this "
        "with the gate class.")
    for label, bad in _bad_descriptors().items():
        res = _plan_described(_chip(), gate_desc=bad)
        assert res == {"error": cgb.BAD_DESCRIPTION,
                       "refusals": [cgb.BAD_DESCRIPTION]}, label
    # without any pulse too: never a gate that holds no pulse
    for label, bad in _bad_descriptors().items():
        res = _plan_described(_chip(), gate_desc=bad, pulses=None)
        assert res.get("refusals") == [cgb.BAD_DESCRIPTION] and "creates" not in res, label
    for label, bad in _bad_descriptors().items():
        assert cgb.class_problem(_chip(), "q1-2", DESCRIBED_GATE,
                                 gate_desc=bad) == cgb.BAD_DESCRIPTION, label
    for label, bad in _bad_descriptors().items():
        assert cgb.desc_problem(DESCRIBED_GATE, bad) == cgb.BAD_DESCRIPTION, label
    # a call that is wrong twice hears about the descriptor first
    spect = [{"qubit": "q3", "length": 48, "level": 0.05}]
    for label, bad in _bad_descriptors().items():
        res = _plan_described(_chip(), gate_desc=bad, spectators=spect)
        assert res.get("refusals") == [cgb.BAD_DESCRIPTION], label


def test_a_repeated_role_or_line_never_reaches_the_plan():
    # the two rows would be written as one operation; the plan must not answer
    # with a sentence about a model gate, which a described class does not have
    sk = _described()["skeleton"]
    for second in (cgb.Slot("b", "control_pulse", "control", True),
                   cgb.Slot("b", "control_pulse", "target", True),
                   cgb.Slot("b", "target_pulse", "control", True)):
        bad = {"skeleton": lambda: {"a": None, "b": None, "__class__": DESCRIBED_GATE},
               "slots": (cgb.Slot("a", "control_pulse", "control", True), second)}
        pulses = {f: _described_pulses()["control_flux_pulse"] for f in ("a", "b")}
        res = _plan_described(_chip(), gate_desc=bad, pulses=pulses)
        assert res == {"error": cgb.BAD_DESCRIPTION,
                       "refusals": [cgb.BAD_DESCRIPTION]}, second
        assert "model gate" not in res["error"]
        assert cgb.class_problem(_chip(), "q1-2", DESCRIBED_GATE,
                                 gate_desc=bad) == cgb.BAD_DESCRIPTION, second
    assert sk()["__class__"] == DESCRIBED_GATE


def _bad_skeletons():
    full = {"control_flux_pulse": None, "coupler_flux_pulse": None, "duration": None}
    return {
        "a list": [],
        "a list of pairs": [("__class__", DESCRIBED_GATE)],
        "a string": DESCRIBED_GATE,
        "None": None,
        "empty": {},
        "empty class": {"__class__": ""},
        "class a number": {"__class__": 5},
        "fields but no class": dict(full),
        "fields but an empty class": {**full, "__class__": ""},
        "fields but a class that is a number": {**full, "__class__": 5},
        "fields but a class that is None": {**full, "__class__": None},
        "fields but a class of blanks": {**full, "__class__": " "},
        "fields but another unlisted class": {**full, "__class__": "lab_pkg.Other.Other"},
        "fields but a table class": {**full, "__class__": cgb.CZ_GATE},
        "fields but the asked class with a blank": {**full, "__class__": DESCRIBED_GATE + " "},
        "a field missing": {"control_flux_pulse": None, "duration": None,
                            "__class__": DESCRIBED_GATE},
        "a field already holds a pulse": {
            **full, "__class__": DESCRIBED_GATE,
            "coupler_flux_pulse": {"id": "x", "length": 48, "amplitude": 0.1,
                                   "__class__": SQUARE}},
        "a field already holds a name": {**full, "__class__": DESCRIBED_GATE,
                                         "control_flux_pulse": "cz_old"},
        "a field already holds an empty dict": {**full, "__class__": DESCRIBED_GATE,
                                                "control_flux_pulse": {}},
    }


def test_what_the_skeleton_returns_is_checked_before_a_gate_is_planned():
    slots = _described()["slots"]
    for label, made in _bad_skeletons().items():
        bad = {"skeleton": lambda made=made: copy.deepcopy(made), "slots": slots}
        # the descriptor itself reads fine: only the plan calls the skeleton
        assert cgb.desc_problem(DESCRIBED_GATE, bad) is None, label
        res = _plan_described(_chip(), gate_desc=bad)
        assert res == {"error": cgb.BAD_DESCRIPTION,
                       "refusals": [cgb.BAD_DESCRIPTION]}, label
        # also when the request has other faults: no half answer about pulses
        res = _plan_described(_chip(), gate_desc=bad, pulses=None)
        assert res == {"error": cgb.BAD_DESCRIPTION,
                       "refusals": [cgb.BAD_DESCRIPTION]}, label
        # a table class never reads it
        res = cgb.plan(_chip(), "q1-2", gate_name="cz_new", gate_class=cgb.CZ_GATE,
                       pulses=_square(), gate_desc=bad)
        assert "creates" in res, label


def test_the_planned_gate_has_the_asked_class():
    for cls in (DESCRIBED_GATE,):
        desc = _described()
        made = {**desc["skeleton"](), "__class__": cls}
        desc["skeleton"] = lambda made=made: dict(made)
        res = _plan_described(_chip(), gate_desc=desc)
        assert "error" not in res, res
        macro = dict(res["creates"])["qubit_pairs.q1-2.macros.cz_new"]
        assert macro["__class__"] == cls == desc["skeleton"]()["__class__"]
        assert set(macro) == set(made)
        assert isinstance(macro["control_flux_pulse"], dict)
        assert "__class__" not in res["fields"]["macro"]


SLOT_NAMES = ("control", "target", "coupler")
SLOT_SUBSETS = [tuple(n for i, n in enumerate(SLOT_NAMES) if bits >> i & 1)
                for bits in range(1, 8)]


def _slot_class_desc(names, spectators):
    """A descriptor of the shape the slot class planner hands in: every slot
    required, field ``<slot>_flux_pulse``, role ``<slot>_pulse``, line the
    slot; the skeleton holds the slot fields empty and the class last."""
    qclass = "quam_config.MyGate.MyGate"

    def skeleton():
        macro = {f"{n}_flux_pulse": None for n in names}
        macro.update({"phase_shift_control": 0.0, "phase_shift_target": 0.0,
                      "duration": None, "extras": {}, "fidelity": {}})
        if spectators:
            for key in ("spectator_qubits", "spectator_qubits_control",
                        "spectator_qubits_phase_shift"):
                macro[key] = {}
        macro["__class__"] = qclass
        return macro

    return qclass, {"skeleton": skeleton,
                    "slots": tuple(cgb.Slot(f"{n}_flux_pulse", f"{n}_pulse", n, True)
                                   for n in names)}


@pytest.mark.parametrize("spectators", [False, True])
@pytest.mark.parametrize("names", SLOT_SUBSETS)
def test_every_slot_class_shape_is_accepted(names, spectators):
    assert len(SLOT_SUBSETS) == 7
    qclass, desc = _slot_class_desc(names, spectators)
    assert cgb.desc_problem(qclass, desc) is None
    assert cgb.class_problem(_chip(), "q1-2", qclass, gate_desc=desc) is None
    pulses = {s.field: {"class": SQUARE, "fields": {"length": 48, "amplitude": 0.3}}
              for s in desc["slots"]}
    res = cgb.plan(_chip(), "q1-2", gate_name="cz_new", gate_class=qclass,
                   pulses=pulses, gate_desc=desc)
    assert "error" not in res, res
    macro = dict(res["creates"])["qubit_pairs.q1-2.macros.cz_new"]
    assert macro["__class__"] == qclass and set(macro) == set(desc["skeleton"]())
    assert len(res["creates"]) == 1 + len(names)


def test_a_bad_descriptor_for_a_table_class_is_never_read():
    plain = cgb.plan(_chip(), "q1-2", gate_name="cz_new", gate_class=cgb.CZ_GATE,
                     pulses=_square())
    assert "creates" in plain
    for label, bad in _bad_descriptors().items():
        for cls in cgb.GATE_CLASSES:
            assert cgb.desc_problem(cls, bad) is None, label
            assert cgb.class_problem(_chip(), "q1-2", cls, gate_desc=bad) is None, label
    for label, bad in _bad_descriptors().items():
        assert _dump(cgb.plan(_chip(), "q1-2", gate_name="cz_new", gate_class=cgb.CZ_GATE,
                              pulses=_square(), gate_desc=bad)) == _dump(plain), label


def test_a_well_formed_descriptor_passes_the_check_as_a_tuple_or_a_list():
    good = _described()
    assert cgb.desc_problem(DESCRIBED_GATE, good) is None
    assert cgb.desc_problem(DESCRIBED_GATE, None) is None
    as_list = {**good, "slots": list(good["slots"])}
    assert cgb.desc_problem(DESCRIBED_GATE, as_list) is None
    assert _dump(_plan_described(_chip(), gate_desc=as_list)) == _dump(_plan_described(_chip()))
    assert "creates" in _plan_described(_chip(), gate_desc=as_list)
    assert set(cgb.SLOT_LINES) == set(cgb._lines(_chip(), "q1-2")) - {"_ids"}


# --- whole sentences: the missing pulse and the spectators ----------------

NEEDS = "This gate needs a pulse for {}. Pick a pulse class for it."
COUPLER_ONLY_ADVICE = (" A coupler pulse alone is not a gate of this class; for a "
                       "coupler-only gate pick the coupler-only gate class.")


def test_the_missing_pulse_sentence_is_whole_for_every_kind_of_class():
    # the class with an optional coupler pulse points at the coupler-only class
    res = cgb.plan(_chip(), "q1-2", gate_name="cz_new", gate_class=cgb.CZ_GATE)
    assert res["refusals"] == [NEEDS.format("flux_pulse_qubit") + COUPLER_ONLY_ADVICE]
    res = cgb.plan(_chip(), "q1-2", gate_name="cz_new", gate_class=cgb.CZ_GATE,
                   pulses=_coupler_only())
    assert res["refusals"] == [NEEDS.format("flux_pulse_qubit") + COUPLER_ONLY_ADVICE]
    # the coupler-only class itself gets no such advice
    res = cgb.plan(_chip(), "q1-2", gate_name="cz_new", gate_class=cgb.COUPLER_CZ_GATE)
    assert res["refusals"] == [NEEDS.format("coupler_flux_pulse")]
    # nor does a described class: its slots are its own, each named plainly
    one = _described_pulses()
    res = _plan_described(_chip(), pulses={"control_flux_pulse": one["control_flux_pulse"]})
    assert res["refusals"] == [NEEDS.format("coupler_flux_pulse")]
    res = _plan_described(_chip(), pulses={"coupler_flux_pulse": one["coupler_flux_pulse"]})
    assert res["refusals"] == [NEEDS.format("control_flux_pulse")]
    res = _plan_described(_chip(), pulses=None)
    assert res["refusals"] == [NEEDS.format("control_flux_pulse"),
                               NEEDS.format("coupler_flux_pulse")]


def test_spectators_for_a_described_class_are_refused_with_a_true_sentence():
    spect = [{"qubit": "q3", "length": 48, "level": 0.05}]
    assert cgb.NO_SPECTATORS_DESCRIBED == (
        "This step plans the gate's own pulses only. Leave the spectators out of "
        "this request.")
    # true for a class with spectator fields and for one without: no claim
    # about any other place that plans them
    assert "separately" not in cgb.NO_SPECTATORS_DESCRIBED
    for with_fields in (False, True):
        qclass, desc = _slot_class_desc(("coupler",), with_fields)
        res = cgb.plan(_chip(), "q1-2", gate_name="cz_new", gate_class=qclass,
                       pulses={"coupler_flux_pulse": _described_pulses()["coupler_flux_pulse"]},
                       spectators=spect, gate_desc=desc)
        assert res == {"error": cgb.NO_SPECTATORS_DESCRIBED,
                       "refusals": [cgb.NO_SPECTATORS_DESCRIBED]}
    res = _plan_described(_chip(), spectators=spect)
    assert res == {"error": cgb.NO_SPECTATORS_DESCRIBED,
                   "refusals": [cgb.NO_SPECTATORS_DESCRIBED]}
    assert "copies the layout" not in cgb.NO_SPECTATORS_DESCRIBED
    assert cgb.spectator_refusal(DESCRIBED_GATE, gate_desc=_described()) == cgb.NO_SPECTATORS_DESCRIBED
    # an empty list is no spectator: the gate is planned as without the keyword
    assert _dump(_plan_described(_chip(), spectators=[])) == _dump(_plan_described(_chip()))
    # the other classes keep their sentences, with or without a descriptor
    assert cgb.spectator_refusal(DESCRIBED_GATE) == cgb.NO_SPECTATORS_COPIED
    assert cgb.spectator_refusal(LAB_GATE) == cgb.NO_SPECTATORS_COPIED
    assert cgb.spectator_refusal(cgb.CZ_GATE, gate_desc=_described()) is None
    assert (cgb.spectator_refusal(cgb.COUPLER_CZ_GATE, gate_desc=_described())
            == cgb.NO_SPECTATORS_COUPLER)
    res = cgb.plan(_chip(), "q1-2", gate_name="cz_new", gate_class=DESCRIBED_GATE,
                   pulses=_described_pulses(), spectators=spect)
    assert res["refusals"] == [cgb.NO_SPECTATORS_COPIED]
    with_table = cgb.plan(_chip(), "q1-2", gate_name="cz_new", gate_class=cgb.CZ_GATE,
                          pulses=_square(), spectators=spect, gate_desc=_described())
    assert "creates" in with_table and with_table["spectators"]
