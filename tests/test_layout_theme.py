"""Audit 2026-11-13, section 3 (layout / theme / keyboard).

- F13: a narrow window collapses the sidebar into an overlay without touching
  the stored wide-window choice; the OS colour scheme decides the theme only
  while nothing is stored (``tests/layout_theme_selfcheck.cjs``, real app.js
  + the real theme boot under jsdom).
- Compact run rows keep the name's last ``_`` part visible (``run_name``).
- Ctrl+Enter (docs/113) is named on every Apply button it presses.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app

_ROOT = Path(__file__).resolve().parent.parent
_WEB = _ROOT / "quam_state_manager" / "web"
_SELFCHECK = _ROOT / "tests" / "layout_theme_selfcheck.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_layout_theme_client_selfcheck():
    proc = subprocess.run(["node", str(_SELFCHECK)], capture_output=True, text=True,
                          cwd=str(_ROOT), timeout=120)
    if "Cannot find module 'jsdom'" in proc.stderr:
        pytest.skip("jsdom not installed")
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"


def _css_rule(selector: str) -> str:
    css = (_WEB / "static" / "style.css").read_text(encoding="utf-8")
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert m, selector
    return m.group(1)


class TestNarrowOverlayCss:
    def test_the_open_narrow_sidebar_is_an_overlay(self):
        rule = _css_rule(".app-layout.sidebar-narrow:not(.sidebar-collapsed) #sidebar")
        assert "position: fixed" in rule and "z-index" in rule
        assert "var(--pico-background-color)" in rule, "an overlay needs an opaque ground"

    def test_the_resizer_hides_in_narrow_mode(self):
        assert "display: none" in _css_rule(".sidebar-collapsed .sidebar-resizer, .sidebar-narrow .sidebar-resizer")


class TestRunNameTail:
    @pytest.fixture()
    def run_name(self, tmp_path):
        app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
        return app.jinja_env.filters["run_name"]

    def test_the_last_part_is_its_own_span(self, run_name):
        out = str(run_name("31_chevron_11_20"))
        # a last part under 4 characters widens the tail to the last two parts
        assert out == ('<span class="entry-name-lead">31_<wbr>chevron_<wbr></span>'
                       '<span class="entry-name-tail">11_<wbr>20</span>')
        assert re.sub(r"<[^>]+>", "", out) == "31_chevron_11_20", "textContent is the plain name"

    @staticmethod
    def _pieces(out):
        return {c: re.sub(r"<[^>]+>", "", t) for c, t in
                re.findall(r'<span class="entry-name-(\w+)">(.*?)</span>', out)}

    def test_the_lead_keeps_the_node_number_and_first_word(self, run_name):
        # audit recheck: 34a_cz_phase_compensation rendered as "3compensation"
        p = self._pieces(str(run_name("34a_cz_phase_compensation")))
        assert p == {"lead": "34a_cz_", "head": "phase_", "tail": "compensation"}
        p = self._pieces(str(run_name("37c_two_qubit_paired_interleaved_rb_dsp")))
        assert p == {"lead": "37c_two_", "head": "qubit_paired_interleaved_", "tail": "rb_dsp"}

    def test_only_the_head_ellipsizes(self):
        # lead and tail keep a flex item's min-width:auto (their nowrap text
        # width), so neither may gain an overflow rule that lets it shrink
        css = (_WEB / "static" / "style.css").read_text(encoding="utf-8")
        assert ".entry-name-lead" not in css and ".entry-name-tail" not in css

    def test_no_split_without_a_short_tail(self, run_name):
        assert str(run_name("rabi")) == '<span class="entry-name-head">rabi</span>'
        assert str(run_name("a_" + "x" * 13)).count("<span") == 1
        assert str(run_name("_lead")).count("<span") == 1

    def test_it_escapes(self, run_name):
        out = str(run_name('a<b>_"c"'))
        assert "<b>" not in out and "&lt;b&gt;" in out and "&#34;c&#34;" in out

    def test_the_tree_macro_uses_it(self):
        macros = (_WEB / "templates" / "_sidebar_tree_macros.html").read_text(encoding="utf-8")
        assert "{{ entry.experiment_name | run_name }}" in macros

    def test_compact_rows_ellipsize_only_the_head(self):
        assert "display: flex" in _css_rule("body.exp-list-compact .entry-name")
        head = _css_rule(".entry-name-head")
        assert "overflow: hidden" in head and "text-overflow: ellipsis" in head


class TestApplyShortcutIsNamed:
    def test_every_ctrl_enter_target_says_so(self):
        sync = (_WEB / "templates" / "_sync_control.html").read_text(encoding="utf-8")
        buttons = re.findall(r'<button type="button" class="sync-control-act btn-apply-live.*?</button>', sync, re.S)
        assert len(buttons) == 4
        for b in buttons:
            assert "Shortcut: Ctrl+Enter" in b, b
        bulk = (_WEB / "templates" / "_bulkedit.html").read_text(encoding="utf-8")
        m = re.search(r'<button[^>]*id="bulk-apply-all"[^>]*>', bulk, re.S)
        assert m and "Shortcut: Ctrl+Enter" in m.group(0)

    def test_the_cheat_sheet_lists_it(self):
        app = (_WEB / "static" / "app.js").read_text(encoding="utf-8")
        assert "['Ctrl+Enter', 'Apply:" in app
