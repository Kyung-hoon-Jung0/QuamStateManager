"""v1.5.1 U P2-19 / P2-23: header Search finds pages and actions by name, and
the chip report has a labelled entry.

* P2-19 -- the UX round taught Ctrl+K pages, Export, the version and the chip
  report; header Search still searched chip fields only ("export" found
  nothing). Both now read ONE list (routes._NAV_PAGES): Search offers the
  matching pages / actions above the fields, with or without a chip.
* P2-23 -- the printable chip report was reachable only through a 24x13 px
  printer icon in the sidebar (and, since the UX round, Ctrl+K); the Chip
  Status header now carries a labelled "Chip report" link.
Pinned on REAL rendered markup.
"""

from __future__ import annotations

import json
import re
from html import unescape

from quam_state_manager.web.app import create_app
from tests.test_cz_plan_page import _client


def _pages(html: str) -> list[tuple[str, str, str]]:
    block = re.search(r'<div class="search-pages"[^>]*>(.*?)</div>', html, re.S)
    if not block:
        return []
    return [(tag, unescape(attrs), unescape(re.sub(r"<[^>]+>", "", label)).strip())
            for tag, attrs, label in re.findall(r'<(a|button)\b([^>]*)>(.*?)</\1>', block.group(1), re.S)]


def _palette(html: str) -> list[dict]:
    return json.loads(re.search(r'<script id="cmd-palette-data"[^>]*>(.*?)</script>', html, re.S).group(1))["pages"]


def test_header_search_offers_the_pages_ctrl_k_knows(tmp_path):
    c = _client(tmp_path)
    got = _pages(c.get("/search?q=export").get_data(as_text=True))
    assert [(t, lbl) for t, _a, lbl in got] == [("a", "Export... (CSV)")], got
    assert 'href="/table"' in got[0][1]
    ver = _pages(c.get("/search?q=version").get_data(as_text=True))
    assert ver and ver[0][2].startswith("About - version "), ver
    rep = _pages(c.get("/search?q=chip report").get_data(as_text=True))
    assert rep and rep[0][2] == "Chip report (print)" and 'target="_blank"' in rep[0][1], rep
    sett = _pages(c.get("/search?q=settings").get_data(as_text=True))
    assert sett and sett[0][0] == "button" and "toggleSettings(this)" in sett[0][1], sett
    # the fields are still searched below
    body = c.get("/search?q=f_01").get_data(as_text=True)
    assert "search-table" in body and not _pages(body)


def test_without_a_chip_the_pages_are_still_offered(tmp_path):
    c = create_app(testing=True, instance_path=str(tmp_path / "_i")).test_client()
    got = _pages(c.get("/search?q=trends").get_data(as_text=True))
    assert ("a", "Trends") in [(t, lbl) for t, _a, lbl in got], got


def test_one_list_for_ctrl_k_and_search(tmp_path, monkeypatch):
    c = create_app(testing=True, instance_path=str(tmp_path / "_i")).test_client()
    labels = [p["label"] for p in _palette(c.get("/").get_data(as_text=True))]
    assert "Export... (CSV)" in labels and "Chip report (print)" in labels and "Experiment Runner" not in labels
    assert not _pages(c.get("/search?q=experiment runner").get_data(as_text=True))
    monkeypatch.setenv("SM_EXPERIMENTAL", "1")
    assert "Experiment Runner" in [p["label"] for p in _palette(c.get("/").get_data(as_text=True))]
    assert _pages(c.get("/search?q=experiment runner").get_data(as_text=True))[0][2] == "Experiment Runner"


def test_chip_status_carries_a_labelled_chip_report_link(tmp_path):
    c = _client(tmp_path)
    html = c.get("/topology", headers={"HX-Request": "true"}).get_data(as_text=True)
    m = re.search(r'<a class="outline btn-sm topo-report-link"[^>]*href="/chip-status/report"[^>]*>([^<]+)</a>', html)
    assert m and m.group(1).strip() == "Chip report", "the report is named where the chip is"
    toolbar = html[html.index('class="topology-toolbar"'):]
    assert toolbar.index("topo-report-link") < toolbar.index("</div>")
