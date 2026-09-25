"""QA F4 -- the Datasets title row wraps instead of pushing its controls off
the pane.

Measured in real Chrome on the QA rig (KRISS + KH folders, 4,161 runs): at
1366x768 the search box, '?', Properties and Rescan sat past the pane's
clientWidth (scrollWidth 1278 vs 1045, a sideways scrollbar); at 1600x950 the
search box was 102 px wide and Rescan hung off the right edge. Three causes:

* the controls div was ``flex:1; min-width:0`` -- a ZERO flex basis, so the
  row's flex-wrap always found "room" for it beside the title and squeezed it
  to ~30 px (its children then overflowed);
* the title was ``white-space: nowrap`` as a whole, so it could not give way;
* the closed Properties menu, anchored ``left: 0`` under a button near the
  right edge, stuck ~30 px out of the pane.

jsdom has no layout, so these pins hold the three CSS facts the measurement
depended on (after the fix: 1366 -> controls on their own line, Rescan's
right edge 1025 <= 1045; 1600 -> 1258 <= 1279 with no sideways scroll; 1920
-> still one line beside the title).
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TPL = (ROOT / "quam_state_manager" / "web" / "templates" / "_datasets.html").read_text(encoding="utf-8")
CSS = (ROOT / "quam_state_manager" / "web" / "static" / "style.css").read_text(encoding="utf-8")


def _rule(selector: str) -> str:
    m = re.search(r"(?m)^" + re.escape(selector) + r"\s*\{([^}]*)\}", CSS)
    assert m, f"no CSS rule for {selector!r}"
    return m.group(1)


def _header_row() -> str:
    i = TPL.index('<div class="table-header-row">')
    return TPL[i:TPL.index('id="dataset-search"', i)]


def test_the_controls_have_a_real_flex_basis():
    row = _header_row()
    assert 'class="ds-head-controls"' in row
    assert "flex:1;min-width:0" not in row.replace(" ", ""), \
        "a zero-basis controls div is squeezed beside the title instead of wrapping"
    decl = _rule(".ds-head-controls")
    m = re.search(r"flex:\s*1\s+1\s+([\d.]+)rem", decl)
    assert m, decl
    # >= 20rem: search + Properties + Rescan stay usable; <= 25rem: the
    # controls still fit beside the title on a 1920 px screen (measured
    # 525 px free at the app's 21 px root size).
    assert 20 <= float(m.group(1)) <= 25, decl
    assert "min-width: 0" in decl and "display: flex" in decl


def test_the_title_wraps_between_its_segments_not_inside_them():
    row = _header_row()
    h2 = re.search(r"<h2[^>]*>", row).group(0)
    assert "nowrap" not in h2, h2
    assert 'class="ds-head-title"' in h2
    assert re.search(r"white-space:\s*nowrap", _rule(".ds-head-title > small"))
    # the run-count contract dataset-virtual.js rewrites
    # (`.table-header-row h2 > small`, /^\(\d+ runs/) is kept
    assert re.search(r'<h2 class="ds-head-title">\{\{ page_title \}\} '
                     r'<small class="muted">\(\{\{ total \}\} runs', row)


def test_the_column_menu_opens_leftward_inside_the_pane():
    decl = _rule(".ds-colvis .bulk-colvis-menu")
    assert re.search(r"left:\s*auto", decl) and re.search(r"right:\s*0", decl), decl
