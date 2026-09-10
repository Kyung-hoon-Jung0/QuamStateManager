"""Type `m` and see the parameters that start with m.

Customer, 2026-09-10:

  "사이드바 검색에서, 파라미터를 입력할때, 예컨데, 사람은, m, mu, mul, mult,
   multi, multip... 이렇게 순차적으로 타이핑하잖아? 이것을 우리 youtube나
   vscode에서 자동완성 후보군 보여주는 것처럼 즉각적으로, m을 치면 m으로
   시작하는 parameter들이 쭉 아래로 팝업되게 할수있어?"
  "live edit 하고 json tree view에서 -- 이게 진짜 SM의 가치인데 -- 사람이
   ampl까지만 치면 amplitude에 관련된 json key들이 모두 다 뜰수있게."

MEASURED, and it is what decided the design: the whole parameter vocabulary on
the customer's archive (1,766 runs, 210 keys, every distinct value and count) is
16.7 KB and takes 4.5 ms to build. At 5,000 runs it is 12.3 KB / 27.7 ms; at
20,000 runs, 12.9 KB / 139 ms. The payload is FLAT because a vocabulary is not
an index -- ten times the runs is not ten times the words. So it ships once per
workspace version and a keystroke costs no request at all.

Same shape for the chip: a real 21Q chip grown to 50Q has 31,025 leaves but only
460 distinct path segments (7.6 KB); at 100Q, 56,725 leaves and 560 segments
(8.6 KB). And the vocabulary is DERIVED from the chip, never listed here, which
is what makes a key a lab added by hand appear the day they open it -- measured:
two different 21Q chips carry 402 and 288 segments.

What is pinned here is the part that is a decision rather than a rendering: that
every suggestion is a query that finds something, that the scan says where each
token is without changing what the tokens are, and that the route is cheap.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.core import param_vocab as pv
from quam_state_manager.core.search_query import (
    caret_span, scoped_spans, scoped_tokens)
from quam_state_manager.web.app import create_app

_ROOT = Path(__file__).resolve().parent.parent
_STATIC = _ROOT / "quam_state_manager" / "web" / "static"


class _Entry:
    """The fields ``routes._entry_matches`` reads. Only ``filter_params``
    carries anything: a suggestion must match on the PARAM axis alone, never by
    accidentally appearing in a name, a date or a status."""

    def __init__(self, params):
        self.filter_params = params
        self.experiment_name = ""
        self.date_str = ""
        self.status = ""
        self.run_id = None
        self.qubits = []
        self.qubit_pairs = []


class _Group:
    def __init__(self, entries):
        self.entries = entries


def _tree(*param_dicts):
    return {"root": [_Group([_Entry(p) for p in param_dicts])]}


# ── the scan grew offsets and did not change ───────────────────────────────
class TestTheScanSaysWhereWithoutSayingAnythingElse:
    CORPUS = [
        "", "   ", "multiplexed=true", "q2,q5", "q2, q5", 'name:"a, b"',
        'x"y z"w', 'a"b c', "rabi | ramsey", "a  b", "a\x0bb", "a\x85b",
        "-p:reset=active", '"k=a b"', "a,,b", '""', 'a""b',
    ]

    # What the scan produced BEFORE it learned offsets, transcribed by hand.
    # This is the reference, because `routes._tokenize_query` is now a call to
    # `scoped_tokens` -- comparing the two would compare a function with itself
    # and could never fail, which is precisely what three mutations proved when
    # this pin was written that way.
    GOLDEN = {
        "": [], "   ": [],
        "multiplexed=true": ["multiplexed=true"],
        "q2,q5": ["q2", "q5"], "q2, q5": ["q2", "q5"],
        'name:"a, b"': ["name:a, b"],
        'x"y z"w': ["xy zw"], 'a"b c': ["ab c"],
        "rabi | ramsey": ["rabi", "|", "ramsey"],
        "a  b": ["a", "b"], "a\x0bb": ["a", "b"], "a\x85b": ["a", "b"],
        "-p:reset=active": ["-p:reset=active"],
        '"k=a b"': ["k=a b"],
        "a,,b": ["a", "b"], '""': [], 'a""b': ["ab"],
    }

    def test_the_tokens_are_the_ones_the_scan_always_produced(self):
        """An independent golden, not a comparison with itself."""
        for q, want in self.GOLDEN.items():
            assert scoped_tokens(q) == want, (q, scoped_tokens(q), want)

    def test_and_routes_still_answers_with_exactly_those(self):
        """`_tokenize_query` is a call to `scoped_tokens` now -- this asserts
        the wrapper is still wired, which the golden above cannot."""
        from quam_state_manager.web import routes
        for q, want in self.GOLDEN.items():
            assert routes._tokenize_query(q) == want, q

    def test_a_span_addresses_the_raw_text(self):
        """The span covers the quote characters the token text drops, because a
        caller splicing into an input needs offsets into what the user SEES."""
        spans = scoped_spans('x"y z"w')
        assert spans == [(0, 7, "xy zw")]
        spans = scoped_spans("rabi ramsey")
        assert spans == [(0, 4, "rabi"), (5, 11, "ramsey")]
        for q in self.CORPUS:
            for start, end, tok in scoped_spans(q):
                assert 0 <= start < end <= len(q)
                assert q[start:end].replace('"', "") == tok

    def test_the_caret_names_the_token_being_finished(self):
        t = "rabi multiplexed=tr"
        assert caret_span(t, 0) is None            # nothing typed yet
        assert caret_span(t, 5) is None            # just after the separator
        assert caret_span(t, 1) == (0, 4, "rabi")
        assert caret_span(t, 4) == (0, 4, "rabi")
        assert caret_span(t, len(t)) == (5, 19, "multiplexed=tr")

    def test_an_open_quote_refuses(self):
        """Past an open quote the scan finds no separator, so the span runs to
        the end of the box; splicing into it would rewrite characters the user
        does not think they are inside."""
        assert caret_span('a "b c', 5) is None
        assert caret_span('a "b c" d', 5) is None      # still inside
        # …and once the quote closes, the caret is answerable again
        assert caret_span('"b" x', 5) == (4, 5, "x")
        # inside the quoted run, the quote is still open at this caret
        assert caret_span('"b c" ', 4) is None


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_the_two_languages_scan_alike():
    """The JS twin is a SEPARATE implementation, so it is a real reference --
    and it is the one that matters, because the browser decides which token the
    caret is in and the server decides what that token means. `\x0b` and
    `\x85` are in the corpus deliberately: `/\s/` and `str.isspace()` disagree
    about U+0085, so a regex on the JS side would split a pasted query
    differently on the two sides of the wire."""
    corpus = list(TestTheScanSaysWhereWithoutSayingAnythingElse.GOLDEN)
    script = (
        "const fs=require('fs');"
        "const src=fs.readFileSync(%r,'utf8');"
        "const w={}; new Function('window',src).call(w,w);"
        "const q=JSON.parse(process.argv[1]);"
        "console.log(JSON.stringify(q.map(function(s){"
        "  return [w.SearchQuery.scopedTokens(s), w.SearchQuery.caretSpan(s, s.length)];})));"
        % str(_STATIC / "search-query.js")
    )
    r = subprocess.run(["node", "-e", script, json.dumps(corpus)],
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    got = json.loads(r.stdout)
    for q, (toks, span) in zip(corpus, got):
        assert toks == scoped_tokens(q), (q, toks, scoped_tokens(q))
        py = caret_span(q, len(q))
        assert (list(py) if py else None) == span, (q, py, span)


# ── every suggestion is a query that finds something ───────────────────────
class TestASuggestionIsAQueryThatWorks:
    def test_the_offer_routes_to_a_param_condition(self):
        """The load-bearing one. A suggestion that falls through to free text
        matches nothing (a bare param name is pinned at 0 hits on the real
        archive in test_sidebar_param_search), so a typeahead that offered one
        would be worse than no typeahead."""
        from quam_state_manager.web import routes
        awkward = {
            "multiplexed": True, "reset_type": "active_gef", "num_shots": 2000,
            "_leading": 1, "9digit": 2, "spacey": "a b", "commay": "a,b",
            "sci": 1e-05, "neg": -3, "flt": 0.5, "long": "x" * 39,
        }
        payload = pv.to_payload(pv.build_vocab(_tree(awkward)))
        offered = [(k["k"], v) for k in payload["keys"] for v, _n in k["v"]]
        assert offered, "the fixture offered nothing"
        for key, val in offered:
            tok = pv.insert_token(key, val)
            assert tok is not None, (key, val)
            conds = routes._parse_tree_query(tok)
            assert len(conds) == 1, (tok, conds)
            assert conds[0]["field"] == "param", (tok, conds)

    def test_the_offer_actually_matches_the_run_it_came_from(self):
        from quam_state_manager.web import routes
        params = {"multiplexed": True, "reset_type": "active", "spacey": "a b",
                  "sci": 1e-05, "_leading": 7}
        entry = _Entry(params)
        payload = pv.to_payload(pv.build_vocab(_tree(params)))
        for k in payload["keys"]:
            for val, _n in k["v"]:
                tok = pv.insert_token(k["k"], val)
                conds = routes._parse_tree_query(tok)
                assert routes._entry_matches(entry, conds), (tok, params)

    def test_the_grammar_cannot_carry_a_quote_so_it_is_not_offered(self):
        """The tokenizer consumes `"` with no escape, so `a"b` would be searched
        as `ab`. Refused and COUNTED, never quietly dropped."""
        payload = pv.to_payload(pv.build_vocab(_tree({"k": 'a"b', "ok": "x"})))
        offered = [(d["k"], v) for d in payload["keys"] for v, _ in d["v"]]
        assert ("ok", "x") in offered
        assert not any(k == "k" and '"' in v for k, v in offered)
        assert payload["omitted"] >= 1

    def test_a_value_the_python_side_spells_differently(self):
        """`str(1e-05)` is '1e-05' in Python and '0.00001' in JavaScript
        (measured, both). The vocabulary is built server-side for exactly this
        reason: a client-built one would offer a value the server can never
        match."""
        assert pv.param_norm(1e-05) == "1e-05"
        assert pv.param_norm(True) == "true" and pv.param_norm(False) == "false"
        assert pv.param_norm("ACTIVE") == "active"

    def test_insert_token_refuses_what_it_cannot_express(self):
        assert pv.insert_token("k", 'a"b') is None
        assert pv.insert_token('a"b', "x") is None
        assert pv.insert_token("k=x", "1") is None
        assert pv.insert_token("k:x", "1") is None
        assert pv.insert_token("k", "") is None
        # …and quotes the whole token when a separator would split it
        assert pv.insert_token("k", "a b") == '"k=a b"'
        assert pv.insert_token("k", "a,b") == '"k=a,b"'
        # a key the BARE form cannot carry takes the scope path
        assert pv.insert_token("_lead", "1") == "p:_lead=1"
        assert pv.insert_token("9dig", "1") == "p:9dig=1"
        assert pv.insert_token("ok", "1") == "ok=1"


# ── the route ──────────────────────────────────────────────────────────────
class TestTheRouteIsCheap:
    @pytest.fixture
    def client(self, tmp_path):
        state = {"qubits": {"q1": {"id": "q1", "f_01": 5e9}}, "qubit_pairs": {}}
        (tmp_path / "state.json").write_text(json.dumps(state), encoding="utf-8")
        (tmp_path / "wiring.json").write_text(
            json.dumps({"wiring": {}, "network": {"host": "x"}}), encoding="utf-8")
        app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
        c = app.test_client()
        c.post("/load", data={"folder": str(tmp_path)})
        return c

    def test_it_answers_json_with_a_version(self, client):
        r = client.get("/workspace/param-vocab")
        assert r.status_code == 200
        d = r.get_json()
        assert "v" in d and "keys" in d and "n_runs" in d and "hydrating" in d
        assert r.headers.get("Cache-Control") == "no-store"

    def test_the_same_version_is_a_204(self, client):
        d = client.get("/workspace/param-vocab").get_json()
        assert client.get("/workspace/param-vocab?v=%s" % d["v"]).status_code == 204
        assert client.get("/workspace/param-vocab?v=999999").status_code == 200

    def test_it_never_rescans(self):
        """`/workspace/tree` and `/workspace/tree/poll` own the staleness probe.
        A third caller would re-stat every root's spine on a request whose whole
        job is to be cheap."""
        src = (_ROOT / "quam_state_manager" / "web" / "routes.py").read_text(encoding="utf-8")
        i = src.index("def workspace_param_vocab(")
        body = src[i:src.index("\n@bp.route", i)]
        # The docstring EXPLAINS why it does not rescan, so strip it before
        # looking for the call — a pin that trips on its own rationale is a pin
        # nobody can leave a comment near.
        code = "\n".join(ln for ln in body.splitlines()
                         if not ln.lstrip().startswith("#"))
        q = code.find('"""')
        if q >= 0:
            code = code[:q] + code[code.find('"""', q + 3) + 3:]
        assert "rescan_if_stale" not in code, code
        assert "ws.version" in code


# ── the widget is one widget ───────────────────────────────────────────────
class TestOneWidgetThreeConsumers:
    def test_the_three_boxes_share_one_implementation(self):
        js = (_STATIC / "sidebar-typeahead.js").read_text(encoding="utf-8")
        assert "window.Typeahead = (function" in js
        for consumer, box in (("SidebarTypeahead", "sidebar-filter-input"),
                              ("BulkTypeahead", "bulk-search"),
                              ("TreeTypeahead", "explorer-search")):
            assert ("window." + consumer) in js, consumer
            assert ("'" + box + "'") in js, box
        # …and each one attaches through the SAME door
        assert js.count("window.Typeahead.attach(") == 3

    def test_the_plain_boxes_do_not_use_the_param_grammar(self):
        """Live Edit and the Json Tree search AND-ed words; classifying their
        tokens with the sidebar's `key=value`/scope rules would refuse an
        ordinary word the moment it held a colon."""
        js = (_STATIC / "sidebar-typeahead.js").read_text(encoding="utf-8")
        assert "function classifyPlain(" in js
        assert "(cfg.plain ? classifyPlain : classify)" in js
        assert js.count("plain: true") == 2

    def test_a_tree_swap_does_not_dismiss_it(self):
        """The sidebar box fires /workspace/tree on a 250 ms debounce, so every
        keystroke swaps #sidebar-tree. A blanket close on htmx swaps dismissed
        the panel ~250 ms after it opened, every time -- measured in real
        Chrome, where the panel was correct and invisible."""
        js = (_STATIC / "sidebar-typeahead.js").read_text(encoding="utf-8")
        assert "htmx:beforeSwap" not in js
        i = js.index("htmx:afterSwap")
        block = js[i:i + 900]
        assert "_open.input" in block and "_anchorPopover" in block
        # …and only the instance whose box is open answers: three instances
        # share this handler, and without the guard the Live-Edit one closed a
        # panel the sidebar had opened.
        assert "_open.input.id !== inputId" in block

    def test_it_is_a_core_script_loaded_after_app_js(self):
        base = (_ROOT / "quam_state_manager" / "web" / "templates" / "base.html").read_text(
            encoding="utf-8")
        i_app = base.index("asset_url('app.js')")
        i_th = base.index("asset_url('sidebar-typeahead.js')")
        assert i_th > i_app, "it calls window._anchorPopover, which app.js defines"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_param_typeahead_selfcheck_passes():
    r = subprocess.run(
        ["node", str(_ROOT / "tests" / "param_typeahead_selfcheck.cjs")],
        capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT))
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "all checks passed" in r.stdout, (r.stdout + r.stderr)
