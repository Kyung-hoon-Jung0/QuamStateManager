"""QA liveedit-r2-19: the Live State Edit header stack on a short window.

On a 1366x768 laptop at 125% (1093x614) the grid's first row opened at
y=737, below the fold. Three Pico style leaks inside /bulk cost ~120 px at
every width (measured in real Chrome on the KRS 5Q chip copy, first-row top
before -> after: 1707x768 626 -> 503, 1280x620 721 -> 570, 1093x614
737 -> 578, 1024x640 743 -> 584):

- Pico's `[role=group]{width:100%; margin-bottom:var(--pico-spacing)}` made the
  Table/Flat switch a full-width bar on a line of its own;
- Pico's `details{margin-bottom:1rem}` padded the pickers row;
- the chip scroller's `flex-basis:auto` (its whole content width) pushed the
  AND button onto a line of its own above the chips.

A max-height block then trims spacing and type only -- nothing is hidden.
The 200+ px top bar (docs/203) is out of this page's reach and not changed.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSS = (ROOT / "quam_state_manager/web/static/style.css").read_text(encoding="utf-8")


def _decls(selector: str) -> str:
    m = re.search(r"(?:^|\n)" + re.escape(selector) + r"\s*\{([^}]*)\}", CSS)
    assert m, f"no rule for {selector!r}"
    return m.group(1)


def test_table_flat_switch_sits_on_the_title_row():
    d = _decls(".bulk-toolbar-title > .bulk-segmented")
    for want in ("flex: 0 0 auto", "width: auto", "margin-bottom: 0"):
        assert want in d, (want, d)


def test_doc_badges_drop_the_group_margin():
    assert "margin-bottom: 0" in _decls(".bulk-toolbar-title > .bulk-docbadges")


def test_the_pickers_drop_the_details_margin_on_this_page_only():
    # scoped: Datasets reuses details.bulk-colvis in its own toolbar
    assert "margin-bottom: 0" in _decls(".bulk-panel .bulk-toolbar > details.bulk-colvis")
    assert not re.search(r"(?:^|\n)details\.bulk-colvis\s*\{", CSS)


def test_the_and_button_shares_the_chips_line():
    assert "flex: 1 1 0" in _decls(".bulk-chipbar > .bulk-chip-scroll")


def test_a_short_window_trims_spacing_and_hides_nothing():
    i = CSS.index("@media screen and (max-height: 760px)")
    block = CSS[i:CSS.index("\n}\n", i)]
    assert ".bulk-toolbar-title h2 { font-size: 1.3rem; }" in block
    assert "display: none" not in block and "visibility" not in block


def test_the_pinned_toolbar_rule_is_still_the_first_match():
    """test_single_scroll's `_rule('.bulk-panel .bulk-toolbar')` reads the FIRST
    match; a new rule spelled that way above it would make that pin read ours."""
    m = re.search(re.escape(".bulk-panel .bulk-toolbar") + r"\s*\{([^}]*)\}", CSS)
    assert m and "z-index: 8" in m.group(1), m and m.group(1)
