"""v1.5.1 R4 a1: a sync-panel merge refuses only when a field it writes moved.

R3 M4 made every panel merge declare the live chip it read (its whole hash),
and any lab write since the panel read -- even to a field nobody edited,
which the merge's pull takes and keeps -- refused it with "the merge could
replace a value the panel did not name", which was false.

The panel now also declares the live value (a digest) at every field the
merge writes, through the same rule Keep mine uses
(``working_copy.replaced_values``). On a moved live chip the press is
refused only when one of those fields moved -- named in the answer -- and
merges otherwise, keeping the lab's write. Checked before the pull and again
on what the pull read. A press that declares no values (an older page)
keeps the whole-chip refusal, now worded as what it is.
"""
from __future__ import annotations

import html
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from quam_state_manager.core import working_copy
from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app

ROOT = Path(__file__).resolve().parent.parent
_WIRING = {"network": {"host": "127.0.0.1", "cluster_name": "C1"}}
_BUMP = [0]


def _state(t1=1.0e-5, f01=5.0e9, t2=1.0e-5):
    return {"qubits": {"q1": {"id": "q1", "T1": t1, "f_01": f01, "T2": t2}}, "qubit_pairs": {},
            "active_qubit_names": ["q1"]}


def _lab_writes(live, **kw):
    (live / "state.json").write_text(json.dumps(_state(**kw)), encoding="utf-8")
    _BUMP[0] += 1
    t = time.time() + 2 * _BUMP[0]
    os.utime(live / "state.json", (t, t))


def _live(live):
    return json.loads((live / "state.json").read_text(encoding="utf-8"))["qubits"]["q1"]


@pytest.fixture
def chip(tmp_path):
    live = tmp_path / "chip" / "quam_state"
    live.mkdir(parents=True)
    (live / "state.json").write_text(json.dumps(_state()), encoding="utf-8")
    (live / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")
    routes._quam_cache.clear()
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    assert c.post("/field/edit", data={"dot_path": "qubits.q1.T1", "value": "3e-05"}).get_json()["ok"]
    return c, live


def _panel_merge(c):
    """What the panel's merge button declares (as SyncPanel.merge sends it)."""
    page = c.get("/state/review").get_data(as_text=True)
    btn = re.search(r'id="sp-merge"[^>]*>', page).group(0)
    lh = re.search(r'data-live-hash="([0-9a-f]+)"', btn).group(1)
    lv = html.unescape(re.search(r"data-live-values='([^']*)'", btn).group(1))
    return lh, lv


def _press(c, lh, lv=None, picks=None):
    data = {"mode": "apply", "check_collisions": "1", "expect_live_hash": lh}
    if lv is not None:
        data["expect_live_values"] = lv
    if picks:
        data["picks"] = json.dumps(picks)
    return c.post("/state/sync", data=data)


def test_a_lab_write_to_a_field_nobody_edited_merges_and_is_kept(chip):
    c, live = chip
    _lab_writes(live, f01=5.05e9)                      # the panel shows this one
    lh, lv = _panel_merge(c)
    assert json.loads(lv) and "qubits.q1.T1" in json.loads(lv)
    _lab_writes(live, f01=5.05e9, t2=2.2e-5)           # after the panel read: T2, edited by nobody
    r = _press(c, lh, lv)
    assert r.status_code == 200 and r.get_json()["status"] == "ok", r.get_data(as_text=True)[:600]
    got = _live(live)
    assert got["T1"] == 3e-5 and got["T2"] == 2.2e-5 and got["f_01"] == 5.05e9, got


def test_a_picked_field_that_moved_again_is_refused_and_named(chip):
    c, live = chip
    _lab_writes(live, t1=1.9e-5)                       # a collision the panel shows
    lh, lv = _panel_merge(c)
    _lab_writes(live, t1=2.5e-5)                       # the lab writes it again after the read
    r = _press(c, lh, lv, picks={"qubits.q1.T1": "mine"})
    body = r.get_json()
    assert r.status_code == 409 and body["status"] == "live_moved", body
    assert "qubits.q1.T1" in body["message"] and body.get("paths") == ["qubits.q1.T1"], body
    assert "did not name" not in body["message"]
    assert _live(live)["T1"] == 2.5e-5


def test_a_press_that_declares_no_values_is_refused_without_a_false_reason(chip):
    c, live = chip
    _lab_writes(live, f01=5.05e9)
    lh, _lv = _panel_merge(c)
    _lab_writes(live, f01=5.05e9, t2=2.2e-5)
    r = _press(c, lh)                                  # an older page: the hash alone
    body = r.get_json()
    assert r.status_code == 409 and body["status"] == "live_moved", body
    assert "could not tell" in body["message"] and "did not name" not in body["message"], body
    assert _live(live)["T1"] == 1e-5


@pytest.mark.parametrize("field,refused", [("T2", False), ("T1", True)])
def test_a_write_landing_during_the_pull_is_judged_the_same_way(chip, monkeypatch, field, refused):
    c, live = chip
    _lab_writes(live, f01=5.05e9)
    lh, lv = _panel_merge(c)
    real = working_copy.sync_from_live

    def late(wc):                                      # lands after the pre-check passed
        _lab_writes(live, f01=5.05e9, **({"t2": 2.2e-5} if field == "T2" else {"t1": 2.5e-5}))
        monkeypatch.setattr(working_copy, "sync_from_live", real)
        return real(wc)
    monkeypatch.setattr(working_copy, "sync_from_live", late)
    r = _press(c, lh, lv)
    body = r.get_json()
    if refused:
        assert r.status_code == 409 and body["status"] == "live_moved", body
        assert "qubits.q1.T1" in body["message"], body
        assert _live(live)["T1"] == 2.5e-5
    else:
        assert r.status_code == 200 and body["status"] == "ok", body
        assert _live(live)["T2"] == 2.2e-5 and _live(live)["T1"] == 3e-5


def test_the_panel_presses_declare_the_values(chip, tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    c, live = chip
    mine = c.get("/state/review").get_data(as_text=True)          # Apply to live
    _lab_writes(live, f01=5.05e9)
    both = c.get("/state/review").get_data(as_text=True)          # the merge
    fx = tmp_path / "fixture.json"
    fx.write_text(json.dumps({"mine": mine, "both": both}), encoding="utf-8")
    r = subprocess.run([node, str(ROOT / "tests" / "v151_r4_merge_live_values_selfcheck.cjs"), str(fx)],
                       capture_output=True, text=True, encoding="utf-8", timeout=180, cwd=str(ROOT))
    if r.returncode == 2:
        pytest.skip("jsdom not installed")
    assert r.returncode == 0 and "DONE" in r.stdout, r.stdout[-3000:] + r.stderr[-3000:]
