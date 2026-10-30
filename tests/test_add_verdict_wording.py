"""C10: verdicts name their phase and never promise an unrun check."""
from copy import deepcopy
from quam_state_manager.core import add_verdict


def rule():
    return {"id": "r1", "source": None,
            "gate": {"name": "cz_bm", "macro_class": "pkg.Gate"},
            "items": [{"role": "moving", "on_choice": "control", "pulse_class": "pkg.Pulse"}],
            "numbers": {"moving.length": 40, "moving.amplitude": 0.05}}


def spec():
    return {"qubits": ["q1", "q2", "q3"], "qubit_pairs": [["q1", "q2"], ["q2", "q3"]],
            "lines": [{"element": q, "line": "flux"} for q in ("q1", "q2", "q3")]}


def manifest():
    return {"classes": {"pkg.Gate": {"role": "macro_pair", "fields": {}},
                        "pkg.Pulse": {"role": "pulse", "fields": {}}}}


def chip():
    return {"qubits": {q: {"z": {"operations": {}}} for q in ("q1", "q2", "q3")},
            "qubit_pairs": {pid: {"qubit_control": "#/qubits/" + c,
                                   "qubit_target": "#/qubits/" + t, "macros": {}}
                            for pid,c,t in (("q1-2", "q1", "q2"), ("q2-3", "q2", "q3"))}}


def test_phase_wording_and_unknown_checks():
    before = add_verdict.judge_pairs(spec(), [["q1", "q2"]], rule())[0]
    after = add_verdict.judge_pairs(chip(), ["q1-2"], rule())[0]
    assert before["reason"].endswith("by Populate frequency")
    assert after["reason"].endswith("by chip f_01")
    assert before["status"] == after["status"] == "Needs a look"
    assert "checked at Build" in before["reason"]
    assert "checked at Build" not in after["reason"]


def test_chip_lab_check_is_one_batch_and_refusal_is_per_pair():
    calls = []
    def check(writes, *, info):
        calls.append(deepcopy(writes))
        return "the gate code refused this value", {2}
    doc = chip()
    result = add_verdict.judge_pairs(doc, ["q1-2", "q2-3"], rule(), manifest(), lab_check=check)
    assert len(calls) == 1 and len(calls[0]) == 4
    assert [r["status"] for r in result] == ["Ready", "Can't"]
    assert "refused this value" in result[1]["reason"]
    assert not doc["qubit_pairs"]["q1-2"]["macros"]


def test_lab_notes_are_not_ready():
    def check(writes, *, info):
        info["notes"] = ["the environment could not run the gate code"]
    result = add_verdict.judge_pairs(chip(), ["q1-2"], rule(), manifest(), lab_check=check)
    assert result[0]["status"] == "Needs a look"
    assert "could not run" in result[0]["reason"]


def test_spec_missing_channel_is_cant():
    doc = spec()
    doc["lines"] = []
    result = add_verdict.judge_pairs(doc, [["q1", "q2"]], rule(), manifest())[0]
    assert result["status"] == "Can't"
    assert "line" in result["reason"]


def test_chip_adapter_uses_existing_lab_checker_once(monkeypatch):
    from threading import RLock
    from types import SimpleNamespace
    from quam_state_manager.web import routes
    calls = []
    store = SimpleNamespace(merged=chip(), _lock=RLock())
    def check(got_store, writes, **kw):
        assert got_store is store
        calls.append(writes)
    monkeypatch.setattr(routes, "_lab_write_refusal", check)
    rows = routes._pair_addition_verdicts(store, ["q1-2", "q2-3"], rule(), manifest())
    assert len(calls) == 1 and len(calls[0]) == 4
    assert all(r["status"] == "Ready" for r in rows)
