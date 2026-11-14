"""Spectator pulses of a new gate of the official class: request checks, the
writes, the plan answer, the dialog data, timeline lanes and intent rows, the
lab check's coverage, and delete / undo / hash."""
from __future__ import annotations

import copy
import json
import tempfile

import pytest

from quam_state_manager.core import cz_gate_build as cgb
from quam_state_manager.core import gate_check, gate_intent, gate_parts, gate_timeline_view
from quam_state_manager.web import cz_gate_routes as czr
from tests import cz_gate_chip as cc
from tests.cz_spectator_golden import GOLDEN, collect, dump
from tests.test_cz_gate_routes import App, CHECK, CREATE, PLAN, _fake

SQ = "quam.components.pulses.SquarePulse"
CZ = cgb.CZ_GATE
KEY = "cz_sp_spectator_pulse_q1_q2"


def _chip(n=4):
    s = {"qubits": {}, "qubit_pairs": {}}
    for i in range(1, n + 1):
        q = f"q{i}"
        s["qubits"][q] = {"id": q, "z": {"operations": {"const": {
            "amplitude": 0.1, "length": 100, "__class__": SQ}},
            "opx_output": f"#/ports/analog_outputs/con1/1/{i}"}}
    s["ports"] = {"analog_outputs": {"con1": {"1": {
        str(i): {"output_mode": "direct", "offset": 0.1 if i == 4 else 0.0}
        for i in range(1, n + 1)}}}}
    for i in range(1, n):
        s["qubit_pairs"][f"q{i}-q{i + 1}"] = cc._pair(f"q{i}", f"q{i + 1}")
    return s


def _plan(state, spectators, name="cz_sp", pair="q1-q2", cls=CZ, pulses=None):
    return cgb.plan(state, pair, gate_name=name, gate_class=cls,
                    pulses=cc.square() if pulses is None else pulses,
                    spectators=spectators)


TWO = [{"qubit": "q3", "length": 48, "level": 0.05},
       {"qubit": "q4", "length": 32, "level": -0.03}]


# 1. request checks ---------------------------------------------------------

@pytest.mark.parametrize("spec, words", [
    ({"qubit": "q9", "length": 48, "level": 0.1}, "The spectator q9 is not a qubit of this chip"),
    ({"qubit": "q1", "length": 48, "level": 0.1}, "The spectator q1 is the control of pair q1-q2"),
    ({"qubit": "q2", "length": 48, "level": 0.1}, "The spectator q2 is the target of pair q1-q2"),
    ({"qubit": "q3", "length": 50, "level": 0.1}, "For the spectator q3: the pulse length 50 ns"),
    ({"qubit": "q3", "length": 0, "level": 0.1}, "For the spectator q3: the pulse length 0 ns"),
    ({"qubit": "q3", "level": 0.1}, "For the spectator q3: the length is required"),
    ({"qubit": "q3", "length": 48}, "For the spectator q3: the level is required"),
    ({"qubit": "q3", "length": 48, "level": "x"}, "the level 'x' must be a number of V"),
    ({"qubit": "q3", "length": 48, "level": True}, "the level True must be a number of V"),
    ({"qubit": "q3", "length": 48, "level": 0}, "a level of 0 V plays nothing"),
    ({"qubit": "q3", "length": 48, "level": 0.6}, "outside its port's range of ±0.5 V"),
    ({"qubit": "q4", "length": 48, "level": 0.45}, "on top of its 0.1 V offset (0.55 V)"),
    ({"length": 48, "level": 0.1}, "Each spectator needs a qubit"),
])
def test_each_bad_spectator_is_refused_in_words(spec, words):
    res = _plan(_chip(), [spec])
    assert "error" in res and any(words in r for r in res["refusals"]), res


def test_a_spectator_listed_twice_and_one_without_a_pulsed_line():
    s = _chip()
    s["qubits"]["q4"]["z"] = None
    res = _plan(s, [TWO[0], TWO[0]])
    assert any("The spectator q3 is listed twice" in r for r in res["refusals"])
    res = _plan(s, [{"qubit": "q4", "length": 48, "level": 0.1}])
    assert any("The spectator q4 has no pulsed flux line" in r for r in res["refusals"])


def test_a_level_inside_the_range_with_its_offset_is_fine():
    assert "creates" in _plan(_chip(), [{"qubit": "q4", "length": 48, "level": 0.39}])


def test_spectators_that_are_not_a_list_are_refused():
    res = _plan(_chip(), {"qubit": "q3"})
    assert "must be a list" in res["error"]


@pytest.mark.parametrize("cls, why", [(cgb.COUPLER_CZ_GATE, cgb.NO_SPECTATORS_COUPLER),
                                      ("lab_pkg.TwoFluxGate", cgb.NO_SPECTATORS_COPIED)])
def test_classes_without_spectators_say_why(cls, why):
    res = _plan(_chip(), TWO, cls=cls, pulses={})
    assert res["refusals"] == [why]


# 2. writes -----------------------------------------------------------------

def test_operation_owned_writes_reference_the_spectator_operations():
    s = cc.reverse(_chip())
    res = _plan(s, TWO)
    by = dict(res["creates"])
    macro = by["qubit_pairs.q1-q2.macros.cz_sp"]
    assert macro["spectator_qubits"] == {"q3": "#/qubits/q3", "q4": "#/qubits/q4"}
    assert macro["spectator_qubits_control"] == {
        "q3": f"#/qubits/q3/z/operations/{KEY}", "q4": f"#/qubits/q4/z/operations/{KEY}"}
    assert macro["spectator_qubits_phase_shift"] == {"q3": 0.0, "q4": 0.0}
    op3 = by[f"qubits.q3.z.operations.{KEY}"]
    assert (op3["__class__"], op3["id"], op3["length"], op3["amplitude"]) == (SQ, KEY, 48, 0.05)
    op4 = by[f"qubits.q4.z.operations.{KEY}"]
    assert (op4["length"], op4["amplitude"]) == (32, -0.03)


def test_macro_owned_writes_link_the_operation_to_the_macro_pulse():
    res = _plan(_chip(), TWO)
    by = dict(res["creates"])
    owned = by["qubit_pairs.q1-q2.macros.cz_sp"]["spectator_qubits_control"]["q3"]
    assert (owned["id"], owned["length"], owned["amplitude"]) == (KEY, 48, 0.05)
    op = by[f"qubits.q3.z.operations.{KEY}"]
    ref = "#/qubit_pairs/q1-q2/macros/cz_sp/spectator_qubits_control/q3"
    assert op["amplitude"] == ref + "/amplitude" and op["length"] == ref + "/length"
    assert op["id"] is None


def test_a_name_collision_is_refused_before_anything_is_written():
    s = _chip()
    s["qubits"]["q3"]["z"]["operations"][KEY] = {"amplitude": 0.1, "length": 16, "__class__": SQ}
    res = _plan(s, TWO)
    assert "creates" not in res
    assert any(f"The operation {KEY} already exists on q3.z" in r for r in res["refusals"])


# 3. plan answer ------------------------------------------------------------

def test_the_plan_names_every_spectator_object_and_counts_per_qubit():
    s = _chip()
    s["qubits"]["q3"]["z"]["operations"]["cz_old_spectator_pulse_q1_q2"] = {
        "amplitude": 0.1, "length": 16, "__class__": SQ}
    res = _plan(s, TWO)
    paths = [p for p, _ in res["creates"]]
    assert f"qubits.q3.z.operations.{KEY}" in paths and f"qubits.q4.z.operations.{KEY}" in paths
    assert res["spectator_ops"] == {"q3": 2, "q4": 1}
    assert [r["not_calibrated"] for r in res["spectators"]] == [cgb.NOT_CALIBRATED] * 2
    assert [r["phase_shift"] for r in res["spectators"]] == [0.0, 0.0]


def test_no_spectators_adds_no_answer_keys():
    res = _plan(_chip(), None)
    assert "spectators" not in res and "spectator_ops" not in res
    assert _plan(_chip(), [])["creates"] == res["creates"]


def test_without_spectators_the_answers_and_state_equal_the_golden(monkeypatch):
    assert dump(collect(monkeypatch)) == GOLDEN.read_text(encoding="utf-8")


@pytest.fixture
def app(monkeypatch):
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        a = App(tmp, state=_chip())
        monkeypatch.setattr(czr, "run_check", _fake())
        monkeypatch.setattr(czr, "_env_python", lambda: "C:/fake/python.exe")
        yield a
        from quam_state_manager.core.hub_index import close_readers
        close_readers()


def _body(**kw):
    b = {"pairs": ["q1-q2"], "gate_name": "cz_sp", "gate_class": CZ,
         "pulses": cc.square(), "spectators": {"q1-q2": TWO}}
    b.update(kw)
    return b


def test_the_plan_route_answers_the_spectators(app):
    j = app.c.post(PLAN, json=_body()).get_json()
    row = j["pairs"][0]
    assert row["ok"] and row["spectator_ops"] == {"q3": 1, "q4": 1}
    assert [s["qubit"] for s in row["spectators"]] == ["q3", "q4"]
    assert f"qubits.q4.z.operations.{KEY}" in row["paths"]
    assert row["operations"][f"spectator_qubits_control.q3"]["group"] == "spectator"


def test_the_plan_route_takes_a_bare_list_for_one_pair_and_refuses_strays(app):
    j = app.c.post(PLAN, json=_body(spectators=TWO)).get_json()
    assert j["pairs"][0]["spectator_ops"] == {"q3": 1, "q4": 1}
    j = app.c.post(PLAN, json=_body(spectators={"q2-q3": TWO})).get_json()
    assert "Spectators were given for pair q2-q3" in j["pairs"][0]["refusals"][0]
    j = app.c.post(PLAN, json=_body(pairs=["q1-q2", "q3-q4"], spectators=TWO)).get_json()
    assert j["pairs"][0]["refusals"] == [czr.SPECTATORS_ONE_PAIR]


def test_a_second_pair_does_not_inherit_the_first_pair_s_spectators(app):
    body = _body(pairs=["q1-q2", "q3-q4"], spectators={"q1-q2": TWO[:1]})
    rows = app.c.post(PLAN, json=body).get_json()["pairs"]
    assert rows[0]["ok"] and rows[1]["ok"], rows
    assert "spectators" not in rows[1]


def test_create_writes_spectators_in_one_group_and_one_undo_restores(app):
    before = json.dumps(app.store.state)
    chk = app.c.post(CHECK, json=_body()).get_json()
    made = app.c.post(CREATE, json={"check_token": chk["check_token"],
                                    "plan_sig": chk["plan_sig"]}).get_json()
    assert made["ok"], made
    st = app.store.state
    assert st["qubits"]["q4"]["z"]["operations"][KEY]["length"] == f"#/qubit_pairs/q1-q2/macros/cz_sp/spectator_qubits_control/q4/length"
    assert {e.group_id for e in app.store.change_log} == {made["group"]}
    assert app.c.post("/undo").status_code == 200
    assert json.dumps(app.store.state) == before


# 4. dialog data ------------------------------------------------------------

def test_the_dialog_lists_candidates_and_which_classes_offer_spectators(app):
    with app.store._lock:
        pairs = czr._pairs(app.store.merged)
        classes = czr._classes(app.store, app.store.merged)
    p = next(x for x in pairs if x["id"] == "q1-q2")
    assert [c["qubit"] for c in p["spectator_candidates"]] == ["q3", "q4"]
    assert p["spectator_candidates"][0] == {"qubit": "q3", "line": "q3.z",
                                            "port": "#/ports/analog_outputs/con1/1/3",
                                            "range": 0.5}
    by = {c["class"]: c["spectators"] for c in classes}
    assert by[CZ] == {"offered": True, "why": ""}
    assert by[cgb.COUPLER_CZ_GATE] == {"offered": False, "why": cgb.NO_SPECTATORS_COUPLER}
    assert by["ParametricCZGate"]["why"] == cgb.NO_SPECTATORS_COPIED


# 5. timeline lanes and intent rows ----------------------------------------

def _row(el, op, start, end, level):
    return {"element": el, "port": ["con1", 1, 1], "operation": op, "start_ns": start,
            "end_ns": end, "level": level, "scale": 1.0, "samples": None, "constant": level,
            "total": end - start, "truncated": False, "sampling_rate": 1e9,
            "length_ns": end - start, "range_v": 0.5, "offset_v": 0.0, "port_kind": "lf"}


def _entry():
    return {"timeline": {"rows": [_row("q1.z", "cz_sp_pulse_q1_q2", 0, 48, 0.3),
                                  _row("q3.z", KEY, 0, 48, 0.05),
                                  _row("q4.z", KEY, 0, 32, -0.03)],
                         "notes": [], "cannot_draw": None}}


def test_each_spectator_is_its_own_lane_and_its_own_intent_row():
    plan = _plan(_chip(), TWO)
    tl = gate_timeline_view.timeline_data(_entry(), control="q1", target="q2")
    groups = {ln["key"]: ln["group"] for ln in tl["lanes"]}
    assert groups == {"q1.z": "gate", "q3.z": "spectator", "q4.z": "spectator"}
    rows = {r["field"]: r for r in gate_intent.intent_rows(plan, _entry())}
    s3, s4 = rows["spectator_qubits_control.q3"], rows["spectator_qubits_control.q4"]
    assert (s3["group"], s3["lane_key"], s4["lane_key"]) == ("spectator", "q3.z", "q4.z")
    assert s3["intended"]["length"] == 48 and s4["intended"]["amplitude"] == -0.03
    assert s4["actual"]["length_ns"] == 32 and not any(s4["differs"].values())
    assert rows["flux_pulse_qubit"]["group"] == "gate"


# 6. the lab check covers the spectators -------------------------------------

def _found(el, op, length, level):
    return {"element": el, "operation": op, "found": True, "length": length,
            "waveform": {"type": "constant", "sample": level}, "port_kind": "lf",
            "output_mode": "direct", "port": ["con1", 1, 1], "offset": 0.0}


def _ran(ops):
    g = {"ops": ops, "script": None}
    return {"status": "ok", "data": {"before": {}, "after": {},
                                     "gates": {"qubit_pairs.q1-q2.macros.cz_sp": g}}}


def test_the_previews_require_every_spectator():
    pv, _ = czr._previews(_plan(_chip(), TWO))
    assert pv[gate_check.REQUIRED] == [("q3.z", KEY), ("q4.z", KEY)]
    assert pv[("q3.z", KEY)] == [0.05] * 48 and pv[("q4.z", KEY)] == [-0.03] * 32


def test_a_spectator_the_lab_does_not_play_fails_with_its_name():
    pv, _ = czr._previews(_plan(_chip(), TWO))
    ops = [_found("q1.z", "cz_sp_pulse_q1_q2", 48, 0.3), _found("q3.z", KEY, 48, 0.05)]
    res = gate_check.evaluate([("q1-q2", "cz_sp")], _ran(ops), {("q1-q2", "cz_sp"): pv})
    msg = res["gates"][0]["checks"]["preview"]
    assert msg["status"] == "fail" and f"{KEY} on q4.z" in msg["message"]


def test_a_spectator_level_or_length_that_differs_fails_the_preview():
    pv, _ = czr._previews(_plan(_chip(), TWO))
    for bad in (_found("q4.z", KEY, 32, -0.02), _found("q4.z", KEY, 36, -0.03)):
        ops = [_found("q1.z", "cz_sp_pulse_q1_q2", 48, 0.3), _found("q3.z", KEY, 48, 0.05), bad]
        res = gate_check.evaluate([("q1-q2", "cz_sp")], _ran(ops), {("q1-q2", "cz_sp"): pv})
        assert res["gates"][0]["checks"]["preview"]["status"] == "fail"
    ops = [_found("q1.z", "cz_sp_pulse_q1_q2", 48, 0.3), _found("q3.z", KEY, 48, 0.05),
           _found("q4.z", KEY, 32, -0.03)]
    res = gate_check.evaluate([("q1-q2", "cz_sp")], _ran(ops), {("q1-q2", "cz_sp"): pv})
    assert res["gates"][0]["checks"]["preview"]["status"] == "pass"


# 7. delete, status hash ----------------------------------------------------

@pytest.mark.parametrize("reverse", [False, True])
def test_the_parts_cover_every_spectator_and_the_hash_follows_its_level(reverse):
    s0 = cc.reverse(_chip()) if reverse else _chip()
    s = cc.apply(s0, _plan(s0, TWO)["creates"])
    parts = gate_parts.gate_parts(s, "q1-q2", "cz_sp")
    assert parts["ok"], parts
    assert {f"qubits.q3.z.operations.{KEY}", f"qubits.q4.z.operations.{KEY}"} <= set(parts["operations"])
    h = gate_parts.content_hash(s, "q1-q2", "cz_sp")
    s2 = copy.deepcopy(s)
    if reverse:
        s2["qubits"]["q4"]["z"]["operations"][KEY]["amplitude"] = -0.04
    else:
        s2["qubit_pairs"]["q1-q2"]["macros"]["cz_sp"]["spectator_qubits_control"]["q4"]["amplitude"] = -0.04
    assert gate_parts.content_hash(s2, "q1-q2", "cz_sp") != h


def test_a_spectator_operation_another_gate_uses_is_not_removed():
    s0 = cc.reverse(_chip())
    s = cc.apply(s0, _plan(s0, TWO)["creates"])
    s["qubit_pairs"]["q2-q3"]["macros"]["cz_x"] = dict(
        cgb._cz_skeleton(), spectator_qubits_control={"q3": f"#/qubits/q3/z/operations/{KEY}"})
    parts = gate_parts.gate_parts(s, "q1-q2", "cz_sp")
    assert not parts["ok"]
    assert any(f"qubits.q3.z.operations.{KEY} is also used by" in r for r in parts["refusals"])


def test_delete_route_removes_spectators_in_one_group_and_undo_restores(app):
    before = json.dumps(app.store.state)
    chk = app.c.post(CHECK, json=_body()).get_json()
    app.c.post(CREATE, json={"check_token": chk["check_token"], "plan_sig": chk["plan_sig"]})
    made = json.dumps(app.store.state)
    r = app.c.post("/api/pulse/delete-together", json={"pair": "q1-q2", "gate": "cz_sp"})
    assert r.status_code == 200 and r.get_json()["ok"], r.data[:400]
    assert json.dumps(app.store.state) == before
    assert app.c.post("/undo").status_code == 200
    assert json.dumps(app.store.state) == made
