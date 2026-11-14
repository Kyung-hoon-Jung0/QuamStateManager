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
    assert any("also used by the gate cz_other of pair q1-q2" in r
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
