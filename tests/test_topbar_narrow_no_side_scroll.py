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

from pathlib import Path

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
            # slot rule
            {"sel": ".topbar #topbar-tray-slot", "prop": "flex-shrink", "eq": "1",
             "why": "the tray slot may be narrower than its one-row content"},
            {"sel": ".topbar #topbar-tray-slot", "prop": "min-width", "eq": "0px",
             "why": "the tray slot may shrink below a long chip name (base nav item rule)"},
            # tray rule
            {"sel": "#pending-tray", "prop": "flex-wrap", "eq": "wrap",
             "why": "the tray pills wrap onto their own rows"},
            {"sel": "#pending-tray", "prop": "row-gap", "eq": "var(--ui-gap-1)",
             "why": "wrapped tray rows keep the kit gap between them"},
            {"sel": "#pending-tray", "prop": "max-width", "eq": "100%",
             "why": "the tray never runs past its slot"},
            # tray children rule (the status control is a tray child)
            {"sel": ".topbar .sync-control", "prop": "flex", "eq": "0 1 auto",
             "why": "the status control may shrink to the window"},
            {"sel": ".topbar .sync-control", "prop": "min-width", "eq": "0px",
             "why": "the status control may shrink below its one-row content"},
            {"sel": ".topbar #pending-tray > :not(.sync-control)", "prop": "max-width", "eq": "100%",
             "why": "a tray pill never runs past the tray"},
            # status control rule
            {"sel": ".topbar .sync-control", "prop": "flex-wrap", "eq": "wrap",
             "why": "the action drops under the state words instead of painting over them"},
            {"sel": ".topbar .sync-control", "prop": "border-radius", "eq": "var(--ui-r-sm)",
             "why": "a two-row control is a box, not a stretched pill"},
            # badge rule
            {"sel": ".topbar .sync-control-main", "prop": "min-width", "eq": "0px",
             "why": "the state words may shrink below their one-line width (base rule)"},
            {"sel": ".topbar .sync-control-main", "prop": "text-align", "eq": "left",
             "why": "wrapped state lines read from the left edge"},
            # text rule
            {"sel": ".topbar .sync-control-text", "prop": "min-width", "eq": "0px",
             "why": "the state text may shrink so it can wrap"},
            {"sel": ".topbar .sync-control-text", "prop": "white-space", "eq": "normal",
             "why": "the full state text wraps and stays readable, never clipped"},
            # action rule
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
            {"sel": ".topbar #topbar-tray-slot", "prop": "flex-shrink", "eq": "0",
             "why": "a wide window never squeezes the tray slot"},
            {"sel": ".topbar .sync-control-text", "prop": "white-space", "neq": "normal",
             "why": "a wide window keeps the state words on one line"},
            {"sel": ".topbar .sync-control-act", "prop": "flex", "eq": "0 0 auto",
             "why": "a wide window keeps the action at its own width"},
        ]})
    run_css_cases(tmp_path, cases)


_KIT = Path(__file__).resolve().parents[1] / "quam_state_manager" / "web" / "static" / "ui-kit.css"
_BADGE = ".topbar.tb-fit-11 .sync-control .state-status-badge.sync-control-main"


def test_the_floor_badge_grows_into_its_row():
    """The state words take the row at the floor (flex 1 1 auto).

    Pinned on the source, not the cascade: jsdom ranks a selector LIST by its
    most specific member, so the base `.topbar .sync-control-main, ...,
    .topbar #pending-tray > *` list outranks this rule there, while a browser
    ranks by the member that matched and this rule wins (measured in Chrome).
    """
    css = _KIT.read_text(encoding="utf-8")
    hits = [ln for ln in css.splitlines() if ln.startswith(_BADGE + " {")]
    assert len(hits) == 1, "the floor badge rule exists once, scoped to the floor"
    body = hits[0].split("{", 1)[1].split("}", 1)[0]
    decls = {d.split(":", 1)[0].strip(): d.split(":", 1)[1].strip()
             for d in body.split(";") if ":" in d}
    assert decls.get("flex") == "1 1 auto", "the state words grow into the row"
