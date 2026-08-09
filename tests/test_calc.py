"""Tests for the converter/calculator badge (feedback #2).

The math itself is verified by tests/calc_selfcheck.cjs (node, driven below when
node is present). These pin the mount + the no-eval security boundary + that the
topbar actually renders the badge.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app

_ROOT = Path(__file__).resolve().parent.parent
_BASE = _ROOT / "quam_state_manager" / "web" / "templates" / "base.html"
_CALC = _ROOT / "quam_state_manager" / "web" / "static" / "calc.js"
_STATIC = _ROOT / "quam_state_manager" / "web" / "static"


class TestCalcMarkup:
    def test_base_includes_calc_js_and_trigger(self):
        b = _BASE.read_text(encoding="utf-8")
        assert "calc.js" in b
        assert 'id="calc-btn"' in b and 'id="calc-popover"' in b

    def test_calc_lives_in_the_sidebar_tool_row(self):
        """docs/89 — the calculator moved out of the top-right rail (nobody
        found it there, and its ∵ glyph meant nothing) into the sidebar's tool
        row, with Settings above it. The popover is body-level, AFTER the
        sidebar, because #sidebar is overflow-y:auto and would clip it."""
        b = _BASE.read_text(encoding="utf-8")
        assert 'class="calc-wrap"' not in b and 'class="settings-wrap"' not in b
        i_tools = b.index('class="sidebar-tools"')
        i_btn = b.index('id="calc-btn"')
        i_aside = b.index("</aside>")
        i_pop = b.index('id="calc-popover"')
        assert i_tools < i_btn < i_aside < i_pop
        # Settings first inside the row
        assert b.index("Settings</span>") < b.index("Calculator</span>")

    def test_calc_js_never_uses_eval_or_function(self):
        # the free expression box is parsed by a whitelisted recursive-descent parser;
        # a topbar-global text input must never reach eval()/new Function (calcEval is
        # the function NAME, not a call to eval — match a bare eval( only).
        c = _CALC.read_text(encoding="utf-8")
        # strip comments so the doc-comment "NEVER eval()" can't trip the check
        code = re.sub(r"/\*.*?\*/", "", c, flags=re.S)
        code = re.sub(r"//[^\n]*", "", code)
        assert re.search(r"(?<![A-Za-z0-9_.])eval\s*\(", code) is None, "calc.js must not call eval()"
        assert "new Function" not in code


class TestCalcRenders:
    def test_topbar_renders_the_badge(self, tmp_path):
        app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
        body = app.test_client().get("/").get_data(as_text=True)
        assert 'id="calc-btn"' in body and 'id="calc-popover"' in body
        assert 'src="' in body and "calc.js" in body


class TestCalcPolish:
    """Round-2 polish: rename to Calculator (B1), section headers + draggable (B2/B3)."""

    def test_b1_renamed_to_calculator(self):
        b = _BASE.read_text(encoding="utf-8")
        assert '<strong class="calc-title">Calculator</strong>' in b
        assert 'aria-label="Calculator"' in b
        # no user-visible "Converter" left (the JS header comment is internal, ignored)
        assert ">Converter<" not in b and 'aria-label="Converter' not in b and 'title="Converter' not in b

    def test_b2_section_headers_bold_with_caret(self):
        css = (_STATIC / "style.css").read_text(encoding="utf-8")
        lbl = css[css.index(".calc-sec-label {"):][:200]
        assert "font-weight: 700" in lbl
        assert ".calc-sec-label::before" in css  # the rotating caret
        # edit boxes the user likes must stay byte-identical (12.5px mono)
        assert ".calc-popover .calc-in {" in css

    def test_b3_draggable(self):
        js = _CALC.read_text(encoding="utf-8")
        css = (_STATIC / "style.css").read_text(encoding="utf-8")
        assert "function enableDrag" in js and "calc-floating" in js
        assert "calc-header-tools" in js  # pin/close excluded from drag
        assert ".calc-popover.calc-floating" in css


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
class TestCalcFormulas:
    def test_node_selfcheck_passes(self):
        r = subprocess.run(
            ["node", str(_ROOT / "tests" / "calc_selfcheck.cjs")],
            capture_output=True, text=True, encoding="utf-8",
        )
        assert r.returncode == 0, (r.stdout + r.stderr)
