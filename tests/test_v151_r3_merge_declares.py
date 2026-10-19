"""v1.5.1 R3 M4: every sync-panel merge declares the live chip it read.

SB-P2-1 checked ``expect_live_hash`` only when a pick was "Keep mine", and
the panel's merge sent ``informed`` (no same-field check) and, with no
collision shown, no hash at all. A lab write to a field the user edited,
landing after the panel was read, was then replaced by the user's edit with
no word. Now the panel's merge (and its plain Apply) send the same-field
check and the live hash every time, and the server checks a declared hash
whatever the picks. Pinned over the real routes, and on the real panel
markup under jsdom for the client half.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app

ROOT = Path(__file__).resolve().parent.parent
WIRING = {"network": {"host": "127.0.0.1", "cluster_name": "C1"}}


def _state(t1, f01):
    return {"qubits": {"q1": {"id": "q1", "T1": t1, "f_01": f01}}, "qubit_pairs": {},
            "active_qubit_names": ["q1"]}


def _write_live(live, st, bump):
    (live / "state.json").write_text(json.dumps(st), encoding="utf-8")
    t = time.time() + 2 * bump
    os.utime(live / "state.json", (t, t))


def _live(live):
    return json.loads((live / "state.json").read_text(encoding="utf-8"))["qubits"]["q1"]


@pytest.fixture
def env(tmp_path):
    live = tmp_path / "chip" / "quam_state"
    live.mkdir(parents=True)
    (live / "state.json").write_text(json.dumps(_state(1.0e-5, 5.0e9)), encoding="utf-8")
    (live / "wiring.json").write_text(json.dumps(WIRING), encoding="utf-8")
    routes._quam_cache.clear()
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    assert c.post("/field/edit", data={"dot_path": "qubits.q1.T1", "value": "3e-05"}).get_json()["ok"]
    return {"app": app, "c": c, "live": live, "tmp": tmp_path}


def _merge_button(html):
    m = re.search(r'id="sp-merge"[^>]*', html)
    assert m, html[:3000]
    return m.group(0)


def _hash(btn):
    m = re.search(r'data-live-hash="([0-9a-f]+)"', btn)
    assert m, btn
    return m.group(1)


def test_a_merge_with_no_pick_does_not_replace_a_lab_write_made_after_the_panel(env):
    c, live = env["c"], env["live"]
    _write_live(live, _state(1.0e-5, 5.05e9), 1)      # the lab writes ANOTHER field: no collision shown
    btn = _merge_button(c.get("/state/review").get_data(as_text=True))
    assert "data-needs-picks" not in btn, "no collision is shown"
    h = _hash(btn)
    _write_live(live, _state(1.9e-5, 5.05e9), 2)      # the lab now writes T1, which the user edited
    # what SyncPanel.merge sends for this panel now
    r = c.post("/state/sync", data={"mode": "apply", "check_collisions": "1", "expect_live_hash": h})
    assert r.status_code in (200, 409), r.data[:300]
    assert (r.get_json() or {}).get("status") in ("collision", "live_moved"), r.get_json()
    assert _live(live)["T1"] == 1.9e-5, "the lab's value, written after the panel read, stands"


def test_a_declared_hash_is_checked_without_a_mine_pick(env):
    """The server half alone: a hash declared with no pick is still checked."""
    c, live = env["c"], env["live"]
    _write_live(live, _state(1.0e-5, 5.05e9), 1)
    h = _hash(_merge_button(c.get("/state/review").get_data(as_text=True)))
    _write_live(live, _state(1.9e-5, 5.05e9), 2)
    r = c.post("/state/sync", data={"mode": "apply", "expect_live_hash": h})
    assert r.status_code == 409 and r.get_json()["status"] == "live_moved", r.data[:300]
    assert _live(live)["T1"] == 1.9e-5
    assert "Nothing was written" in r.get_json()["message"]


def test_a_live_pick_merge_is_refused_when_live_moved_on_another_edited_field(env):
    c, live = env["c"], env["live"]
    assert c.post("/field/edit", data={"dot_path": "qubits.q1.f_01",
                                       "value": "5100000000.0"}).get_json()["ok"]
    _write_live(live, _state(2.0e-5, 5.0e9), 1)       # a T1 collision the panel shows
    h = _hash(_merge_button(c.get("/state/review").get_data(as_text=True)))
    _write_live(live, _state(2.0e-5, 5.2e9), 2)       # the lab now writes f_01 too
    r = c.post("/state/sync", data={"mode": "apply", "check_collisions": "1",
                                    "picks": json.dumps({"qubits.q1.T1": "live"}),
                                    "expect_live_hash": h})
    assert (r.get_json() or {}).get("status") in ("collision", "live_moved"), r.get_json()
    assert _live(live)["f_01"] == 5.2e9, "the lab's f_01 stands"


def test_the_merge_still_writes_when_live_is_what_the_panel_read(env):
    c, live = env["c"], env["live"]
    _write_live(live, _state(1.0e-5, 5.05e9), 1)
    h = _hash(_merge_button(c.get("/state/review").get_data(as_text=True)))
    r = c.post("/state/sync", data={"mode": "apply", "check_collisions": "1", "expect_live_hash": h})
    assert r.status_code == 200 and r.get_json()["status"] == "ok", r.data[:300]
    assert _live(live) == {"id": "q1", "T1": 3e-5, "f_01": 5.05e9}


def test_the_real_panel_merge_sends_the_check_and_the_hash(env):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    c, live = env["c"], env["live"]
    _write_live(live, _state(1.0e-5, 5.05e9), 1)
    html = c.get("/state/review").get_data(as_text=True)
    h = _hash(_merge_button(html))
    fx = env["tmp"] / "fixture.json"
    fx.write_text(json.dumps({"panel_html": html, "live_hash": h}), encoding="utf-8")
    r = subprocess.run([node, str(ROOT / "tests" / "v151_r3_merge_declares_selfcheck.cjs"), str(fx)],
                       capture_output=True, text=True, encoding="utf-8", timeout=180, cwd=str(ROOT))
    if r.returncode == 2:
        pytest.skip("jsdom not installed")
    assert r.returncode == 0 and "DONE" in r.stdout, r.stdout[-3000:] + r.stderr[-3000:]
