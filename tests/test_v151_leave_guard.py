"""v1.5.1 SB-P2-8: "Leave site?" only when something would truly be lost.

Every full-page navigation with a non-empty tray raised "Leave site?",
although the rows are checkpointed server-side. Pinned on the REAL full page
under jsdom (tests/v151_leave_guard_selfcheck.cjs): a checkpointed tray lets
the page go; a tray whose checkpoint writes are failing still asks.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from quam_state_manager.core import pending_tray, safe_io
from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app

ROOT = Path(__file__).resolve().parent.parent
_WIRING = {"network": {"host": "127.0.0.1", "cluster_name": "C1"}}


def test_leaving_a_page_asks_only_when_the_tray_is_not_checkpointed(tmp_path, monkeypatch):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
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
    assert c.post("/field/edit", data={"dot_path": "qubits.q1.T1", "value": "2e-05"}).get_json()["ok"]
    pending_tray.flush_all()
    durable = c.get("/help").get_data(as_text=True)
    assert 'data-tray-durable="1"' in durable
    # the checkpoint writes start failing (a full disk, a denied instance folder)
    real = safe_io.atomic_write_json

    def refuse(path, *a, **k):
        if ".pending_tray" in str(path):
            raise PermissionError(13, "denied", str(path))
        return real(path, *a, **k)

    monkeypatch.setattr(safe_io, "atomic_write_json", refuse)
    assert c.post("/field/edit", data={"dot_path": "qubits.q1.T1", "value": "3e-05"}).get_json()["ok"]
    p = app.config["contexts"][app.config["active_context"]]["_pending_tray"]
    end = time.time() + 10
    while not p.failing and time.time() < end:
        time.sleep(0.05)
    assert p.failing, "fixture: the checkpoint writer reports the failure"
    failing = c.get("/help").get_data(as_text=True)
    assert 'data-tray-durable="1"' not in failing
    fx = tmp_path / "fixture.json"
    fx.write_text(json.dumps({"page_durable": durable, "page_failing": failing}), encoding="utf-8")
    r = subprocess.run([node, str(ROOT / "tests" / "v151_leave_guard_selfcheck.cjs"), str(fx)],
                       capture_output=True, text=True, encoding="utf-8", timeout=180, cwd=str(ROOT))
    if r.returncode == 2:
        pytest.skip("jsdom not installed")
    assert r.returncode == 0 and "DONE" in r.stdout, r.stdout[-3000:] + r.stderr[-3000:]
