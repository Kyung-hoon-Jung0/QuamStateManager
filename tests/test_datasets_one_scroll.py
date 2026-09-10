"""One scrollbar on the Datasets page, and the Sort badges start folded.

Customer, 2026-09-11:

  "dataset 페이지에서 scrolling이 두번 분리되어서 너무 불편하다. 단 하나의
   global scrolling 만 할수있게 하자."
  "dataset 페이지 가면, 여러가지 sort badge들이 클릭할수있게 나온다. 이거
   default로는 그냥 접혀진 채로 나오게 하고, 대신 살짝 버튼을 진짜 조금만 살짝
   크기를 키우고 블루칼라 SM 특유의 modern한 블루칼라로 살짝만 포인트 주자."

MEASURED in real Chrome before the change, 1000 px viewport: `#table-pane`
scrolled (951 vs 818) AND `.datasets-scroll` scrolled — with a clientHeight of
**158 px**. The run table, which is what the page is for, had a 160 px window at
the bottom of the screen while the Experiments band above it took ~600 px. Two
scrollbars, and the wrong one was doing the work.

After: `#table-pane` is the only scroller (59,105 vs 818), the other two boxes
are `visible`, and the virtual window follows the pane — scrollTop 1500 puts
#1771 at the top, 20000 puts #1193.

The second item is not decoration. Folding a control away is only kind if the
way back is obvious, which is the trap docs/152 recorded on the Overview panel
(a hover-only kebab "nobody found"). So the toggle grew a little and took an
SM-blue tint at the same time as the band it opens went away.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_STATIC = _ROOT / "quam_state_manager" / "web" / "static"
_TPL = _ROOT / "quam_state_manager" / "web" / "templates"


def _css() -> str:
    return (_STATIC / "style.css").read_text(encoding="utf-8")


def _js() -> str:
    return (_STATIC / "dataset-virtual.js").read_text(encoding="utf-8")


def _strip_css_comments(text: str) -> str:
    return re.sub(r"/\*.*?\*/", " ", text, flags=re.S)


def _strip_js_comments(text: str) -> str:
    return "\n".join(ln for ln in text.splitlines()
                     if not ln.lstrip().startswith("//"))


def _rule(css: str, selector: str) -> str:
    """The declaration block of the LAST rule with this exact selector.

    Comments are stripped: a pin that trips on the sentence explaining WHY a
    declaration is absent is a pin nobody can leave a comment near — the same
    trap this session already hit once on ``rescan_if_stale``.
    """
    i = css.rindex(selector + " {")
    return _strip_css_comments(css[i:css.index("}", i)])


class TestOneScroller:
    def test_the_list_box_does_not_scroll(self):
        block = _rule(_css(), ".datasets-scroll")
        assert "overflow: visible" in block, block
        # …and NOT one axis only: CSS forces a non-visible value on the other
        # axis, so `overflow-x: auto` here silently restores the vertical
        # scrollbar this change removes.
        assert "overflow-y: auto" not in block
        assert "overflow-x: auto" not in block
        assert "max-height: none" in block

    def test_the_page_takes_its_natural_height(self):
        """`height: 100%` is what capped the table at whatever the banners
        left — 158 px, measured."""
        block = _rule(_css(), ".datasets-page")
        assert "height: 100%" not in block, block

    def test_the_header_anchors_to_the_pane_with_its_padding_subtracted(self):
        """`top: 0` sticks the header at the pane's CONTENT-box top and leaves
        the padding strip above it showing the row scrolling past — measured
        once the pane became the scroller. Same expression the two grids use."""
        block = _rule(_css(), ".datasets-table-virtual thead th")
        assert "top: calc(-1 * var(--table-pane-pad-v" in block, block

    def test_the_virtual_scroller_reads_the_pane(self):
        js = _js()
        assert "document.getElementById('table-pane') || scroll" in js
        assert "state.scrollEl.addEventListener('scroll'" in js

    def test_the_window_is_measured_from_the_list_not_the_scroller(self):
        """With the pane scrolling, the row list starts some way down it, so
        `scrollEl.scrollTop` is no longer 'how far into the list are we'."""
        js = _js()
        assert "function listMetrics()" in js
        i = js.index("function renderWindow(")
        body = _strip_js_comments(js[i:i + 700])
        assert "listMetrics()" in body
        assert "state.scrollEl.scrollTop" not in body, body
        # the two other places that asked the same question follow the same rule
        assert "listMetrics().top <= ROW_HEIGHT" in js


class TestTheSortBandStartsFolded:
    def test_absent_preference_means_folded(self):
        js = _js()
        i = js.index("function _restoreSortCollapsed()")
        body = _strip_js_comments(js[i:i + 800])
        assert "!== '0'" in body, body
        assert "=== '1'" not in body, (
            "reading it as `=== '1'` makes an ABSENT preference mean OPEN, "
            "which is how the page arrived with every badge row showing")

    def test_an_explicit_open_survives_a_reload(self):
        """Folded by default is not the same as folded always: a user who has
        opened the band keeps it open."""
        js = _js()
        i = js.index("window.toggleSortBannerCollapsed")
        body = _strip_js_comments(js[i:i + 400])
        assert "collapsed ? '1' : '0'" in body, body

    def test_the_markup_starts_closed_too(self):
        """Otherwise the band paints open for one frame before the script runs."""
        html = (_TPL / "_datasets.html").read_text(encoding="utf-8")
        i = html.index('id="sort-banner-toggle"')
        block = html[i:i + 400]
        assert 'aria-expanded="false"' in block, block

    def test_the_way_back_is_marked(self):
        """docs/152: a control folded behind something too quiet goes unfound.
        The toggle is bigger than its Experiments twin and carries the SM blue."""
        css = _css()
        block = _rule(css, "#sort-banner-toggle")
        assert "--pico-primary" in block
        base = _rule(css, ".exp-filter-toggle")
        base_size = re.search(r"font-size:\s*\.(\d+)em", base)
        mine = re.search(r"font-size:\s*\.(\d+)em", block)
        assert base_size and mine, (base, block)
        assert int(mine.group(1)) > int(base_size.group(1)), (
            "the Sort toggle must be a little LARGER than the plain one: "
            "%s vs %s" % (mine.group(0), base_size.group(0)))
        # …a tint, not a filled button — it sits in a toolbar beside date pills
        assert "color-mix" in block and "background: var(--pico-primary)" not in block


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_dataset_selfchecks_still_pass():
    """The virtual scroller's own harnesses, since this moved its scroller."""
    for name in ("dataset_poll_selfcheck.cjs", "newrun_poll_selfcheck.cjs"):
        p = _ROOT / "tests" / name
        if not p.is_file():
            continue
        r = subprocess.run(["node", str(p)], capture_output=True, text=True,
                           encoding="utf-8", cwd=str(_ROOT))
        if r.returncode == 2:
            pytest.skip("jsdom not installed")
        assert r.returncode == 0, (name, r.stdout + r.stderr)
