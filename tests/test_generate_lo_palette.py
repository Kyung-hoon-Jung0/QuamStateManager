"""docs/301 F32: the Populate step's LO-group tags never wear an alarm colour.

Each LO / FSP cell carries a small pill naming its port group ("Out8") and a
3px accent in the group's colour. The palette held an error red (#dc2626),
plus a pink, a brick and two warning-family hues: a cell striped red reads as
"this value is invalid", while it only says which ports share an LO. The
palette is pinned here by what a user reads into a colour, not by hex value.
"""
import colorsys
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_CSS = _ROOT / "quam_state_manager" / "web" / "static" / "style.css"
_JS = _ROOT / "quam_state_manager" / "web" / "static" / "generate.js"


def _palette() -> dict[str, str]:
    css = _CSS.read_text(encoding="utf-8")
    return dict(re.findall(r"(--lo-c\d+):\s*(#[0-9a-fA-F]{6})", css))


def _rgb(h: str) -> tuple[float, float, float]:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def _lin(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _contrast_with_white(h: str) -> float:
    r, g, b = (_lin(c) for c in _rgb(h))
    return 1.05 / (0.2126 * r + 0.7152 * g + 0.0722 * b + 0.05)


def test_every_palette_slot_the_wizard_uses_is_defined_once():
    pal = _palette()
    slots = re.findall(r'"(--lo-c\d+)"', _JS.read_text(encoding="utf-8"))
    assert slots and len(slots) == len(set(slots)), slots
    assert set(slots) == set(pal), (sorted(slots), sorted(pal))
    assert len(set(v.lower() for v in pal.values())) == len(pal), pal


def test_no_group_colour_reads_as_an_error_or_a_warning():
    """Red, pink, brick, orange and amber are the colours this app uses for
    errors and warnings. A hue between 330 and 50 degrees is in that family
    unless it is close to grey (saturation <= 0.25, e.g. a warm stone)."""
    bad = {}
    for name, hexv in _palette().items():
        h, _l, s = colorsys.rgb_to_hls(*_rgb(hexv))
        deg = h * 360
        if s > 0.25 and (deg >= 330 or deg <= 50):
            bad[name] = (hexv, round(deg), round(s, 2))
    assert not bad, bad
    assert "#dc2626" not in {v.lower() for v in _palette().values()}


def test_white_pill_text_stays_readable():
    """The pill text is white on every theme, so each colour keeps a WCAG AA
    contrast against white."""
    low = {n: round(_contrast_with_white(v), 2) for n, v in _palette().items()
           if _contrast_with_white(v) < 4.5}
    assert not low, low
