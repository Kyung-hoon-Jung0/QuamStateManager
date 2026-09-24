"""QA F27: the step-4 Pairs rail must hold a pair row without scrolling sideways.

A pair row (renderPairs in generate.js) is select, arrow, select, x -- sized
in rem (two 6rem selects, 0.5rem gaps) plus the x's fixed 26px box. The rail
that holds them had a PX flex basis (240px) while Pico scales the root from
16px up to 21px with the viewport, so at 1600 wide the row (309px) overran
the 308px rail by a pixel: a horizontal scrollbar, and the x -- a shrinkable
flex item -- squeezed to 12px at the edge.

The invariant pinned here is the flex one that actually decides it: under
``flex-wrap`` a rail that shares a line gets AT LEAST its basis, and one that
cannot wraps to a full-width line of its own. So a basis that covers the row
minimum at every root size means the rail never scrolls sideways. jsdom has
no layout engine; this reads the shipped CSS and does the arithmetic the
browser does (real-Chrome widths measured by the QA probe: rail 366/358/318px
at 1600/1366/1180, x 26px, scrollWidth == clientWidth).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

CSS = (Path(__file__).resolve().parent.parent / "quam_state_manager" / "web"
       / "static" / "style.css")

# Pico's root font-size ladder: 100% .. 131.25% of 16px across its breakpoints.
ROOTS_PX = (16.0, 17.0, 18.0, 19.0, 20.0, 21.0)
# The arrow span ("->" / "<->" glyph) measured 13.2px at a 21px root in real
# Chrome (0.63rem); allow 0.7rem.
ARROW_REM = 0.7


def _css() -> str:
    return re.sub(r"/\*.*?\*/", "", CSS.read_text(encoding="utf-8"), flags=re.S)


def _rule(css: str, selector: str) -> str:
    """Body of the first rule whose selector list is exactly *selector*."""
    m = re.search(r"(?:^|\})\s*" + re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert m, f"no rule for {selector!r}"
    return m.group(1)


def _decl(body: str, prop: str) -> str:
    m = re.search(r"(?:^|;|\s)" + re.escape(prop) + r"\s*:\s*([^;]+)", body)
    assert m, f"no {prop} in {body!r}"
    return m.group(1).strip()


def _to_px(length: str, root: float) -> float:
    m = re.fullmatch(r"([0-9.]+)(rem|px)", length.strip())
    assert m, f"unsupported length {length!r}"
    v = float(m.group(1))
    return v * root if m.group(2) == "rem" else v


def _row_min_px(css: str, root: float) -> float:
    sel_min = _decl(_rule(css, ".gen-pair-row select"), "min-width")
    gap = _decl(_rule(css, ".gen-pair-row, .gen-twpa-row"), "gap")
    del_w = _decl(_rule(css, ".gen-row-del"), "width")
    # select, arrow, select, x -> three gaps
    return (2 * _to_px(sel_min, root) + 3 * _to_px(gap, root)
            + ARROW_REM * root + _to_px(del_w, root))


class TestPairsRailFit:
    def test_rail_basis_covers_a_pair_row_at_every_root(self):
        css = _css()
        flex = _decl(_rule(css, ".gen-pairs-rail"), "flex").split()
        assert len(flex) == 3, flex
        for root in ROOTS_PX:
            basis = _to_px(flex[2], root)
            need = _row_min_px(css, root)
            assert basis >= need, (
                f"root {root}px: rail basis {basis:.1f}px < pair row "
                f"{need:.1f}px -> the rail scrolls sideways")

    def test_the_x_keeps_its_box_in_a_pair_row(self):
        # A shrinkable x is squeezed to glyph width whenever the row runs
        # short; scoped to pair rows so the TWPA/QDAC rows are unchanged.
        body = _rule(_css(), ".gen-pair-row .gen-row-del")
        flex = _decl(body, "flex") if re.search(r"(?:^|;|\s)flex\s*:", body) else ""
        shrink = (_decl(body, "flex-shrink")
                  if re.search(r"flex-shrink\s*:", body) else "")
        assert flex == "none" or shrink == "0", body


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
