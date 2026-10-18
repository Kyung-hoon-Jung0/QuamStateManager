"""v1.5.1 R3 MINOR 6: a run started after a tab's first scheduler poll is seen.

Without the runner UI (no SM_EXPERIMENTAL) a tab asks /scheduler/status once
and stops when nothing runs (U P2-28). A run started afterwards -- an Agent
run_node, the MCP -- was never seen by that tab: no editing lock, no running
badge, no re-render when the run wrote the chip. The drift poll every page
already makes now carries a runner token (the queue file's mtime + whether
this process runs a queue or a plan); when it moves, the tab asks again.

Pinned over the real routes (the token moves when a run is written to the
queue) and on the REAL page's scripts under jsdom
(tests/v151_r3_run_after_load_selfcheck.cjs).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from quam_state_manager.core import scheduler
from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app

ROOT = Path(__file__).resolve().parent.parent
_WIRING = {"network": {"host": "127.0.0.1", "cluster_name": "C1"}}


def test_a_run_started_after_the_first_poll_reaches_the_tab(tmp_path, monkeypatch):
    monkeypatch.delenv("SM_EXPERIMENTAL", raising=False)
    live = tmp_path / "chip" / "quam_state"
    live.mkdir(parents=True)
    (live / "state.json").write_text(json.dumps(
        {"qubits": {"q1": {"id": "q1", "T1": 1e-5}}, "qubit_pairs": {},
         "active_qubit_names": ["q1"]}), encoding="utf-8")
    (live / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")
    routes._quam_cache.clear()
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    page = c.get("/help").get_data(as_text=True)
    assert 'data-sm-runner="1"' not in page, "fixture: the plain build (no runner UI)"
    idle = c.get("/scheduler/status").get_json()
    d0 = c.get("/state/drift").get_json()
    assert isinstance(d0.get("runner_sig"), str), d0

    # a run starts on this chip from outside any page (an Agent run_node writes the queue)
    with app.test_request_context():
        scope = routes._sched_inst()
    time.sleep(0.02)
    with scheduler._QLOCK:
        st = scheduler.load_queue(scope)
        st["queue"] = [{"id": "abcd1234", "name": "04_power_rabi", "status": "running",
                        "enabled": True}]
        st["run"] = dict(st.get("run") or {}, status="running", current_id="abcd1234",
                         worker_pid=os.getpid(), chip_rev=1)
        scheduler.save_queue(scope, st)
    t = time.time() + 5
    os.utime(scheduler.queue_path(scope), (t, t))
    d1 = c.get("/state/drift").get_json()
    assert d1["runner_sig"] != d0["runner_sig"], "the drift poll's runner token did not move"
    running = c.get("/scheduler/status").get_json()

    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    fx = tmp_path / "fixture.json"
    fx.write_text(json.dumps({"page": page, "idle": idle, "running": running,
                              "drift0": d0, "drift1": d1}), encoding="utf-8")
    r = subprocess.run([node, str(ROOT / "tests" / "v151_r3_run_after_load_selfcheck.cjs"), str(fx)],
                       capture_output=True, text=True, encoding="utf-8", timeout=180, cwd=str(ROOT))
    if r.returncode == 2:
        pytest.skip("jsdom not installed")
    assert r.returncode == 0 and "DONE" in r.stdout, r.stdout[-3000:] + r.stderr[-3000:]
