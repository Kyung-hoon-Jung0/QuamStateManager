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
