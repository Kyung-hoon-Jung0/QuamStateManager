"""v1.5.1 (r6 P2-5): the "N windows" list is a plain vertical list.

The list the top bar's "2 windows" marker opens sits inside the top bar's
<nav>, where Pico lays every <ul> out as a flex row and every <li> as a
padded inline-block: "this window" and the peer line ("port 5752 · PID ...
· open since ...") sat side by side and the peer line wrapped in a narrow
column. Pinned on the REAL /instances/banner?compact=1 markup placed where
the REAL page puts its slot (header.topbar > nav > ul > li), with the
shipped stylesheets (tests/v151_css_selfcheck.cjs).
"""

from __future__ import annotations

import re

from tests.css_pin import run_css_cases
from tests import test_multi_instance as _mi
from tests.test_multi_instance import foreign_pid  # noqa: F401 -- fixture

_B = _mi.TestSameChipBanner()


def test_the_windows_list_is_a_vertical_list_inside_the_top_bar(tmp_path, foreign_pid):  # noqa: F811
    _app, c, inst, chip = _B._app_with_chip(tmp_path)
    _B._seed_peer(inst, foreign_pid, chip)
    page = c.get("/qubits").get_data(as_text=True)
    # where the real page puts the marker's slot: inside the top bar's nav list
    head = page[page.index('<header class="topbar">'):page.index('id="multi-instance-slot"')]
    assert "<nav>" in head and "<ul" in head and "<li" in head, "the slot sits in header.topbar > nav > ul > li"
    html = c.get("/instances/banner?compact=1").get_data(as_text=True)
    assert re.search(r'<div class="sync-peers-list"[^>]*>.*<ul>.*this window.*</ul>', html, re.S), html
    wrapped = ('<header class="topbar"><nav><ul><li>'
               '<span id="multi-instance-slot" class="multi-instance-slot">' + html + '</span>'
               '</li></ul></nav></header>')
    checks = [
        {"sel": ".multi-instance-slot .sync-peers-list ul", "prop": "display", "eq": "block",
         "why": "a plain list, not the nav's flex row"},
        {"sel": ".multi-instance-slot .sync-peers-list li", "prop": "display", "eq": "list-item", "all": True,
         "why": "each window on its own line, with its bullet"},
        {"sel": ".multi-instance-slot .sync-peers-list ul", "prop": "marginLeft", "eq": "1rem",
         "why": "the bullets sit inside the box (not the nav's negative first-list margin)"},
    ]
    run_css_cases(tmp_path, [
        {"name": "windows list (dark)", "theme": "dark", "html": wrapped, "checks": checks},
        {"name": "windows list (light)", "theme": "light", "html": wrapped, "checks": checks},
    ])
