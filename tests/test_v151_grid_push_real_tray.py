"""v1.5.1 R2 M1: the grid push reads the rows from the tray the server renders.

The SB-F4 fix declared the rows its confirm showed (``seen_paths``), read
from ``#tray-drawer .tray-change-item`` -- an element no template renders
(the rows live in the sync panel). On the real page the capture was empty,
the push sent ``seen_paths=[]``, and a SINGLE-window user's own edits were
refused as "staged in another State Manager window?". Its pin invented the
element, so it could not see that.

Now ``#pending-tray`` publishes its pending paths (``data-change-paths``),
the confirm and the push read them there, and an empty capture declares
nothing (the count/signature rules decide). Pinned end to end: the REAL
/bulk page and /state/tray fragment are rendered here, the REAL page scripts
run on that markup under jsdom (tests/v151_grid_push_real_tray_selfcheck.cjs),
and every write request the page makes is replayed against this server.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from urllib.parse import parse_qsl

import pytest

from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app

# R2 MINOR 6: these door tests run with the mid-save guard ON (conftest)
pytestmark = pytest.mark.mid_save_guard

ROOT = Path(__file__).resolve().parent.parent
_WIRING = {"network": {"host": "127.0.0.1", "cluster_name": "C1"}}
HX = {"HX-Request": "true"}
JSON = {"Accept": "application/json"}


def _state():
    qs = {f"q{i}": {"id": f"q{i}", "T1": 1.0e-5 * i} for i in (1, 2, 3, 4)}
    return {"qubits": qs, "qubit_pairs": {}, "active_qubit_names": sorted(qs)}


@pytest.fixture
def env(tmp_path):
    live = tmp_path / "chip" / "quam_state"
    live.mkdir(parents=True)
    (live / "state.json").write_text(json.dumps(_state()), encoding="utf-8")
    (live / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")
    routes._quam_cache.clear()
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    return {"app": app, "c": c, "live": live, "tmp": tmp_path}


def _live(env, q):
    return json.loads((env["live"] / "state.json").read_text(encoding="utf-8"))["qubits"][q]["T1"]


def _edit(c, path, value):
    r = c.post("/field/edit", data={"dot_path": path, "value": value})
    assert r.status_code == 200 and r.get_json()["ok"], r.data


def _page(env) -> str:
    html = env["c"].get("/bulk").get_data(as_text=True)
    assert 'id="pending-tray"' in html
    return html


def _tray(env) -> str:
    return env["c"].get("/state/tray", headers=HX).get_data(as_text=True)


def _run(env, fixture: dict) -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    try:
        subprocess.run([node, "-e", "require('jsdom')"], check=True, capture_output=True,
                       timeout=30, cwd=str(ROOT))
    except Exception:
        pytest.skip("jsdom not installed")
    fx = env["tmp"] / "fixture.json"
    fx.write_text(json.dumps(fixture), encoding="utf-8")
    r = subprocess.run([node, str(ROOT / "tests" / "v151_grid_push_real_tray_selfcheck.cjs"), str(fx)],
                       capture_output=True, text=True, encoding="utf-8", timeout=180, cwd=str(ROOT))
    if r.returncode == 2:
        pytest.skip("jsdom not installed")
    assert r.returncode == 0 and "DONE" in r.stdout, r.stdout[-3000:] + r.stderr[-3000:]
    got = {"REQ": [], "CONFIRM": [], "EXTRAS": [], "TOAST": []}
    for line in r.stdout.splitlines():
        tag, _, rest = line.partition(" ")
        if tag in got:
            got[tag].append(json.loads(rest))
    return got


def _replay(env, req: dict):
    """Send the page's request to this server, as the browser would."""
    headers = {k: v for k, v in (req.get("headers") or {}).items()
               if k.lower() in ("x-sm-chip-key", "hx-request", "accept")}
    headers.setdefault("Accept", "application/json")
    data = dict(parse_qsl(req["body"])) if req["via"] == "fetch" else dict(req["values"])
    return env["c"].post(req["url"], data=data, headers=headers)


def test_the_tray_publishes_its_pending_paths(env):
    _edit(env["c"], "qubits.q3.T1", "3.3e-05")
    for html in (_page(env), _tray(env)):
        assert "data-change-paths='[\"qubits.q3.T1\"]'" in html, html[:2000]


def test_type_then_click_pushes_the_windows_own_rows(env):
    """One window: an edit staged earlier (q3), then a cell typed and "Apply to live now"
    button clicked -- the click-away commits it (q1) before the push runs."""
    c = env["c"]
    _edit(c, "qubits.q3.T1", "3.3e-05")
    _edit(c, "qubits.q1.T1", "1.1e-05")
    got = _run(env, {"scenario": "type_then_click", "page_html": _page(env),
                     "tray_html": _page(env)})
    assert got["CONFIRM"] and "qubits.q3.T1" in got["CONFIRM"][0] \
        and "qubits.q1.T1" in got["CONFIRM"][0], got["CONFIRM"]
    pushes = [q for q in got["REQ"] if q["url"].startswith("/state/sync")]
    assert len(pushes) == 1, got["REQ"]
    sent = dict(parse_qsl(pushes[0]["body"]))
    assert json.loads(sent.get("seen_paths", "[]")) and sent["mode"] == "apply", sent
    r = _replay(env, pushes[0])
    assert r.status_code == 200, r.get_data(as_text=True)[:400]
    assert (_live(env, "q1"), _live(env, "q3")) == (1.1e-5, 3.3e-5)


def test_a_stale_confirm_refuses_only_another_windows_edit(env):
    """SB-F4 on the real markup: the confirm saw this window's q3; the grid's
    commit then swapped in the server's tray, which also holds another
    window's q4. The push declares q3 + the typed q1, never q4."""
    c = env["c"]
    _edit(c, "qubits.q3.T1", "3.3e-05")              # this window, earlier
    page = _page(env)
    _edit(c, "qubits.q4.T1", "4.4e-05")              # another window
    _edit(c, "qubits.q1.T1", "1.1e-05")              # this window's grid commit
    got = _run(env, {"scenario": "stale_confirm", "page_html": page, "tray_html": page,
                     "tray_after_html": _tray(env), "typed": ["qubits.q1.T1"]})
    assert got["EXTRAS"] and "qubits.q3.T1" in got["EXTRAS"][0], \
        f"the confirm's 'also carries' line does not name the tray's edit: {got['EXTRAS']}"
    pushes = [q for q in got["REQ"] if q["url"].startswith("/state/sync")]
    assert len(pushes) == 1, got["REQ"]
    r = _replay(env, pushes[0])
    body = r.get_json(silent=True) or {}
    assert r.status_code == 409 and body.get("status") == "unseen_changes" \
        and body.get("paths") == ["qubits.q4.T1"], (r.status_code, body)
    assert _live(env, "q1") == 1.0e-5 and _live(env, "q3") == 1.0e-5 * 3
    acked = dict(pushes[0], body=pushes[0]["body"] + "&ack_unseen=1")
    assert _replay(env, acked).status_code == 200
    assert (_live(env, "q1"), _live(env, "q3"), _live(env, "q4")) == (1.1e-5, 3.3e-5, 4.4e-5)


def test_an_empty_capture_declares_the_signature_not_an_empty_set(env):
    """A saved draft and nothing in the tray: the confirm captured no rows,
    so the direct push declares the tray's count/signature, not []."""
    c = env["c"]
    _edit(c, "qubits.q2.T1", "2.2e-05")
    assert c.post("/save").status_code == 200
    page = _page(env)
    assert "data-change-paths='[]'" in page
    got = _run(env, {"scenario": "type_then_click", "page_html": page, "tray_html": page})
    pushes = [q for q in got["REQ"] if q["url"].startswith("/state/apply-to-live")]
    assert len(pushes) == 1, got["REQ"]
    vals = pushes[0]["values"]
    assert "seen_paths" not in vals and "seen_sig" in vals, vals
    r = _replay(env, pushes[0])
    assert r.status_code == 200 and _live(env, "q2") == 2.2e-5, r.get_data(as_text=True)[:400]


def test_the_server_reads_an_empty_declaration_as_none(env):
    c = env["c"]
    _edit(c, "qubits.q3.T1", "3.3e-05")
    r = c.post("/state/sync", data={"mode": "apply", "seen_paths": "[]"}, headers=JSON)
    assert r.status_code == 200, r.get_data(as_text=True)[:400]
    assert _live(env, "q3") == 3.3e-5
