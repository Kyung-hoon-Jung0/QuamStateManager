"""The pulse length floor follows the chip's QOP version (docs Guides/timing_in_qua.md:14-16).

16 ns on a chip that declares a QOP before 3.3; 4 ns in the configuration (8 ns from QUA) on
QOP 3.3 or later and on a chip with no version, which also gets one note line.
"""
from __future__ import annotations

import copy

import pytest

from quam_state_manager.core import cz_gate_build as cgb
from quam_state_manager.core import gate_check, gate_timeline_view, slot_gate_template
from quam_state_manager.core import spec_constraints as sc
from tests import test_cz_gate_build as tb
from tests import test_cz_gate_spectators as ts
from tests.test_gate_check import GATE, SCRIPT, _cfg, _checks, _msg, _ran

OLD = {"extras": {"qop_version": "3.2.1"}}
NEW = {"extras": {"qop_version": "3.3.0"}}
NONE = {"extras": {}}
NOTE = sc.LENGTH_FLOOR_NOTE


def _with(chip, version_of):
    out = copy.deepcopy(chip)
    out["extras"] = copy.deepcopy(version_of["extras"])
    return out


# --- the shared helper ---------------------------------------------------

@pytest.mark.parametrize("state,config,qua,note", [
    (OLD, 16, 16, None), ({"extras": {"versions": {"qop": "3.2"}}}, 16, 16, None),
    (NEW, 4, 8, None), ({"extras": {"qop_version": "v3_8_1"}}, 4, 8, None),
    (NONE, 4, 8, NOTE), (None, 4, 8, NOTE), ({"extras": {"qop_version": "unknown"}}, 4, 8, NOTE)])
def test_floor_by_declared_version(state, config, qua, note):
    assert sc.length_floor_ns(state) == (config, note)
    assert sc.length_floor_ns(state, from_qua=True) == (qua, note)


def test_note_is_plain_words():
    assert NOTE.isascii() and " -- " not in NOTE and NOTE.endswith(".")


# --- the CZ gate builder ---------------------------------------------------

@pytest.mark.parametrize("length,floor,ok", [
    (4, 4, True), (8, 4, True), (12, 16, False), (8, 16, False), (4, 16, False),
    (16, 16, True), (6, 4, False), (6, 16, False), (0, 4, False), (0, 16, False)])
def test_length_rule_takes_the_floor(length, floor, ok):
    assert (cgb.length_rule({"length": length}, floor) is None) is ok


def test_length_rule_default_is_the_floor_every_qop_plays():
    assert cgb.length_rule({"length": 12}) == ("The pulse length 12 ns must be a multiple of 4 ns and "
                                               "at least 16 ns. Change the length.")
    assert cgb.length_rule({"length": 16}) is None


@pytest.mark.parametrize("version,length,ok", [
    (NEW, 4, True), (NONE, 4, True), (OLD, 4, False), (OLD, 8, False), (OLD, 12, False),
    (OLD, 16, True), (NEW, 6, False), (NONE, 6, False), (OLD, 6, False),
    (NEW, 0, False), (NONE, 0, False), (OLD, 0, False)])
def test_known_class_plan_uses_the_chip_floor(version, length, ok):
    res = tb._plan(_with(tb._chip(), version), pulses=tb._square(length=length))
    assert ("creates" in res) is ok, res.get("error")
    if ok:
        pulse = dict(res["creates"])["qubit_pairs.q1-2.macros.cz_new"]["flux_pulse_qubit"]
        assert pulse["length"] == length


def test_old_qop_refusal_text_is_unchanged():
    res = tb._plan(_with(tb._chip(), OLD), pulses=tb._square(length=12))
    assert ("flux_pulse_qubit.length: The pulse length 12 ns must be a multiple of 4 ns and at "
            "least 16 ns. Change the length.") in res["error"]


def test_new_qop_refusal_names_the_4_ns_floor():
    res = tb._plan(_with(tb._chip(), NEW), pulses=tb._square(length=0))
    assert "must be a multiple of 4 ns and at least 4 ns" in res["error"]


@pytest.mark.parametrize("version,ok", [(NEW, True), (NONE, True), (OLD, False)])
def test_copied_layout_plan_uses_the_chip_floor(version, ok):
    s = _with(tb._lab_chip(), version)
    res = cgb.plan(s, "q2-3", gate_name="cz_new", gate_class=tb.LAB_GATE,
                   pulses={"flux_pulse_control": {"fields": {"length": 4}}})
    assert ("creates" in res) is ok, res.get("error")
    if not ok:
        assert "at least 16 ns" in res["error"]


@pytest.mark.parametrize("version,length,ok", [
    (NEW, 4, True), (NONE, 4, True), (OLD, 4, False), (OLD, 12, False), (NEW, 6, False)])
def test_spectator_length_uses_the_chip_floor(version, length, ok):
    res = ts._plan(_with(ts._chip(), version), [{"qubit": "q3", "length": length, "level": 0.05}])
    assert ("error" not in res) is ok, res
    if not ok:
        assert any("For the spectator q3: the pulse length" in r for r in res["refusals"])


# --- the gate check ----------------------------------------------------------

def _length(length, chip, script=SCRIPT):
    res = gate_check.evaluate([GATE], _ran(cfg=_cfg(length=length), script=script), merged=chip)
    return _checks(res)["length"], _msg(res, "length")


def test_gate_check_4_ns_passes_on_qop_33():
    status, msg = _length(4, NEW)
    assert status == "pass" and "at least 4 ns" in msg and NOTE not in msg


def test_gate_check_4_ns_on_a_chip_without_version_is_a_note():
    status, msg = _length(4, NONE)
    assert status == "note" and msg.endswith(NOTE) and "at least 4 ns" in msg


def test_gate_check_16_ns_on_a_chip_without_version_has_no_note():
    status, msg = _length(16, NONE)
    assert status == "pass" and NOTE not in msg


@pytest.mark.parametrize("length", [4, 8, 12])
def test_gate_check_under_16_refused_before_qop_33(length):
    status, msg = _length(length, OLD)
    assert status == "fail"
    assert msg == (f"A pulse length must be a multiple of 4 ns and at least 16 ns: cz_pulse on q1.z "
                   f"is {length} ns.")


@pytest.mark.parametrize("chip", [OLD, NEW, NONE])
@pytest.mark.parametrize("length", [6, 0])
def test_gate_check_6_and_0_refused_everywhere(chip, length):
    assert _length(length, chip)[0] == "fail"


def test_gate_check_old_qop_pass_text_is_unchanged():
    assert _length(16, OLD) == ("pass", "All 1 pulse lengths are multiples of 4 ns and at least 16 ns.")


def _timed(stmt, chip):
    script = SCRIPT.replace('play("cz_pulse", "q1.z")', stmt + '\n    play("cz_pulse", "q1.z")')
    return _length(16, chip, script)


def test_qua_duration_floor_is_8_ns_on_qop_33():
    assert _timed('play("cz_pulse", "q1.z", duration=2)', NEW)[0] == "pass"
    status, msg = _timed('play("cz_pulse", "q1.z", duration=1)', NEW)
    assert status == "fail" and "less than 2 clock cycles (8 ns)" in msg
    status, msg = _timed('wait(1, "q1.z")', NEW)
    assert status == "note" and "minimum wait of 2 clock cycles (8 ns) on QOP 3.3" in msg
    assert NOTE not in msg


def test_qua_duration_under_16_ns_without_version_is_a_note():
    for stmt in ('play("cz_pulse", "q1.z", duration=2)', 'wait(2, "q1.z")'):
        status, msg = _timed(stmt, NONE)
        assert status == "note" and msg.endswith(NOTE), (stmt, msg)
    assert _timed('play("cz_pulse", "q1.z", duration=4)', NONE)[0] == "pass"


def test_qua_duration_texts_before_qop_33_are_unchanged():
    assert _timed('play("cz_pulse", "q1.z", duration=2)', OLD) == ("fail", (
        'The statement play("cz_pulse", "q1.z", duration=2) plays for less than 4 clock cycles '
        "(16 ns) or not a whole number of cycles."))
    assert _timed('wait(3, "q1.z")', OLD) == ("note",
        'The statement wait(3, "q1.z") waits less than 4 clock cycles. QUA documents a minimum '
        "wait of 4 clock cycles (16 ns); check that this runs on your controller version.")


# --- the timeline flag and the slot template ---------------------------------

def test_timeline_flags_a_length_under_the_stored_floor():
    assert gate_timeline_view.length_flag(4, 4) is False
    assert gate_timeline_view.length_flag(4) is True
    assert gate_timeline_view.length_flag(6, 4) is True
    tl = {"rows": [{"element": "q1.z", "operation": "p", "start_ns": 0, "end_ns": 4, "constant": 0.1}],
          "cannot_draw": None}
    flags = lambda t: gate_timeline_view.timeline_data({"timeline": t}, control="q1", target="q2")[
        "lanes"][0]["segments"][0]["flags"]
    assert "bad_length" in flags(tl)
    assert "bad_length" not in flags({**tl, "length_floor_ns": 4})


def test_gate_check_stores_the_chip_floor_with_the_timeline():
    res = gate_check.evaluate([GATE], _ran(), merged=NEW)
    assert gate_check.gate_result(res, *GATE)["timeline"]["length_floor_ns"] == 4
    res = gate_check.evaluate([GATE], _ran(), merged=OLD)
    assert gate_check.gate_result(res, *GATE)["timeline"]["length_floor_ns"] == 16


def test_slot_template_keeps_the_floor_every_qop_plays():
    assert slot_gate_template.MIN_LENGTH_NS == sc.LENGTH_FLOOR_ANY_QOP_NS == 16


# --- the plan carries the note to every surface that shows it -----------------

@pytest.mark.parametrize("version,length,notes", [
    (NONE, 8, [NOTE]), (NONE, 4, [NOTE]), (NONE, 12, [NOTE]), (NONE, 16, None), (NONE, 48, None),
    (NEW, 8, None), (NEW, 4, None), (OLD, 16, None)])
def test_known_class_plan_notes_a_short_length_without_version(version, length, notes):
    res = tb._plan(_with(tb._chip(), version), pulses=tb._square(length=length))
    assert "creates" in res, res.get("error")
    assert res.get("notes") == notes


def test_old_qop_plan_answer_has_no_notes_key():
    assert "notes" not in tb._plan(_with(tb._chip(), OLD), pulses=tb._square(length=48))
    assert "notes" not in tb._plan(_with(tb._chip(), OLD), pulses=tb._square(length=8))


@pytest.mark.parametrize("version,length,notes", [
    (NONE, 8, [NOTE]), (NONE, 48, None), (NEW, 8, None)])
def test_spectator_plan_notes_a_short_length_without_version(version, length, notes):
    res = ts._plan(_with(ts._chip(), version), [{"qubit": "q3", "length": length, "level": 0.05}])
    assert "error" not in res, res
    assert res.get("notes") == notes


def test_two_short_lengths_give_the_note_once():
    res = ts._plan(_with(ts._chip(), NONE), [{"qubit": "q3", "length": 8, "level": 0.05},
                                             {"qubit": "q4", "length": 12, "level": 0.05}],
                   pulses=None)
    assert res.get("notes") == [NOTE]


@pytest.mark.parametrize("version,length,notes", [
    (NONE, 8, [NOTE]), (NONE, 48, None), (NEW, 8, None)])
def test_copied_layout_plan_notes_a_short_length_without_version(version, length, notes):
    res = cgb.plan(_with(tb._lab_chip(), version), "q2-3", gate_name="cz_new",
                   gate_class=tb.LAB_GATE,
                   pulses={"flux_pulse_control": {"fields": {"length": length}}})
    assert "creates" in res, res.get("error")
    assert res.get("notes") == notes


def test_plan_and_check_doors_show_the_note(monkeypatch, tmp_path):
    from tests import test_cz_gate_routes as tr
    a = tr.App(tmp_path, state=_with(tb._chip(4), NONE))
    try:
        body = tr._body()
        body["pulses"]["flux_pulse_qubit"]["fields"]["length"] = 8
        plan = a.c.post(tr.PLAN, json=body).get_json()
        assert plan["pairs"][0]["notes"] == [NOTE], plan
        # no env picked: a known class is still created without the lab check,
        # and the check answer (which the dialog shows after the write) names the floor
        monkeypatch.setattr(tr.czr, "_env_python", lambda: None)
        chk = tr._check(a.c, body)
        row = chk["pairs"][0]
        assert row["verdict"]["allowed"] and row["notes"] == [NOTE], row
        body["pulses"]["flux_pulse_qubit"]["fields"]["length"] = 48
        assert a.c.post(tr.PLAN, json=body).get_json()["pairs"][0]["notes"] == []
    finally:
        from quam_state_manager.core.hub_index import close_readers
        close_readers()


# --- ratchet: no caller drops the floor's note -------------------------------

#: callers that read only the floor, and where the note is shown instead
FLOOR_ONLY = {
    ("gate_check.py", "_one_gate_lab"): "the timeline stores the floor; the length check of "
                                        "the same gate shows the note",
}


def _floor_calls():
    import ast
    from pathlib import Path
    pkg = Path(sc.__file__).resolve().parents[1]
    names = {"length_floor_ns", "chip_length_rule"}
    out = []
    for path in sorted(pkg.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) or fn.name in names:
                continue
            calls, bound = [], []
            for node in ast.walk(fn):
                if isinstance(node, ast.Call):
                    f = node.func
                    name = f.id if isinstance(f, ast.Name) else getattr(f, "attr", None)
                    if name in names:
                        calls.append(node)
                if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                        and len(node.targets) == 1 and isinstance(node.targets[0], ast.Tuple)
                        and len(node.targets[0].elts) == 2
                        and isinstance(node.targets[0].elts[1], ast.Name)):
                    f = node.value.func
                    if (f.id if isinstance(f, ast.Name) else getattr(f, "attr", None)) in names:
                        bound.append(node.targets[0].elts[1].id)
            if calls:
                read = {n.id for n in ast.walk(fn)
                        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
                used = any(b != "_" and b in read for b in bound)
                out.append((path.name, fn.name, used))
    return out


def test_every_floor_caller_shows_the_note():
    calls = _floor_calls()
    assert len(calls) >= 5, calls
    dropped = [(m, f) for m, f, used in calls if not used and (m, f) not in FLOOR_ONLY]
    assert not dropped, f"these functions drop the floor's note: {dropped}"
    stale = set(FLOOR_ONLY) - {(m, f) for m, f, _ in calls}
    assert not stale, stale
