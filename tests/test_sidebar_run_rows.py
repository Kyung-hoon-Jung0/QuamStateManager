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
        assert rows == "1.32em", "docs/165 raised the rows 1.06 -> 1.32em"
        # the date header is never smaller than the rows beneath it
        assert _token("--tree-date-label-font") == rows
        # the badge and the name are each 1em OF THE ROW, so they follow it
        # without a second number to keep in step
        assert _token("--tree-run-id-font") == "1em"
        assert _token("--tree-entry-name-font") == "1em"
        assert _token("--tree-entry-pad-v") == "0.18rem"

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
