"""QA generate-r2-23: at 150% browser zoom the top bar took a third of the window.

A 1600x950 screen at 150% zoom is 1066x633 CSS px. The top bar's left group
wraps to three rows there, and Pico pads every ``nav li`` 1rem top AND bottom --
a spacing sized for a one-line bar, paid again on every wrapped row. Measured in
real Chrome on the customer chip copy: 231 of 633 px (35%) before the page
began; the Generate wizard's step content started below the fold.

The fix spends less on that padding on a short or narrow window (the rows go
from ~75 to ~45 px: 231 -> 140 px at 1066x633, 1366x768 likewise) and hides
nothing. jsdom computes no layout; the measurement is the real-browser rig's.
"""
from __future__ import annotations

import re
from pathlib import Path

_CSS = (Path(__file__).resolve().parents[1] / "quam_state_manager" / "web" / "static"
        / "style.css").read_text(encoding="utf-8")


def _block() -> str:
    m = re.search(r"@media screen and \(max-height: 800px\), screen and \(max-width: 1150px\) \{", _CSS)
    assert m, "the short/narrow-window top-bar block is missing"
    end = _CSS.index("\n}", m.end())
    return _CSS[m.end():end]


def test_a_short_or_narrow_window_trims_the_bar_row_padding():
    blk = _block()
    rule = re.search(r"\.topbar > nav > ul > li \{([^}]*)\}", blk)
    assert rule, blk
    decls = dict((k.strip(), v.strip()) for k, v in
                 (d.split(":", 1) for d in rule.group(1).split(";") if ":" in d))
    for side in ("padding-top", "padding-bottom"):
        v = decls.get(side, "")
        m = re.fullmatch(r"([\d.]+)rem", v)
        assert m and float(m.group(1)) <= 0.3, f"{side} is {v!r}; Pico's 1rem per row is the defect"


def test_it_hides_nothing_and_keeps_the_horizontal_spacing():
    blk = _block()
    assert "display" not in blk and "visibility" not in blk and "hidden" not in blk
    assert "padding:" not in blk.replace(" ", "").replace("padding-top", "").replace("padding-bottom", ""), \
        "only the vertical padding moves; the items keep their horizontal spacing"


def test_it_outranks_picos_nav_li_rule():
    """Pico's ``nav li`` is (0,0,2); the override must be more specific, not
    rely on source order against a stylesheet loaded before ours."""
    assert ".topbar > nav > ul > li {" in _block()
