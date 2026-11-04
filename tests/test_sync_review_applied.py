"""Final walk R-f / R-e / R-d: a value SM applied that an outside save then
removed is NEWER than live; the sync panel counts it as a loss, offers a merge
that keeps it, and every window agrees on the verdict. Also drives
sync_poll_guard_selfcheck.cjs (the drift poll's guards in the real app.js)."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import quam_state_manager.web.routes as routes_mod
from quam_state_manager.web.app import create_app
from tests.test_autosync_merge import _write_chip

_HERE = Path(__file__).parent


def _st(t1=2.0e-5, f01=5.0e9, t2=1.0e-5):
    return {"qubits": {"q1": {"id": "q1", "f_01": f01, "T1": t1, "T2ramsey": t2}},
            "qubit_pairs": {}, "active_qubit_names": ["q1"]}


@pytest.fixture
def env(tmp_path):
    live = tmp_path / "chips" / "live"
    _write_chip(live, _st())
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    return {"app": app, "client": c, "live": live}


def _live(env):
    return json.loads((env["live"] / "state.json").read_text(encoding="utf-8"))["qubits"]["q1"]


def _outside(env, state):
    _write_chip(env["live"], state)
    with env["app"].test_request_context():
        ctx = routes_mod._active_ctx()
        ctx["_live_hash_checked_at"] = None
        routes_mod._refresh_live_diverged(ctx)


def _apply_t1(env):
    c = env["client"]
    assert c.post("/field/edit", data={"dot_path": "qubits.q1.T1", "value": "3.3e-05"}).status_code == 200
    r = c.post("/state/apply-to-live")
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    assert _live(env)["T1"] == 3.3e-05


def _run_rewrite(env):
    """A node's save from its in-memory copy: T1 back to the old value, two
    other fields moved."""
    _outside(env, _st(t1=2.0e-5, f01=5.2e9, t2=1.5e-5))


def _button(html, cls):
    m = re.search(r'<button[^>]*class="[^"]*' + cls + r'[^"]*"[^>]*>.*?</button>', html, re.S)
    assert m, f"no {cls} button"
    return m.group(0)


class TestTheReviewCountsAnAppliedValue:
    def test_the_rows_and_choices_name_it(self, env):
        _apply_t1(env)
        _run_rewrite(env)
        html = env["client"].get("/state/review").get_data(as_text=True)
        row = re.search(r'<tr[^>]*data-path="qubits.q1.T1".*?</tr>', html, re.S)
        assert row and "you applied" in row.group(0), html[:2000]
        take = _button(html, "sp-take")
        assert "sync-arm" in take.split(">", 1)[0]
        lost = re.search(r'<span class="sp-lost">(.*?)</span>', take, re.S).group(1)
        assert "1 value you applied" in lost, lost
        keep = _button(html, "sp-keep")
        what = re.search(r'<span class="sp-choice-what">(.*?)</span>', keep, re.S).group(1)
        assert "older value" not in what or "you applied" in what, what
        assert "1 value you applied" in what, what
        assert 'id="sp-merge"' in html and "data-keep-applied" in html

    def test_the_merge_keeps_both(self, env):
        _apply_t1(env)
        _run_rewrite(env)
        r = env["client"].post("/state/sync?mode=apply&keep_applied=1")
        assert r.status_code == 200, r.get_data(as_text=True)[:400]
        q = _live(env)
        assert q["f_01"] == 5.2e9 and q["T2ramsey"] == 1.5e-5, q
        assert q["T1"] == 3.3e-05, f"the applied value was lost: {q}"
        with env["app"].test_request_context():
            assert not routes_mod._active_ctx()["store"].change_log


class TestEveryWindowAgrees:
    def test_two_windows_one_verdict(self, env):
        c1, c2 = env["client"], env["app"].test_client()
        _apply_t1(env)
        _run_rewrite(env)
        d1, d2 = c1.get("/state/drift").get_json(), c2.get("/state/drift").get_json()
        assert d1["sync"]["state"] == d2["sync"]["state"] == "live", (d1["sync"], d2["sync"])
        assert d1["sync"]["sig"] == d2["sync"]["sig"]
        for c in (c1, c2):
            assert "Live chip changed" in c.get("/state/tray").get_data(as_text=True)
        assert c2.post("/state/sync?mode=discard").status_code == 200
        d1, d2 = c1.get("/state/drift").get_json(), c2.get("/state/drift").get_json()
        assert d1["sync"]["state"] == d2["sync"]["state"] == "synced", (d1["sync"], d2["sync"])
        assert d1["sync"]["sig"] == d2["sync"]["sig"]
        for c in (c1, c2):
            t = c.get("/state/tray").get_data(as_text=True)
            assert "Live chip changed" not in t and 'data-sync-state="synced"' in t


def test_an_unapplied_edit_and_a_live_change_read_both(env):
    c = env["client"]
    assert c.post("/field/edit", data={"dot_path": "qubits.q1.T1", "value": "3.3e-05"}).status_code == 200
    _outside(env, _st(f01=5.2e9))
    d = c.get("/state/drift").get_json()
    assert d["sync"]["state"] == "both", d["sync"]
    assert "unapplied · live changed" in c.get("/state/tray").get_data(as_text=True)


def test_the_drift_poll_guards_in_the_real_app_js():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    r = subprocess.run([node, str(_HERE / "sync_poll_guard_selfcheck.cjs")],
                       capture_output=True, text=True, encoding="utf-8", timeout=180)
    if r.returncode == 2 and "jsdom not installed" in r.stderr:
        pytest.skip("jsdom not installed")
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-2000:]
    assert "PASS 12/12" in r.stdout, r.stdout[-1500:]
