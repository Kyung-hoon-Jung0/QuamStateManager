"""QA F24: the Populate step's unit selectors kept no room for the chevron.

Pico paints every single-line <select>'s chevron as a background image
(`background-position: center right .75rem; background-size: 1rem auto`) and
reserves `padding-right: spacing + 1.5rem` for it. `.gen-pop-unit select`
(the Units row on /generate and /regenerate step 6: Frequency / Time /
Voltage / Amplitude + the Power-input select) overrode the padding with
`0.1rem 0.3rem` and `width: auto`, so the box shrank to the text and the
chevron was drawn over it -- measured in real Chrome on the KRS 5Q copy:
clientWidth GHz 41 / ns 27 / V 33 / 0-1 42 px, padding-right 6.3 px, the
chevron covering "GHz", "ns", "V", "0-1", "dBm" and "absolute dBm (auto FSP)".

The pin derives the chevron's inner edge from the shipped pico.min.css, so it
fails if either side of the geometry moves out from under the other.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "quam_state_manager/web/static"
CSS = (STATIC / "style.css").read_text(encoding="utf-8")
PICO = (STATIC / "pico.min.css").read_text(encoding="utf-8")

SEL = ".gen-pop-unit select"


def _rem(v: str) -> float:
    v = v.strip()
    if v in ("0", "0rem"):
        return 0.0
    m = re.fullmatch(r"(\d*\.?\d+)rem", v)
    assert m, f"expected a rem length, got {v!r}"
    return float(m.group(1))


def _chevron_inner_edge_rem() -> float:
    """Distance from the select's right edge to the chevron's LEFT edge."""
    m = re.search(
        r"select:not\(\[multiple\],\[size\]\)\{[^}]*?"
        r"background-position:center right ([^;}]+);background-size:([^ ;}]+) auto",
        PICO,
    )
    assert m, "Pico's select chevron rule moved -- re-derive this pin"
    return _rem(m.group(1)) + _rem(m.group(2))


def _padding_right_rem() -> float:
    """The last padding-right any style.css rule gives `.gen-pop-unit select`."""
    right = None
    for sels, body in re.findall(r"([^{}]+)\{([^{}]*)\}", CSS):
        if SEL not in [s.strip() for s in sels.split(",")]:
            continue
        for prop, val in re.findall(r"(padding(?:-right|-inline-end)?)\s*:\s*([^;]+)", body):
            parts = val.split()
            if prop == "padding":
                right = parts[1] if len(parts) >= 2 else parts[0]
            else:
                right = parts[0]
    assert right is not None, f"no padding rule for {SEL!r}"
    return _rem(right)


def test_the_unit_selects_keep_the_chevrons_room():
    edge = _chevron_inner_edge_rem()
    assert edge == 1.75  # sanity: Pico 2's .75rem offset + 1rem icon
    pr = _padding_right_rem()
    assert pr >= edge, (
        f"{SEL} padding-right {pr}rem < chevron inner edge {edge}rem: "
        "the chevron is painted over the unit text"
    )


def test_the_rule_does_not_move_the_chevron_itself():
    # the fix is room for the chevron, not hiding it or shifting it
    for sels, body in re.findall(r"([^{}]+)\{([^{}]*)\}", CSS):
        if SEL in [s.strip() for s in sels.split(",")]:
            assert "appearance" not in body, body
            assert "background" not in body, body
