"""The diff workbench's optional THIRD source + the figures tab (customer
2026-08-27). The 2-way page must stay as it was; a set ``c=`` grows the list
view by a C column and the figures tab by a third column; figures are the
runs' own images served by ref + name, one row per figure name."""
from __future__ import annotations

import json
import re
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
        # docs/141 4z: two sources keep the 2-way list; no pane table
        assert 'diff-panes-table' not in html and 'diff-wb-list-3' not in html
        assert 'name="d"' not in html, "D appears only once C carries a source... or D does"

    def test_three_way_is_the_pane_view(self, env):
        # docs/141 4z: three sources are read as PANES -- one per source,
        # every value beside each other, Δ against the baseline (A by default)
        html = _get(env, _url(env))
        assert 'diff-panes-table' in html and 'diff-wb-list-3' not in html
        assert html.count('class="dp-pane-head') == 3
        # docs/141 4ab: the Keys column is a tree -- container rows with the
        # count of differing keys beneath, leaves indented under them
        assert '<th class="dp-key-col">Keys</th>' in html
        assert 'class="dp-row dp-dir" data-path="qubits" data-parent="" data-depth="0"' in html
        assert 'class="dp-row dp-dir" data-path="qubits.qA1" data-parent="qubits" data-depth="1"' in html
        assert 'data-path="qubits.qA1.T1" data-parent="qubits.qA1" data-depth="2"' in html
        assert 'class="dp-key dp-key-leaf">T1</span>' in html
        row = html.split('data-path="qubits.qA1.T1"', 1)[1].split("</tr>", 1)[0]
        # A 1e-5 (baseline) -> B 2e-5 (+100%), C 5e-5 (+400%): both from the
        # house delta chip, against the BASELINE, not the previous column
        assert "(+100%)" in row and "(+400%)" in row and "(+150%)" not in row, row
        assert row.count("dp-diff") == 2 and row.count("dp-base") == 1
        # f_01 agrees on all three -> not a row
        assert "qubits.qA1.f_01" not in html
        assert 'name="d"' in html and 'name="e"' not in html, "the next empty slot is offered, not all of them"

    def test_the_baseline_is_a_url_parameter(self, env):
        html = _get(env, _url(env) + "&base=2")
        row = html.split('data-path="qubits.qA1.T1"', 1)[1].split("</tr>", 1)[0]
        # C 5e-5 is the baseline: A and B carry Δ against it
        assert ("80%)" in row and "60%)" in row and "+80%" not in row), row
        assert 'data-base="2"' in html
        # out of range clamps to the last pane
        assert 'data-base="2"' in _get(env, _url(env) + "&base=9")

    def test_tab_strip_and_view_toggle_carry_c(self, env):
        html = _get(env, _url(env))
        c_q = quote(env["cc"])
        assert html.count(f"&amp;c={c_q}&amp;d=&amp;e=&amp;tab=") >= 5, "every tab button carries c (and the empty d/e)"
        # three sources: no tree/list toggle, the view is fixed at panes
        assert "3 panes" in html and "view=tree" not in html

    def test_three_sources_are_panes_whatever_view_is_asked(self, env):
        # docs/141 4z: the tree reads A -> B only, so it is never offered for 3+
        html = _get(env, _url(env, view="tree"))
        assert 'diff-panes-table' in html and 'id="diff-tree"' not in html
        two = _get(env, _url(env, view="tree", three=False))
        assert 'id="diff-tree"' in two and 'diff-panes-table' not in two
        # two sources can ASK for panes
        two_p = _get(env, _url(env, view="panes", three=False))
        assert 'diff-panes-table' in two_p and two_p.count('class="dp-pane-head') == 2

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
        assert 'diff-panes-table' in three and "sv-take" not in three


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
    def test_shared_names_pair_by_name_leftovers_pair_positionally(self, env):
        """docs/147: fig_a (all three runs) pairs BY NAME; fig_b (A only) and
        fig_c (C only) share ONE positional row, each cell captioned with its
        own file name -- never a column of A's figures followed by B's."""
        html = _get(env, _url(env, tab="figures"))
        assert "diff-wb-figs" in html
        assert "repeat(3, 1fr)" in html
        # every image still renders exactly once
        assert html.count('src="/diff/fig?') == 2 + 1 + 2
        # 2 rows: the name-matched fig_a + ONE mixed positional row
        assert html.count('class="diff-fig-name"') == 2
        # the mixed row's cells carry their own captions
        assert html.count('class="diff-fig-cellname"') == 2
        assert '<code>fig_b.png</code>' in html and '<code>fig_c.png</code>' in html
        # honest blank only where a row truly has no figure for that source
        assert html.count('class="compare-figure-na"') == 1

    def test_two_way_figures(self, env):
        html = _get(env, _url(env, tab="figures", three=False))
        assert "repeat(2, 1fr)" in html and html.count('src="/diff/fig?') == 3

    def test_disjoint_experiments_still_sit_side_by_side(self, env, tmp_path):
        """The customer's exact case: two DIFFERENT experiments -- zero shared
        figure names -- must still render pairwise rows, not two stacked
        columns."""
        d1 = _run(tmp_path, "dx1", 1e-5, ["alpha.png", "beta.png"])
        d2 = _run(tmp_path, "dx2", 2e-5, ["gamma.png", "delta.png", "eps.png"])
        html = _get(env, f"/diff?a={quote(d1)}&b={quote(d2)}&tab=figures&view=list")
        # 3 rows (max of the two counts), every one positional/mixed
        assert html.count('class="diff-fig-name"') == 3
        assert html.count('src="/diff/fig?') == 5
        assert html.count('class="compare-figure-na"') == 1   # B's 3rd vs A's absent
        # side-by-side: A's first figure and B's first figure share the SAME
        # row (names are folder-sorted, so B's first is delta) -- the exact
        # opposite of the reported stacked-columns rendering
        first_row = html.split('class="diff-fig-name"')[1]
        assert "alpha.png" in first_row and "delta.png" in first_row
        # the two MIXED rows caption each cell; the last row holds only B's
        # third figure, so it names itself in the row label instead
        assert html.count('class="diff-fig-cellname"') == 4

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


class Test4acRegressions:
    """docs/141 4ac -- the review round over 4m-4ab. Each test here FAILS if
    its fix is reverted; the mutation is named in the docstring."""

    def test_show_more_keeps_every_slot(self, env):
        """R5-1 (CRITICAL). `_qs | join('&amp;')` produced a plain string
        holding `&amp;`, which Jinja escaped AGAIN, so the browser read a
        literal `&amp;` -- not a query separator. One press of the only paging
        control replaced a 3-pane comparison with 'Pick two sources'.
        Mutation: join('&') -> join('&amp;')."""
        import html as _html
        import re

        # >300 differing leaves so the Show-more button renders
        big = env["root"] / "big"
        for i, name in enumerate(("s1", "s2", "s3")):
            qs = big / name / "quam_state"
            qs.mkdir(parents=True)
            (qs / "state.json").write_text(json.dumps(
                {"qubits": {f"q{k}": {"T1": k + i} for k in range(400)}}))
            (qs / "wiring.json").write_text("{}")
        refs = [f"run:{big / n / 'quam_state'}" for n in ("s1", "s2", "s3")]
        url = ("/diff?" + "&".join(f"{sl}={quote(r)}" for sl, r in zip("abc", refs))
               + "&tab=state&view=panes")
        page = _get(env, url)
        m = re.search(r'class="btn-sm outline"\s+hx-get="([^"]+)"', page)
        assert m, "the Show-more button must render for a >300-row diff"
        more_url = _html.unescape(m.group(1))
        assert more_url.count("&a=") + more_url.count("?a=") == 1
        for sl in "bc":
            assert f"&{sl}=" in more_url, f"slot {sl} lost from {more_url}"
        page2 = _get(env, more_url)
        assert 'data-n="3"' in page2, "the second page still compares three sources"
        assert page2.count("dp-pane-head") == 3
        assert "Pick two sources" not in page2

    def test_the_cap_never_inflates_the_agree_count(self):
        """R5-2 (CRITICAL). diff_rows_n stopped emitting rows at ROW_CAP and
        then computed same = len(paths) - len(rows), so every leaf it never
        examined was counted as AGREEING -- 5,354 differing leaves reported as
        identical on a real chip pair. Mutation: `continue` -> `break`, or
        same-by-subtraction."""
        from quam_state_manager.core import json_diff

        n = json_diff.ROW_CAP + 500
        a = {f"k{i:06d}": i for i in range(n)}
        b = {f"k{i:06d}": i + 1 for i in range(n)}
        res = json_diff.diff_rows_n([a, b])
        assert len(res["rows"]) == json_diff.ROW_CAP, "the cap still bounds the ROWS"
        assert res["truncated"] is True
        c = res["counts"]
        assert c["same"] == 0, f"nothing agrees; the page must not say {c['same']}"
        assert c["changed"] == n, "every differing leaf is counted, past the cap"
        assert c["shown"] == json_diff.ROW_CAP

        # and the honest case still adds up
        same_doc = {"x": 1, "y": 2}
        r2 = json_diff.diff_rows_n([same_doc, dict(same_doc)])
        assert r2["counts"] == {"changed": 0, "added": 0, "removed": 0,
                                "same": 2, "total": 0, "shown": 0, "one_sided": 0}

    def test_a_non_transitive_triple_is_still_listed(self):
        """R6/4ac. diff_rows_n compared every side against the FIRST present
        value only, and `_eq` carries a relative tolerance, so a triple
        A~B, A~C, B!~C was called 'all equal' and the row never rendered.
        Mutation: back to the single `first` representative."""
        from quam_state_manager.core import json_diff
        from quam_state_manager.core.differ import CMP_TOLERANCE

        a = 1.0
        b = a * (1 + 0.9 * CMP_TOLERANCE)
        c = b * (1 + 0.9 * CMP_TOLERANCE)
        res = json_diff.diff_rows_n([{"v": a}, {"v": b}, {"v": c}])
        assert [r["path"] for r in res["rows"]] == ["v"], \
            "a and c differ under the one rule, so the leaf IS a difference"

    def test_clearing_slot_a_keeps_the_comparison(self, env):
        """R5-8. The picker's A/B selects offer '- pick a source -' and the
        form auto-submits, so one click left src_a None while three other
        slots held sources -- and the page blanked to 'Pick two sources to
        compare.' while the pickers still showed them. Mutation: remove the
        slot compaction."""
        url = (f"/diff?a=&b={quote(env['a'])}&c={quote(env['b'])}"
               f"&d={quote(env['cc'])}&tab=state&view=panes")
        html = _get(env, url)
        assert "Pick two sources to compare." not in html
        assert 'data-n="3"' in html and html.count("dp-pane-head") == 3

    def test_the_pane_letters_are_the_picker_letters(self, env):
        """R5-10, fixed by the same compaction: a source rendered as pane 'C'
        while its picker read 'D', and base= indexes the compacted list, so a
        bookmarked base silently meant a different run."""
        url = (f"/diff?a={quote(env['a'])}&b={quote(env['b'])}&c="
               f"&d={quote(env['cc'])}&tab=state&view=panes")
        html = _get(env, url)
        slots = re.findall(r'<span class="dp-slot">([A-E])</span>', html)
        assert slots == ["A", "B", "C"], slots
        # the third source now IS slot c in the URL the page carries
        assert 'name="c"' in html and 'name="d"' in html

    def test_the_note_says_how_many_of_how_many(self, env):
        """R5-7 / R6-1. The tree is rebuilt per page and its container counts
        were page-local, under a tooltip stating them as the whole truth; the
        note said nothing about paging at all. Mutation: drop total_rows."""
        big = env["root"] / "paged"
        for i, name in enumerate(("p1", "p2")):
            qs = big / name / "quam_state"
            qs.mkdir(parents=True)
            (qs / "state.json").write_text(json.dumps(
                {"qubits": {f"q{k}": {"T1": k + i} for k in range(400)}}))
            (qs / "wiring.json").write_text("{}")
        refs = [f"run:{big / n / 'quam_state'}" for n in ("p1", "p2")]
        url = ("/diff?" + "&".join(f"{sl}={quote(r)}" for sl, r in zip("ab", refs))
               + "&tab=state&view=panes")
        html = _get(env, url)
        assert "showing" in html and "of 400 differing keys" in html
        # the root container's count is the whole diff, not this page
        m = re.search(r'<span class="dp-count" data-count="\d+" title="(\d+) differing keys inside', html)
        assert m and int(m.group(1)) == 400, m.group(0) if m else "no dp-count"
        # C2-22: and the tooltip says the count is not what this page lists
        assert "this page lists 300" in html

    def test_an_n_way_diff_never_prints_plus_zero_added(self, env):
        """R5-6. The N-way payload hard-codes added/removed to 0, so the strip
        read '+0 added -0 removed' beside thousands of one-sided keys."""
        one = env["root"] / "one"
        for name, doc in (("o1", {"qubits": {"q1": {"T1": 1}}}),
                          ("o2", {"qubits": {"q1": {"T1": 2, "extra": 3}}}),
                          ("o3", {"qubits": {"q1": {"T1": 3}}})):
            qs = one / name / "quam_state"
            qs.mkdir(parents=True)
            (qs / "state.json").write_text(json.dumps(doc))
            (qs / "wiring.json").write_text("{}")
        refs = [f"run:{one / n / 'quam_state'}" for n in ("o1", "o2", "o3")]
        url = ("/diff?" + "&".join(f"{sl}={quote(r)}" for sl, r in zip("abc", refs))
               + "&tab=state&view=panes")
        html = _get(env, url)
        assert "+0 added" not in html and "0 removed" not in html
        assert "on some sources only" in html

    def test_the_server_paints_no_delta_on_a_cell_equal_to_the_baseline(self, env):
        """R5-5. The CLIENT delta gate was pinned; the SERVER one was not, and
        removing it rendered a `0` chip on every equal cell (708 of them on the
        real 5-run archive diff) before any script ran. Every existing fixture
        had three sources that all differed, so no test ever rendered a
        dp-same cell. Mutation: drop the `r.eq[base][i] != '1'` term from
        `_show_d` in _diff_panes.html."""
        eq = env["root"] / "eqcell"
        for name, t1 in (("e1", 1e-5), ("e2", 2e-5), ("e3", 1e-5)):
            qs = eq / name / "quam_state"
            qs.mkdir(parents=True)
            (qs / "state.json").write_text(json.dumps({"qubits": {"q1": {"T1": t1}}}))
            (qs / "wiring.json").write_text("{}")
        refs = [f"run:{eq / n / 'quam_state'}" for n in ("e1", "e2", "e3")]
        url = ("/diff?" + "&".join(f"{sl}={quote(r)}" for sl, r in zip("abc", refs))
               + "&tab=state&view=panes&base=0")
        html = _get(env, url)
        # C equals A (the baseline): its cell is dp-same and carries no chip
        same_cells = re.findall(r'<td class="dp-cell dp-same"[^>]*>(.*?)</td>', html, re.S)
        assert same_cells, "the fixture must render at least one equal cell"
        for cell in same_cells:
            assert "val-delta" not in cell, f"a fabricated Δ on an equal cell: {cell[:200]}"
            assert 'class="dp-delta" hidden' in cell or "<span class=\"dp-delta\" hidden>" in cell
        # and the cell that really differs DOES carry one (the pin is not vacuous)
        diff_cells = re.findall(r'<td class="dp-cell dp-diff"[^>]*>(.*?)</td>', html, re.S)
        assert any("val-delta" in c for c in diff_cells), "a real difference still shows its Δ"

    def test_five_identical_sources_do_not_read_as_two(self, env):
        """R5-11. `These two are identical` was hard-coded, so five identical
        runs said 'two' and _diff_panes.html's own N-aware line was dead."""
        same = env["root"] / "same"
        for name in ("z1", "z2", "z3"):
            qs = same / name / "quam_state"
            qs.mkdir(parents=True)
            (qs / "state.json").write_text(json.dumps({"qubits": {"q1": {"T1": 1}}}))
            (qs / "wiring.json").write_text("{}")
        refs = [f"run:{same / n / 'quam_state'}" for n in ("z1", "z2", "z3")]
        url = ("/diff?" + "&".join(f"{sl}={quote(r)}" for sl, r in zip("abc", refs))
               + "&tab=state&view=panes")
        html = _get(env, url)
        assert "These 3 sources are identical" in html
        assert "These two are identical" not in html

class TestSearchBox:
    """docs/141 4ai: the diff had no search at all. The box is rendered by
    BOTH row surfaces, the query survives in the URL, and the server keeps
    filtering out of it -- the counts above the table must go on describing
    the whole diff, not what someone typed."""

    def test_the_pane_view_carries_the_box_and_what_it_needs(self, env):
        html = _get(env, _url(env, view="panes"))
        assert 'class="dp-search"' in html and 'class="dp-search-count' in html
        # the client needs the page's own paging truth to be honest about it
        assert 'data-more="0"' in html
        # and each container's full count, to restore it when the box clears
        assert 'class="dp-count" data-count=' in html

    def test_the_list_view_carries_it_too(self, env):
        html = _get(env, _url(env, view="list", three=False))
        assert 'id="diff-list"' in html and 'class="dp-search"' in html

    def test_the_query_comes_back_in_the_box_escaped(self, env):
        html = _get(env, _url(env, view="panes") + "&q=" + quote('T1 <b>&"x"'))
        assert 'value="T1 &lt;b&gt;&amp;&#34;x&#34;"' in html or \
               'value="T1 &lt;b&gt;&amp;&quot;x&quot;"' in html, \
            "the echoed query must be escaped, not reflected"
        assert "<b>&" not in html.split('class="dp-search"')[1][:200]

    def test_the_server_does_not_filter_by_it(self, env):
        """A query that matches nothing must not change a single row: the
        counts, the row set and the tree are the whole diff either way."""
        plain = _get(env, _url(env, view="panes"))
        typed = _get(env, _url(env, view="panes") + "&q=" + quote("nothing matches this"))
        strip = lambda h: h.replace('value="nothing matches this"', 'value=""')   # noqa: E731
        assert strip(typed) == plain

    @staticmethod
    def _wide(root: Path, name: str, base: float) -> str:
        """A run whose state differs on many leaves, so the page can page."""
        qs = root / name / "quam_state"
        qs.mkdir(parents=True)
        (qs / "state.json").write_text(json.dumps({
            "qubits": {f"q{i}": {"T1": base + i, "f_01": 4.8e9 + base} for i in range(12)},
            "active_qubit_names": [f"q{i}" for i in range(12)]}))
        (qs / "wiring.json").write_text("{}")
        return f"run:{qs}"

    def test_the_paging_truth_is_the_real_remainder(self, env):
        """data-more is what the search reports as unsearchable, so it has to
        be the actual count of rows this page did not render."""
        a = self._wide(env["root"], "w1", 1.0)
        b = self._wide(env["root"], "w2", 2.0)
        url = f"/diff?a={quote(a)}&b={quote(b)}&c={quote(env['cc'])}&tab=state&view=panes"
        whole = _get(env, url)
        n = whole.count('class="dp-row dp-leaf"')
        assert n > 2, n
        paged = _get(env, url + "&rows=2")
        assert paged.count('class="dp-row dp-leaf"') == 2
        assert 'data-more="%d"' % (n - 2) in paged
        assert "Show %s more" % f"{n - 2:,}" in paged     # and the button agrees

    def test_a_long_query_is_bounded(self, env):
        html = _get(env, _url(env, view="panes") + "&q=" + "z" * 500)
        assert 'value="' + "z" * 200 + '"' in html, "the echoed query is capped at 200 chars"

