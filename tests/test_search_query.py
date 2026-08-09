"""Pins for the shared query grammar — ``core/search_query.py`` and its JS twin.

Grammar: space = AND; a STANDALONE ``|`` token with a joinable term on both
sides ORs its neighbours (tight binding — ``x a | b`` = x AND (a|b)); every
other pipe (embedded ``|e>`` ket notation, leading, trailing, doubled, beside
a negated term) stays a literal token.

Three layers:
* the grouping table, including every degenerate case, on the Python module;
* a fuzzed additivity property — a query with NO standalone pipe parses to
  singleton groups whose evaluation is exactly the historic every-token AND;
* structure-for-structure parity with the REAL ``search-query.js`` via node
  (the value_delta precedent — a test that fails when either side moves alone).

Plus the first server-side consumer: the sidebar workspace filter's
``_entry_matches`` (``routes.py``), whose OR/negation semantics are pinned here
against a real-shaped tree entry.
"""

from __future__ import annotations

import json
import random
import shutil
import string
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.core.search_query import (
    group_by, groups, matches_hay, tokens)

_ROOT = Path(__file__).resolve().parent.parent


class TestGrouping:
    def test_plain_words_are_singleton_groups(self):
        assert groups("a b c") == [["a"], ["b"], ["c"]]

    def test_pipe_ors_its_neighbours(self):
        assert groups("a | b") == [["a", "b"]]

    def test_tight_binding(self):
        # x180 amplitude | length  =  x180 AND (amplitude OR length)
        assert groups("x180 amplitude | length") == [["x180"], ["amplitude", "length"]]

    def test_chain(self):
        assert groups("a | b | c") == [["a", "b", "c"]]

    def test_two_independent_groups(self):
        assert groups("a | b x | y") == [["a", "b"], ["x", "y"]]

    def test_case_and_whitespace_normalisation(self):
        assert groups("  Q1   |  Q2  ") == [["q1", "q2"]]

    # -- every pipe that is NOT a standalone operator stays literal ---------
    def test_embedded_pipe_is_literal(self):
        # ket notation in node.json descriptions — |e>, |00>, |baseline|
        assert groups("|e>") == [["|e>"]]
        assert groups("p_|00>") == [["p_|00>"]]

    def test_lone_pipe_query_is_literal(self):
        assert groups("|") == [["|"]]

    def test_leading_and_trailing_pipes_are_literal(self):
        assert groups("| a") == [["|"], ["a"]]
        assert groups("a |") == [["a"], ["|"]]

    def test_doubled_pipe_stays_literal(self):
        # `a | | b` — the first pipe has no valid right operand (another
        # pipe), so BOTH stay literal terms: today's AND, unchanged.
        assert groups("a | | b") == [["a"], ["|"], ["|"], ["b"]]

    def test_empty_query(self):
        assert groups("") == []
        assert groups("   ") == []
        assert groups(None) == []

    # -- structured tokens (the scoped surfaces compose at this level) ------
    def test_negated_terms_never_join_or_groups(self):
        items = [{"v": "-tag:wip", "neg": True}, {"v": "|", "neg": False},
                 {"v": "x", "neg": False}]
        out = group_by(items, get_raw=lambda c: c["v"],
                       joinable=lambda c: not c["neg"])
        # the pipe beside a negated term is literal → three singleton groups
        assert [[c["v"] for c in g] for g in out] == [["-tag:wip"], ["|"], ["x"]]

    def test_scoped_positive_terms_do_join(self):
        items = [{"v": "tag:a", "neg": False}, {"v": "|", "neg": False},
                 {"v": "tag:b", "neg": False}]
        out = group_by(items, get_raw=lambda c: "|" if c["v"] == "|" else "",
                       joinable=lambda c: not c["neg"])
        assert [[c["v"] for c in g] for g in out] == [["tag:a", "tag:b"]]


class TestMatchesHay:
    def test_and_of_or(self):
        g = groups("x180 amplitude | length")
        assert matches_hay("qubits.q1.xy.operations.x180 amplitude 0.1", g)
        assert matches_hay("qubits.q1.z.operations.x180 length 48", g)
        assert not matches_hay("qubits.q1.xy.operations.x90 amplitude", g)
        assert not matches_hay("x180 alpha -1.0", g)

    def test_empty_groups_match_everything(self):
        assert matches_hay("anything", [])


class TestAdditivityFuzz:
    """A query with no standalone pipe must behave EXACTLY as before.

    Historic behaviour on every tokenizing surface: split on whitespace, AND
    every token. The grammar's no-pipe case must be that, not approximately.
    """

    def test_no_pipe_queries_parse_to_singletons(self):
        rng = random.Random(20260809)
        alphabet = string.ascii_lowercase + string.digits + "._-#/>"
        for _ in range(2000):
            toks = ["".join(rng.choice(alphabet)
                            for _ in range(rng.randint(1, 10)))
                    for _ in range(rng.randint(1, 6))]
            q = "  ".join(toks)
            assert groups(q) == [[t] for t in tokens(q)], q

    def test_no_pipe_evaluation_equals_flat_and(self):
        rng = random.Random(99)
        hay = "qubits.qa1.xy.operations.x180_dragcosine amplitude 0.11 t1 2.4e-5"
        words = hay.split() + ["zzz", "q9", "length"]
        for _ in range(500):
            toks = [rng.choice(words) for _ in range(rng.randint(1, 4))]
            q = " ".join(toks)
            assert matches_hay(hay, groups(q)) == all(t in hay for t in tokens(q)), q

    def test_or_is_a_superset_of_the_literal_pipe_and(self):
        # `a | b` used to be AND(a, "|", b); every haystack that matched then
        # (contains a, a pipe, and b) still matches now (contains a or b).
        rng = random.Random(7)
        for _ in range(500):
            a, b = rng.choice(["x", "amp", "q1"]), rng.choice(["y", "len", "q2"])
            hay = f"{a} | {b} extra"
            assert matches_hay(hay, groups(f"{a} | {b}"))


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
class TestJsParity:
    """The JS twin must group every query identically, structure for structure."""

    QUERIES = [
        "", "   ", "a", "a b", "a b c", "A  B",
        "a | b", "a | b | c", "x a | b", "a | b x | y",
        "|", "a |", "| a", "a | | b", "| |",
        "|e>", "p_|00>", "a|b", "-x90", "-x90 | -y90",
        "q1 amplitude", "x180 amplitude | length",
        "qubits.q1.f_01", "#/qubits/q1", "5,000 0.5",
    ]

    def test_groups_match_the_js_twin(self, tmp_path):
        cases = tmp_path / "cases.json"
        cases.write_text(json.dumps(self.QUERIES), encoding="utf-8")
        r = subprocess.run(
            ["node", str(_ROOT / "tests" / "search_query_parity.cjs"), str(cases)],
            capture_output=True, text=True, cwd=str(_ROOT), timeout=60)
        assert r.returncode == 0, r.stdout + r.stderr
        js = json.loads(r.stdout)
        py = [groups(q) for q in self.QUERIES]
        assert js == py, "\n".join(
            f"{q!r}: js={j} py={p}"
            for q, j, p in zip(self.QUERIES, js, py) if j != p)


class TestSidebarFilterOr:
    """First server-side consumer: the workspace sidebar filter."""

    class _E:
        experiment_name = "power_rabi"
        date_str = "2026-08-09"
        status = "finished"
        run_id = 780
        qubits = ["qA1", "qA2"]
        qubit_pairs = ["qA2-qA1"]

    def _m(self, q):
        from quam_state_manager.web.routes import _entry_matches, _parse_tree_query
        return _entry_matches(self._E(), _parse_tree_query(q))

    def test_free_text_or(self):
        assert self._m("qA1 | qZ9")            # left arm hits
        assert self._m("qZ9 | qA1")            # right arm hits
        assert not self._m("qZ8 | qZ9")        # neither

    def test_or_binds_tighter_than_and(self):
        # rabi AND (qA1 OR qZ9) — the AND term still filters
        assert self._m("rabi qA1 | qZ9")
        assert not self._m("ramsey qA1 | qZ9")

    def test_scoped_or(self):
        assert self._m("status:finished | status:error")
        assert not self._m("status:running | status:error")

    def test_negation_never_joins_an_or(self):
        # `-status:error | rabi` — pipe beside a negated term is literal, so
        # this is AND(-status:error, "|", rabi); the literal pipe matches
        # nothing on this entry → no match (exactly the pre-grammar answer).
        assert not self._m("-status:error | rabi")

    def test_plain_and_unchanged(self):
        assert self._m("rabi 2026")
        assert not self._m("rabi 2027")

    def test_run_id_or(self):
        assert self._m("780 | 999")
