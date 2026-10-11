"""v1.3.0 P2 -- the layout items left after the B2 sweep (L1-L27).

Each defect was seen in real Chrome on a copy of the customer chip (1600x950 and
1280x800, light and dark) and fixed in CSS, a template or a few JS lines. Layout
itself cannot be measured here (no browser), so the pins hold the rule that made
the difference; the measurements and screenshots are in the P2 report. Where the
deciding code is JavaScript it runs under jsdom: v130_p2_layout_logic_selfcheck.cjs
(the split presets, the status chip, the run-list reveal), v130_p2_map_selfcheck.cjs
(the component map's paint order), and the existing wiring_portlabel_selfcheck.cjs
and chip_status_hero_selfcheck.cjs, which this batch extended (they run under
test_gen_ux_selfchecks.py and test_topology_hero.py).
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "quam_state_manager" / "web"
TESTS = ROOT / "tests"


def _css() -> str:
    return (WEB / "static" / "style.css").read_text(encoding="utf-8")


def _tpl(name: str) -> str:
    return (WEB / "templates" / name).read_text(encoding="utf-8")


def _block(css: str, head: str) -> str:
    """The declaration block that starts at the (unique) selector text `head`."""
    assert css.count(head) == 1, (head, css.count(head))
    i = css.index(head)
    return css[i:css.index("}", i)]


def _node(script: str, *args: str) -> subprocess.CompletedProcess:
    node = shutil.which("node")
    if node is None or subprocess.run([node, "-e", "require('jsdom')"], capture_output=True,
                                      cwd=str(ROOT)).returncode != 0:
        pytest.skip("node + jsdom not available")
    return subprocess.run([node, str(TESTS / script), *args], capture_output=True, text=True,
                          encoding="utf-8", cwd=str(ROOT), timeout=120)


def _chip(tmp_path: Path, pairs: dict | None = None):
    """A minimal loaded chip and its test client."""
    from quam_state_manager.web.app import create_app
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "state.json").write_text(json.dumps({
        "qubits": {"q1": {"id": "q1", "grid_location": "0,0"},
                   "q2": {"id": "q2", "grid_location": "1,0"}},
        "qubit_pairs": pairs or {},
    }), encoding="utf-8")
    (state_dir / "wiring.json").write_text("{}", encoding="utf-8")
    client = create_app(testing=True, instance_path=str(tmp_path / "instance")).test_client()
    client.post("/load", data={"folder": str(state_dir)})
    return client


class TestL1CompactSearchPlaceholder:
    """At 1280 px the topbar box showed "Search: s" and the Calibration log box
    cut "a qubit" to "a qul": the placeholder carried the whole grammar."""

    def test_the_placeholder_is_one_word_and_the_grammar_lives_in_the_title(self):
        from quam_state_manager.core.search_query import search_hint, search_title
        assert search_hint() == "Search"
        assert search_hint("tag:", "is:") == "Search: tag:, is:"
        title = search_title()
        assert "AND" in title and "standalone |" in title

    def test_the_calibration_log_box_names_its_scope_in_the_title_only(self):
        tpl = _tpl("_journal.html")
        tag = re.search(r'<input type="search" name="q" id="jr-q"[^>]*>', tpl, re.S).group()
        assert 'placeholder="{{ search_hint() }}"' in tag
        assert "search_title('a qubit, pair, node or path')" in tag


class TestL2MapEdgesUnderTheStones:
    """Pairs / Flux / Resonators dim the stones to 50 % opacity, so the pair
    lines showed THROUGH them, across the qubit ids and the M/C/T letters. An
    opaque disc under each dimmed stone hides the line's middle."""

    def test_paint_order_in_all_three_views(self):
        res = _node("v130_p2_map_selfcheck.cjs")
        assert res.returncode == 0, res.stdout + res.stderr

    def test_the_disc_is_opaque_and_inert(self):
        rule = _block(_css(), ".cm-occluder {")
        assert "fill: var(--pico-card-background-color" in rule and "pointer-events: none" in rule
        assert "opacity" not in rule


class TestL4ExperimentChipsBreakAtUnderscores:
    """The Datasets experiment tiles broke names mid-word ("spectrosc / opy")."""

    def test_the_name_carries_soft_breaks_inside_one_flex_item(self):
        tpl = _tpl("_datasets.html")
        assert tpl.count("<span>{{ exp | soft_breaks }}</span></span>") == 2
        # the chip is a flex container: bare <wbr>s would split the name into
        # separate flex items, hence the inner span

    def test_no_break_anywhere(self):
        rule = _block(_css(), ".exp-chip {\n")
        assert "overflow-wrap: normal" in rule and "anywhere" not in rule


class TestL5VersionPathsBreakAtDots:
    """The Versions quick-diff broke dot-paths mid-word (word-break: break-all)."""

    def test_a_dot_path_renders_escaped_with_a_break_after_each_dot(self, tmp_path):
        from flask import render_template_string
        from quam_state_manager.web.app import create_app
        cell = re.search(r'<td class="sv-q-path">.*?</td>', _tpl("_state_versions.html"), re.S).group()
        app = create_app(testing=True, instance_path=str(tmp_path / "instance"))
        with app.test_request_context():
            body = render_template_string(cell, e={"dot_path": "qubits.q1.<img>"})
        assert body.count(".<wbr>") == 2
        assert "&lt;img&gt;" in body and "<img>" not in body
        assert re.sub(r"<[^>]+>", "", body) == "qubits.q1.&lt;img&gt;"

    def test_no_break_all(self):
        rule = _block(_css(), ".sv-q-path code {")
        assert "break-all" not in rule and "anywhere" not in rule


class TestL6L27SplitGutterAndPresets:
    """L6: the three split buttons (20 px tall) sat on a 6 px gutter, over the
    selected run's row. L27: opening a run left about two rows of the run list."""

    @pytest.mark.parametrize("mode", ["gutter", "preset", "context"])
    def test_layout_logic(self, mode):
        res = _node("v130_p2_layout_logic_selfcheck.cjs", mode)
        assert res.returncode == 0, res.stdout + res.stderr

    def test_the_buttons_fit_the_gutter(self):
        css = _css()
        gutter = int(re.search(r"--split-gutter-size:\s*(\d+)px", css).group(1))
        button = int(re.search(r"--inspector-toggle-h:\s*(\d+)px", css).group(1))
        assert gutter >= button + 2


class TestL9MainPaneOverflowAt1280:
    def test_the_qubits_table_is_framed(self):
        """The Qubits table (1,335 px of metric columns) pushed the whole pane
        416 px sideways; it now scrolls inside the house .table-scroll frame."""
        tpl = _tpl("_qubits.html")
        assert '<div class="table-scroll">\n<table id="qubits-table"' in tpl.replace("\r\n", "\n")
        assert "overflow-x: auto" in _block(_css(), ".table-scroll {")

    def test_pulse_operation_names_break_at_underscores(self, tmp_path):
        """Pulses: one unbreakable operation name held the table 37 px wider
        than the pane. A soft break after each underscore lets it fit; the
        visible text is unchanged."""
        from flask import render_template_string
        from quam_state_manager.web.app import create_app
        tpl = _tpl("_pulse_row.html")
        assert "<td>{{ r.op_name | soft_breaks }}" in tpl
        app = create_app(testing=True, instance_path=str(tmp_path / "instance"))
        with app.test_request_context():
            out = render_template_string("{{ name | soft_breaks }}", name="population_loss_<probe>")
        assert out == "population_<wbr>loss_<wbr>&lt;probe&gt;"

    def test_the_pulses_table_is_not_framed(self):
        """Not the .table-scroll frame: overflow-x:auto computes overflow-y to
        auto, so the virtual "All" view's closestScroller() would take the
        frame (which never scrolls vertically) for its scroller."""
        tpl = _tpl("_pulse_rows.html")
        assert "table-scroll" not in tpl
        vt = (WEB / "static" / "pulses-vt.js").read_text(encoding="utf-8")
        assert "if (/(auto|scroll)/.test(cs.overflowY)) return n;" in vt


class TestL10SelectChevronLane:
    """Diff and Trends selects drew Pico's chevron over their own text: a
    compact padding shorthand had dropped the right padding it lives in."""

    @pytest.mark.parametrize("head", [".trends-filters select {", ".diff-side select {"])
    def test_right_padding_keeps_the_chevron_lane(self, head):
        rule = _block(_css(), head)
        pad = re.search(r"padding:\s*([^;]+);", rule).group(1).split()
        assert len(pad) == 4 and pad[1] == "1.8rem", pad


class TestL11L13HistoryHeaders:
    def test_roll_back_stays_together(self):
        """L11: the State History subtitle left "back" alone on a line."""
        assert "roll&nbsp;back</small>" in _tpl("_state_history.html")

    def test_the_param_history_title_comes_before_its_notices(self):
        """L13: the cache/lazy notices rendered above the page title."""
        tpl = _tpl("_param_history_changes.html")
        assert tpl.index('<div class="param-history-header">') < tpl.index("'_hub_notes.html'")


class TestL12HelpHeaderAlignment:
    """The Help page borrows .welcome (centred) for the shared manual body: its
    intro line was centred under a left title and the subtitle sat far right."""

    def test_left_aligned_with_the_subtitle_on_the_title_baseline(self):
        css = _css()
        assert "text-align: left" in _block(css, ".help-page.welcome {")
        row = _block(css, ".help-page > .table-header-row {")
        assert "justify-content: flex-start" in row and "align-items: baseline" in row
        # the no-config Welcome page keeps its centred hero
        assert "text-align: center" in _block(css, ".welcome { max-width")


class TestL15SourceDotsOnActiveChips:
    """Light theme: the blue (save) and green (restore) dots vanished into an
    active source chip's primary fill."""

    def test_an_active_chip_rings_its_dot(self):
        rule = _block(_css(), ".phf-chip.active .trigger-dot {")
        assert "box-shadow: 0 0 0 1.5px var(--pico-primary-inverse)" in rule


class TestL16BrowseBadgeContrast:
    """"This folder contains state.json + wiring.json": primary text on the
    primary-focus tint measured 2.97:1 in the light theme."""

    def test_the_badge_uses_body_text_colour(self):
        rule = _block(_css(), ".browser-quam-badge {")
        assert "color: var(--pico-color)" in rule and "color: var(--pico-primary)" not in rule


class TestL17ValueHistoryClock:
    """The value-history clock rested at opacity .3 -- 1.7-2.1:1 in the light
    theme (pixels, real Chrome), hard to find; .6 clears 3:1 in both themes."""

    def test_rest_is_discoverable_and_hover_still_rises(self):
        css = _css()
        rest = float(re.search(r"opacity:\s*(\.\d+|\d(?:\.\d+)?);",
                               _block(css, ".field-hist-btn {")).group(1))
        i = css.index("tr:hover .field-hist-btn,")
        hover = float(re.search(r"\{ opacity:\s*(\.\d+|\d(?:\.\d+)?); \}", css[i:i + 200]).group(1))
        assert rest >= 0.6, rest
        assert hover > rest, (hover, rest)


class TestL19VersionRowsShareColumns:
    """Each version row laid out its own flex row, so the buttons and the
    metadata started wherever that row's date ended."""

    def test_rows_are_subgrids_of_one_three_column_list(self):
        css = _css()
        lst = _block(css, ".state-versions-list { list-style")
        assert "display: grid" in lst
        assert "grid-template-columns: max-content max-content minmax(0, 1fr)" in lst
        row = _block(css, ".state-version-row { display: grid")
        assert "grid-template-columns: subgrid" in row and "grid-column: 1 / -1" in row
        # Pico's nav list marker would take the first cell of every row
        assert "content: none" in _block(css, ".state-version-row::before {")

    def test_the_list_keeps_inside_the_panel(self):
        """Pico's `nav ul:first-of-type` / `:last-of-type` (0,1,2) gave the list
        -10px side margins that beat its own `margin: 0` (0,1,0); the rows ran
        10 px past the panel on each side (measured 891 px in an 871 px panel)."""
        assert "margin: 0" in _block(_css(), ".state-versions .state-versions-list {")

    def test_the_template_row_has_exactly_three_cells(self):
        tpl = _tpl("_state_versions.html")
        i = tpl.index('<li class="state-version-row')
        row = tpl[i:tpl.index("</li>", i)]
        top = re.findall(r'\n            <(label|span) class="(sv-pick|sv-row-actions|sv-meta)"',
                         row.replace("\r\n", "\n"))
        assert [c for _, c in top] == ["sv-pick", "sv-row-actions", "sv-meta"]


class TestL20DiagnosticsIssueColumn:
    """At 1280 px the nowrap action buttons squeezed the Issue text to a strip."""

    def test_the_issue_cell_has_a_floor_and_the_actions_stack(self, tmp_path):
        css = _css()
        assert "min-width: 16rem" in _block(css, ".diag-results .diag-issue-cell {")
        action = _block(css, ".diag-results .diag-action {")
        assert "white-space: normal" in action and "width: 12rem" in action
        tpl = _tpl("_diagnostics_list.html")
        assert '<th class="diag-issue-cell">Issue</th>' in tpl
        assert '<td class="diag-issue-cell">' in tpl


class TestL21PulseWaveformColumn:
    """The Waveform column wrapped "-> x90_DragCosine" while Class stayed wide."""

    def test_the_alias_target_does_not_wrap(self):
        assert "white-space: nowrap" in _block(_css(), ".pulse-alias-target {")


class TestL22NoCouplersNoHighlight:
    """A chip without couplers drew the full chip map "highlighted"."""

    def test_no_map_and_one_honest_empty_row(self, tmp_path):
        client = _chip(tmp_path, pairs={"q1-q2": {"id": "q1-q2", "qubit_control": "#/qubits/q1",
                                                  "qubit_target": "#/qubits/q2"}})
        body = client.get("/couplers", headers={"HX-Request": "true"}).get_data(as_text=True)
        assert "No couplers found in this state file." in body
        assert 'id="component-map"' not in body and "coupler edges highlighted" not in body

    def test_a_chip_with_couplers_keeps_the_map(self):
        tpl = _tpl("_couplers.html").replace("\r\n", "\n")
        assert "{% if total %}\n{% set cmap_highlight = 'couplers' %}" in tpl


class TestL23PageHeaderToggleClearance:
    """The pane's top-right header toggle overlapped "Modify wiring..."."""

    def test_the_wiring_header_keeps_the_toggle_lane(self):
        assert '<div class="table-header-row iw-header">' in _tpl("_instrument_wiring.html")
        assert "padding-right: 1.8rem" in _block(_css(), ".iw-header {")


class TestL24RunDetailTabs:
    def test_figures_tab_context_starts_closed(self):
        """Two open 400 px context boxes pushed the first figure below the fold."""
        tpl = _tpl("_dataset_detail.html").replace("\r\n", "\n")
        i = tpl.index('<div class="fig-info-row ds-fig-context">')
        row = tpl[i:tpl.index('<div class="figure-grid">', i)]
        assert row.count('<div class="fig-info-col">') == 2
        outer = re.findall(r'<div class="fig-info-col">\s*\{% if [^%]+%\}\s*<details( open)? class="detail-section">',
                           row)
        assert outer == ["", ""], outer

    def test_a_tall_result_row_keeps_its_name_at_the_top(self):
        """A nested result renders as a tall tree; centred, its name sat far
        below the tree's top and the tree read as an unlabelled '+' node."""
        assert "vertical-align: top" in _block(_css(), ".dataset-detail .prop-table td {")

    def test_a_wide_result_tree_scrolls_in_its_cell(self):
        """A tree with one long line set the Value column to its full width:
        a 1,273 px table in a 919 px pane, the whole tab scrolling sideways."""
        rule = _block(_css(), ".dataset-detail .prop-table td.col-val > .json-tree {")
        assert "contain: inline-size" in rule
        assert "overflow: auto" in _block(_css(), ".json-tree {\n    font-family")

    def test_raw_data_cards_share_columns(self):
        rule = _block(_css(), ".ndv-vars {")
        assert "display: grid" in rule and "repeat(auto-fill, minmax(16rem, 1fr))" in rule

    def test_state_tab_buttons_stay_one_line(self):
        css = _css()
        assert "flex-wrap: wrap" in _block(css, ".ds-state-action-bar {\n    display: flex")
        btn = _block(css, ".ds-state-action-bar > button {")
        assert "white-space: nowrap" in btn and "flex: none" in btn

    def test_the_inspector_reserves_its_scrollbar(self):
        """Prev State shifted 15 px sideways when the scrollbar went away."""
        assert "scrollbar-gutter: stable" in _block(_css(), "#inspector-pane {\n    overflow-y")


class TestL25FinishedIsNotSuccess:
    """The run list painted the lifecycle word "finished" green next to a
    failed outcome; finished is neutral now, success/fail keep their colours."""

    def test_status_chip(self):
        res = _node("v130_p2_layout_logic_selfcheck.cjs", "status")
        assert res.returncode == 0, res.stdout + res.stderr

    def test_the_css_and_the_detail_agree(self):
        css = _css()
        assert "var(--pico-muted-color)" in _block(css, ".ds-status-done {")
        assert "var(--pico-muted-color)" in _block(css, ".status-finished {")
        tpl = _tpl("_dataset_detail.html")
        assert ('<span class="outcome-badge{% if run.status != \'finished\' %} outcome-fail{% endif %}">'
                '{{ run.status }}</span>') in tpl
        assert "outcome-ok{% else %}outcome-fail" not in tpl.split("<code>status</code>", 1)[1][:400]
