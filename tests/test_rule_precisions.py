"""C11: conflict, identity, copying and same-name provenance."""
from copy import deepcopy
from quam_state_manager.core import add_verdict
from quam_state_manager.core.add_names import unordered_key
from tests.test_add_verdict_wording import rule, spec, manifest, chip


def test_equal_moving_choices_do_not_conflict_but_different_ones_do():
    doc = spec()
    doc["populate"] = {"pairs": {"q1-q2": {"moving_qubit": "control"}}}
    same = add_verdict.judge_pairs(doc, [["q1", "q2"]], rule(), manifest())[0]
    assert same["status"] == "Ready", same
    different = rule()
    different["items"][0]["on_choice"] = "target"
    refused = add_verdict.judge_pairs(doc, [["q1", "q2"]], different, manifest())[0]
    assert refused["status"] == "Can't"
    assert "another rule sets the moving qubit to q1" in refused["reason"]


def test_flip_preserves_excluded_identity_and_lexical_order():
    assert unordered_key(["q2", "q10"]) == "10-2"
    excluded = {unordered_key(["qA1", "qB2"])}
    assert unordered_key(["qB2", "qA1"]) in excluded
    assert unordered_key("q1-2") == unordered_key(["q2", "q1"])


def test_later_apply_copies_current_source_numbers():
    doc, r = spec(), rule()
    r["source"] = "q1-2"
    doc["additions"] = [
        {"kind": "pulse", "target": "qubits.q1.z.operations", "name": "old_pulse",
         "class": "pkg.Pulse", "fields": {"length": 72, "amplitude": 0.23}},
        {"kind": "macro", "target": "qubit_pairs.q1-2.macros", "name": "cz_bm",
         "class": "pkg.Gate", "fields": {"flux_pulse_qubit": "#/qubits/q1/z/operations/old_pulse"}}]
    added = add_verdict.judge_pairs(doc, [["q2", "q3"]], r, manifest())[0]
    assert added["numbers"] == {"moving.length": 72, "moving.amplitude": 0.23}
    pulse = added["additions"][0]
    assert pulse["copied_value"] == {"length": 72, "amplitude": 0.23}
    assert pulse["fields"]["amplitude"] == 0.23
    r["source"] = None
    assert add_verdict.current_numbers(doc, r) == r["numbers"]


def test_same_name_from_elsewhere_is_cant_only_this_rule_or_source_is_already():
    doc, r = spec(), rule()
    a = {"kind": "macro", "target": "qubit_pairs.q1-2.macros", "name": "cz_bm",
         "class": "pkg.Gate", "fields": {}, "rule_id": "other"}
    doc["additions"] = [a]
    got = add_verdict.judge_pairs(doc, [["q1", "q2"]], r, manifest())[0]
    assert got["status"] == "Can't" and "a gate named cz_bm already exists" in got["reason"]
    a["rule_id"] = r["id"]
    assert add_verdict.judge_pairs(doc, [["q1", "q2"]], r, manifest())[0]["status"] == "Already has it"

    a["rule_id"] = "other"
    r["source"] = "q1-2"
    assert add_verdict.judge_pairs(doc, [["q1", "q2"]], r, manifest())[0]["status"] == "Already has it"


def test_current_chip_source_values_follow_number_pointers():
    doc, r = chip(), rule()
    r["source"] = "q1-2"
    doc["qubits"]["q1"]["z"]["operations"]["stored"] = {
        "amplitude": 0.31, "length": "#./duration", "duration": 64}
    doc["qubit_pairs"]["q1-2"]["macros"]["cz_bm"] = {
        "flux_pulse_qubit": "#/qubits/q1/z/operations/stored"}
    numbers = add_verdict.current_numbers(doc, r)
    assert numbers["moving.amplitude"] == 0.31
    assert numbers["moving.length"] == 64

