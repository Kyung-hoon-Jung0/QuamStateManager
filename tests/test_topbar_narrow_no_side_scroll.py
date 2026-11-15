"""The top bar at a phone-width window never makes the PAGE scroll sideways.

At 400 px the fit ladder already reaches its floor (level 11, two rows), but
the tray cluster (Auto-Sync pill + the status control) was one nowrap row
wider than the window: the status control's right edge sat at 479 px and every
page scrolled sideways by 79 px. At the floor the cluster now wraps its pills,
the status control wraps its action under the state words and the state words
wrap inside it, so every word of the state stays readable on screen.

jsdom has no layout, so this pins the cascade (the declarations that make the
wrap possible) on the REAL rendered page; the width itself was measured in
real Chrome (scrollWidth == clientWidth at 400 / 560 / 700 / 1000 / 1440 px).
Levels below the floor keep the one-row bar unchanged.
"""

from __future__ import annotations

from tests.css_pin import run_css_cases
from tests.test_cz_plan_page import _client

_ACT = '<button type="button" class="sync-control-act btn-apply-live">Apply 2</button>'


def _page(tmp_path) -> str:
    page = _client(tmp_path).get("/qubits").get_data(as_text=True)
    assert 'id="topbar-tray-slot"' in page and 'class="sync-control-text"' in page
    marker = '<span class="sync-control-caret" aria-hidden="true">&#9662;</span>\n</button>'
    assert marker in page
    return page.replace(marker, marker + _ACT, 1)


def _with_fit(page: str, level: int) -> str:
    cls = " ".join(f"tb-fit-{n}" for n in range(1, level + 1))
    out = page.replace('<header class="topbar">', f'<header class="topbar {cls}">', 1)
    assert out != page
    return out


def test_the_tray_cluster_wraps_at_the_floor(tmp_path):
    page = _page(tmp_path)
    floor = _with_fit(page, 11)
    run_css_cases(tmp_path, [
        {"name": "fit floor", "html": floor, "checks": [
            {"sel": ".topbar #topbar-tray-slot", "prop": "flex-shrink", "eq": "1",
             "why": "the tray slot may be narrower than its one-row content"},
            {"sel": ".topbar #topbar-tray-slot", "prop": "max-width", "eq": "100%",
             "why": "the tray slot never runs past the window"},
            {"sel": "#pending-tray", "prop": "flex-wrap", "eq": "wrap",
             "why": "the tray pills wrap onto their own rows"},
            {"sel": ".topbar .sync-control", "prop": "flex-wrap", "eq": "wrap",
             "why": "the action drops under the state words instead of painting over them"},
            {"sel": ".topbar .sync-control", "prop": "flex", "eq": "0 1 auto",
             "why": "the status control may shrink to the window"},
            {"sel": ".topbar .sync-control-text", "prop": "white-space", "eq": "normal",
             "why": "the full state text wraps and stays readable, never clipped"},
            {"sel": ".topbar .sync-control-act", "prop": "flex", "eq": "1 1 auto",
             "why": "a wrapped action fills its row so it reads as one button"},
        ]},
    ])


def test_levels_below_the_floor_keep_one_row(tmp_path):
    page = _page(tmp_path)
    cases = []
    for k in (0, 10):
        html = _with_fit(page, k) if k else page
        cases.append({"name": f"fit level {k}", "html": html, "checks": [
            {"sel": "#pending-tray", "prop": "flex-wrap", "eq": "nowrap",
             "why": "a wide window keeps the tray on one row"},
            {"sel": ".topbar .sync-control", "prop": "flex-wrap", "neq": "wrap",
             "why": "a wide window keeps the status control on one row"},
            {"sel": ".topbar .sync-control", "prop": "flex", "eq": "0 0 auto",
             "why": "a wide window never squeezes the status control"},
        ]})
    run_css_cases(tmp_path, cases)
