"""QA datasets-r2-33 -- ``id>=N`` / ``id<=N`` / ``id=N`` compare the RUN id.

They used to route to the param facet, whose key matches by substring, so
``id`` hit ``target_peak_width`` / ``idle_time`` / ``load_data_id``:
``id>=4100 id<=4105`` kept #3281 (3e6 >= 4100 on one key, 16 <= 4105 on
another) and none of #4100-#4105, and ``id=4113`` found nothing. The
Datasets box (dataset-virtual.js) is pinned to the SAME answers over the same
rows in tests/search_grammar_selfcheck.cjs section 6b; this is the sidebar
twin (routes._parse_tree_query / _entry_matches) plus the typeahead's insert
rule, which must not offer a bare ``id=...`` that would now mean the run id.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from quam_state_manager.core.param_vocab import insert_token
from quam_state_manager.web.routes import _entry_matches, _parse_tree_query

_ROOT = Path(__file__).resolve().parents[1]
_PM = {"target_peak_width": 3e6, "idle_time": 16, "load_data_id": 20}
_IDS = [4100, 4103, 4105, 4113, 4200, 3281]


def _entries():
    return [SimpleNamespace(run_id=i, experiment_name=f"exp{i}",
                            date_str="2026-09-01", status="finished",
                            qubits=["q1"], qubit_pairs=[], filter_params=_PM)
            for i in _IDS]


def _sel(q):
    conds = _parse_tree_query(q)
    return sorted(e.run_id for e in _entries() if _entry_matches(e, conds))


# the same tokens and the same expected counts as selfcheck section 6b
CASES = [
    ("id>=4100 id<=4105", [4100, 4103, 4105]),
    ("id>=4100", [4100, 4103, 4105, 4113, 4200]),
    ("id=4113", [4113]),
    ("id=4105..4100", [4100, 4103, 4105]),
    ("id>4105", [4113, 4200]),
    ("id<4100", [3281]),
    ("ID>=4200", [4200]),
    ("-id>=4100", [3281]),
    ("id:>=4113", [4113, 4200]),
    ("id:41", [4100, 4103, 4105, 4113]),
    ("p:id>=4100", sorted(_IDS)),
    ("id>=4100 | id=3281", sorted(_IDS)),
]


@pytest.mark.parametrize("q,want", CASES)
def test_the_sidebar_twin_reads_the_run_id(q, want):
    assert _sel(q) == want


def test_a_bare_id_comparison_parses_to_the_id_field():
    c = _parse_tree_query("id>=4100")
    assert [x["field"] for x in c] == ["id"]
    assert c[0]["op"] == ">=" and c[0]["wnum"] == 4100


def test_a_non_integer_id_comparison_keeps_its_old_meaning():
    # `id=abc` is not a run-id comparison -> still the param facet
    assert [x["field"] for x in _parse_tree_query("id=abc")] == ["param"]
    # a range needs `=`; `id>=1..2` is not a run-id comparison either
    assert _parse_tree_query("id>=1..2")[0]["field"] != "id"


def test_the_typeahead_scopes_a_param_literally_named_id():
    assert insert_token("id", "5") == "p:id=5"
    assert insert_token("ID", "5", op=">=") == "p:ID>=5"
    # ...and that spelling does reach the parameter
    assert _parse_tree_query("p:id=5")[0]["field"] == "param"
    # keys that merely CONTAIN id keep the bare form
    assert insert_token("idle_time", "16") == "idle_time=16"
    assert insert_token("load_data_id", "20") == "load_data_id=20"


def _js_insert_fn():
    js = (_ROOT / "quam_state_manager" / "web" / "static"
          / "sidebar-typeahead.js").read_text(encoding="utf-8")
    start = js.index("window.__paramVocabInsert = function")
    i = js.index("{", start)
    depth, j = 0, i
    while True:
        if js[j] == "{":
            depth += 1
        elif js[j] == "}":
            depth -= 1
            if depth == 0:
                break
        j += 1
    return js[start:j + 1]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_the_js_insert_rule_agrees():
    asks = [["id", "5", "="], ["ID", "5", ">="], ["idle_time", "16", "="],
            ["load_data_id", "20", "<="], ["Id", "7", "<"]]
    script = ("var window = {};\n" + _js_insert_fn() + ";\n"
              "const a = JSON.parse(process.argv[1]);\n"
              "console.log(JSON.stringify(a.map(x => "
              "window.__paramVocabInsert(x[0], x[1], x[2]))));")
    r = subprocess.run(["node", "-e", script, json.dumps(asks)],
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    got = json.loads(r.stdout)
    assert got == [insert_token(k, v, op=o) for k, v, o in asks]
    assert got[0] == "p:id=5"
