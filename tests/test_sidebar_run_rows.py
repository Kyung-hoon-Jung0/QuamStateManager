"""docs/157 — the sidebar's run rows, larger, with the run number as a badge.

Customer: the run number was hard to spot and the names too small; two-line
names are fine. Pins: the ``soft_breaks`` filter (word-joint wrapping), the
tree macro using it, and the stylesheet's row tokens + badge + one-flow row.
"""

from __future__ import annotations

import re
from pathlib import Path

from markupsafe import Markup

from quam_state_manager.web.app import create_app

_ROOT = Path(__file__).resolve().parent.parent
_CSS = (_ROOT / "quam_state_manager" / "web" / "static" / "style.css").read_text(encoding="utf-8")
_MACROS = (_ROOT / "quam_state_manager" / "web" / "templates" / "_sidebar_tree_macros.html").read_text(encoding="utf-8")


def _css_rule(selector: str) -> str:
    """The FIRST rule body for an exact selector line (comments stripped)."""
    css = re.sub(r"/\*.*?\*/", "", _CSS, flags=re.S)
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert m, selector
    return m.group(1)


def _token(name: str) -> str:
    m = re.search(r"^\s*" + re.escape(name) + r":\s*([^;]+);", _CSS, re.M)
    assert m, name
    return m.group(1).strip()


class TestSoftBreaks:
    def _filter(self, tmp_path):
        app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
        return app.jinja_env.filters["soft_breaks"]

    def test_a_break_opportunity_after_every_underscore(self, tmp_path):
        f = self._filter(tmp_path)
        out = f("34b_cz_phase_compensation_error_amp")
        assert isinstance(out, Markup)
        assert str(out) == "34b_<wbr>cz_<wbr>phase_<wbr>compensation_<wbr>error_<wbr>amp"

    def test_no_underscore_is_unchanged_and_none_is_empty(self, tmp_path):
        f = self._filter(tmp_path)
        assert str(f("power rabi cal")) == "power rabi cal"
        assert str(f(None)) == ""
        assert str(f(7)) == "7"

    def test_every_piece_is_escaped(self, tmp_path):
        """The name comes from a folder on disk — it is HTML-escaped piecewise,
        and the only markup in the output is the <wbr> the filter adds."""
        f = self._filter(tmp_path)
        out = str(f('a<b>_"c"&d'))
        assert out == "a&lt;b&gt;_<wbr>&#34;c&#34;&amp;d"
        assert "<b>" not in out

    def test_the_tree_macro_uses_it_and_the_title_stays_plain(self, tmp_path):
        assert "{{ entry.experiment_name | soft_breaks }}" in _MACROS
        assert 'title="{{ entry.experiment_name }}"' in _MACROS
        # rendered: the visible text carries <wbr>, the title attribute does not
        app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
        with app.app_context(), app.test_request_context("/"):
            from types import SimpleNamespace
            from flask import render_template_string
            entry = SimpleNamespace(run_id=42, experiment_name="18b_xy_coupler_delay",
                                    quam_state_path="/w/#42/quam_state", folder_path="/w/#42")
            app.jinja_env.globals.setdefault("ds_entry_uid", lambda e: "uid")
            html = render_template_string(
                "{% from '_sidebar_tree_macros.html' import entry_rows %}{{ entry_rows([entry]) }}",
                entry=entry)
        assert 'title="18b_xy_coupler_delay"' in html
        assert ">18b_<wbr>xy_<wbr>coupler_<wbr>delay</span>" in html
        assert '<span class="run-id">#42</span>' in html


class TestRowStyle:
    def test_rows_are_larger_and_the_group_header_is_not_smaller(self):
        """docs/157 raised these, docs/165 raised them again (customer: still
        "눈에 잘 안보인다"). The literal is pinned so a change is deliberate, and
        the INVARIANTS are pinned separately so they survive the next raise."""
        rows = _token("--tree-entry-label-font")
        # 2026-09-05, customer: docs/165's 1.32 was liked, "아주 조금만 더 작게".
        # One 4.5% step, measured on 350 real rows.
        assert rows == "1.26em", "docs/165's 1.32em, stepped down once"
        # the date header is never smaller than the rows beneath it
        assert _token("--tree-date-label-font") == rows
        # the badge and the name are each 1em OF THE ROW, so they follow it
        # without a second number to keep in step
        assert _token("--tree-run-id-font") == "1em"
        assert _token("--tree-entry-name-font") == "1em"
        assert _token("--tree-entry-pad-v") == "0.12rem"

    def test_the_row_sets_its_own_leading(self):
        """The customer read the rows as too far apart. Measured in real
        Chrome on 350 rows: the gap between them is ZERO -- what they saw is
        the row's own height, and 66% of rows wrap. The line box was 30.97px
        rather than the 26.84px `.entry-name`'s own 1.3 implies, because the
        STRUT of the block parent inherited the page's 1.5 and won. Naming the
        dense value on the row is what moves it: 64.26 -> 47.51px average, 14.2
        -> 19.2 rows in one sidebar viewport."""
        rule = _css_rule(".tree-entry-label")
        assert "line-height: var(--dense-line-height)" in rule, rule
        # the badge must not push line one back out
        assert "line-height: 1.15" in _css_rule(".run-id")
        # nor the 22px checkbox, which at the tighter leading became what set
        # a one-line row's height
        assert "margin: 0.08em 0 0 0" in _css_rule(".tree-entry-label input[type=checkbox]")

    def test_the_open_run_is_three_cues_not_a_slab(self):
        """It was `background: var(--pico-primary-background)` + inverse text --
        on a three-line row, a solid block -- and it MASKED the app's other,
        subtler active treatment (a 16%/36% tint plus a bold name), so the
        codebase carried two selected-row styles and one was dead. Tint + the
        house accent bar + bold, and the bar is an inset shadow so selecting a
        row costs zero layout (measured: row height and name offset identical
        with and without)."""
        active = _css_rule(".tree-entry-active")
        assert "background" not in active, active
        assert "color" not in active, active
        row = _css_rule(".tree-entry-label:has(.tree-entry-active)")
        assert "color-mix(in srgb, var(--pico-primary) 16%" in row
        assert "box-shadow: inset 3px 0 0 0 var(--pico-primary)" in row
        # the dark-theme tint is still the brighter one
        assert "36%" in _css_rule('[data-theme="dark"] .tree-entry-label:has(.tree-entry-active)')

    def test_a_whole_row_does_not_underline_on_hover(self):
        """The row already tints and the cursor is already a pointer. Keyboard
        focus keeps its own outline -- a different job, untouched."""
        assert "text-decoration: none" in _css_rule(".tree-entry-click:hover")

    def test_the_date_headers_line_up(self):
        """`2026-08-19 (467)` in a proportional font means seven headers that
        do not align. `.run-id` already does this."""
        assert "font-variant-numeric: tabular-nums" in _css_rule(".tree-date-label")

    def test_the_row_holding_level_pins_its_own_header(self):
        """A date group is 3,630px on the real archive -- four sidebar screens,
        48 after "Show all" -- so its header scrolled away and took the only
        way to collapse the group with it.

        `top` cancels the sidebar's own padding rather than being 0: with
        `top: 0` a run row paints in the 8.4px strip above the pinned header,
        because `overflow: auto` does not clip at the padding-box edge.

        The background is NOT decoration. `.tree-dir.tree-leafdir > summary`
        is (0,2,1) and beats `details.tree-branch-active > summary` at (0,1,2),
        so without publishing the tint as a token the active branch would lose
        its colour the moment this rule shipped."""
        assert "{% set _leafdir = node.entries and not node.children %}" in _MACROS
        assert "tree-leafdir" in _MACROS
        rule = _css_rule(".tree-dir.tree-leafdir > summary")
        assert "position: sticky" in rule
        assert "top: calc(-1 * var(--sidebar-pad-v))" in rule
        assert "background: var(--tree-summary-bg" in rule
        assert "--tree-summary-bg" in _css_rule("details.tree-branch-active > summary")

    def test_the_run_number_is_a_badge(self):
        rule = _css_rule(".run-id")
        assert "display: inline-block" in rule
        assert "font-weight: 700" in rule
        assert "font-variant-numeric: tabular-nums" in rule
        assert "background: color-mix(in srgb, var(--pico-primary) 13%, transparent)" in rule
        assert "border-radius: 4px" in rule

    def test_one_text_flow_not_a_flex_row(self):
        """Measured in the 260 px sidebar: a two-column flex row left 113 px
        for the name and wrapped even `38_two_qubit_xeb`. The badge is inline
        and the name flows after it, wrapping under it with the full width."""
        click = _css_rule(".tree-entry-click")
        assert "display: block" in click and "flex-wrap" not in click
        name = _css_rule(".entry-name")
        assert "display: inline" in name and "overflow-wrap: anywhere" in name
        # compact mode keeps its one-line ellipsis contract — which the <wbr>s
        # would break (a wbr is a break opportunity even under nowrap; measured
        # 51–73 px compact rows), so compact hides them
        compact = _css_rule("body.exp-list-compact .entry-name")
        assert "white-space: nowrap" in compact and "text-overflow: ellipsis" in compact
        assert "display: none" in _css_rule("body.exp-list-compact .entry-name wbr")

    def test_the_sidebar_default_grew_with_the_rows(self):
        assert _token("--sidebar-width") == "300px"
        assert _token("--sidebar-max-width") == "420px"
        # the resizer's own clamp still reaches past the new max
        base = (_ROOT / "quam_state_manager" / "web" / "templates" / "base.html").read_text(encoding="utf-8")
        assert "Math.max(160, Math.min(640, startW + (e.clientX - startX)))" in base
