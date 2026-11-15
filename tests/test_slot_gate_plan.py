"""Slot gate class plan pieces: descriptor, unit amplitudes, spectator rows, drafts, the scale check."""
import ast
import copy
from pathlib import Path

import pytest

from quam_state_manager.core import chip_copy
from quam_state_manager.core import cz_gate_build as cgb
from quam_state_manager.core import gate_check
from quam_state_manager.core import pulse_class_drafts as drafts
from quam_state_manager.core import slot_gate_plan as sgp
from quam_state_manager.core import slot_gate_template as sgt

PKG = Path(sgp.__file__).resolve().parents[1]
PAIR, GATE = "q1-2", "cz_slot"
SEGS = ("qubit_pairs", PAIR, "macros", GATE)
UNIT = sgt.SPECTATOR_UNIT_OP


def _qubit(name, unit=None):
    ops = {"const": {"amplitude": 0.1}}
    if unit is not None:
        ops[UNIT] = {"id": UNIT, "amplitude": unit, "length": 16,
                     "__class__": cgb.SPECTATOR_CLASS}
    return {"id": name, "z": {"operations": ops}}


def _chip(units=None):
    units = units or {}
    qs = {f"q{i}": _qubit(f"q{i}", units.get(f"q{i}")) for i in range(1, 6)}
    pair = {"id": PAIR, "qubit_control": "#/qubits/q1", "qubit_target": "#/qubits/q2",
            "moving_qubit": "control", "macros": {}, "coupler": {"id": "c", "operations": {}}}
    return {"qubits": qs, "qubit_pairs": {PAIR: pair}}


def _macro():
    return sgp.slot_desc(sgt.SlotSpec("SlotGate", frozenset(sgt.SLOTS), True))["skeleton"]()


def _plan(merged, rows, planned=None):
    before = copy.deepcopy(merged)
    macro = _macro()
    out = sgp.plan_spectators(merged, PAIR, GATE, rows, macro, SEGS, planned)
    assert merged == before
    return out, macro


# ---------------------------------------------------------------- descriptor

@pytest.mark.parametrize("key", sorted(sgt.PRESETS))
def test_slot_desc_matches_rendered_fields(key):
    for spect in (False, True):
        spec = sgt.SlotSpec("SlotGate", sgt.PRESETS[key], spect)
        desc = sgp.slot_desc(spec)
        assert set(desc) == set(cgb.GATE_CLASSES[cgb.CZ_GATE])
        macro = desc["skeleton"]()
        assert macro is not desc["skeleton"]()
        assert macro["__class__"] == "quam_config.SlotGate.SlotGate"
        fields = [s.field for s in desc["slots"]]
        assert fields == [f"{s}_flux_pulse" for s in sgt.SLOTS if s in spec.slots]
        assert all(s.required and s.role == f"{s.line}_pulse" and s.role in cgb.ROLES
                   for s in desc["slots"])
        assert all(macro[f] is None for f in fields)
        assert macro["phase_shift_control"] == 0.0 and macro["phase_shift_target"] == 0.0
        assert macro["duration"] is None and macro["extras"] == {} and macro["fidelity"] == {}
        has = {k for k in sgp.SPECTATOR_FIELDS if k in macro}
        assert has == (set(sgp.SPECTATOR_FIELDS) if spect else set())
        # every key the rendered class declares, and nothing else
        text = sgt.render(spec)
        declared = {ln.split(":")[0].strip() for ln in text.split("\n")
                    if ln.startswith("    ") and not ln.startswith("     ") and ":" in ln
                    and not ln.strip().startswith(("#", "def", "@", '"', "if", "for"))}
        assert declared == set(macro) - {"__class__"}


# ---------------------------------------------------------------- units

def test_unit_amplitudes_reads_the_stored_op():
    m = _chip({"q3": 0.25})
    m["qubits"]["q4"]["z"]["operations"][UNIT] = {"id": UNIT}
    m["qubits"]["q6"] = "#/qubits/q3"
    got = sgp.unit_amplitudes(m, ["q3", "q4", "q5", "q6", "nope"])
    assert got == {"q3": 0.25, "q4": None, "q5": None, "q6": 0.25, "nope": None}


# ---------------------------------------------------------------- plan rows

def test_existing_unit_is_reused_read_only_and_rows_carry_reachable():
    m = _chip({"q3": 0.3})
    (creates, extra, refusals), macro = _plan(m, [{"qubit": "q3", "length": 20, "level": 0.5}])
    assert refusals == [] and creates == []
    assert macro[sgp.SPECTATOR_QUBITS] == {"q3": "#/qubits/q3"}
    assert macro[sgp.SPECTATOR_LEVELS] == {"q3": 0.5}
    assert macro[sgp.SPECTATOR_LENGTHS] == {"q3": 20}
    row = extra["spectators"][0]
    assert row["unit"] == 0.3 and row["reachable"] == sgt.reachable_level(0.3)
    assert "not_calibrated" not in row and row["unit_created"] is False
    assert row["line"] == "q3.z"


def test_missing_unit_is_created_once_with_min_length():
    m = _chip()
    planned = {}
    (creates, extra, refusals), _ = _plan(
        m, [{"qubit": "q3", "length": 20, "level": 0.1, "unit": 0.2}], planned)
    assert refusals == []
    assert [p for p, _ in creates] == ["qubits.q3.z.operations." + UNIT]
    op = creates[0][1]
    assert op == {"id": UNIT, **cgb._fields_for(
        cgb.SPECTATOR_CLASS, {"length": sgt.MIN_LENGTH_NS, "amplitude": 0.2}, None)[0]}
    assert (op["id"], op["length"], op["amplitude"], op["__class__"]) == (
        UNIT, sgt.MIN_LENGTH_NS, 0.2, cgb.SPECTATOR_CLASS)
    assert planned == {"q3": 0.2} and extra["spectators"][0]["unit_created"] is True
    # a second gate of the same request reuses the planned unit: no second create
    (creates2, extra2, refusals2), _ = _plan(m, [{"qubit": "q3", "length": 24, "level": -0.3}],
                                             planned)
    assert refusals2 == [] and creates2 == [] and extra2["spectators"][0]["unit"] == 0.2


def test_given_unit_differing_from_the_stored_one_is_refused():
    (c, e, r), macro = _plan(_chip({"q3": 0.3}),
                             [{"qubit": "q3", "length": 20, "level": 0.1, "unit": 0.4}])
    assert c == [] and e == {} and r == [sgp.UNIT_GIVEN_PROBLEM.format(name="q3", have=0.3)]
    assert macro[sgp.SPECTATOR_LEVELS] == {}


def _refusal(rows, units=None):
    (c, e, r), macro = _plan(_chip(units), rows)
    assert c == [] and e == {} and len(r) == 1
    assert macro[sgp.SPECTATOR_QUBITS] == {} and macro[sgp.SPECTATOR_LEVELS] == {}
    return r[0]


def test_level_exactly_at_the_limit_is_refused_with_the_template_sentence():
    unit = 0.25
    level = sgt.AMP_SCALE_LIMIT * unit
    assert _refusal([{"qubit": "q3", "length": 20, "level": level}], {"q3": unit}) == \
        sgt.LEVEL_PROBLEM.format(name="q3", value=level)


def test_level_just_below_the_limit_passes():
    unit = 0.25
    level = (sgt.AMP_SCALE_LIMIT - 0.001) * unit
    (c, e, r), _ = _plan(_chip({"q3": unit}), [{"qubit": "q3", "length": 20, "level": level}])
    assert r == [] and e["spectators"][0]["level"] == level


def test_negative_unit_uses_its_magnitude():
    (c, e, r), _ = _plan(_chip({"q3": -0.25}), [{"qubit": "q3", "length": 20, "level": 0.4}])
    assert r == [] and e["spectators"][0]["reachable"] == sgt.reachable_level(-0.25) > 0
    assert _refusal([{"qubit": "q3", "length": 20, "level": 0.5}], {"q3": -0.25}) == \
        sgt.LEVEL_PROBLEM.format(name="q3", value=0.5)


def test_unit_zero_refused_and_never_created():
    assert _refusal([{"qubit": "q3", "length": 20, "level": 0.1, "unit": 0}]) == \
        sgt.UNIT_VALUE_PROBLEM.format(name="q3", value=0.0)
    assert _refusal([{"qubit": "q3", "length": 20, "level": 0.1}], {"q3": 0}) == \
        sgt.UNIT_VALUE_PROBLEM.format(name="q3", value=0)


def test_unit_op_missing_and_no_unit_given():
    assert _refusal([{"qubit": "q3", "length": 20, "level": 0.1}]) == \
        sgt.UNIT_PROBLEM.format(name="q3")


def test_duplicate_qubit_refused():
    rows = [{"qubit": "q3", "length": 20, "level": 0.1}] * 2
    assert _refusal(rows, {"q3": 0.3}) == sgt.DUPLICATE_PROBLEM.format(first="q3", second="q3")


def test_spectator_equal_to_control_refused():
    assert _refusal([{"qubit": "q1", "length": 20, "level": 0.1}], {"q1": 0.3}) == \
        sgt.OWN_PROBLEM.format(name="q1")


@pytest.mark.parametrize("length, ok", [(18, False), (20, True), (12, False), (16, True)])
def test_length_rule(length, ok):
    rows = [{"qubit": "q3", "length": length, "level": 0.1}]
    if ok:
        assert _plan(_chip({"q3": 0.3}), rows)[0][2] == []
    else:
        assert _refusal(rows, {"q3": 0.3}) == sgt.LENGTH_PROBLEM.format(name="q3", value=length)


def test_unknown_qubit_with_a_unit_gets_the_line_reason():
    why = _refusal([{"qubit": "q9", "length": 20, "level": 0.1, "unit": 0.2}])
    assert why == cgb.spectator_line(_chip(), "q9")


# ---------------------------------------------------------------- drafts

def test_slot_drafts_lists_parse_ok_only(tmp_path):
    spec = sgt.SlotSpec("GoodGate", frozenset({"coupler"}), True)
    drafts.save_draft(tmp_path, "chipA", "GoodGate", sgt.render(spec))
    drafts.save_draft(tmp_path, "chipA", "BrokenGate",
                      sgt.render(sgt.SlotSpec("BrokenGate", frozenset({"coupler"}))) + "# edit\n")
    drafts.save_draft(tmp_path, "chipA", "Renamed",
                      sgt.render(sgt.SlotSpec("OtherName", frozenset({"coupler"}))))
    drafts.save_draft(tmp_path, "chipA", "PulseThing", "class PulseThing: pass\n")
    got = sgp.slot_drafts(tmp_path, "chipA")
    assert [n for n, _ in got] == ["GoodGate"] and got[0][1] == spec
    assert sgp.slot_drafts(tmp_path, "chipB") == []


# ---------------------------------------------------------------- gate check item

def _evaluate(after_merged):
    gate = {"script": None, "ops": [], "error": None}
    ran = {"status": "ok", "error": None, "data": {
        "before": {}, "after": {}, "gates": {gate_check.macro_path(PAIR, GATE): gate},
        "before_gates": {}, "quam_config": None}}
    return gate_check.gate_result(
        gate_check.evaluate([(PAIR, GATE)], ran, {}, None, after_merged), PAIR, GATE)


def _with_gate(levels, unit=0.25):
    m = _chip({"q3": unit})
    macro = _macro()
    macro[sgp.SPECTATOR_QUBITS] = {"q3": "#/qubits/q3"}
    macro[sgp.SPECTATOR_LEVELS] = {"q3": levels}
    macro[sgp.SPECTATOR_LENGTHS] = {"q3": 20}
    m["qubit_pairs"][PAIR]["macros"][GATE] = macro
    return m


def test_spectator_scale_is_a_check_with_words():
    assert "spectator_scale" in gate_check.CHECKS
    assert gate_check._CHECK_WORDS["spectator_scale"] == "the spectator levels"


def test_spectator_scale_fails_with_the_same_sentence_as_the_plan():
    level = sgt.AMP_SCALE_LIMIT * 0.25
    g = _evaluate(_with_gate(level))
    c = g["checks"]["spectator_scale"]
    plan_why = _refusal([{"qubit": "q3", "length": 20, "level": level}], {"q3": 0.25})
    assert c["status"] == "fail" and c["message"] == plan_why


def test_spectator_scale_passes_below_and_is_omitted_without_rows():
    assert _evaluate(_with_gate(0.1))["checks"]["spectator_scale"]["status"] == "pass"
    for after in (_chip(), None):
        g = _evaluate(after)
        assert "spectator_scale" not in g["checks"]
        assert "spectator_scale" not in g["unchecked"]
        assert "spectator_scale" not in g["partly_checked"]


def test_check_gates_reads_the_chip_with_the_creates(tmp_path):
    import json
    from quam_state_manager.core.loader import QuamStore
    folder = tmp_path / "chip"
    folder.mkdir()
    m = _chip({"q3": 0.25})
    m["__class__"] = "pkg.Root"
    (folder / "state.json").write_text(json.dumps(m), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps({"wiring": {}}), encoding="utf-8")
    store = QuamStore(folder)
    macro = _with_gate(sgt.AMP_SCALE_LIMIT * 0.25)["qubit_pairs"][PAIR]["macros"][GATE]

    def fake(py, before, after, macros, **kw):
        return {"status": "ok", "error": None, "data": {
            "before": {}, "after": {}, "before_gates": {}, "quam_config": None,
            "gates": {macros[0]: {"script": None, "ops": [], "error": None}}}}
    res = gate_check.check_gates(store, "python.exe", [(PAIR, GATE)],
                                 [(".".join(SEGS), macro)], runner=fake)
    c = gate_check.gate_result(res, PAIR, GATE)["checks"]["spectator_scale"]
    assert c["status"] == "fail" and res["failed"]


def test_rows_with_empty_levels_are_still_checked():
    m = _with_gate(0.1)
    m["qubit_pairs"][PAIR]["macros"][GATE][sgp.SPECTATOR_LEVELS] = {}
    c = _evaluate(m)["checks"]["spectator_scale"]
    assert c["status"] == "fail" and c["message"].startswith("Spectator keys differ")


def test_spectator_scale_is_judged_when_the_chip_with_the_write_does_not_load():
    gate_ran = {"status": "ok", "error": None, "data": {
        "before": {}, "after": {"load_error": "ValueError: broken"},
        "gates": {}, "before_gates": {}, "quam_config": None}}
    level = sgt.AMP_SCALE_LIMIT * 0.25
    g = gate_check.gate_result(gate_check.evaluate(
        [(PAIR, GATE)], gate_ran, {}, None, _with_gate(level)), PAIR, GATE)
    assert g["checks"]["spectator_scale"]["status"] == "fail"
    assert {g["checks"][c]["status"] for c in gate_check.LAB_CHECKS[1:]} == {"skipped"}


def test_unit_created_in_the_same_write_is_read_by_the_check(tmp_path):
    """The unit operation and the gate arrive as creates of one write; the
    stored chip has neither, and the store is left as it was."""
    import json
    from quam_state_manager.core.loader import QuamStore
    folder = tmp_path / "chip"
    folder.mkdir()
    m = _chip()
    m["__class__"] = "pkg.Root"
    (folder / "state.json").write_text(json.dumps(m), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps({"wiring": {}}), encoding="utf-8")
    store = QuamStore(folder)
    before = copy.deepcopy(store.merged)
    (creates, _extra, refusals), macro = _plan(
        m, [{"qubit": "q3", "length": 20, "level": 0.1, "unit": 0.2}])
    assert refusals == [] and len(creates) == 1

    def fake(py, b, a, macros, **kw):
        return {"status": "ok", "error": None, "data": {
            "before": {}, "after": {}, "before_gates": {}, "quam_config": None,
            "gates": {macros[0]: {"script": None, "ops": [], "error": None}}}}
    res = gate_check.check_gates(store, "python.exe", [(PAIR, GATE)],
                                 creates + [(".".join(SEGS), macro)], runner=fake)
    c = gate_check.gate_result(res, PAIR, GATE)["checks"]["spectator_scale"]
    assert c["status"] == "pass", c
    assert store.merged == before


def test_outcome_kind_of_a_gate_without_rows_is_unchanged():
    from quam_state_manager.core import gate_check_log
    gate = {"script": None, "ops": [], "error": None}
    ran = {"status": "ok", "error": None, "data": {
        "before": {}, "after": {}, "gates": {gate_check.macro_path(PAIR, GATE): gate},
        "before_gates": {}, "quam_config": None}}
    res = gate_check.evaluate([(PAIR, GATE)], ran, {}, None, _chip())
    entry = gate_check.gate_result(res, PAIR, GATE)
    oc = gate_check_log.outcome(res, entry)
    assert "spectator_scale" not in [x["check"] for x in oc["not_run"] + oc["failed"]]
    without = dict(entry, checks={k: v for k, v in entry["checks"].items()
                                  if k != "spectator_scale"})
    assert gate_check_log.outcome(res, without)["kind"] == oc["kind"]


# ---------------------------------------------------------------- data never raises

HUGE = 10 ** 400
ROW = {"qubit": "q3", "length": 20, "level": 0.1}


def test_huge_level_and_huge_unit_are_refused_with_the_template_sentence():
    assert _refusal([dict(ROW, level=HUGE)], {"q3": 0.3}) == \
        sgt.LEVEL_PROBLEM.format(name="q3", value=HUGE)
    assert _refusal([dict(ROW, unit=HUGE)]) == \
        sgt.UNIT_VALUE_PROBLEM.format(name="q3", value=HUGE)


@pytest.mark.parametrize("rows, why", [
    (5, sgt.TYPE_PROBLEM.format(what="rows", kind="a list", value=5)),
    ({"q": 5}, sgt.TYPE_PROBLEM.format(what="rows", kind="a list", value={"q": 5})),
    ([5], sgp.ROW_PROBLEM.format(value=5)),
    ([{"qubit": 7}], sgp.ROW_PROBLEM.format(value=7)),
])
def test_rows_of_another_shape_are_refused(rows, why):
    assert _refusal(rows, {"q3": 0.3}) == why


@pytest.mark.parametrize("field, value, sentence", [
    ("level", True, sgt.LEVEL_PROBLEM), ("level", float("nan"), None),
    ("level", float("inf"), sgt.LEVEL_PROBLEM), ("level", "0.1", sgt.LEVEL_PROBLEM),
    ("length", True, sgt.LENGTH_PROBLEM), ("length", 20.5, sgt.LENGTH_PROBLEM),
    ("length", HUGE, sgt.LENGTH_PROBLEM), ("length", float("inf"), sgt.LENGTH_PROBLEM),
    ("length", "20", sgt.LENGTH_PROBLEM),
])
def test_values_that_are_no_sane_number_are_refused(field, value, sentence):
    why = _refusal([dict(ROW, **{field: value})], {"q3": 0.3})
    if sentence is None:
        assert why.startswith("Spectator 'q3' level nan must be a finite number")
    else:
        assert why == sentence.format(name="q3", value=value)


def test_a_boolean_or_text_unit_never_stands_for_a_number():
    # True == 1.0 in Python: it must not pass as "the same amplitude"
    assert _refusal([dict(ROW, unit=True)], {"q3": 1.0}) == \
        sgp.UNIT_GIVEN_PROBLEM.format(name="q3", have=1.0)
    assert _refusal([dict(ROW, unit=True)]) == \
        sgt.UNIT_VALUE_PROBLEM.format(name="q3", value=True)
    assert _refusal([dict(ROW, unit="0.2")]) == \
        sgt.UNIT_VALUE_PROBLEM.format(name="q3", value="0.2")


def test_a_chip_of_another_shape_is_refused_not_raised():
    for merged in (None, 5, {"qubits": 5}, {"qubits": {"q3": 5}}):
        macro = _macro()
        c, e, r = sgp.plan_spectators(merged, PAIR, GATE, [dict(ROW)], macro, SEGS)
        assert c == [] and e == {} and len(r) == 1 and macro[sgp.SPECTATOR_LEVELS] == {}


def test_lengths_are_stored_as_whole_numbers():
    (c, e, r), macro = _plan(_chip({"q3": 0.3}), [dict(ROW, length=20.0)])
    assert r == []
    for got in (macro[sgp.SPECTATOR_LENGTHS]["q3"], e["spectators"][0]["length"]):
        assert got == 20 and type(got) is int


def test_a_level_of_zero_is_accepted_on_purpose():
    # the template's rule accepts it, and it is the value before calibration
    (c, e, r), macro = _plan(_chip({"q3": 0.3}), [dict(ROW, level=0)])
    assert r == [] and macro[sgp.SPECTATOR_LEVELS] == {"q3": 0.0}
    m = _with_gate(0)
    assert _evaluate(m)["checks"]["spectator_scale"]["status"] == "pass"


# ---------------------------------------------------------------- one qubit, two names

def _alias_chip(alias_of, units=None):
    m = _chip(units)
    m["qubits"]["q6"] = "#/qubits/" + alias_of
    return m


def _stored(m, qubits, level=0.1):
    macro = _macro()
    macro[sgp.SPECTATOR_QUBITS] = dict(qubits)
    macro[sgp.SPECTATOR_LEVELS] = {k: level for k in qubits}
    macro[sgp.SPECTATOR_LENGTHS] = {k: 20 for k in qubits}
    m["qubit_pairs"][PAIR]["macros"][GATE] = macro
    return _evaluate(m)["checks"]["spectator_scale"]


def test_real_qubit_follows_an_alias_entry():
    m = _alias_chip("q3")
    m["qubits"]["q7"] = "#/qubits/q6"
    m["qubits"]["q8"] = "#/qubits/nope"
    assert [sgp.real_qubit(m, r) for r in ("q3", "q6", " #/qubits/q6 ", "q7", "q8", "q9")] == \
        ["q3", "q3", "q3", "q3", "q8", "q9"]
    assert sgp.real_qubit(m, None) is None and sgp.real_qubit(m, "#/qubit_pairs/q1-2") is None


def test_two_names_of_one_qubit_are_refused_and_never_get_two_unit_operations():
    m = _alias_chip("q3")
    rows = [dict(ROW, unit=0.2), {"qubit": "q6", "length": 20, "level": 0.9, "unit": 0.5}]
    (c, e, r), macro = _plan(m, rows)
    assert c == [] and e == {} and macro[sgp.SPECTATOR_QUBITS] == {}
    assert r == [sgt.DUPLICATE_PROBLEM.format(first="q3", second="q6")]


def test_an_alias_row_is_planned_on_the_real_qubit():
    planned = {}
    (c, e, r), macro = _plan(_alias_chip("q3"), [dict(ROW, qubit="q6", unit=0.2)], planned)
    assert r == [] and [p for p, _ in c] == ["qubits.q3.z.operations." + UNIT]
    assert macro[sgp.SPECTATOR_QUBITS] == {"q3": "#/qubits/q3"}
    assert planned == {"q3": 0.2} and e["spectators"][0]["qubit"] == "q3"
    assert e["spectators"][0]["line"] == "q3.z"
    # the next gate names the same qubit by its own name: no second create
    (c2, e2, r2), _ = _plan(_alias_chip("q3"), [dict(ROW)], planned)
    assert r2 == [] and c2 == [] and e2["spectators"][0]["unit"] == 0.2


def test_an_alias_of_the_control_is_refused_as_the_pair_own_qubit():
    (c, e, r), macro = _plan(_alias_chip("q1", {"q1": 0.3}), [dict(ROW, qubit="q6")])
    assert c == [] and e == {} and r == [sgt.OWN_PROBLEM.format(name="q1")]
    m = _alias_chip("q2", {"q2": 0.3})
    assert _plan(m, [dict(ROW, qubit="q6")])[0][2] == [sgt.OWN_PROBLEM.format(name="q2")]


def test_a_pair_whose_control_is_an_alias_refuses_the_real_qubit():
    m = _alias_chip("q3", {"q3": 0.3})
    m["qubit_pairs"][PAIR]["qubit_control"] = "#/qubits/q6"
    assert _plan(m, [dict(ROW)])[0][2] == [sgt.OWN_PROBLEM.format(name="q3")]


def test_the_stored_check_sees_two_names_of_one_qubit_and_an_alias_of_the_control():
    c = _stored(_alias_chip("q3", {"q3": 0.25}), {"a": "#/qubits/q3", "b": "#/qubits/q6"})
    assert c == {"status": "fail",
                 "message": sgt.DUPLICATE_PROBLEM.format(first="a", second="b")}
    c = _stored(_alias_chip("q1", {"q1": 0.25}), {"a": "#/qubits/q6"})
    assert c == {"status": "fail", "message": sgt.OWN_PROBLEM.format(name="a")}
    # an alias alone is read with the real qubit's unit
    assert _stored(_alias_chip("q3", {"q3": 0.25}), {"a": "q6"})["status"] == "pass"


# ---------------------------------------------------------------- behaviours that had no pin

def test_the_stored_check_reads_the_unit_by_row_key_not_by_qubit_name():
    assert _stored(_chip({"q3": 0.25}), {"s1": "#/qubits/q3"})["status"] == "pass"
    level = sgt.AMP_SCALE_LIMIT * 0.25
    c = _stored(_chip({"q3": 0.25}), {"s1": "#/qubits/q3"}, level)
    assert c == {"status": "fail", "message": sgt.LEVEL_PROBLEM.format(name="s1", value=level)}


def test_a_unit_differing_from_one_planned_by_another_gate_is_refused_in_its_own_words():
    planned = {"q3": 0.2}
    (c, e, r), macro = _plan(_chip(), [dict(ROW, unit=0.3)], planned)
    assert c == [] and e == {} and planned == {"q3": 0.2}
    assert r == [sgp.UNIT_PLANNED_PROBLEM.format(name="q3", have=0.2)]
    assert "already exists" not in r[0] and "another gate of this request" in r[0]
    # the same amplitude again is fine, and makes no second create
    (c2, _e2, r2), _ = _plan(_chip(), [dict(ROW, unit=0.2)], planned)
    assert r2 == [] and c2 == []


def test_a_fresh_gate_with_empty_spectator_fields_has_no_own_check():
    m = _chip()
    macro = _macro()
    assert all(macro[k] == {} for k in sgp.SPECTATOR_FIELDS)
    m["qubit_pairs"][PAIR]["macros"][GATE] = macro
    assert sgp.macro_spectator_problem(m, PAIR, macro) is False
    assert "spectator_scale" not in _evaluate(m)["checks"]


@pytest.mark.parametrize("written", ["#/qubits/q3", "  q3 ", " #/qubits/q3 "])
def test_a_row_qubit_given_as_a_reference_or_padded_is_one_plain_name(written):
    (c, e, r), macro = _plan(_chip({"q3": 0.3}), [dict(ROW, qubit=written)])
    assert r == [] and macro[sgp.SPECTATOR_QUBITS] == {"q3": "#/qubits/q3"}
    assert macro[sgp.SPECTATOR_LEVELS] == {"q3": 0.1} and e["spectators"][0]["qubit"] == "q3"


@pytest.mark.parametrize("own", ["q1", "q2"])
def test_the_stored_check_refuses_the_pair_own_qubit(own):
    c = _stored(_chip({own: 0.25}), {own: "#/qubits/" + own})
    assert c == {"status": "fail", "message": sgt.OWN_PROBLEM.format(name=own)}


def test_a_spectator_read_failure_is_unchecked_never_a_pass(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("no")
    monkeypatch.setattr(sgp, "macro_spectator_problem", boom)
    g = _evaluate(_with_gate(0.1))
    assert g["checks"]["spectator_scale"]["status"] == "skipped"
    assert "spectator_scale" in g["unchecked"]


# ---------------------------------------------------------------- honest sentences

@pytest.mark.parametrize("op", [{"id": UNIT}, {"id": UNIT, "amplitude": None},
                                "#/qubits/q4/z/operations/const"])
@pytest.mark.parametrize("unit", [None, 0.2])
def test_a_unit_operation_without_an_amplitude_is_said_to_exist(op, unit):
    m = _chip()
    m["qubits"]["q3"]["z"]["operations"][UNIT] = op
    row = dict(ROW) if unit is None else dict(ROW, unit=unit)
    (c, e, r), _ = _plan(m, [row])
    assert c == [] and r == [sgp.UNIT_NO_AMPLITUDE_PROBLEM.format(name="q3")]
    assert "missing" not in r[0] and "already exists" not in r[0]
    # the stored check says the same about the same chip
    assert _stored(m, {"q3": "#/qubits/q3"}) == {"status": "fail", "message": r[0]}


def test_an_amplitude_that_is_a_reference_names_the_value():
    m = _chip()
    m["qubits"]["q3"]["z"]["operations"][UNIT] = {"id": UNIT, "amplitude": "#/x"}
    assert _plan(m, [dict(ROW)])[0][2] == [sgt.UNIT_VALUE_PROBLEM.format(name="q3", value="#/x")]


@pytest.mark.parametrize("unit", [None, 0.2])
def test_a_qubit_that_is_not_on_the_chip_gets_the_line_reason_first(unit):
    row = dict(ROW, qubit="q9") if unit is None else dict(ROW, qubit="q9", unit=unit)
    why = _refusal([row])
    assert why == cgb.spectator_line(_chip(), "q9") and "unit operation" not in why
    m = _chip()
    del m["qubits"]["q4"]["z"]
    (c, e, r), _ = _plan(m, [dict(ROW, qubit="q4", length=18)])
    assert r == [cgb.spectator_line(m, "q4")]
    # a stored gate naming such a qubit reads the same reason
    for q in ("q9", "q4"):
        assert _stored(m, {q: "#/qubits/" + q}) == {
            "status": "fail", "message": cgb.spectator_line(m, q)}


def test_a_missing_unit_operation_on_a_real_line_is_still_called_missing():
    assert _stored(_chip(), {"q3": "#/qubits/q3"}) == {
        "status": "fail", "message": sgt.UNIT_PROBLEM.format(name="q3")}


@pytest.mark.parametrize("planned", [(), [], 5, "q3"])
def test_a_raising_input_is_refused_with_a_sentence_that_names_no_exception(planned):
    row = {"qubit": "q3", "length": 20, "level": 0.1, "unit": 0.2}
    (c, e, r), macro = _plan(_chip(), [row], planned)
    assert c == [] and e == {} and r == [sgp.UNREADABLE_PROBLEM]
    assert "Error" not in r[0] and "{" not in r[0] and macro[sgp.SPECTATOR_QUBITS] == {}


def test_rows_problem_names_no_exception_either(monkeypatch):
    def boom(*a, **k):
        raise AttributeError("x")
    monkeypatch.setattr(sgt, "spectator_rows_problem", boom)
    assert sgp.rows_problem(_chip(), {}, {}, {}, "q1", "q2", {}) == sgp.UNREADABLE_PROBLEM


@pytest.mark.parametrize("written", ["q.a", "#/qubits/q.a"])
def test_a_qubit_name_with_a_dot_is_refused_before_any_create(written):
    m = _chip()
    m["qubits"]["q.a"] = _qubit("q.a")
    planned = {}
    (c, e, r), macro = _plan(m, [{"qubit": written, "length": 20, "level": 0.1, "unit": 0.2}],
                             planned)
    assert c == [] and e == {} and r == [sgp.DOT_PROBLEM.format(name=sgt.qubit_name(written))]
    assert planned == {} and macro[sgp.SPECTATOR_QUBITS] == {}
    # also with the unit operation already there: no path with a dot is ever handed out
    m["qubits"]["q.a"] = _qubit("q.a", 0.2)
    (c, e, r), _ = _plan(m, [{"qubit": written, "length": 20, "level": 0.1}])
    assert c == [] and e == {} and r == [sgp.DOT_PROBLEM.format(name=sgt.qubit_name(written))]


@pytest.mark.parametrize("landing", ["qubits", "extra"])
@pytest.mark.parametrize("unit", [None, 0.2])
def test_a_plain_name_that_lands_on_a_dotted_one_is_refused_too(landing, unit):
    """The written name has no dot: the real name (an alias inside the qubit
    table) or the line path (an object outside it) has one."""
    m = _chip()
    m.setdefault(landing, {})["q.a"] = _qubit("q.a", unit)
    m["qubits"]["q6"] = "#/" + landing + "/q.a"
    planned = {}
    (c, e, r), macro = _plan(m, [{"qubit": "q6", "length": 20, "level": 0.1, "unit": 0.2}],
                             planned)
    assert c == [] and e == {} and r == [sgp.DOT_PROBLEM.format(name="q6")]
    assert planned == {} and macro[sgp.SPECTATOR_QUBITS] == {}


@pytest.mark.parametrize("unit", [None, 0.2])
def test_a_dot_in_a_container_key_of_the_line_path_is_refused(unit):
    """Neither the written name nor the qubit's own key has a dot: a key
    above it on the way to the flux line has one."""
    m = _chip()
    m["e.x"] = {"lines": {"qa": _qubit("qa", unit)}}
    m["qubits"]["q6"] = "#/e.x/lines/qa"
    planned = {}
    row = {"qubit": "q6", "length": 20, "level": 0.1}
    if unit is None:
        row["unit"] = 0.2
    (c, e, r), macro = _plan(m, [row], planned)
    assert c == [] and e == {} and r == [sgp.DOT_PROBLEM.format(name="q6")]
    assert planned == {} and macro[sgp.SPECTATOR_QUBITS] == {}


def test_a_unit_is_the_same_only_when_the_numbers_are_exactly_equal():
    near = 0.1 + 0.2
    assert near != 0.3
    row = {"qubit": "q3", "length": 20, "level": 0.1, "unit": near}
    (c, e, r), _ = _plan(_chip({"q3": 0.3}), [row])
    assert c == [] and e == {} and r == [sgp.UNIT_GIVEN_PROBLEM.format(name="q3", have=0.3)]
    planned = {"q3": 0.3}
    (c, e, r), _ = _plan(_chip(), [row], planned)
    assert c == [] and r == [sgp.UNIT_PLANNED_PROBLEM.format(name="q3", have=0.3)]
    assert planned == {"q3": 0.3}
    (c, e, r), _ = _plan(_chip({"q3": near}), [row])
    assert c == [] and r == [] and e["spectators"][0]["unit"] == near


def _listed_chip():
    m = _chip()
    m["extra"] = [_qubit("qa"), _qubit("qb", 0.2), {"keep": 1}]
    m["qubits"]["q6"] = "#/extra/0"
    m["qubits"]["q7"] = "#/extra/1"
    return m


def test_a_new_unit_on_a_line_inside_a_list_is_refused():
    """The create door steps through dicts only, so the plan hands out no
    create that goes through a list."""
    m = _listed_chip()
    planned = {}
    row = {"qubit": "q6", "length": 20, "level": 0.1, "unit": 0.2}
    (c, e, r), macro = _plan(m, [row], planned)
    assert c == [] and e == {} and r == [sgp.LIST_PROBLEM.format(name="q6")]
    assert planned == {} and macro[sgp.SPECTATOR_QUBITS] == {}
    # a good row before it changes nothing either
    (c, e, r), macro = _plan(m, [{"qubit": "q3", "length": 20, "level": 0.1, "unit": 0.2}, row],
                             planned)
    assert c == [] and e == {} and r == [sgp.LIST_PROBLEM.format(name="q6")]
    assert planned == {} and macro[sgp.SPECTATOR_QUBITS] == {}


def test_a_unit_that_exists_on_a_line_inside_a_list_is_read_and_needs_no_create():
    m = _listed_chip()
    (c, e, r), macro = _plan(m, [{"qubit": "q7", "length": 20, "level": 0.1}])
    assert r == [] and c == []
    assert e["spectators"][0]["unit"] == 0.2 and e["spectators"][0]["unit_created"] is False
    after = gate_check._with_creates(m, [(".".join(SEGS), macro)])
    assert gate_check._spectator_scale_check((PAIR, GATE), after)["status"] == "pass"


def _door(m, creates):
    """What the create door leaves of *m*, or None when it refuses."""
    try:
        state, _wiring = chip_copy.apply_creates(copy.deepcopy(m), {}, creates)
    except chip_copy.CreateError:
        return None
    return state


@pytest.mark.parametrize("path", [
    "extra.0.z.operations.new", "extra.3.z", "extra.01.z", "extra.-1.z", "extra.x.z", "extra.5",
    "extra.2", "qubits.q1.none.new", "qubits.q1.z.operations.const", "qubits.q1.id.new",
    "qubits..new", "qubits.q1.z.operations.", ".new"])
def test_the_overlay_leaves_out_every_create_the_door_refuses(path):
    m = _listed_chip()
    before = copy.deepcopy(m)
    assert _door(m, [(path, {"new": 1})]) is None
    assert gate_check._with_creates(m, [(path, {"new": 1})]) == before and m == before


@pytest.mark.parametrize("one_gate", [True, False])
def test_the_overlay_and_the_door_leave_the_same_chip(one_gate):
    m = _listed_chip()
    before = copy.deepcopy(m)
    rows = [{"qubit": "q3", "length": 20, "level": 0.1, "unit": 0.2},
            {"qubit": "q7", "length": 20, "level": 0.1},
            {"qubit": "q4", "length": 20, "level": 0.1, "unit": 0.3}]
    plans = [rows] if one_gate else [rows[:1], rows[1:]]
    creates, planned, names = [], {}, []
    for i, part in enumerate(plans):
        macro = _macro()
        name = f"cz_{i}"
        c, e, r = sgp.plan_spectators(m, PAIR, name, part, macro, SEGS[:3] + (name,), planned)
        assert r == []
        creates += list(c) + [(".".join(SEGS[:3] + (name,)), macro)]
        names.append(name)
    assert len(creates) == 2 + len(plans)
    after = gate_check._with_creates(m, creates)
    assert m == before
    assert after == _door(m, creates) and after != before
    assert after["qubits"]["q3"]["z"]["operations"][UNIT]["amplitude"] == 0.2
    assert "const" in after["qubits"]["q3"]["z"]["operations"] and after["extra"] == before["extra"]
    for name in names:
        assert gate_check._spectator_scale_check((PAIR, name), after)["status"] == "pass"


def test_new_sentences_keep_the_user_text_rules():
    for text in (sgp.UNIT_GIVEN_PROBLEM, sgp.UNIT_PLANNED_PROBLEM, sgp.UNIT_NO_AMPLITUDE_PROBLEM,
                 sgp.ROW_PROBLEM, sgp.UNREADABLE_PROBLEM, sgp.DOT_PROBLEM, sgp.LIST_PROBLEM):
        assert text.isascii()
        assert not any(bad in text for bad in (" -- ", "->", "CAS", "patch"))


# ---------------------------------------------------------------- labels, counts, whole-skip paths

def test_every_check_has_a_label_in_every_python_label_table():
    from quam_state_manager.core import gate_check_log
    for table in (gate_check._CHECK_WORDS, gate_check_log.CHECK_LABELS):
        assert set(table) == set(gate_check.CHECKS)
        assert all(isinstance(v, str) and v.strip() for v in table.values())
    assert gate_check_log.CHECK_LABELS[gate_check.OWN_CHECK] == \
        gate_check._CHECK_WORDS[gate_check.OWN_CHECK] == "the spectator levels"
    assert gate_check_log.UNCOUNTED_NOTES == (gate_check.OWN_CHECK,)


def _outcome(after_merged, ran=None):
    from quam_state_manager.core import gate_check_log
    gate = {"script": None, "ops": [], "error": None}
    ran = ran or {"status": "ok", "error": None, "data": {
        "before": {}, "after": {}, "gates": {gate_check.macro_path(PAIR, GATE): gate},
        "before_gates": {}, "quam_config": None}}
    res = gate_check.evaluate([(PAIR, GATE)], ran, {}, None, after_merged)
    entry = gate_check.gate_result(res, PAIR, GATE)
    return res, entry, gate_check_log.outcome(res, entry)


def test_a_failed_spectator_check_is_named_in_plain_words():
    # the bare runner data of this file fails a lab check too: compare with the same gate in range
    base = _outcome(_with_gate(0.1))[2]
    _res, _entry, oc = _outcome(_with_gate(sgt.AMP_SCALE_LIMIT * 0.25))
    assert oc["kind"] == "failed" and oc["total"] == len(gate_check.CHECKS)
    assert [f["label"] for f in oc["failed"]] == \
        [f["label"] for f in base["failed"]] + ["the spectator levels"]
    assert f" of {len(gate_check.CHECKS)} checks failed: " in oc["summary"]
    assert oc["summary"].endswith("the spectator levels.")
    assert "spectator_scale" not in oc["summary"]


def _hand_outcome(own):
    from quam_state_manager.core import gate_check_log
    checks = {c: {"status": "pass", "message": "ok"} for c in gate_check.LAB_CHECKS}
    checks[gate_check.OWN_CHECK] = own
    entry = {"ran": True, "checks": checks, "unchecked": [], "partly_checked": []}
    return gate_check_log.outcome({"status": "ok", "checked": True}, entry)


def test_counts_of_a_gate_without_rows_read_as_before_the_check_existed():
    lab = len(gate_check.LAB_CHECKS)
    _res, entry, oc = _outcome(_chip())
    assert "spectator_scale" not in entry["checks"]
    assert oc["total"] == lab
    oc = _hand_outcome({"status": "note", "message": gate_check.NO_SPECTATOR_ROWS})
    assert (oc["kind"], oc["passed"], oc["total"], oc["not_run"]) == ("checked", lab, lab, [])
    # a gate WITH rows counts its spectator check
    assert _outcome(_with_gate(0.1))[2]["total"] == lab + 1
    oc = _hand_outcome({"status": "pass", "message": "ok"})
    assert (oc["kind"], oc["passed"], oc["total"]) == ("checked", lab + 1, lab + 1)


@pytest.mark.parametrize("status", ["no-env", "busy", "crash", "timeout"])
def test_the_own_check_is_judged_when_the_lab_code_did_not_run(status):
    ran = {"status": status, "error": "words", "data": None}
    lab = list(gate_check.LAB_CHECKS)
    # no rows: omitted; the lab checks are the unchecked ones
    res, entry, oc = _outcome(_chip(), ran)
    assert "spectator_scale" not in entry["checks"]
    assert entry["unchecked"] == lab and not res["failed"]
    assert oc["kind"] == "none" and oc["total"] == len(lab)
    # rows at the limit: failed although the lab's code did not run
    level = sgt.AMP_SCALE_LIMIT * 0.25
    res, entry, oc = _outcome(_with_gate(level), ran)
    assert entry["checks"]["spectator_scale"] == {
        "status": "fail", "message": sgt.LEVEL_PROBLEM.format(name="q3", value=level)}
    assert res["failed"] and not res["ok"] and oc["kind"] == "failed"
    assert entry["unchecked"] == lab
    # rows in range: a pass, and still not checked with the lab's code
    res, entry, oc = _outcome(_with_gate(0.1), ran)
    assert entry["checks"]["spectator_scale"]["status"] == "pass"
    assert not res["failed"] and not res["all_checked"] and oc["kind"] == "none"


def test_the_own_check_is_judged_when_the_chip_as_it_is_does_not_load():
    ran = {"status": "ok", "error": None, "data": {
        "before": {"load_error": "ImportError: lab"}, "after": {}, "gates": {},
        "before_gates": {}, "quam_config": None}}
    res, entry, _oc = _outcome(_with_gate(sgt.AMP_SCALE_LIMIT * 0.25), ran)
    assert entry["checks"]["spectator_scale"]["status"] == "fail" and res["failed"]
    res, entry, _oc = _outcome(_chip(), ran)
    assert entry["unchecked"] == list(gate_check.LAB_CHECKS) and not res["failed"]


def test_check_gates_without_an_environment_still_judges_the_write(tmp_path):
    import json
    from quam_state_manager.core.loader import QuamStore
    folder = tmp_path / "chip"
    folder.mkdir()
    m = _chip({"q3": 0.25})
    m["__class__"] = "pkg.Root"
    (folder / "state.json").write_text(json.dumps(m), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps({"wiring": {}}), encoding="utf-8")
    store = QuamStore(folder)
    macro = _with_gate(sgt.AMP_SCALE_LIMIT * 0.25)["qubit_pairs"][PAIR]["macros"][GATE]
    res = gate_check.check_gates(store, None, [(PAIR, GATE)], [(".".join(SEGS), macro)])
    g = gate_check.gate_result(res, PAIR, GATE)
    assert res["status"] == "no-env" and res["failed"]
    assert g["checks"]["spectator_scale"]["status"] == "fail"


# ---------------------------------------------------------------- static rule

def test_scale_limit_assigned_in_one_file_and_no_bare_two_in_the_plan():
    hits = [p for p in PKG.rglob("*.py")
            if "AMP_SCALE_LIMIT =" in p.read_text(encoding="utf-8")]
    assert [p.name for p in hits] == ["slot_gate_template.py"]
    tree = ast.parse(Path(sgp.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Compare, ast.BinOp)):
            parts = ([node.left, *node.comparators] if isinstance(node, ast.Compare)
                     else [node.left, node.right])
            assert not any(isinstance(x, ast.Constant) and x.value in (2, 2.0)
                           and not isinstance(x.value, bool) for x in parts), ast.dump(node)


def _outside_chip(unit=None):
    m = _chip()
    m["spares"] = {"x": _qubit("x", unit)}
    m["qubits"].update({"q6": "#/spares/x", "q7": "#/spares/x"})
    return m


def test_two_aliases_of_an_outside_object_are_one_qubit():
    m = _outside_chip()
    planned = {}
    (c, e, r), _ = _plan(m, [dict(ROW, qubit="q6", unit=0.2),
                             dict(ROW, qubit="q7", unit=0.5, level=0.9)], planned)
    assert (c, e, r) == ([], {}, [sgt.DUPLICATE_PROBLEM.format(first="q6", second="q7")])
    assert planned == {}
    assert _stored(_outside_chip(0.2), {"q6": "q6", "q7": "q7"}) == {
        "status": "fail", "message": r[0]}


def test_outside_object_has_one_planned_unit_across_gates():
    planned = {}
    m = _outside_chip()
    (c, _, r), _ = _plan(m, [dict(ROW, qubit="q6", unit=0.2)], planned)
    assert r == [] and len(c) == 1
    (c2, e2, r2), _ = _plan(m, [dict(ROW, qubit="q7")], planned)
    assert r2 == [] and c2 == [] and e2["spectators"][0]["unit"] == 0.2
    assert len(planned) == 1


@pytest.mark.parametrize("own", ["q1", "q2"])
def test_alias_of_an_outside_control_or_target_is_refused(own):
    m = _outside_chip(0.2)
    m["qubits"][own] = "#/spares/x"
    (c, e, r), _ = _plan(m, [dict(ROW, qubit="q6")])
    assert (c, e, r) == ([], {}, [sgt.OWN_PROBLEM.format(name="q6")])
    assert _stored(m, {"q6": "q6"}) == {"status": "fail", "message": r[0]}


def test_refusal_never_changes_planned_units():
    planned = {"q4": 0.3}
    (c, e, r), macro = _plan(_chip(), [dict(ROW, unit=0.2, length=18)], planned)
    assert r and c == [] and e == {} and macro == _macro()
    assert planned == {"q4": 0.3}


def test_explicit_unit_equal_to_stored_amplitude_is_reused():
    (c, e, r), _ = _plan(_chip({"q3": 0.2}), [dict(ROW, unit=0.2)])
    assert r == [] and c == []
    assert e["spectators"][0]["unit"] == 0.2
    assert e["spectators"][0]["unit_created"] is False
