"""The diff workbench's optional THIRD source + the figures tab (customer
2026-08-27). The 2-way page must stay as it was; a set ``c=`` grows the list
view by a C column and the figures tab by a third column; figures are the
runs' own images served by ref + name, one row per figure name."""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import quote

import pytest

from quam_state_manager.web.app import create_app

PNG = (b"\x89PNG\r\n\x1a\n" + bytes(range(16)))  # any bytes -- served, not decoded


def _run(root: Path, name: str, t1: float, figs: list[str]) -> str:
    """A run folder: quam_state/ + node.json + data.json + figure files.
    Returns the workbench ref (run:<quam_state folder>)."""
    run = root / name
    qs = run / "quam_state"
    qs.mkdir(parents=True)
    (qs / "state.json").write_text(json.dumps({
        "qubits": {"qA1": {"T1": t1, "f_01": 4.8e9}}, "active_qubit_names": ["qA1"]}))
    (qs / "wiring.json").write_text("{}")
    (run / "node.json").write_text(json.dumps({"name": name, "id": 1}))
    (run / "data.json").write_text(json.dumps(
        {"figures": {f.split(".")[0]: "./" + f for f in figs}}))
    for f in figs:
        (run / f).write_bytes(PNG)
    return f"run:{qs}"


@pytest.fixture
def env(tmp_path):
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    a = _run(tmp_path, "r1", 1e-5, ["fig_a.png", "fig_b.png"])
    b = _run(tmp_path, "r2", 2e-5, ["fig_a.png"])
    c = _run(tmp_path, "r3", 5e-5, ["fig_a.png", "fig_c.png"])
    return {"c": app.test_client(), "a": a, "b": b, "cc": c, "root": tmp_path}


def _get(env, url):
    return env["c"].get(url, headers={"HX-Request": "true"}).data.decode()


def _url(env, tab="state", view="list", three=True):
    u = f"/diff?a={quote(env['a'])}&b={quote(env['b'])}&tab={tab}&view={view}"
    return u + (f"&c={quote(env['cc'])}" if three else "")


class TestThirdSource:
    def test_two_way_page_keeps_its_shape_and_offers_c(self, env):
        html = _get(env, _url(env, three=False))
        assert 'name="c"' in html and "optional third" in html
        assert 'data-diff3="1"' not in html and 'diff-wb-list-3' not in html
        assert '<th>C</th>' not in html

    def test_three_way_list_shows_all_three_values(self, env):
        html = _get(env, _url(env))
        assert 'diff-wb-list-3' in html and '<th>C</th>' in html
        row = html.split('data-diff3="1"', 1)[1].split("</tr>", 1)[0]
        assert "qubits.qA1.T1" in row
        # A 1e-5 -> B 2e-5 -> C 5e-5: both deltas rendered, from the house
        # delta chip, in their own columns (format-agnostic on the values)
        assert "(+100%)" in row and "(+150%)" in row, row
        assert row.count('<td class="diff-list-c"><code>') == 1
        # f_01 agrees on all three -> not a row
        assert "qubits.qA1.f_01" not in html

    def test_tab_strip_and_view_toggle_carry_c(self, env):
        html = _get(env, _url(env))
        c_q = quote(env["cc"])
        assert html.count(f"&amp;c={c_q}&amp;tab=") >= 5, "every tab button carries c"
        assert f"&amp;c={c_q}&amp;tab=state&amp;view=tree" in html

    def test_tree_view_says_it_reads_a_to_b(self, env):
        html = _get(env, _url(env, view="tree"))
        assert "diff-tree-c-note" in html and 'id="diff-tree"' in html
        assert "diff-tree-c-note" not in _get(env, _url(env, view="tree", three=False))

    def test_three_way_never_offers_take(self, env):
        """A per-value take writes into the WORKING side (docs/132); with a
        third source there is no single 'other side' to take from, so the
        button must not render -- while the same pair WITHOUT c still offers it."""
        live = Path(env["b"][len("run:"):])
        r = env["c"].post("/load", data={"folder": str(live)})
        assert r.status_code in (200, 302)
        working = f"working:{live}"
        two = _get(env, f"/diff?a={quote(env['a'])}&b={quote(working)}&tab=state&view=list")
        assert "sv-take" in two, "the 2-way page must still offer the take (else this pin is vacuous)"
        three = _get(env, f"/diff?a={quote(env['a'])}&b={quote(working)}&c={quote(env['cc'])}&tab=state&view=list")
        assert 'diff-wb-list-3' in three and "sv-take" not in three


class TestRowsN:
    def test_a_leaf_past_the_row_cap_is_still_compared(self):
        """Caught on the real chip (8,822 leaves): the walk was capped at the
        ROW cap, so a change in the last leaf read as 'identical · capped'."""
        from quam_state_manager.core import json_diff
        n = json_diff.ROW_CAP + 50
        a = {"k": {f"p{i:05d}": i for i in range(n)}}
        b = {"k": {f"p{i:05d}": i for i in range(n)}}
        c = {"k": {f"p{i:05d}": i for i in range(n)}}
        b["k"][f"p{n - 1:05d}"] = -1          # the LAST leaf, past ROW_CAP
        res = json_diff.diff_rows_n([a, b, c])
        assert [r["path"] for r in res["rows"]] == [f"k.p{n - 1:05d}"]
        assert res["rows"][0]["vals"] == [n - 1, -1, n - 1]
        assert res["truncated"] is False

    def test_absence_on_one_side_is_a_row(self):
        from quam_state_manager.core import json_diff
        res = json_diff.diff_rows_n([{"x": 1, "y": 2}, {"x": 1}, {"x": 1, "y": 2}])
        assert [(r["path"], r["present"]) for r in res["rows"]] == [("y", [True, False, True])]


class TestFiguresTab:
    def test_one_column_per_run_one_row_per_figure(self, env):
        html = _get(env, _url(env, tab="figures"))
        assert "diff-wb-figs" in html
        assert "repeat(3, 1fr)" in html
        # union of names, first-seen order: A's two, then C's extra
        i_a, i_b, i_c = (html.index("<code>fig_a.png</code>"), html.index("<code>fig_b.png</code>"),
                         html.index("<code>fig_c.png</code>"))
        assert i_a < i_b < i_c
        # a run that lacks the figure gets a blank cell, never a substitute
        assert html.count('src="/diff/fig?') == 2 + 1 + 2
        assert html.count('class="compare-figure-na"') == (3 * 3) - 5

    def test_two_way_figures(self, env):
        html = _get(env, _url(env, tab="figures", three=False))
        assert "repeat(2, 1fr)" in html and html.count('src="/diff/fig?') == 3

    def test_figures_are_the_runs_own_files(self, env):
        r = env["c"].get(f"/diff/fig?ref={quote(env['a'])}&name=fig_a.png")
        assert r.status_code == 200 and r.data == PNG

    def test_fig_route_refuses_traversal_and_unknown(self, env):
        c = env["c"]
        assert c.get(f"/diff/fig?ref={quote(env['a'])}&name=../node.json").status_code == 404
        # a real file of the run folder that is NOT an image must not be served
        assert c.get(f"/diff/fig?ref={quote(env['a'])}&name=node.json").status_code == 404
        assert c.get(f"/diff/fig?ref={quote(env['a'])}&name=nope.png").status_code == 404
        assert c.get("/diff/fig?ref=run:/nowhere&name=fig_a.png").status_code == 404

    def test_no_figures_anywhere_is_said_not_blank(self, env, tmp_path):
        x = _run(tmp_path, "r4", 1e-5, [])
        y = _run(tmp_path, "r5", 2e-5, [])
        html = _get(env, f"/diff?a={quote(x)}&b={quote(y)}&tab=figures")
        assert "No figures on any side" in html
