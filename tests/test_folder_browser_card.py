"""QA datasets-r2-27 -- the Browse Folders dialog must be a CARD.

Pico's global ``dialog{...}`` rule makes every <dialog> a full-viewport
overlay (``min-width:100%; min-height:100%``, the overlay colour + blur painted
on the element, children centred) and expects an inner <article> card. Our
``.folder-browser-dialog`` set only ``width``/``max-height`` -- which the min-*
beat -- so the dialog filled the screen (measured 1600x950 / 1366x768) and its
header, path row and list floated separately on the blur. The class rule must
override exactly those Pico properties. Static on purpose: jsdom lays nothing
out; the real-browser rect is checked in the QA rig.
"""
from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_STATIC = _ROOT / "quam_state_manager" / "web" / "static"


def _decls(css: str, selector: str) -> dict[str, str]:
    m = re.search(r"(?m)^" + re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert m, f"no {selector} rule"
    body = re.sub(r"/\*.*?\*/", "", m.group(1), flags=re.S)
    out: dict[str, str] = {}
    for part in body.split(";"):
        if ":" in part:
            k, v = part.split(":", 1)
            out[k.strip()] = v.strip()
    return out


def test_pico_still_makes_every_dialog_full_screen():
    """The premise: if Pico ever drops this, the overrides are moot."""
    pico = (_STATIC / "pico.min.css").read_text(encoding="utf-8")
    assert re.search(r"dialog\{[^}]*min-width:100%[^}]*min-height:100%", pico)


def test_the_folder_browser_overrides_the_full_screen_overlay():
    d = _decls((_STATIC / "style.css").read_text(encoding="utf-8"),
               ".folder-browser-dialog")
    assert d.get("min-width") == "0" and d.get("min-height") == "0"
    assert d.get("height") == "fit-content"
    assert d.get("margin") == "auto"
    assert d.get("backdrop-filter") == "none"
    assert d.get("background", "").startswith("var(--pico-background-color")
    assert d.get("align-items") == "stretch"
    assert d.get("justify-content") == "flex-start"


def test_the_breadcrumb_row_does_not_shrink_in_the_capped_card():
    """Once the card is capped at 75vh, a long folder list must scroll itself;
    the breadcrumbs row (a scroll container, so its min-height is 0) was the
    one squeezed, growing its own vertical scrollbar."""
    d = _decls((_STATIC / "style.css").read_text(encoding="utf-8"),
               ".browser-breadcrumbs")
    assert d.get("flex-shrink") == "0"
