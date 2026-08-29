"""docs/141 §4u — the Calculator, Settings and the Config Manual are three
floating windows with one frame and one drag core.

The user's ask: make the Calculator and Settings float like the Config
Manual, with the same frame; Settings could not be dragged at all; and
opening Settings closed the Calculator ("a bug"). Pinned here: the core
script `float-panel.js` loads before the scripts that call it; the two
owners delegate their drag to it (the copied drag loop is gone from
calc.js and manual.js); Settings has a header (handle + close) and a
floating rule; the three panels share the frame rule; neither toggle
touches the other panel any more. The jsdom harnesses pin the behaviour
(float_panel_selfcheck.cjs: the core; sidebar_tools_selfcheck.cjs: the two
windows staying open together, Settings floating and surviving an outside
click once dragged).
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "quam_state_manager" / "web" / "static"
BASE = (ROOT / "quam_state_manager/web/templates/base.html").read_text(encoding="utf-8")
CSS = (STATIC / "style.css").read_text(encoding="utf-8")
APP = (STATIC / "app.js").read_text(encoding="utf-8")
CALC = (STATIC / "calc.js").read_text(encoding="utf-8")
MANUAL = (STATIC / "manual.js").read_text(encoding="utf-8")
FP = (STATIC / "float-panel.js").read_text(encoding="utf-8")


class TestWiring:
    def test_the_core_loads_before_its_callers(self):
        i = BASE.index("asset_url('float-panel.js')")
        assert BASE.index("asset_url('app.js')") < i < BASE.index("asset_url('calc.js')") < BASE.index("asset_url('manual.js')")

    def test_the_owners_delegate_to_the_core(self):
        assert "window.FloatPanel.drag(pop, { handle: head, tools: '.calc-header-tools', floatClass: 'calc-floating' });" in CALC
        assert "window.FloatPanel.drag(p, { handle: head, tools: '.manual-header-tools', floatClass: 'manual-floating' });" in MANUAL
        assert 'window.FloatPanel.drag(dd, { handle: head, tools: ".settings-header-tools", floatClass: "settings-floating" });' in APP
        # the copied drag loops are gone from the owners
        for src, name in ((CALC, "calc.js"), (MANUAL, "manual.js")):
            assert "document.addEventListener('mousemove', onMove);" not in src, name
        assert "document.addEventListener('mousemove', onMove);" in FP

    def test_settings_has_a_header_and_floats(self):
        assert 'id="settings-header"' in BASE and 'class="settings-header-tools"' in BASE
        assert BASE.index('id="settings-header"') > BASE.index('id="settings-dropdown"')
        assert ".settings-dropdown.settings-floating { position: fixed; right: auto; top: auto; margin-top: 0; }" in CSS
        assert ".settings-header {" in CSS and "cursor: grab" in CSS[CSS.index(".settings-header {"):CSS.index(".settings-header {") + 400]

    def test_the_three_panels_share_one_frame(self):
        i = CSS.index(".settings-dropdown, .calc-popover, .manual-popover {")
        rule = CSS[i:CSS.index("}", i)]
        assert "border: 1.5px solid color-mix(in srgb, var(--pico-primary) 60%, transparent)" in rule
        assert "border-radius: 10px" in rule and "box-shadow: 0 8px 28px" in rule

    def test_neither_toggle_closes_the_other(self):
        ts = APP[APP.index("window.toggleSettings = function(trigger) {"):APP.index("window.setFontSize = function")]
        assert "calc-hidden" not in ts
        assert 'if (dd.classList.contains("settings-floating")) return;' in ts   # a dragged window survives an outside click
        tc = CALC[CALC.index("window.toggleCalc = function (trigger) {"):CALC.index("function _calcOutside(e) {")]
        assert "settings-dropdown" not in tc and "settings-hidden" not in tc

    def test_the_outside_click_closers_ignore_the_other_tool(self):
        """The second path that closed the Calculator: its outside-click closer
        saw the click on the Settings button as "outside" (real Chrome)."""
        co = CALC[CALC.index("function _calcOutside(e) {"):CALC.index("function _calcOutside(e) {") + 900]
        assert "e.target.closest('.settings-btn, #settings-dropdown')" in co
        ts = APP[APP.index("window.toggleSettings = function(trigger) {"):APP.index("window.setFontSize = function")]
        assert 'e.target.closest(".settings-btn, .calc-btn, #calc-popover")' in ts

    def test_the_frame_rule_comes_after_the_panels_own_rules(self):
        """Same specificity: source order decides. Placed before .calc-popover's
        own 1 px / 6 px border, the calculator kept its old frame (real Chrome)."""
        i = CSS.index(".settings-dropdown, .calc-popover, .manual-popover {")
        assert i > CSS.index(".calc-popover {") and i > CSS.index(".manual-popover {") and i > CSS.index(".settings-dropdown {")


def _node(script: str, min_ok: int):
    node = shutil.which("node")
    try:
        subprocess.run([node, "-e", "require('jsdom')"], check=True, capture_output=True, timeout=30)
    except Exception:
        pytest.skip("jsdom not installed")
    r = subprocess.run([node, str(ROOT / "tests" / script)], capture_output=True, text=True, encoding="utf-8", timeout=180, cwd=str(ROOT))
    if r.returncode == 2:
        pytest.skip("jsdom not installed")
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.count("ok - ") >= min_ok, r.stdout


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_float_panel_selfcheck():
    _node("float_panel_selfcheck.cjs", 14)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_sidebar_tools_two_windows_selfcheck():
    _node("sidebar_tools_selfcheck.cjs", 20)
