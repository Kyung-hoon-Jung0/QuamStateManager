"""v1.5.1 SA-F10: entity tables follow the working copy after a pull.

The Qubits page kept q2.T1 = 16.04 us after a Take live brought 44 us, while
the top bar said "In sync"; a reload showed the new value. Server-rendered
entity tables have no leaf the in-place patch can reach. They now carry
``data-refresh-rows`` and re-read their rows on ``sm:wc-moved``
(tests/v151_entity_rows_refresh_selfcheck.cjs drives the page side).

Pinned here: every entity page's table carries the marker, and the partial
the rows re-read fetches holds the pulled value under the selector it uses.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app

ROOT = Path(__file__).resolve().parents[1]
_WIRING = {"network": {"host": "1.1.1.1", "cluster_name": "C1"}}


def _state(t1=1.604e-05):
    q = lambda i, t: {"id": f"q{i}", "T1": t, "f_01": 5.0e9 + i * 1e8,  # noqa: E731
                      "resonator": {"f_01": 7e9}, "z": {"joint_offset": 0.01}}
    return {"qubits": {"q1": q(1, 1.2e-05), "q2": q(2, t1)},
            "qubit_pairs": {"q1-q2": {"id": "q1-q2", "qubit_control": "#/qubits/q1",
                                      "qubit_target": "#/qubits/q2"}},
            "active_qubit_names": ["q1", "q2"]}


@pytest.fixture
def env(tmp_path):
    live = tmp_path / "chips" / "live"
    live.mkdir(parents=True)
    (live / "state.json").write_text(json.dumps(_state()), encoding="utf-8")
    (live / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")
    routes._quam_cache.clear()
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    return {"app": app, "client": c, "live": live}


HX = {"HX-Request": "true"}


@pytest.mark.parametrize("page, table_id", [
    ("/qubits", "qubits-table"), ("/pairs", "pairs-table"),
    ("/resonators", "resonators-table"), ("/flux", "flux-table"),
    ("/table", "comparison-table"),
])
def test_every_entity_table_is_marked_to_follow_the_working_copy(env, page, table_id):
    html = env["client"].get(page, headers=HX).get_data(as_text=True)
    m = re.search(r'<table id="%s"[^>]*>' % table_id, html)
    assert m, f"{page}: no #{table_id} rendered"
    assert "data-refresh-rows" in m.group(0), m.group(0)


def test_the_couplers_table_is_marked_too():
    tpl = (ROOT / "quam_state_manager" / "web" / "templates" / "_couplers.html").read_text(encoding="utf-8")
    assert re.search(r'<table id="couplers-table" data-refresh-rows', tpl)


def test_after_take_live_the_rows_the_page_re_reads_hold_the_pulled_value(env):
    c = env["client"]
    before = c.get("/qubits", headers=HX).get_data(as_text=True)
    assert "16.04" in before
    st = _state(t1=4.4e-05)
    (env["live"] / "state.json").write_text(json.dumps(st), encoding="utf-8")
    assert c.post("/state/sync", data={"mode": "discard"}).status_code == 200
    html = c.get("/qubits", headers=HX).get_data(as_text=True)
    body = re.search(r'<table id="qubits-table".*?<tbody>(.*?)</tbody>', html, re.S)
    assert body and "44.00" in body.group(1), (body.group(1)[:600] if body else html[:600])


def test_the_page_re_reads_its_rows_on_the_working_copy_signal_selfcheck():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    try:
        subprocess.run([node, "-e", "require('jsdom')"], check=True, capture_output=True,
                       timeout=30, cwd=str(ROOT))
    except Exception:
        pytest.skip("jsdom not installed")
    r = subprocess.run([node, str(ROOT / "tests" / "v151_entity_rows_refresh_selfcheck.cjs")],
                       capture_output=True, text=True, encoding="utf-8", timeout=180, cwd=str(ROOT))
    if r.returncode == 2:
        pytest.skip("jsdom not installed")
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.count("ok - ") >= 12, r.stdout


# ---- R2 M6: the rows the refresh re-reads are the pane's own ---------------

def _qstate():
    ids = ["qA1", "qA2", "qB1", "qB2"]
    return {"qubits": {q: {"id": q, "T1": 1e-5 + i * 1e-6, "f_01": 5e9 + i * 1e8,
                           "resonator": {"f_01": 7e9 + i * 1e7}}
                       for i, q in enumerate(ids)},
            "qubit_pairs": {}, "active_qubit_names": ids}


@pytest.fixture
def chains(tmp_path):
    live = tmp_path / "chips" / "chains"
    live.mkdir(parents=True)
    (live / "state.json").write_text(json.dumps(_qstate()), encoding="utf-8")
    (live / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")
    routes._quam_cache.clear()
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    return {"app": app, "client": c}


def _body_rows(html: str, table_id: str, attr: str = "data-qubit-id"):
    m = re.search(r'<table id="%s".*?<tbody>(.*?)</tbody>' % table_id, html, re.S)
    assert m, html[:800]
    return re.findall(attr + r'="([^"]+)"', m.group(1))


def _refresh_url(html: str, table_id: str) -> str:
    m = re.search(r'<table id="%s"[^>]*data-refresh-url="([^"]*)"' % table_id, html)
    assert m, f"#{table_id} names no refresh url"
    return m.group(1).replace("&amp;", "&")


@pytest.mark.parametrize("shown", [
    "/qubits?chain=B&per_page=50",               # a chain tab (no push-url)
    "/qubits?page=2&per_page=2",                 # page 2 of the pagination bar
    "/resonators?chain=B&per_page=50",
])
def test_the_refresh_re_reads_the_rows_on_screen(chains, shown):
    c = chains["client"]
    table = "resonators-table" if shown.startswith("/resonators") else "qubits-table"
    html = c.get(shown, headers=HX).get_data(as_text=True)
    on_screen = _body_rows(html, table)
    assert on_screen and len(on_screen) < 4, on_screen
    url = _refresh_url(html, table)
    again = c.get(url, headers=HX).get_data(as_text=True)
    assert _body_rows(again, table) == on_screen, (url, on_screen)


def test_the_comparison_table_keeps_its_props_and_sort(chains):
    c = chains["client"]
    shown = "/table?props=T1&props=f_01&sort=T1&dir=desc&per_page=50"
    html = c.get(shown, headers=HX).get_data(as_text=True)
    url = _refresh_url(html, "comparison-table")
    again = c.get(url, headers=HX).get_data(as_text=True)

    def head_and_rows(h):
        t = re.search(r'<table id="comparison-table".*?</table>', h, re.S).group(0)
        return re.findall(r"<th[^>]*>(.*?)</th>", t, re.S)[:3], re.findall(r"<td[^>]*>(.*?)</td>", t, re.S)
    assert head_and_rows(again) == head_and_rows(html), url
    plain = c.get("/table", headers=HX).get_data(as_text=True)
    assert head_and_rows(plain) != head_and_rows(html), "the fixture cannot tell the views apart"


def test_a_pane_rendered_by_a_post_re_reads_the_plain_page(chains):
    app = chains["app"]
    with app.test_request_context("/load", method="POST"):
        assert app.jinja_env.globals["refresh_url"]("/qubits") == "/qubits"
    with app.test_request_context("/qubits?chain=B", method="GET"):
        assert app.jinja_env.globals["refresh_url"]("/qubits") == "/qubits?chain=B"
