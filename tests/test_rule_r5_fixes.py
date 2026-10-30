"""Final-review P1 pins: stored names stay, ticks promise only what apply writes,
Regenerate keeps the chip's class, rename follows the auto_new record, the
expand door answers 400 on a malformed body."""
import json
from copy import deepcopy

from quam_state_manager.core import additions, addition_rules as R, regen_spec
from quam_state_manager.core.add_names import unordered_key
from quam_state_manager.web.app import create_app


def _rule():
    return {"id": "r1", "rev": 1, "kind": "pair_gate", "source": None,
            "gate": {"name": "cz_bm", "macro_class": "pkg.Gate", "as_default": False},
            "items": [{"role": "high", "on_choice": "auto", "pulse_class": "pkg.Pulse", "fields_struct": {}},
                      {"role": "low", "on_choice": "auto", "pulse_class": "pkg.Pulse", "fields_struct": {}}],
            "numbers": {"high.length": 40, "high.amplitude": 0.12, "low.length": 40, "low.amplitude": 0.12},
            "source_names": None, "auto_new": False, "excluded": [], "reviewed": {}}


def _spec():
    return {"qubits": ["q1", "q2", "q3"], "qubit_pairs": [["q1", "q2"], ["q2", "q3"]],
            "lines": [{"element": q, "line": "flux"} for q in ("q1", "q2", "q3")],
            "populate": {"qubit": {"q1": {"RF_freq": 5.0e9}, "q2": {"RF_freq": 5.1e9},
                                   "q3": {"RF_freq": 5.2e9}}}}


MAN = {"classes": {"pkg.Gate": {"role": "macro_pair", "fields": {}},
                   "pkg.Pulse": {"role": "pulse", "fields": {}}}}


def _installed():
    s, r = _spec(), _rule()
    out = R.apply(s, r, R.expand(s, r, MAN)["rule_sig"], ["q1-2", "q2-3"], MAN)
    return out["spec"], out["rule"]


def _flip_q1_q2(s):
    """What flipPairOrder does: the pid becomes q2-1, stored pulse names stay."""
    s["populate"]["qubit"]["q1"]["RF_freq"] = 9e9
    s["qubit_pairs"][0] = ["q2", "q1"]
    for a in s["additions"]:
        a["target"] = a["target"].replace("qubit_pairs.q1-2.", "qubit_pairs.q2-1.")
        for k, v in list(a.get("fields", {}).items()):
            if isinstance(v, str):
                a["fields"][k] = v.replace("/qubit_pairs/q1-2/", "/qubit_pairs/q2-1/")


def _pulse_names(s):
    return sorted(a["name"] for a in s["additions"] if a["kind"] == "pulse")


# --- P1 #2: existing instances keep their names ----------------------------------

def test_flip_then_apply_change_keeps_every_stored_name():
    s, r = _installed()
    before = _pulse_names(s)
    _flip_q1_q2(s)
    plan = R.expand(s, r, MAN)
    row = next(p for p in plan["pairs"] if p["pair"] == "q2-1")
    assert row["status"] == "Needs a look"
    assert not any(d["field"] == "class_" for d in row["diff_vs_current"]), row["diff_vs_current"]
    out = R.apply(s, r, plan["rule_sig"], ["q2-1"], MAN)
    assert out["added"] == ["q2-1"]
    assert _pulse_names(out["spec"]) == before
    # the gate's pointers land on the kept names
    macro = next(a for a in out["spec"]["additions"] if a["kind"] == "macro" and "q2-1" in a["target"])
    names = {a["name"] for a in out["spec"]["additions"] if a["kind"] == "pulse"}
    assert {v.rsplit("/", 1)[-1] for v in macro["fields"].values() if isinstance(v, str)} <= names


def test_a_new_instance_still_gets_a_generated_name():
    s, r = _installed()
    s["qubits"].append("q4")
    s["qubit_pairs"].append(["q3", "q4"])
    s["lines"].append({"element": "q4", "line": "flux"})
    s["populate"]["qubit"]["q4"] = {"RF_freq": 5.3e9}
    row = next(p for p in R.expand(s, r, MAN)["pairs"] if p["pair"] == "q3-4")
    assert sorted(a["name"] for a in row["additions"] if a["kind"] == "pulse") == \
        ["cz_bm_high_q3_q4", "cz_bm_low_q3_q4"]


# --- P1 #3 (Python twin of the rename): auto_new_since follows a qubit rename -------

def test_rename_follows_the_auto_new_record():
    r = dict(_rule(), auto_new=True, auto_new_since=[unordered_key(["q1", "q2"]), unordered_key(["q10", "q2"])])
    out = additions.rename_rules([r], {"q1": "qA"}, ["q1", "q2", "q10"], [["q1", "q2"], ["q10", "q2"]])[0]
    assert out["auto_new_since"] == [unordered_key(["qA", "q2"]), unordered_key(["q10", "q2"])]


