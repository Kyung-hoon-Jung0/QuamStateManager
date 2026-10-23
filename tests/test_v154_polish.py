"""v1.5.4 polish: uniform overflow-menu rows, the full title at 950 px, and an
opaque band under the CZ plan's stuck header (CSS pins; the attention mark is
pinned in tests/topbar_more_selfcheck.cjs)."""

from __future__ import annotations

import re
from pathlib import Path

CSS = (Path(__file__).resolve().parent.parent / "quam_state_manager" / "web" / "static"
       / "style.css").read_text(encoding="utf-8")
TPL = Path(__file__).resolve().parent.parent / "quam_state_manager" / "web" / "templates"


def _rule(sel_part: str) -> str:
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", CSS):
        if sel_part in m.group(1):
            yield m.group(2)


def test_every_menu_item_is_one_uniform_row():
    bodies = [b for b in _rule(".topbar .topbar-more-menu .wb-link") if "min-height" in b]
    assert bodies, "no row rule"
    body = bodies[-1]
    for prop in ("min-height", "font-size", "padding", "justify-content: flex-start", "width: 100%"):
        assert prop in body, prop
    for sel in (".topbar-reload", ".diag-header-badge", ".agent-pill-link", ".state-version-chip"):
        assert any(".topbar .topbar-more-menu " + sel in m.group(1) and m.group(2) == body
                   for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", CSS)), sel
    # status items keep their accent as a right-hand badge
    assert ".diag-error .tbm-badge" in CSS and "margin-left: auto" in CSS


def test_icons_share_one_column():
    base = (TPL / "base.html").read_text(encoding="utf-8")
    assert '<span class="tbm-ico" aria-hidden="true">&#8635;</span>' in base
    assert '<span class="tbm-ico" aria-hidden="true">⌗</span>' in base
    assert 'class="tbm-label">Diagnostics<' in (TPL / "_diagnostics_badge.html").read_text(encoding="utf-8")


def test_the_full_title_shows_at_950px():
    m = re.search(r"@media \(max-width: (\d+)px\) \{\s*\.topbar \.app-title \{ font-size: 0; \}", CSS)
    assert m and int(m.group(1)) < 950, m and m.group(1)


def test_the_stuck_cz_header_has_an_opaque_band_under_it():
    bodies = list(_rule(".czp-wrap-fits .czp-table thead th::after"))
    assert bodies and "background: var(--pico-background-color)" in bodies[0] and "top: 100%" in bodies[0]
