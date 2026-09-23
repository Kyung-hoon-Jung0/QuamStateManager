"""docs/203 — a Live State Edit commit made the whole screen jump, and at some
widths shake.

Customer report: "live state에서 parameter 업데이트 할때 가끔 SM화면 전체가
마구 흔들린다". Measured in real Chrome on a copy of the customer's 5Q chip:

* The pending tray lives in the top bar's WRAPPING left group. A commit adds
  "● N unsaved · Review · Apply to live now", the group wraps one or two rows
  taller (135px at 1280/1366, 85 at 1707, 13 at 1920), and everything below
  moves by that. Undo / Apply / an auto-apply flush take it away again.
* The layout was sized ``calc(100vh - var(--topbar-height))`` from a number a
  ResizeObserver publishes AFTER the bar grew. For that frame the document
  overflowed, a window scrollbar appeared, the bar re-wrapped 15px narrower,
  and the publish landed — two or three painted shifts per commit plus a
  "ResizeObserver loop" error.

Two fixes, pinned here and in ``topbar_hold_selfcheck.cjs``:

1. The shell is a flex column (no document overflow is possible, so the
   scrollbar feedback cannot start).
2. ``window.TopbarHold`` holds the left group at the tallest height it reached
   at this width, so an edit/apply cycle never moves the page.

jsdom computes no layout; the effect itself is measured in Chrome (docs/203).
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_STATIC = _ROOT / "quam_state_manager" / "web" / "static"
_SELFCHECK = _ROOT / "tests" / "topbar_hold_selfcheck.cjs"


def _css() -> str:
    return (_STATIC / "style.css").read_text(encoding="utf-8")


def _js() -> str:
    return (_STATIC / "app.js").read_text(encoding="utf-8")


def _screen_shell_block() -> str:
    css = _css()
    m = re.search(r"@media screen \{\s*body:has\(> \.app-layout\) \{", css)
    assert m, "the flex-column app shell block is missing"
    # the block ends at the first line that is a bare closing brace
    end = re.search(r"\n\}", css[m.start():])
    return css[m.start(): m.start() + end.start()]


class TestTheShellCannotOverflow:
    def test_body_is_a_viewport_high_flex_column_that_never_scrolls(self):
        blk = _screen_shell_block()
        body = blk.split("body:has(> .app-layout) {", 1)[1].split("}", 1)[0]
        assert "height: 100vh" in body
        assert "overflow: hidden" in body
        assert "display: flex" in body and "flex-direction: column" in body

    def test_the_layout_takes_what_is_left(self):
        blk = _screen_shell_block()
        assert re.search(
            r"body:has\(> \.app-layout\) > \.app-layout \{[^}]*flex: 1 1 auto;[^}]*min-height: 0",
            blk)

    def test_everything_above_the_layout_keeps_its_own_height(self):
        blk = _screen_shell_block()
        assert re.search(r"body:has\(> \.app-layout\) > :not\(\.app-layout\) \{[^}]*flex: 0 0 auto", blk)

    def test_the_panels_stop_trusting_the_published_number(self):
        """#sidebar / #main still carry ``calc(100vh - var(--topbar-height))``
        as the no-:has fallback; inside the shell they must not, or the late
        publish would cap them again."""
        blk = _screen_shell_block()
        assert re.search(
            r"body:has\(> \.app-layout\) #sidebar,\s*body:has\(> \.app-layout\) #main \{[^}]*max-height: none",
            blk)

    def test_print_is_left_alone(self):
        css = _css()
        i = css.find("body:has(> .app-layout) {")
        head = css[:i]
        assert head.rstrip().endswith("@media screen {"), \
            "the shell must be screen-only: a printed page has to flow"


class TestTheBarHoldsItsHeight:
    def test_the_group_reads_the_hold_and_packs_rows_to_the_top(self):
        css = _css()
        m = re.search(r"\.topbar > nav > ul:first-child \{[^}]*min-height: var\(--topbar-hold, 0px\)[^}]*\}", css)
        assert m, "the left group must read the held height"
        assert "align-content: flex-start" in m.group(0), \
            "without it the held space is split between rows and the bar's items move"

    def test_a_hidden_bar_still_zeroes_the_group(self):
        """html.topbar-hidden's `min-height: 0` must outrank the hold rule
        (0,2,3 vs 0,2,2) — the hold is a variable for exactly this reason."""
        css = _css()
        assert re.search(
            r"html\.topbar-hidden \.topbar > nav,\s*html\.topbar-hidden \.topbar > nav > ul \{[^}]*min-height: 0",
            css)

    def test_the_module_is_wired(self):
        js = _js()
        blk = js.split("window.TopbarHold = (function () {", 1)[1][:4000]
        assert "ResizeObserver" in blk
        assert "htmx:pushedIntoHistory" in blk and "'popstate'" in blk
        assert "window.innerWidth === lastW" in blk, \
            "a height-only resize must keep the hold"
        assert "Math.ceil" not in blk.split("function release", 1)[0], \
            "a rounded-up hold is itself a resize: the observer loops"


def test_topbar_hold_selfcheck():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    if not (_ROOT / "node_modules" / "jsdom").exists():
        pytest.skip("jsdom not installed")
    r = subprocess.run([node, str(_SELFCHECK)], capture_output=True, text=True,
                       encoding="utf-8", timeout=120, cwd=str(_ROOT))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "all ok" in r.stdout


class TestBannersKeepTheirSpacing:
    """A flex container never collapses its items' margins. With the banner
    slots as direct children of the flex body, every gap between two stacked
    banners doubled (measured in Chrome: the layout 10px shorter with three
    banners up). The bar and the slots share one block box instead."""

    def _base(self) -> str:
        return (_ROOT / "quam_state_manager" / "web" / "templates" / "base.html"
                ).read_text(encoding="utf-8")

    def test_the_bar_and_every_banner_slot_live_in_one_block(self):
        html = self._base()
        head = html.split('<div class="shell-head">', 1)
        assert len(head) == 2, "the .shell-head wrapper is missing"
        inner, rest = head[1].split("</div>{# /.shell-head #}", 1)
        assert '<header class="topbar">' in inner
        for slot in ("live-diverged-slot", "disk-guard-slot", "multi-instance-slot",
                     "type-alarm-slot", "diagnostics-banner-slot"):
            assert f'id="{slot}"' in inner, f"{slot} escaped the block box"
        assert "_wc_gc_banner.html" in inner
        assert rest.lstrip().startswith('<div class="app-layout">'), \
            "the layout must follow the head directly"
