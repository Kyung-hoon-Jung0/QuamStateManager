"""docs/141 4z -- the diff workbench's pane view.

Client behaviour (baseline switch, Δ rules, request rewrite) is pinned by
tests/diff_panes_selfcheck.cjs under jsdom; the route side (three sources are
panes whatever view is asked, two can ask for panes, base= clamps, Δ against
the baseline) lives in test_diff_three_way.py. Here: the equality-group rule
that lets the client switch baselines without a second equality.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.web import routes

_ROOT = Path(__file__).resolve().parent.parent


def test_diff_panes_selfcheck():
    node = shutil.which("node")
    if node is None or subprocess.run([node, "-e", "require('jsdom')"], capture_output=True, cwd=str(_ROOT)).returncode != 0:
        pytest.skip("jsdom not installed for node")
    res = subprocess.run([node, str(_ROOT / "tests" / "diff_panes_selfcheck.cjs")],
                         capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT))
    assert res.returncode == 0, res.stdout + res.stderr
    assert "ok - a tab-strip request is rewritten to the current baseline" in res.stdout
    assert "ok - two words are AND (SearchQuery)" in res.stdout
    assert "ok - a search expands the containers on the way to a hit" in res.stdout


class TestRowEq:
    """eq[i][j] == '1' exactly when json_diff's own equality says side i and
    side j hold the same value; two absent cells agree with each other and
    with nothing else."""

    def test_numeric_equality_is_json_diffs_not_pythons(self):
        row = {"present": [True, True, True, True], "vals": [100, 100.0, 100.0000000000001, 101]}
        # 100 == 100.0 under compare_equal (docs/118: one rule), the 1e-13
        # neighbour too (float tolerance), 101 differs from all three
        assert routes._diff_row_eq(row) == ["1110", "1110", "1110", "0001"]

    def test_absent_and_null_are_distinct(self):
        row = {"present": [True, False, True], "vals": [None, None, None]}
        assert routes._diff_row_eq(row) == ["101", "010", "101"]

    def test_strings_and_pointers(self):
        row = {"present": [True, True, True], "vals": ["#/a", "#/b", "#/a"]}
        assert routes._diff_row_eq(row) == ["101", "010", "101"]

    def test_nan_agrees_with_nan(self):
        nan = float("nan")
        row = {"present": [True, True], "vals": [nan, nan]}
        assert routes._diff_row_eq(row) == ["11", "11"]

    def test_two_absent_sides_agree_with_each_other(self):
        row = {"present": [True, False, False], "vals": [1, None, None]}
        assert routes._diff_row_eq(row) == ["100", "011", "011"]

    def test_the_matrix_is_symmetric_and_reflexive(self):
        row = {"present": [True, True, True], "vals": [1, 2, 1]}
        eq = routes._diff_row_eq(row)
        for i, r in enumerate(eq):
            assert r[i] == "1"
            for j, c in enumerate(r):
                assert c == eq[j][i]

    def test_a_non_transitive_tolerance_never_splits_two_equal_sides(self):
        """docs/141 4ac, the CRITICAL this replaced group ids for.

        ``compare_equal`` compares numbers with a RELATIVE tolerance, so it is
        not transitive: a~b and b~c while a!~c. The old first-match grouping
        answered [0, 0, 1] for (a, b, c) -- painting c as differing from b
        under baseline B, though the app's one rule calls them equal -- and
        [0, 0, 0] for the same values in the order (b, a, c). A matrix has no
        such ordering dependence.
        """
        from quam_state_manager.core.differ import CMP_TOLERANCE, compare_equal
        a = 1.0
        b = a * (1 + 0.9 * CMP_TOLERANCE)
        c = b * (1 + 0.9 * CMP_TOLERANCE)
        assert compare_equal(a, b) and compare_equal(b, c) and not compare_equal(a, c)

        eq = routes._diff_row_eq({"present": [True] * 3, "vals": [a, b, c]})
        assert eq[1][2] == "1" and eq[2][1] == "1", "b and c ARE equal under the one rule"
        assert eq[0][2] == "0" and eq[0][1] == "1"
        # and the answer does not depend on which slot each run was dropped in
        shuffled = routes._diff_row_eq({"present": [True] * 3, "vals": [b, a, c]})
        assert shuffled[0][2] == "1" and shuffled[1][2] == "0"


class TestTreeRows:
    """docs/141 4ab: the differing leaves folded into their hierarchy, DFS."""

    @staticmethod
    def _rows(*paths):
        return [{"path": p, "vals": [1, 2], "present": [True, True], "kind": "changed",
                 "eq": ["10", "01"]} for p in paths]

    def test_dfs_with_containers_counts_and_depths(self):
        out = routes._diff_tree_rows(self._rows("qubits.q1.T1", "qubits.q1.f_01", "qubits.q2.T1", "wiring.x"))
        flat = [(t["kind"], t["path"], t["depth"]) for t in out]
        assert flat == [
            ("dir", "qubits", 0), ("dir", "qubits.q1", 1), ("leaf", "qubits.q1.T1", 2), ("leaf", "qubits.q1.f_01", 2),
            ("dir", "qubits.q2", 1), ("leaf", "qubits.q2.T1", 2), ("dir", "wiring", 0), ("leaf", "wiring.x", 1)]
        counts = {t["path"]: t["count"] for t in out if t["kind"] == "dir"}
        assert counts == {"qubits": 3, "qubits.q1": 2, "qubits.q2": 1, "wiring": 1}
        parents = {t["path"]: t["parent"] for t in out}
        assert parents["qubits"] == "" and parents["qubits.q1.T1"] == "qubits.q1" and parents["wiring.x"] == "wiring"
        assert all(t["row"]["path"] == t["path"] for t in out if t["kind"] == "leaf")

    def test_list_indices_read_as_numbers(self):
        out = routes._diff_tree_rows(self._rows("m.10.0", "m.2.0", "m.0.1"))
        assert [t["path"] for t in out if t["kind"] == "dir"] == ["m", "m.0", "m.2", "m.10"]

    def test_a_root_level_leaf_and_an_empty_input(self):
        assert routes._diff_tree_rows([]) == []
        out = routes._diff_tree_rows(self._rows("created_at"))
        assert [(t["kind"], t["depth"], t["parent"]) for t in out] == [("leaf", 0, "")]

    def test_a_value_that_is_also_a_container_elsewhere(self):
        out = routes._diff_tree_rows(self._rows("a.b", "a.b.c"))
        assert [(t["kind"], t["path"], t["depth"]) for t in out] == [
            ("dir", "a", 0), ("dir", "a.b", 1), ("leaf", "a.b", 2), ("leaf", "a.b.c", 2)]

    def test_the_doubled_key_gets_its_own_ROW_KEY(self):
        """docs/141 4ac: the container and its value row shared a `path`, so
        the client's `{data-path: row}` collapse map lost the container to its
        own leaf -- the toggle then hid nothing, every descendant resolved its
        ancestor to a row that can never be collapsed, and the value row's
        parent was itself (a self-loop stopped only by the 64-step guard)."""
        out = routes._diff_tree_rows(self._rows("a.b", "a.b.c"))
        keys = [t["key"] for t in out]
        assert len(keys) == len(set(keys)), f"row keys must be unique: {keys}"
        dir_row = next(t for t in out if t["kind"] == "dir" and t["path"] == "a.b")
        val_row = next(t for t in out if t["kind"] == "leaf" and t["path"] == "a.b")
        assert dir_row["key"] == "a.b"
        assert val_row["key"] != dir_row["key"]
        # the value row hangs off the container, and the container is reachable
        assert val_row["parent"] == dir_row["key"]
        child = next(t for t in out if t["path"] == "a.b.c")
        assert child["parent"] == dir_row["key"]
        # every row's parent, when set, names a row that exists
        by_key = {t["key"]: t for t in out}
        for t in out:
            if t["parent"]:
                assert t["parent"] in by_key and by_key[t["parent"]] is not t

    def test_the_count_is_the_whole_diff_not_the_page(self):
        """docs/141 4ac: the tree is rebuilt per page, so a container's count
        was the count WITHIN THE PAGE -- `ds_raw` read 70 on page 1 and 182 on
        page 2 of the same diff, under a tooltip stating it as fact."""
        every = self._rows(*[f"qubits.q{i}.T1" for i in range(400)])
        page = every[:300]
        out = routes._diff_tree_rows(page, total_rows=every)
        counts = {t["path"]: t["count"] for t in out if t["kind"] == "dir"}
        assert counts["qubits"] == 400, "the container counts every differing key, not this page's"
        assert len([t for t in out if t["kind"] == "leaf"]) == 300
        # with no total_rows the page IS the whole diff (the un-paged caller)
        assert routes._diff_tree_rows(page)[0]["count"] == 300

    def test_a_unicode_digit_segment_does_not_raise(self):
        """`str.isdigit()` is true for U+00B2 and friends, which `int()`
        refuses -- and the tree walk runs while rendering the page."""
        out = routes._diff_tree_rows(self._rows("m.².x", "m.0.x"))
        assert [t["path"] for t in out if t["kind"] == "dir"] == ["m", "m.0", "m.²"]


def test_keys_share_the_values_face_and_size():
    """4ab' (user): keys read in the same face/size as the value cells, only a touch heavier."""
    css = (_ROOT / "quam_state_manager/web/static/style.css").read_text(encoding="utf-8")
    i = css.index("\n.dp-key {")
    rule = css[i:css.index("}", i)]
    assert "font-family: inherit" in rule and "font-size: 1em" in rule and "monospace" not in rule
    assert "color: var(--pico-color)" in rule
    assert ".dp-key-leaf { font-weight: 500; }" in css and ".dp-key-dir { font-weight: 600; }" in css


def test_the_bundle_ships_the_client():
    base = (_ROOT / "quam_state_manager/web/templates/base.html").read_text(encoding="utf-8")
    assert "'compare': ['topo-graph.js', 'compare-hub.js', 'diff-panes.js']" in base
    js = (_ROOT / "quam_state_manager/web/static/diff-panes.js").read_text(encoding="utf-8")
    assert "htmx:configRequest" in js and "window.ValueDelta.chipHtml" in js
    # the client never re-derives equality: it reads the server's pairwise
    # matrix (docs/141 4ac -- group ids could not survive a non-transitive rule)
    assert "data-eq" in js and "data-groups" not in js
    assert "compare_equal" not in js and "parseFloat(" not in js
    assert "applyVisibility" in js and "data-collapsed" in js, "the key tree collapses client-side"

    # docs/141 4ai: the search is the app's ONE grammar, filtering client-side
    assert "window.SearchQuery.groups" in js and "window.SearchQuery.matchesHay" in js, \
        "the diff search must use the shared grammar, not a private tokenizer"
    assert "data-nomatch" in js and "data-more" in js, \
        "rows are marked, and the unloaded rest is named"


def test_the_search_box_is_one_partial_for_both_views():
    """4ai: the pane view and the list view render the SAME widget -- two
    copies would drift the way the five search boxes docs/96 unified did."""
    root = _ROOT / "quam_state_manager/web/templates"
    part = (root / "_diff_search.html").read_text(encoding="utf-8")
    assert 'type="search"' in part and 'class="dp-search"' in part
    assert 'value="{{ q }}"' in part
    for name in ("_diff_panes.html", "_diff_workbench.html"):
        assert "_diff_search.html" in (root / name).read_text(encoding="utf-8"), name
