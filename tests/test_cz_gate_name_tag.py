"""The planner's name routing tag: one Python source, one JS twin, one rule.

The shared vectors (tests/fixtures/name_tag_vectors.json) are checked here
against ``cz_gate_build.plain_reason`` / ``plain_reasons`` and in
tests/cz_gate_dialog_selfcheck.cjs against the dialog's ``plainReason`` /
``plainAnswer``, so the two copies cannot drift apart.
"""
from __future__ import annotations

import io
import json
import re
import tokenize
from pathlib import Path

import pytest

from quam_state_manager.core import cz_gate_build as cgb

ROOT = Path(__file__).resolve().parent.parent
VECTORS = json.loads((Path(__file__).parent / "fixtures" / "name_tag_vectors.json").read_text(encoding="utf-8"))


def test_the_vectors_use_the_producers_tag():
    assert VECTORS["tag"] == cgb.NAME_TAG
    assert cgb.name_reason("The x is taken.") == cgb.NAME_TAG + "The x is taken."


@pytest.mark.parametrize("v", VECTORS["reasons"], ids=[v["case"] for v in VECTORS["reasons"]])
def test_a_reason_reads_plainly(v):
    assert cgb.plain_reason(v["text"], v["pairs"]) == v["plain"]


@pytest.mark.parametrize("v", VECTORS["answers"], ids=[v["case"] for v in VECTORS["answers"]])
def test_an_answer_reads_plainly(v):
    assert cgb.plain_reasons(v["answer"]) == v["plain"]


def test_the_plain_answer_leaves_the_input_untouched():
    v = VECTORS["answers"][0]
    before = json.dumps(v["answer"], sort_keys=True)
    cgb.plain_reasons(v["answer"])
    assert json.dumps(v["answer"], sort_keys=True) == before


def test_every_reason_a_name_fixes_is_built_with_the_constant():
    src = (ROOT / "quam_state_manager" / "core" / "cz_gate_build.py").read_text(encoding="utf-8")
    # the tag is spelled once, in NAME_TAG; every producer calls name_reason
    assert src.count('"name: ') == 1 and 'NAME_TAG = "name: "' in src
    assert src.count("name_reason(") >= 6
    merged = {"qubit_pairs": {"q1-2": {"macros": {}}}}
    out = cgb.plan(merged, "q1-2", gate_name="bad", gate_class=sorted(cgb.GATE_CLASSES)[0])
    assert out["refusals"][0].startswith(cgb.NAME_TAG), out


@pytest.mark.parametrize("rel", ["quam_state_manager/web/agent_api.py",
                                 "quam_state_manager/web/cz_gate_sim_routes.py"])
def test_the_doors_import_the_one_helper(rel):
    """An agent or Simulate door strips with ``cz_gate_build.plain_reasons``
    and declares no copy of the tag or its rule."""
    src = (ROOT / rel).read_text(encoding="utf-8")
    assert "cz_gate_build.plain_reasons(" in src
    strings = [t.string for t in tokenize.generate_tokens(io.StringIO(src).readline)
               if t.type == tokenize.STRING]
    assert not [s for s in strings if "name: " in s and not s.lstrip("rbuRBU").startswith('"""')]
    assert not re.search(r"REASON_KEYS|NAME_TAG|_plain_reasons\b", src)


def test_the_js_twin_names_the_same_tag_and_keys():
    js = (ROOT / "quam_state_manager" / "web" / "static" / "cz-gate.js").read_text(encoding="utf-8")
    assert "var NAME_TAG = '%s';" % cgb.NAME_TAG in js
    m = re.search(r"var REASON_KEYS = \{([^}]*)\}", js)
    assert m and set(re.findall(r"(\w+): 1", m.group(1))) == set(cgb.REASON_KEYS)
    assert "cz_gate_build.plain_reasons" in js
