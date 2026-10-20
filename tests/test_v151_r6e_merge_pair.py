"""R6e: an unreadable or newly changed live pair never licenses replay."""
import json
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.core import working_copy
from quam_state_manager.web import routes
from tests.test_v151_r4_merge_live_values import chip, _lab_writes, _live


def _ctx(c):
    with c.application.app_context():
        return routes._active_ctx()


@pytest.mark.parametrize("automatic", [False, True])
def test_unreadable_verdict_refuses_without_pulling(chip, monkeypatch, automatic):
    c, live = chip
    ctx = _ctx(c)
    if automatic:
        c.post("/auto-sync/set", data={"pull": "1", "push": "1"})
    before = (live / "state.json").read_bytes()
    calls = []
    monkeypatch.setattr(routes, "_auto_pull_verdict", lambda *a, **k: None)
    real = working_copy.sync_from_live
    def tracked(wc):
        calls.append(True)
        return real(wc)
    monkeypatch.setattr(working_copy, "sync_from_live", tracked)
    data = {"mode": "apply", "check_collisions": "1"}
    if automatic:
        with c.application.app_context():
            data["expect_chip"] = routes._active_chip_token()
    r = c.post("/state/sync", data=data)
    b = r.get_json()
    assert r.status_code == 409 and b["status"] == "error", b
    assert b.get("error_code") == "live_busy", b
    assert "being written right now" in b["message"]
    assert "nothing was pulled or applied" in b["message"]
    assert not calls
    assert (live / "state.json").read_bytes() == before
    assert len(ctx["store"].change_log) == 1
    if automatic:
        assert ctx.get("auto_apply") and "resolving this" not in b["tray_html"]


@pytest.mark.parametrize("mode,stash", [("apply", False), ("apply", True), ("reapply", False)])
def test_collision_is_checked_on_the_pair_actually_pulled(chip, monkeypatch, mode, stash):
    c, live = chip
    ctx = _ctx(c)
    if stash:
        with c.application.app_context():
            routes._stash_reapply({"qubits.q1.T1": 3e-5}, ctx)
    _lab_writes(live, f01=5.05e9)
    real = working_copy.sync_from_live
    def late(wc):
        _lab_writes(live, t1=2.5e-5, f01=5.05e9)
        return real(wc)
    monkeypatch.setattr(working_copy, "sync_from_live", late)
    b = c.post("/state/sync", data={"mode": mode, "check_collisions": "1"}).get_json()
    assert b["status"] == "collision" and b["paths"] == ["qubits.q1.T1"], b
    assert _live(live)["T1"] == 2.5e-5
    assert ctx["store"].state["qubits"]["q1"]["T1"] == 3e-5
    assert [e.dot_path for e in ctx["store"].change_log] == ["qubits.q1.T1"]
    assert ctx["working_copy"].synced_live_hash != working_copy.live_content_hash(ctx["working_copy"])
    # A retry must still ask, rather than mistaking the refused pull for consent.
    assert c.post("/state/sync", data={"mode": mode, "check_collisions": "1"}).get_json()["status"] == "collision"


def test_acknowledged_collision_can_replay(chip, monkeypatch):
    c, live = chip
    _lab_writes(live, t1=2.5e-5)
    b = c.post("/state/sync", data={"mode": "apply", "check_collisions": "1", "ack_collision": "1"}).get_json()
    assert b["status"] == "ok", b
    assert _live(live)["T1"] == 3e-5


def test_busy_refusal_clients(tmp_path):
    import os
    r = subprocess.run(["node", str(Path(__file__).with_name("v151_r6e_merge_busy_selfcheck.cjs"))],
                       capture_output=True, text=True, encoding="utf-8", timeout=90,
                       env=dict(os.environ, NODE_PATH="D:/work/statemanager/node_modules"))
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.parametrize("attempt", range(3))
def test_midwrite_never_replaces_live_value_unasked(chip, attempt):
    """Reviewer's merge_midwrite shape: truncate, pause, finish the pair."""
    import os
    import threading
    import time
    c, live = chip
    state_file = live / "state.json"
    state = json.loads(state_file.read_text())
    value = 2.5e-5 + attempt * 1e-6
    state["qubits"]["q1"]["T1"] = value
    content = json.dumps(state)
    for name in ("state.json", "wiring.json"):
        os.utime(live / name, (time.time() - 600,) * 2)
    writing = threading.Event()
    def writer():
        with state_file.open("w", encoding="utf-8") as f:
            f.write(content[:8]); f.flush()
            writing.set()
            time.sleep(1.5)
            f.write(content[8:])
        wiring = live / "wiring.json"
        wiring.write_bytes(wiring.read_bytes())
    t = threading.Thread(target=writer)
    t.start()
    try:
        assert writing.wait(5)
        b = c.post("/state/sync", data={"mode": "apply", "check_collisions": "1"}).get_json()
    finally:
        t.join(10)
    assert not t.is_alive()
    assert b["status"] in ("error", "collision"), b
    assert _live(live)["T1"] == value
    assert [e.dot_path for e in _ctx(c)["store"].change_log] == ["qubits.q1.T1"]
