"""A reason a new gate name fixes is marked structurally, never in its words.

The planner (core/cz_gate_build.py) writes every refusal as the plain
sentence people read. Whether it is a name refusal travels in a separate
field, ``name_refusals``: on the plan itself (``_refuse``) and, for the web
doors whose rows copy only the words, on every row with refusals
(``with_name_refusals`` over what ``collect_names`` recorded while the door
ran). No surface rewrites a refusal, so the text a person typed reaches them
byte-identical. The display vectors (tests/fixtures/refusal_display_vectors.json)
are checked here for the agent's answer and in tests/cz_gate_dialog_selfcheck.cjs
for the dialog.
"""
from __future__ import annotations

import io
import json
import tokenize
from pathlib import Path

import pytest

from quam_state_manager.core import cz_gate_build as cgb
from tests.test_cz_gate_build import _chip, _plan
from tests.test_cz_gate_routes import PLAN, _body, chip  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent
VECTORS = json.loads((Path(__file__).parent / "fixtures" / "refusal_display_vectors.json")
                     .read_text(encoding="utf-8"))["reasons"]
A_PLAN = "/api/agent/cz-gate/plan"
AGENT = {"X-SM-Agent": "claude"}


@pytest.mark.parametrize("v", VECTORS, ids=[v["case"] for v in VECTORS])
def test_a_reason_reaches_the_answer_byte_identical(v):
    answer = {"ok": True, "pairs": [{"pair": v["pair"], "ok": False, "refusals": [v["text"]],
                                     "verdict": {"reason": v["text"], "headline": v["text"]}}],
              "error": v["text"], "skipped": [{"pair": v["pair"], "reason": v["text"]}]}
    before = json.dumps(answer, sort_keys=True)
    out = cgb.with_name_refusals(answer, {v["text"]} if v["name"] else set())
    assert json.dumps(answer, sort_keys=True) == before          # the input is not touched
    row = out["pairs"][0]
    assert row["refusals"] == [v["text"]]
    assert row["verdict"] == {"reason": v["text"], "headline": v["text"]}
    assert out["error"] == v["text"] and out["skipped"] == [{"pair": v["pair"], "reason": v["text"]}]
    assert row["name_refusals"] == ([v["text"]] if v["name"] else [])


def test_the_vectors_cover_the_cases_that_once_lost_text():
    texts = [v["text"] for v in VECTORS]
    assert "" in texts
    assert any(": " in v["pair"] for v in VECTORS)
    assert any(not t.isascii() for t in texts) and any(not v["pair"].isascii() for v in VECTORS)
    assert any(t.endswith("name: ") for t in texts)
    assert {"\ufeff", "\x1c", "\t", "\xa0"} <= {c for t in texts for c in t}
    assert "The gate name 'x. name: Foo' must start with cz_. Pick a name like cz_square." in texts
    assert "gate setting name: Type a number." in texts


@pytest.mark.parametrize("name", ["x. name: Foo", "cz_a. name: B", "cz_\u00fc"])
def test_a_typed_name_the_planner_echoes_is_kept_and_marked(name):
    out = _plan(_chip(), name=name)
    assert name in out["error"], out
    assert out["name_refusals"] == [out["error"]]
    if name.isascii():
        assert out["error"] in [v["text"] for v in VECTORS]


def _taken_op():
    s = _chip()
    s["qubits"]["q1"]["z"]["operations"]["cz_new_pulse_q1_q2"] = {"amplitude": 1}
    return _plan(s)


def _taken_gate():
    s = _chip()
    s["qubit_pairs"]["q1-2"]["macros"]["cz_used"] = {}
    return _plan(s, name="cz_used")


@pytest.mark.parametrize("make,words", [
    (lambda: _plan(_chip(), name="bad"), "must start with cz_"),
    (lambda: _plan(_chip(), name="cz_" + "x" * 60), "longer than 64 characters"),
    (_taken_op, "already exists on q1.z"),
    (_taken_gate, "already has a gate named cz_used"),
], ids=["name rule", "operation name too long", "operation exists", "gate exists"])
def test_every_name_producer_marks_its_reason(make, words):
    out = make()
    hit = [r for r in out["refusals"] if words in r]
    assert hit and hit == [r for r in out["name_refusals"] if words in r], out


def test_a_reason_a_name_does_not_fix_is_not_marked():
    s = _chip()
    s["qubits"]["q-1"] = s["qubits"].pop("q1")
    s["qubit_pairs"]["q1-2"]["qubit_control"] = "#/qubits/q-1"
    out = _plan(s)
    assert "contains a character" in out["error"] and out["name_refusals"] == []


def test_no_reason_is_rewritten_anywhere():
    """No strip of an in-text tag is left: no helper, no tag constant, and every
    producer goes through ``name_reason``, which returns the words unchanged."""
    assert cgb.name_reason("name: The x.") == "name: The x."
    assert not hasattr(cgb, "plain_reasons") and not hasattr(cgb, "plain_reason")
    assert not hasattr(cgb, "NAME_TAG")
    src = (ROOT / "quam_state_manager" / "core" / "cz_gate_build.py").read_text(encoding="utf-8")
    assert src.count("name_reason(") == 7      # the def, 5 producers, the capped-name copy
    for rel in ("quam_state_manager/web/agent_api.py", "quam_state_manager/web/cz_gate_sim_routes.py"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        strings = [t.string for t in tokenize.generate_tokens(io.StringIO(text).readline)
                   if t.type == tokenize.STRING and not t.string.lstrip("rbuRBUf").startswith('"""')]
        assert not [s for s in strings if "name: " in s], rel
    js = (ROOT / "quam_state_manager" / "web" / "static" / "cz-gate.js").read_text(encoding="utf-8")
    assert "plainAnswer" not in js and "NAME_TAG" not in js
    assert "j: j || {}" in js


@pytest.mark.parametrize("url,headers", [(PLAN, {}), (A_PLAN, AGENT)], ids=["person", "agent"])
def test_a_door_shows_the_typed_name_and_marks_the_row(chip, url, headers):  # noqa: F811
    j = chip.c.post(url, json=_body(name="x. name: Foo"), headers=headers).get_json()
    row = j["pairs"][0]
    want = "The gate name 'x. name: Foo' must start with cz_. Pick a name like cz_square."
    assert row["refusals"] == [want] and row["name_refusals"] == [want], row
    ok = chip.c.post(url, json=_body(), headers=headers).get_json()["pairs"][0]
    assert ok["ok"] and ok["name_refusals"] == []
