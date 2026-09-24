"""QA datasets-r2-10 -- a sidebar filter that matches nothing said "No workspace
roots added yet." and took every root's header and x with it (it read as "my
folders are gone"); a filter matching only SOME roots silently hid the others'
headers too. ``_filter_tree`` drops a root with no matching run and the
template's ``{% if tree %}`` could not tell ``{}`` from "no roots". The render
tree now keeps every workspace root and each unmatched root says so.
``_filter_tree`` itself is unchanged (pinned by test_sidebar_param_search.py
and test_web.py::TestSidebarFilter).
"""
from __future__ import annotations

import json
import re
import time

import pytest

from quam_state_manager.web.app import create_app

NO_ROOTS = "No workspace roots added yet"
NOMATCH = "tree-nomatch-note"


def _root(base, name, rids):
    root = base / name
    for rid in rids:
        run = root / "2026-08-01" / f"#{rid}_exp{rid}_12000{rid}"
        (run / "quam_state").mkdir(parents=True)
        (run / "quam_state" / "state.json").write_text(
            json.dumps({"qubits": {"q1": {"f_01": 5e9 + rid}}}), encoding="utf-8")
        (run / "quam_state" / "wiring.json").write_text(
            json.dumps({"wiring": {}, "network": {}}), encoding="utf-8")
        (run / "node.json").write_text(json.dumps(
            {"id": rid, "metadata": {"name": f"exp{rid}", "status": "finished"},
             "created_at": "2026-08-01T12:00:00", "parameters": {"model": {}}}),
            encoding="utf-8")
    return root


@pytest.fixture
def two_roots(tmp_path):
    ra = _root(tmp_path, "rootA", [1, 2])
    rb = _root(tmp_path, "rootB", [7])
    app = create_app(testing=True, instance_path=str(tmp_path / "inst"))
    c = app.test_client()
    for r in (ra, rb):
        assert c.post("/workspace/add", data={"folder": str(r)}).status_code in (200, 302)
    from quam_state_manager.web import routes as rm
    with app.app_context():
        ws = rm._ws()
        for _ in range(100):                     # docs/142 listing-first hydration
            if not ws.hydrating_roots():
                break
            time.sleep(0.05)
    return c


def _roots(html):
    return len(re.findall(r'class="tree-root"', html)), html.count("btn-remove")


@pytest.mark.parametrize("q", ["zzzzqq", "date:2030"])
def test_a_filter_matching_nothing_keeps_every_root(two_roots, q):
    html = two_roots.get(f"/workspace/tree?name={q}").get_data(as_text=True)
    assert NO_ROOTS not in html
    assert _roots(html) == (2, 2), "both roots and their x stay"
    assert html.count(NOMATCH) == 2
    assert "No runs in this folder match" in html and q in html


def test_a_refresh_under_a_filter_keeps_every_root_too(two_roots):
    html = two_roots.post("/workspace/refresh", data={"name": "zzzzqq"}).get_data(as_text=True)
    assert NO_ROOTS not in html
    assert _roots(html) == (2, 2)
    assert html.count(NOMATCH) == 2, "the refresh render knows the filter (name_filter passed)"


def test_a_partial_match_keeps_the_unmatched_root(two_roots):
    html = two_roots.get("/workspace/tree?name=exp7").get_data(as_text=True)
    assert _roots(html) == (2, 2), "rootA's header and x no longer vanish"
    assert html.count(NOMATCH) == 1
    blocks = html.split('class="tree-root"')[1:]
    a = next(b for b in blocks if "rootA" in b.split("</summary>", 1)[0])
    b = next(b for b in blocks if "rootB" in b.split("</summary>", 1)[0])
    assert NOMATCH in a and NOMATCH not in b
    assert "exp7" in b


def test_no_filter_and_no_roots_still_say_so(tmp_path):
    c = create_app(testing=True, instance_path=str(tmp_path / "inst")).test_client()
    html = c.get("/workspace/tree").get_data(as_text=True)
    assert NO_ROOTS in html
    html = c.get("/workspace/tree?name=zzzzqq").get_data(as_text=True)
    assert NO_ROOTS in html, "an EMPTY workspace is still an empty workspace"


def test_the_unfiltered_tree_has_no_note(two_roots):
    html = two_roots.get("/workspace/tree").get_data(as_text=True)
    assert _roots(html) == (2, 2) and NOMATCH not in html
