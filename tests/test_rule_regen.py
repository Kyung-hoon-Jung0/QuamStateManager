"""Re-generate carries addition rules and their instance meta; the recipe never holds them."""
import json
from copy import deepcopy

from quam_state_manager.core import additions, regen_script, regen_spec
from quam_state_manager.core.add_names import unordered_key

RULE = {"id": "r1", "rev": 2, "kind": "pair_gate", "source": "q1-2",
        "gate": {"name": "cz_bm", "macro_class": "pkg.Gate", "as_default": False},
        "items": [{"role": "moving", "pulse_class": "pkg.Pulse", "on_choice": "auto"}],
        "numbers": {"moving.length": 40, "moving.amplitude": 1e-9},
        "source_names": {"moving": "cz_bm_moving_q1_q2"},
        "excluded": [], "reviewed": {}}


def _inst(name, cls="pkg.Pulse", **extra):
    return dict({"kind": "pulse", "target": "qubits.q1.z.operations", "name": name, "class": cls,
                 "fields": {"length": 40}, "rule_id": "r1", "copied_value": {"length": 40},
                 "choice_here": "control"}, **extra)


def _chip(classes):
    return {"qubits": {"q1": {"z": {"operations": {
        n: {"__class__": c, "length": 40} for n, c in classes.items()}}}}}


def _write(tmp_path, adds, rules):
    d = tmp_path / ".regen"
    d.mkdir()
    (d / "generate_spec.json").write_text(json.dumps({"content_hash": "x", "spec": {
        "additions": adds, "addition_rules": rules}}), encoding="utf-8")


def test_round_trip_carries_rules_and_meta_exactly(tmp_path):
    adds = [_inst("cz_bm_moving_q1_q2")]
    _write(tmp_path, adds, [RULE])
    got = regen_spec.sidecar_additions(tmp_path, _chip({"cz_bm_moving_q1_q2": "pkg.Pulse"}))
    assert got == adds
    assert regen_spec.sidecar_rules(tmp_path) == [RULE]
    assert type(got[0]["copied_value"]["length"]) is int


def test_class_change_marks_exactly_that_instance_edited(tmp_path):
    adds = [_inst("a_one"), _inst("a_two")]
    _write(tmp_path, adds, [RULE])
    got = regen_spec.sidecar_additions(tmp_path, _chip({"a_one": "pkg.Other", "a_two": "pkg.Pulse"}))
    assert [a.get("edited") for a in got] == [True, None]


def test_recipe_bytes_do_not_depend_on_rules():
    from tests.test_regen_script import _mini_spec
    base = _mini_spec()
    base["additions"] = [{"kind": "pulse", "target": "qubits.q1.z.operations", "name": "p",
                          "class": "pkg.Pulse", "fields": {"length": 40}}]
    with_rules = deepcopy(base)
    with_rules["addition_rules"] = [RULE]
    with_rules["additions"][0].update(rule_id="r1", copied_value={"length": 40}, choice_here="control",
                                      edited=True)
    assert (regen_script.emit_build_script(base, chip_name="mini")
            == regen_script.emit_build_script(with_rules, chip_name="mini"))


def test_rename_rewrites_rule_keys_and_leaves_q10():
    ids, pairs = ["q1", "q2", "q10"], [["q1", "q2"], ["q10", "q2"]]
    r = deepcopy(RULE)
    r.update(excluded=[unordered_key(["q1", "q2"]), unordered_key(["q10", "q2"])],
             reviewed={unordered_key(["q1", "q2"]): "sig"},
             source_names={"moving": "cz_bm_moving_q1_q2", "high": "cz_q10_q2"})
    out = additions.rename_rules([r], {"q1": "qA"}, ids, pairs)[0]
    assert out["source"] == "qA-2"
    assert out["excluded"] == [unordered_key(["qA", "q2"]), unordered_key(["q10", "q2"])]
    assert list(out["reviewed"]) == [unordered_key(["qA", "q2"])] and out["reviewed"][out["excluded"][0]] == "sig"
    assert out["source_names"] == {"moving": "cz_bm_moving_qA_q2", "high": "cz_q10_q2"}
    assert out["numbers"] == r["numbers"] and r["source"] == "q1-2"


def test_recalibrated_chip_keeps_rules_through_reconstruct(tmp_path):
    from quam_state_manager.core import regenerate
    state = _chip({"p1": "pkg.Pulse", "p2": "pkg.Changed"})
    wiring = {"wiring": {"qubits": {"q1": {}}}}
    d = tmp_path / "chip"
    d.mkdir()
    spec = {"qubits": ["q1"], "addition_rules": [RULE],
            "additions": [_inst("p1"), _inst("p2")]}
    regen_spec.write_spec_sidecar(d, spec, state, wiring)
    state["qubits"]["q1"]["z"]["operations"]["p1"]["length"] = 41   # a recalibration
    (d / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (d / "wiring.json").write_text(json.dumps(wiring), encoding="utf-8")
    rec = regenerate.reconstruct_from_folder(d)
    assert not rec.exact
    assert rec.spec["addition_rules"] == [RULE]
    assert [a.get("edited") for a in rec.spec["additions"]] == [None, True]
    assert [a["name"] for a in rec.spec["additions"]] == ["p1", "p2"]
    assert rec.spec["additions"][0]["fields"]["length"] == 41       # chip value, meta kept
    assert rec.spec["additions"][0]["copied_value"] == {"length": 40}
