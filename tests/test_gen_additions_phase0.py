"""Phase-0 fixes of the wizard's lab additions, driven under node + jsdom.

Z1 -- an additions edit (add, edit, remove, the Default select, an accepted
      proposal) is ONE wizard Ctrl+Z step covering spec.additions and
      populate.pairs (gen_additions_undo_selfcheck.cjs).
Z2 -- a qubit rename rewrites spec.additions (target, pointers, name tokens)
      by the rebuild's rules; the wizard's JS and additions.rename_qubits agree.
Z3 -- removing an added gate removes the pulses it plays that nothing else
      plays, after a confirm that states how many.
Skips without node + jsdom.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent


def _run(name: str) -> None:
    if shutil.which("node") is None:
        pytest.skip("node not on PATH")
    r = subprocess.run(["node", str(_ROOT / "tests" / name)], capture_output=True, text=True,
                       encoding="utf-8", cwd=str(_ROOT))
    if r.returncode == 2 and "jsdom not installed" in r.stderr:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, r.stdout + r.stderr
    assert " 0 failed" in r.stdout, r.stdout + r.stderr


def test_z1_additions_edits_are_one_undo_step():
    _run("gen_additions_undo_selfcheck.cjs")


# -- Z2 + C3: a qubit rename rewrites spec.additions -----------------------
import json  # noqa: E402

from quam_state_manager.core import additions  # noqa: E402

_PAIRS = [["q1", "q2"], ["q2", "q3"]]
_QS = ["q1", "q2", "q3", "q10"]


def _rule_instance():
    return [
        {"kind": "pulse", "target": "qubits.q2.z.operations", "name": "cz_bm_high_q2_q3",
         "class": "lab.P", "fields": {"amplitude": 0.1}},
        {"kind": "pulse", "target": "qubits.q3.z.operations", "name": "cz_bm_low_q2_q3",
         "class": "lab.P", "fields": {}},
        {"kind": "macro", "target": "qubit_pairs.q2-3.macros", "name": "cz_bm_q2_q3", "class": "lab.G",
         "fields": {"flux_pulse_high": "#/qubits/q2/z/operations/cz_bm_high_q2_q3",
                    "flux_pulse_low": "#/qubits/q3/z/operations/cz_bm_low_q2_q3",
                    "nested": [{"p": "#../cz_bm_high_q2_q3"}], "plain": "q2"}},
        {"kind": "default_gate", "target": "qubit_pairs.q2-3.macros", "name": "cz", "to": "cz_bm_q2_q3"},
        {"kind": "pulse", "target": "qubits.q10.xy.operations", "name": "x_q10", "class": "lab.P", "fields": {}},
        {"kind": "field", "target": "qubits.q2.extras.note_q2", "value": "q2 free text"},
        {"kind": "field", "target": "extras.ref_q2", "value": "#/qubits/q2/extras/q2"},
        {"kind": "field", "target": "qubit_pairs.q1-2.extras.x", "value": 3},
        {"kind": "pulse", "target": "qubits.q3.xy.operations", "name": "aq2_q2b", "class": "lab.P",
         "fields": {}},
    ]


def test_z2_c3_a_rule_instance_follows_the_rename_whole():
    """C3: target, pointer and the qubit tokens in the names follow together."""
    out = additions.rename_qubits(_rule_instance(), {"q2": "qA"}, _QS, _PAIRS)
    assert out[0]["target"] == "qubits.qA.z.operations" and out[0]["name"] == "cz_bm_high_qA_q3"
    assert out[2]["target"] == "qubit_pairs.qA-3.macros" and out[2]["name"] == "cz_bm_qA_q3"
    assert out[2]["fields"]["flux_pulse_high"] == "#/qubits/qA/z/operations/cz_bm_high_qA_q3"
    assert out[2]["fields"]["nested"] == [{"p": "#../cz_bm_high_qA_q3"}]
    assert out[2]["fields"]["plain"] == "q2"          # a non-pointer value is data, kept
    assert out[3]["to"] == "cz_bm_qA_q3" and out[3]["target"] == "qubit_pairs.qA-3.macros"
    assert out[4]["name"] == "x_q10"                  # q2 inside no token, q1 inside q10 kept
    assert out[5]["target"] == "qubits.qA.extras.note_q2" and out[5]["value"] == "q2 free text"
    assert out[6]["value"] == "#/qubits/qA/extras/q2"
    assert out[7]["target"] == "qubit_pairs.q1-A.extras.x"
    assert out[8]["name"] == "aq2_q2b"                # q2 only as part of a word: kept
    assert _rule_instance()[0]["name"] == "cz_bm_high_q2_q3"   # the input is not mutated


def test_z2_c3_the_renamed_instance_still_lands_on_a_chip():
    """The renamed additions placed on a chip in the NEW ids: every pulse and
    macro is added and every pointer resolves (the offline build check)."""
    adds = [a for a in additions.rename_qubits(_rule_instance()[:4], {"q2": "qA"}, _QS, _PAIRS)]
    state = {"qubits": {q: {"id": q, "z": {"operations": {}}} for q in ("q1", "qA", "q3")},
             "qubit_pairs": {"qA-3": {"qubit_control": "#/qubits/qA", "qubit_target": "#/qubits/q3",
                                      "macros": {}}}}
    spec = {"qubits": ["q1", "qA", "q3"], "qubit_pairs": [["q1", "qA"], ["qA", "q3"]],
            "lines": [{"line": "flux", "element": q} for q in ("q1", "qA", "q3")], "additions": adds}
    rep = additions.apply_additions(state, spec, None)
    assert not rep["refused"], rep["refused"]
    assert len(rep["added"]) == 4
    m = state["qubit_pairs"]["qA-3"]["macros"]
    assert m["cz_bm_qA_q3"]["flux_pulse_high"] == "#/qubits/qA/z/operations/cz_bm_high_qA_q3"
    assert "cz_bm_high_qA_q3" in state["qubits"]["qA"]["z"]["operations"]


def test_z2_without_the_rename_the_pointer_would_dangle():
    """The failure C3 names: renaming only the target leaves the macro's
    pointer at a name nothing carries (kept as the reason the rule exists)."""
    adds = _rule_instance()[:3]
    adds[0]["target"] = "qubits.qA.z.operations"
    names = {f"#/qubits/qA/z/operations/{adds[0]['name']}"}
    assert adds[2]["fields"]["flux_pulse_high"] not in names


_CASES = [
    {"adds": _rule_instance(), "map": {"q2": "qA"}, "qubits": _QS, "pairs": _PAIRS},
    {"adds": _rule_instance(), "map": {"q2": "q3", "q3": "q2"}, "qubits": _QS, "pairs": _PAIRS},
    {"adds": _rule_instance(), "map": {"q1": "q0", "q2": "q1", "q3": "q2", "q10": "q9"},
     "qubits": _QS, "pairs": _PAIRS},
    {"adds": _rule_instance(), "map": {"q2": "q2_b"}, "qubits": _QS + ["q2_b2"], "pairs": _PAIRS},
    {"adds": _rule_instance(), "map": {"q2": "qB7"}, "qubits": _QS, "pairs": _PAIRS},
    {"adds": _rule_instance(), "map": {}, "qubits": _QS, "pairs": _PAIRS},
    {"adds": [{"kind": "pulse", "target": "qubits.qA1.xy.operations", "name": "x_qA1_qA10",
               "fields": {"p": "#/qubit_pairs/qA1-A10/coupler/operations/c_qA1"}}],
     "map": {"qA1": "qZ1"}, "qubits": ["qA1", "qA10"], "pairs": [["qA1", "qA10"]]},
]


def test_z2_the_wizard_and_the_rebuild_rename_alike(tmp_path):
    """Parity: GenAdditions.renameQubits (what the wizard runs) == additions.rename_qubits
    (the regen rules) on every case."""
    if shutil.which("node") is None:
        pytest.skip("node not on PATH")
    p = tmp_path / "cases.json"
    p.write_text(json.dumps(_CASES), encoding="utf-8")
    r = subprocess.run(["node", str(_ROOT / "tests" / "gen_additions_rename_selfcheck.cjs"),
                        "parity", str(p)], capture_output=True, text=True, encoding="utf-8")
    if r.returncode == 2 and "jsdom not installed" in r.stderr:
        pytest.skip("jsdom not installed")
    assert r.returncode == 0, r.stderr
    js = json.loads(r.stdout)
    py = [additions.rename_qubits(c["adds"], c["map"], c["qubits"], c["pairs"]) for c in _CASES]
    for i, (a, b) in enumerate(zip(js, py)):
        assert a == b, (i, a, b)


def test_z2_the_wizard_rename_rewrites_the_additions():
    _run("gen_additions_rename_selfcheck.cjs")


# -- Z3: removing a gate removes the pulses it created ---------------------

def test_z3_a_gate_takes_its_pulses_and_says_how_many():
    _run("gen_additions_gate_remove_selfcheck.cjs")
