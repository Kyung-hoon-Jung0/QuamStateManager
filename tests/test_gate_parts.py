"""What a CZ gate owns (core.gate_parts): the one set the gate delete and the
check record's content hash read."""
from __future__ import annotations

import copy

from quam_state_manager.core import cz_gate_build as cgb
from quam_state_manager.core import gate_parts as gp
from quam_state_manager.core.pointer_resolver import target_path
from tests.cz_gate_chip import add_gate, chip, reverse, square

M = "qubit_pairs.q1-q2.macros.cz_sq"
OP = "qubits.q1.z.operations.cz_sq_pulse_q1_q2"


def test_macro_owned_gate_owns_its_operation():
    s, plan = add_gate(chip())
    assert plan["direction"] == cgb.MACRO_OWNS
    parts = gp.gate_parts(s, "q1-q2", "cz_sq")
    assert parts["ok"], parts
    assert parts["operations"] == [OP]
    assert parts["pulses"] == [M + ".flux_pulse_qubit"]
    assert parts["paths"] == [M, OP]
    assert not parts["active"] and parts["alias"] is None


def test_two_line_gate_owns_both_operations():
    s, _ = add_gate(chip(), pulses=square(coupler=True))
    parts = gp.gate_parts(s, "q1-q2", "cz_sq")
    assert parts["ok"], parts
    assert parts["operations"] == sorted(
        [OP, "qubit_pairs.q1-q2.coupler.operations.cz_sq_coupler_pulse_q1_q2"])


def test_reverse_direction_gate_owns_the_operation_it_names():
    s, plan = add_gate(reverse(chip()))
    assert plan["direction"] == cgb.OPERATION_OWNS
    parts = gp.gate_parts(s, "q1-q2", "cz_sq")
    assert parts["ok"], parts
    assert parts["operations"] == [OP]
    assert parts["pulses"] == []


def test_a_general_operation_the_macro_names_stays():
    s, _ = add_gate(reverse(chip()))
    s["qubit_pairs"]["q1-q2"]["macros"]["cz_sq"]["flux_pulse_qubit"] = "const"
    s["qubits"]["q1"]["z"]["operations"].pop("cz_sq_pulse_q1_q2")
    parts = gp.gate_parts(s, "q1-q2", "cz_sq")
    assert parts["operations"] == []
    assert parts["kept"] == ["qubits.q1.z.operations.const"]


def test_spectator_operation_by_the_name_rule():
    s, _ = add_gate(chip())
    s["qubits"]["q3"]["z"]["operations"]["cz_sq_spectator_pulse_q1_q2"] = {"amplitude": 0.0}
    parts = gp.gate_parts(s, "q1-q2", "cz_sq")
    assert "qubits.q3.z.operations.cz_sq_spectator_pulse_q1_q2" in parts["operations"]


def test_shared_operation_is_refused_naming_the_other_gate():
    s, _ = add_gate(chip())
    other = copy.deepcopy(s["qubit_pairs"]["q1-q2"]["macros"]["cz_sq"])
    other["flux_pulse_qubit"] = "#/qubits/q1/z/operations/cz_sq_pulse_q1_q2"
    s["qubit_pairs"]["q1-q2"]["macros"]["cz_other"] = other
    parts = gp.gate_parts(s, "q1-q2", "cz_sq")
    assert not parts["ok"]
    assert any("also used by qubit_pairs.q1-q2.macros.cz_other.flux_pulse_qubit" in r
               for r in parts["refusals"]), parts["refusals"]


def test_a_reference_from_outside_is_refused():
    s, _ = add_gate(chip())
    s["qubits"]["q2"]["amp_ref"] = "#/qubit_pairs/q1-q2/macros/cz_sq/flux_pulse_qubit/amplitude"
    parts = gp.gate_parts(s, "q1-q2", "cz_sq")
    assert not parts["ok"]
    assert any("qubits.q2.amp_ref" in r for r in parts["refusals"])


def test_active_gate_carries_the_alias():
    s, _ = add_gate(chip())
    s["qubit_pairs"]["q1-q2"]["macros"]["cz"] = "#./cz_sq"
    parts = gp.gate_parts(s, "q1-q2", "cz_sq")
    assert parts["ok"] and parts["active"]
    assert parts["alias"] == "qubit_pairs.q1-q2.macros.cz"


def test_not_a_gate():
    parts = gp.gate_parts(chip(), "q1-q2", "cz_none")
    assert not parts["ok"] and parts["refusals"]


def test_content_hash_follows_the_operation_too():
    s, _ = add_gate(reverse(chip()))
    h = gp.content_hash(s, "q1-q2", "cz_sq")
    s["qubits"]["q1"]["z"]["operations"]["cz_sq_pulse_q1_q2"]["amplitude"] = 0.2
    assert gp.content_hash(s, "q1-q2", "cz_sq") != h
    assert gp.content_hash(s, "q1-q2", "nope") is None


def test_gate_of_path_for_an_operation_and_a_macro_field():
    s, _ = add_gate(reverse(chip()))
    assert gp.gate_of_path(s, OP) == ("q1-q2", "cz_sq")
    assert gp.gate_of_path(s, M + ".flux_pulse_qubit") == ("q1-q2", "cz_sq")
    assert gp.gate_of_path(s, "qubits.q1.z.operations.const") is None


def test_rename_keeps_pulse_id_equal_to_the_key_and_every_reference_resolves():
    from quam_state_manager.core import regen_merge
    s, _ = add_gate(chip(), pulses=square(coupler=True))
    renamed = regen_merge.rename_source_qubits(s, {}, {"q1": "qa", "q2": "qb"})[0]
    macro = renamed["qubit_pairs"]["q1-q2"]["macros"]["cz_sq"]
    key = macro["flux_pulse_qubit"]["id"]
    assert key == "cz_sq_pulse_qa_qb"
    assert key in renamed["qubits"]["qa"]["z"]["operations"]
    for holder, ref in gp._walk_refs(renamed, ()):
        segs = target_path(renamed, ref, holder)
        node = renamed
        for seg in segs or []:
            if seg == "inferred_id" or seg == "inferred_duration":
                break
            assert isinstance(node, dict) and seg in node, (holder, ref)
            node = node[seg]


def test_any_other_macro_naming_the_operation_is_a_refusal():
    s, _ = add_gate(chip())
    s["qubit_pairs"]["q1-q2"]["macros"]["swap"] = {"__class__": "lab.Swap",
                                                    "flux_pulse": "cz_sq_pulse_q1_q2"}
    s["qubits"]["q3"]["macros"] = {"park": {"pulse_ref": "#/qubits/q1/z/operations/cz_sq_pulse_q1_q2"}}
    parts = gp.gate_parts(s, "q1-q2", "cz_sq")
    assert not parts["ok"]
    text = " ".join(parts["refusals"])
    assert "qubit_pairs.q1-q2.macros.swap.flux_pulse" in text
    assert "qubits.q3.macros.park.pulse_ref" in text


def test_a_plain_name_on_two_lines_of_an_unknown_class_is_refused():
    s = chip()
    s["qubit_pairs"]["q1-q2"]["macros"]["cz_lab"] = {"__class__": "lab.CZX", "flux_pulse": "shared_op"}
    for q in ("q1", "q2"):
        s["qubits"][q]["z"]["operations"]["shared_op"] = {"amplitude": 0.1}
    parts = gp.gate_parts(s, "q1-q2", "cz_lab")
    assert not parts["ok"]
    assert any("qubits.q1.z.operations.shared_op and qubits.q2.z.operations.shared_op" in r
               for r in parts["refusals"])


def test_the_qubit_slot_plays_on_the_moving_line_only():
    s = chip()
    s["qubit_pairs"]["q1-q2"]["moving_qubit"] = "target"
    from quam_state_manager.core import cz_gate_build as cgb2
    m = cgb2._cz_skeleton()
    m["flux_pulse_qubit"] = "cz_y_op"
    s["qubit_pairs"]["q1-q2"]["macros"]["cz_y"] = m
    for q in ("q1", "q2"):
        s["qubits"][q]["z"]["operations"]["cz_y_op"] = {"amplitude": 0.1}
    parts = gp.gate_parts(s, "q1-q2", "cz_y")
    assert parts["operations"] == ["qubits.q2.z.operations.cz_y_op"]


def test_non_dict_shapes_never_raise():
    for bad in ("x", [1], 3, None):
        parts = gp.gate_parts({"qubit_pairs": bad}, "q1-q2", "cz_sq")
        assert not parts["ok"]
        assert gp.gate_of_path({"qubit_pairs": bad}, "qubit_pairs.q1-q2.macros.cz_sq") is None
    assert not gp.gate_parts("x", "p", "g")["ok"]


def _gate_with_op_key():
    s, _ = add_gate(chip())
    return s, "cz_sq_pulse_q1_q2"


def test_a_plain_name_nested_in_the_same_qubit_macro_is_a_refusal():
    for shape, field in (({"pulses": {"main": None}}, "pulses.main"), ({"pulses": [None]}, "pulses.0")):
        s, key = _gate_with_op_key()
        m = copy.deepcopy(shape)
        if isinstance(m["pulses"], dict):
            m["pulses"]["main"] = key
        else:
            m["pulses"][0] = key
        s["qubits"]["q1"]["macros"] = {"x": m}
        parts = gp.gate_parts(s, "q1-q2", "cz_sq")
        assert not parts["ok"]
        assert any("qubits.q1.macros.x." + field in r for r in parts["refusals"]), parts["refusals"]


def test_a_plain_name_on_an_unrelated_owner_or_under_note_does_not_count():
    s, key = _gate_with_op_key()
    s["qubits"]["q3"]["macros"] = {"x": {"pulses": {"main": key}}}
    s["qubit_pairs"]["q2-q3"]["macros"]["other"] = {"pulses": [key]}
    s["qubits"]["q1"]["macros"] = {"y": {"note": key, "id": key}}
    parts = gp.gate_parts(s, "q1-q2", "cz_sq")
    assert parts["ok"], parts["refusals"]


def test_a_reference_in_a_list_deep_in_any_macro_counts():
    s, key = _gate_with_op_key()
    s["qubits"]["q3"]["macros"] = {"x": {"seq": [{"a": "#/qubits/q1/z/operations/" + key}]}}
    parts = gp.gate_parts(s, "q1-q2", "cz_sq")
    assert not parts["ok"]
    assert any("qubits.q3.macros.x.seq.0.a" in r for r in parts["refusals"])


def test_numbers_equal_in_value_hash_equal():
    s, _ = add_gate(chip())
    h = gp.content_hash(s, "q1-q2", "cz_sq")
    p = s["qubit_pairs"]["q1-q2"]["macros"]["cz_sq"]["flux_pulse_qubit"]
    p["length"] = float(p["length"])
    assert gp.content_hash(s, "q1-q2", "cz_sq") == h
    s["qubit_pairs"]["q1-q2"]["macros"]["cz_sq"]["phase_shift_control"] = 0
    assert gp.content_hash(s, "q1-q2", "cz_sq") == h
    s["qubit_pairs"]["q1-q2"]["macros"]["cz_sq"]["phase_shift_control"] = False
    assert gp.content_hash(s, "q1-q2", "cz_sq") != h


def _const_gate():
    s = reverse(chip())
    s["qubit_pairs"]["q1-q2"]["macros"]["cz_old"]["flux_pulse_qubit"] = "const"
    return s


def test_the_hash_covers_a_kept_operation_the_gate_plays():
    s = _const_gate()
    h = gp.content_hash(s, "q1-q2", "cz_old")
    s["qubits"]["q1"]["z"]["operations"]["const"]["amplitude"] = 0.77
    assert gp.content_hash(s, "q1-q2", "cz_old") != h
    parts = gp.gate_parts(s, "q1-q2", "cz_old")
    assert "qubits.q1.z.operations.const" in parts["kept"]
    assert "qubits.q1.z.operations.const" not in parts["paths"]


def test_the_hash_follows_a_reference_to_its_value_not_its_text():
    s, _ = add_gate(chip())
    s["qubits"]["q3"]["shared_amp"] = 0.3
    s["qubit_pairs"]["q1-q2"]["macros"]["cz_sq"]["flux_pulse_qubit"]["amplitude"] = "#/qubits/q3/shared_amp"
    h = gp.content_hash(s, "q1-q2", "cz_sq")
    s["qubits"]["q3"]["shared_amp"] = 0.31
    assert gp.content_hash(s, "q1-q2", "cz_sq") != h
    s["qubits"]["q3"]["shared_amp"] = 0.3
    assert gp.content_hash(s, "q1-q2", "cz_sq") == h


def test_the_hash_ignores_an_unrelated_operation_and_the_rest_of_the_qubit():
    s, _ = add_gate(chip())
    h = gp.content_hash(s, "q1-q2", "cz_sq")
    s["qubits"]["q1"]["z"]["operations"]["const"]["amplitude"] = 0.5
    s["qubits"]["q1"]["f_01"] = 5.1e9
    assert gp.content_hash(s, "q1-q2", "cz_sq") == h


def test_numbers_exact_and_never_raising():
    n = gp._norm_numbers
    assert n(1) == n(1.0) and n(-0.0) == n(0.0) and n(0) == n(-0.0)
    assert n(2**53) != n(2**53 + 1)
    assert n(True) != n(1) and n("1") != n(1)
    assert n(10**400) == ["int", str(10**400)]
    assert n(float("inf")) != n(float("-inf"))
    assert n(0.1) != n(0.10000000000000002)


def test_a_plain_name_under_any_non_descriptive_key_counts():
    s, _ = add_gate(chip())
    s["qubit_pairs"]["q1-q2"]["macros"]["cz_two"] = {"__class__": "lab.G", "flux_gate_op": "cz_sq_pulse_q1_q2"}
    parts = gp.gate_parts(s, "q1-q2", "cz_sq")
    assert not parts["ok"]
    assert any("qubit_pairs.q1-q2.macros.cz_two.flux_gate_op" in r for r in parts["refusals"])
    s["qubit_pairs"]["q1-q2"]["macros"]["cz_two"] = {"__class__": "lab.G", "label": "cz_sq_pulse_q1_q2"}
    assert gp.gate_parts(s, "q1-q2", "cz_sq")["ok"]


def test_a_plain_cz_name_is_the_alias():
    s, _ = add_gate(chip())
    s["qubit_pairs"]["q1-q2"]["macros"]["cz"] = "cz_sq"
    parts = gp.gate_parts(s, "q1-q2", "cz_sq")
    assert parts["active"] and parts["alias"] == "qubit_pairs.q1-q2.macros.cz"
    assert gp.active_gate("q1-q2", s["qubit_pairs"]["q1-q2"]["macros"]) == "cz_sq"
