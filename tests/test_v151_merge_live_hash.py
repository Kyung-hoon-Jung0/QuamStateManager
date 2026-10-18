"""v1.5.1 SB-P2-1: a "Keep mine" merge replaces only the live value it named.

The sync panel's Pull & apply named "Lost: the live chip's q1.T1 = A" from
its read; the lab wrote B a second later and the merge replaced B with no
word (the backup held it). The panel now carries the live chip it read
(``data-live-hash``); a merge with a Keep-mine pick declares it and is refused
(nothing written, edits kept) when live moved since. Pinned over the real
routes, and on the real panel markup under jsdom for the client half.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app

ROOT = Path(__file__).resolve().parent.parent
_WIRING = {"network": {"host": "127.0.0.1", "cluster_name": "C1"}}


def _state(t1, f01=5.0e9):
    return {"qubits": {"q1": {"id": "q1", "T1": t1, "f_01": f01}}, "qubit_pairs": {},
            "active_qubit_names": ["q1"]}


def _write_live(live: Path, state: dict, bump: int):
    (live / "state.json").write_text(json.dumps(state), encoding="utf-8")
    t = time.time() + 2 * bump
    import os
    os.utime(live / "state.json", (t, t))


@pytest.fixture
def env(tmp_path):
    live = tmp_path / "chip" / "quam_state"
    live.mkdir(parents=True)
    (live / "state.json").write_text(json.dumps(_state(1.0e-5)), encoding="utf-8")
    (live / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")
    routes._quam_cache.clear()
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    assert c.post("/field/edit", data={"dot_path": "qubits.q1.T1", "value": "3e-05"}).get_json()["ok"]
    _write_live(live, _state(2.05e-5), 1)                  # the lab writes the same field
    return {"app": app, "c": c, "live": live, "tmp": tmp_path}


def _panel(env) -> tuple[str, str]:
    html = env["c"].get("/state/review").get_data(as_text=True)
    m = re.search(r'id="sp-merge"[^>]*data-live-hash="([0-9a-f]+)"', html)
    assert m, html[:3000]
    return html, m.group(1)


def _live_t1(env):
    return json.loads((env["live"] / "state.json").read_text(encoding="utf-8"))["qubits"]["q1"]["T1"]


def _merge(env, live_hash):
    return env["c"].post("/state/sync", data={
        "mode": "apply", "picks": json.dumps({"qubits.q1.T1": "mine"}),
        "expect_live_hash": live_hash})


def test_a_lab_write_after_the_panel_read_is_not_replaced_unnamed(env):
    html, h = _panel(env)
    assert "2.05e-05" in html or "0.0000205" in html or "2.05" in html
    _write_live(env["live"], _state(1.9e-5), 2)           # the lab writes again
    r = _merge(env, h)
    assert r.status_code == 409, (r.status_code, r.data[:400])
    d = r.get_json()
    assert d["status"] == "live_moved" and "Nothing was written" in d["message"]
    assert _live_t1(env) == 1.9e-5, "the lab's newer value stands"
    ctx = env["app"].config["contexts"][env["app"].config["active_context"]]
    assert any(c.dot_path == "qubits.q1.T1" for c in ctx["store"].change_log), "the edit is kept"


def test_the_merge_writes_when_live_is_what_the_panel_named(env):
    _html, h = _panel(env)
    r = _merge(env, h)
    assert r.status_code == 200, (r.status_code, r.data[:400])
    assert r.get_json()["status"] == "ok"
    assert _live_t1(env) == 3e-5


def test_the_real_panel_declares_what_it_read(env):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    html, h = _panel(env)
    fx = env["tmp"] / "fixture.json"
    fx.write_text(json.dumps({"panel_html": html, "live_hash": h}), encoding="utf-8")
    r = subprocess.run([node, str(ROOT / "tests" / "v151_merge_live_hash_selfcheck.cjs"), str(fx)],
                       capture_output=True, text=True, encoding="utf-8", timeout=180, cwd=str(ROOT))
    if r.returncode == 2:
        pytest.skip("jsdom not installed")
    assert r.returncode == 0 and "DONE" in r.stdout, r.stdout[-3000:] + r.stderr[-3000:]
