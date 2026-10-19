"""v1.5.1 U P2-14: the top bar's "N windows" marker says what it counts and
lists the windows on a click.

It was a glyph and a number ("⧉ 2") whose meaning lived only in a hover
title, with nothing to click. It now reads "⧉ 2 windows" and opens (a
details/summary, no script) the list: this window and each other one by
port, PID and how long it has been open, with what sharing a working copy
means. Pinned on the REAL /instances/banner?compact=1 markup, and its CSS
on the shipped stylesheets (tests/v151_css_selfcheck.cjs).
"""

from __future__ import annotations

import re

from tests.css_pin import run_css_cases
from tests import test_multi_instance as _mi
from tests.test_multi_instance import foreign_pid  # noqa: F401 -- fixture

_B = _mi.TestSameChipBanner()


def _text(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s)).strip()


def test_the_marker_says_windows_and_lists_them(tmp_path, foreign_pid):  # noqa: F811
    _app, c, inst, chip = _B._app_with_chip(tmp_path)
    _B._seed_peer(inst, foreign_pid, chip)
    html = c.get("/instances/banner?compact=1").get_data(as_text=True)
    summary = re.search(r'<summary class="sync-marker sync-marker-peers"[^>]*>(.*?)</summary>', html, re.S)
    assert summary, html
    assert _text(summary.group(1)).endswith("2 windows"), _text(summary.group(1))
    listing = re.search(r'<div class="sync-peers-list"[^>]*>(.*?)</div>', html, re.S)
    assert listing, "a click opens the list"
    t = _text(listing.group(1))
    assert "Alpha is open in 2 State Manager windows" in t and "this window" in t, t
    assert f"port 5174 · PID {foreign_pid}" in t and "share one working copy" in t, t
    run_css_cases(tmp_path, [{
        "name": "peers list",
        "html": '<span id="multi-instance-slot" class="multi-instance-slot">' + html + "</span>",
        "checks": [
            {"sel": ".multi-instance-slot .sync-peers-list", "prop": "position", "eq": "absolute",
             "why": "the list drops below the marker, the top bar keeps its height"},
            {"sel": ".multi-instance-slot .sync-marker-peers", "prop": "cursor", "eq": "pointer",
             "why": "it is something to click, not a hover-only hint"},
        ]}])


def test_no_marker_when_this_is_the_only_window(tmp_path):
    _app, c, _inst, _chip = _B._app_with_chip(tmp_path)
    assert "sync-peers" not in c.get("/instances/banner?compact=1").get_data(as_text=True)


def test_no_pico_group_on_the_windows_list_or_the_search_pages(tmp_path, foreign_pid):  # noqa: F811
    """R3B MINOR 3: Pico lays a [role=group] out as one squeezed button row --
    the windows list's paragraphs and list, and the header Search's page
    buttons, are not a button group."""
    _app, c, inst, chip = _B._app_with_chip(tmp_path)
    _B._seed_peer(inst, foreign_pid, chip)
    html = c.get("/instances/banner?compact=1").get_data(as_text=True)
    tag = re.search(r'<div class="sync-peers-list"[^>]*>', html)
    assert tag and 'role="group"' not in tag.group(0), tag
    found = c.get("/search?q=settings").get_data(as_text=True)
    tag = re.search(r'<div class="search-pages"[^>]*>', found)
    assert tag and 'role="group"' not in tag.group(0), tag
