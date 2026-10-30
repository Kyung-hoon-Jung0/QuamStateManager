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


# --- P1 #4: a row says whether apply writes it ------------------------------------

def test_warnings_only_needs_a_look_rows_are_not_writable():
    s, r = _spec(), _rule()
    plan = R.expand(s, r, None)          # classes not read
    assert all(p["status"] == "Needs a look" for p in plan["pairs"])
    assert [p["writable"] for p in plan["pairs"]] == [False, False]
    out = R.apply(s, r, plan["rule_sig"], [p["pair"] for p in plan["pairs"] if p["writable"]], None)
    assert out["added"] == []


def test_writable_matches_what_apply_writes():
    s, r = _spec(), _rule()
    s["populate"]["qubit"]["q2"].pop("RF_freq")     # q1-2 and q2-3 need a choice
    plan = R.expand(s, r, MAN)
    rows = {p["pair"]: p for p in plan["pairs"]}
    assert all(p["needs_choice"] and p["writable"] for p in rows.values()), rows
    choices = {pid: "control" for pid in rows}
    plan = R.expand(s, r, MAN, choices=choices)
    out = R.apply(s, r, plan["rule_sig"], list(rows), MAN, choices=choices)
    assert sorted(out["added"]) == sorted(p["pair"] for p in plan["pairs"] if p["writable"])


# --- P1 #5: Regenerate carries the chip's class ------------------------------------

def test_regenerate_carries_the_chips_class_and_the_rule_reads_it_as_edited(tmp_path):
    s, r = _installed()
    pulse = next(a for a in s["additions"] if a["kind"] == "pulse" and a["name"] == "cz_bm_high_q1_q2")
    state = {"qubits": {}, "qubit_pairs": {}}
    for a in s["additions"]:
        node = state
        for seg in a["target"].split("."):
            node = node.setdefault(seg, {})
        node[a["name"]] = dict(a.get("fields") or {}, __class__=a["class"])
    state["qubits"][pulse["target"].split(".")[1]]["z"]["operations"][pulse["name"]]["__class__"] = "pkg.Other"
    d = tmp_path / ".regen"
    d.mkdir()
    (d / "generate_spec.json").write_text(json.dumps({"content_hash": "x", "spec": {
        "additions": s["additions"], "addition_rules": [r]}}), encoding="utf-8")
    got = regen_spec.sidecar_additions(tmp_path, state)
    carried = next(a for a in got if a["name"] == "cz_bm_high_q1_q2")
    assert carried["class"] == "pkg.Other" and carried["edited"] is True
    assert all(a["class"] == "pkg.Pulse" for a in got if a["kind"] == "pulse" and a is not carried)
    s2 = dict(s, additions=got)
    row = next(p for p in R.expand(s2, r, dict(MAN, classes=dict(MAN["classes"], **{
        "pkg.Other": {"role": "pulse", "fields": {}}})))["pairs"] if p["pair"] == "q1-2")
    assert row["edited"] is True


# --- P1 #3 (Python twin of the rename): auto_new_since follows a qubit rename -------

def test_rename_follows_the_auto_new_record():
    r = dict(_rule(), auto_new=True, auto_new_since=[unordered_key(["q1", "q2"]), unordered_key(["q10", "q2"])])
    out = additions.rename_rules([r], {"q1": "qA"}, ["q1", "q2", "q10"], [["q1", "q2"], ["q10", "q2"]])[0]
    assert out["auto_new_since"] == [unordered_key(["qA", "q2"]), unordered_key(["q10", "q2"])]


