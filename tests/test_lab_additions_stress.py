"""Stress-round pins: an agent proposal may not set a calibration value in any
spelling, may not touch the instrument wiring, and no field addition (anyone's)
may write a serialisation key such as ``__class__``."""
from __future__ import annotations

import pytest

from quam_state_manager.core import additions as A

FLAT = "quam.components.pulses.FlatTopGaussianPulse"


def _f(raw, hd=False, kind="plain"):
    return {"raw": raw, "has_default": hd, "kind": kind, "optional": False}


MAN = {"versions": {}, "classes": {FLAT: {"role": "pulse", "origin": "official", "fields": {
    "length": _f("int"), "amplitude": _f("float"), "flat_length": _f("int")}}}}
SPEC = {"qubits": ["q1", "q2"], "qubit_pairs": [["q1", "q2"]],
        "lines": [{"element": q, "line": ln} for q in ("q1", "q2")
                  for ln in ("drive", "flux", "resonator")], "additions": []}


def _field(t, v):
    return {"kind": "field", "target": t, "value": v}


@pytest.mark.parametrize("add", [
    _field("qubits.q1.T1", 1e-5),                                   # number, name not in the list
    _field("qubits.q1.z.joint_offset", 0.1),
    _field("qubits.q1.xy.operations.x180.amplitude", "0.5"),         # numeric text
    _field("qubits.q1.xy.operations.x180.amplitude",
           "#/qubits/q2/xy/operations/x180/amplitude"),             # borrowed by pointer
    _field("ports.mw_outputs.con1.1.1.full_scale_power_dbm", 10),    # instrument wiring
    _field("wiring.qubits.q1.xy.opx_output", "#/ports/x"),
    {"kind": "pulse", "target": "qubits.q1.z.operations", "name": "p", "class": FLAT,
     "fields": {"amplitude": "#/qubits/q2/z/operations/p2/amplitude"}},
], ids=["T1", "offset", "numeric-text", "field-pointer", "ports", "wiring", "pulse-pointer"])
def test_an_agent_cannot_set_a_calibration_value_in_any_spelling(add):
    r = A.lint_proposal(SPEC, add, MAN, author="claude")
    assert not r["ok"], r


def test_a_person_may_still_set_those_fields():
    r = A.lint_proposal(SPEC, _field("qubits.q1.z.joint_offset", 0.1), MAN, author="person")
    assert r["ok"], r


def test_an_agent_may_still_set_a_non_numeric_extras_key():
    r = A.lint_proposal(SPEC, _field("extras.note", "built by the wizard"), MAN, author="claude")
    assert r["ok"], r


@pytest.mark.parametrize("author", ["person", "claude"])
def test_no_field_addition_writes_a_serialisation_key(author):
    r = A.lint_proposal(SPEC, _field("qubits.q1.__class__", "x.Evil"), None, author=author)
    assert not r["ok"] and "__class__" in r["errors"][0], r


# --------------------------------------------------------------- routes

_H = {"Origin": "http://localhost", "Host": "localhost"}
_AG = dict(_H, **{"X-SM-Agent": "claude"})


@pytest.fixture
def client(tmp_path):
    from quam_state_manager.web.app import create_app
    return create_app(testing=True, instance_path=str(tmp_path / "_inst")).test_client()


def test_an_agent_cannot_accept_its_own_proposal(client):
    from quam_state_manager.core import addition_proposals
    inst = client.application.instance_path
    addition_proposals.set_context(inst, SPEC, None)
    card = {"id": "stress_card", "title": "t", "role": "macro_pair", "match": {"fields_all": ["x"]},
            "placement": {"target": "qubit_pairs.<pair>.macros"}}
    res = addition_proposals.propose_card(inst, card, "why", "by_claude")
    pid = res.get("id")
    assert pid, res
    r = client.post(f"/generate/proposals/{pid}/accept", headers=_AG, json={"spec": SPEC})
    assert r.status_code == 403
    assert addition_proposals.get(inst, pid)["status"] == "pending"


def test_an_agent_cannot_rewrite_the_wizard_draft(client):
    r = client.post("/generate/proposals/context", headers=_AG, json={"spec": SPEC})
    assert r.status_code == 403
    assert client.post("/generate/proposals/context", headers=_H, json={"spec": SPEC}).get_json()["ok"]


# --------------------------------------------------------------- pairs never drop silently

def test_a_cz_tunable_pair_without_a_coupler_line_is_refused():
    from quam_state_manager.core.config_generator import validate_spec
    s = {"qubits": ["q1", "q2"], "qubit_pairs": [["q1", "q2"]], "pair_gate": "cz_tunable", "twpas": [],
         "instruments": {"controllers": [{"con": 1, "fems": [{"slot": 1, "fem": "mw"}, {"slot": 2, "fem": "lf"}]}],
                         "opx_plus": [], "octaves": []},
         "lines": [{"element": q, "line": ln, "channel": None} for q in ("q1", "q2")
                   for ln in ("resonator", "drive", "flux")]}
    errs = validate_spec(s)
    assert any("cz_tunable requires a coupler flux line for q1-q2" in e and "leaves this pair out" in e
               for e in errs), errs
    s["lines"].append({"element": "q1-q2", "line": "coupler", "channel": None})
    assert not any("coupler flux line" in e for e in validate_spec(s))
