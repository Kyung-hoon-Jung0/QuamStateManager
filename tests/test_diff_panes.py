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


class TestRowGroups:
    """groups[i] == groups[j] exactly when json_diff's own equality says so;
    absent is -1; ids are dense in first-seen order."""

    def test_numeric_equality_is_json_diffs_not_pythons(self):
        row = {"present": [True, True, True, True], "vals": [100, 100.0, 100.0000000000001, 101]}
        # 100 == 100.0 under compare_equal (docs/118: one rule), the 1e-13
        # neighbour too (float tolerance), 101 is its own class
        assert routes._diff_row_groups(row) == [0, 0, 0, 1]

    def test_absent_and_null_are_distinct(self):
        row = {"present": [True, False, True], "vals": [None, None, None]}
        assert routes._diff_row_groups(row) == [0, -1, 0]

    def test_strings_and_pointers(self):
        row = {"present": [True, True, True], "vals": ["#/a", "#/b", "#/a"]}
        assert routes._diff_row_groups(row) == [0, 1, 0]

    def test_nan_agrees_with_nan(self):
        nan = float("nan")
        row = {"present": [True, True], "vals": [nan, nan]}
        assert routes._diff_row_groups(row) == [0, 0]


class TestTreeRows:
    """docs/141 4ab: the differing leaves folded into their hierarchy, DFS."""

    @staticmethod
    def _rows(*paths):
        return [{"path": p, "vals": [1, 2], "present": [True, True], "kind": "changed", "groups": [0, 1]} for p in paths]

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


def test_the_bundle_ships_the_client():
    base = (_ROOT / "quam_state_manager/web/templates/base.html").read_text(encoding="utf-8")
    assert "'compare': ['topo-graph.js', 'compare-hub.js', 'diff-panes.js']" in base
    js = (_ROOT / "quam_state_manager/web/static/diff-panes.js").read_text(encoding="utf-8")
    assert "htmx:configRequest" in js and "window.ValueDelta.chipHtml" in js
    # the client never re-derives equality: it reads the server's groups
    assert "data-groups" in js and "compare_equal" not in js and "parseFloat(" not in js
    assert "applyVisibility" in js and "data-collapsed" in js, "the key tree collapses client-side"
