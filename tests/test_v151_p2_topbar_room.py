"""v1.5.1 U P2-3 / P2-6 / P2-10: room in and around the top bar.

* P2-3 -- at 1280 any extra pill (read-only, "1 not recovered") pushed the top
  bar's fit ladder to level 4, which HID the chip name (the title read "QSM",
  Reload "Re"). The chip name says which chip every action acts on: it now
  narrows (10ch, then 6ch at level 9) and is never hidden; the read-only
  pill drops its word (the lock stays) before any label.
* P2-6 -- with the bars collapsed (one ☰ press, a customer decision) the
  floating card sat on the page title. The pane now reserves that band unless
  the page pins its own bar there (Chip Status' tabs already do).
* P2-10 -- in the Sync-Qualibrate split, State Manager got 45 % of the window
  and lost most of it to its own sidebar. It gets the larger half by default
  and its sidebar starts narrower in the frame (a dragged width still wins).
Pinned with the shipped stylesheets over REAL rendered pages
(tests/v151_css_selfcheck.cjs).
"""

from __future__ import annotations

import re
from pathlib import Path

from quam_state_manager.web.app import create_app
from tests.css_pin import run_css_cases
from tests.test_cz_plan_page import _client

_STATIC = Path(__file__).resolve().parent.parent / "quam_state_manager" / "web" / "static"


def _with_fit(page: str, level: int) -> str:
    cls = " ".join(f"tb-fit-{n}" for n in range(1, level + 1))
    out = page.replace('<header class="topbar">', f'<header class="topbar {cls}">', 1)
    assert out != page
    return out


def test_the_chip_name_is_never_hidden_by_the_fit_ladder(tmp_path):
    c = _client(tmp_path)
    page = c.get("/qubits").get_data(as_text=True)
    assert 'class="state-status-name"' in page
    cases = [{"name": f"fit level {k}", "html": _with_fit(page, k),
              "checks": [{"sel": ".topbar .sync-control .state-status-name", "prop": "display", "neq": "none",
                          "why": "the chip name stays at every step of the ladder"}]}
             for k in range(1, 11)]
    cases.append({"name": "fit level 3", "html": _with_fit(page.replace(
        '<li id="topbar-tray-slot">', '<li id="topbar-tray-slot"><span class="tray-ro-lock">L<span class="tray-ro-text">read-only</span></span>', 1), 3),
        "checks": [{"sel": ".topbar .tray-ro-text", "prop": "display", "eq": "none",
                    "why": "the read-only pill gives its word before the chip name narrows further"}]})
    run_css_cases(tmp_path, cases)
    css = re.sub(r"/\*.*?\*/", "", (_STATIC / "style.css").read_text(encoding="utf-8"), flags=re.S)
    hiding = [sel for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css)
              if "state-status-name" in sel and "display: none" in body]
    assert not hiding, hiding


def test_the_floating_card_never_sits_on_the_page_title(tmp_path):
    c = _client(tmp_path)
    qubits = c.get("/qubits").get_data(as_text=True)
    topo = c.get("/topology").get_data(as_text=True)
    sel = "html.topbar-hidden #table-pane:not(:has(.topo-subnav))"
    css = (_STATIC / "style.css").read_text(encoding="utf-8")
    rule = re.search(re.escape(sel) + r"::before\s*\{([^}]*)\}", css)
    assert rule and "height: var(--floating-chrome-h)" in rule.group(1), "the pane reserves the floating band"
    run_css_cases(tmp_path, [
        {"name": "Qubits, bars collapsed", "html": qubits, "html_class": "topbar-hidden",
         "checks": [{"sel": sel, "matches": True, "why": "an ordinary page reserves the band"}]},
        {"name": "Chip Status, bars collapsed", "html": topo, "html_class": "topbar-hidden",
         "checks": [{"sel": sel, "matches": False, "why": "its pinned tabs reserve it already"}]},
    ])


def test_the_split_gives_state_manager_room(tmp_path):
    c = create_app(testing=True, instance_path=str(tmp_path / "_i")).test_client()
    wb = c.get("/workbench").get_data(as_text=True)
    m = re.search(r"var DEFAULT_SIZES = \[(\d+), (\d+)\];", wb)
    assert m and int(m.group(2)) > int(m.group(1)), "State Manager gets the larger half by default"
    assert "return DEFAULT_SIZES.slice();" in wb and "return [55, 45];" not in wb
    page = c.get("/").get_data(as_text=True)
    run_css_cases(tmp_path, [
        {"name": "inside the split", "html": page, "html_class": "sm-in-frame",
         "checks": [{"sel": "html", "prop": "--sidebar-width", "eq": "220px",
                     "why": "the sidebar starts narrower in the frame"}]},
        {"name": "a window of its own", "html": page,
         "checks": [{"sel": "html", "prop": "--sidebar-width", "eq": "300px", "why": "unchanged outside it"}]},
    ])
