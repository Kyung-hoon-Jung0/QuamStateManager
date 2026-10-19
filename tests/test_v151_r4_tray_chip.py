"""v1.5.1 R4 a2: the drift poll's tray_durable is applied to the tab's own chip only.

R3 MINOR 4 made every page follow ``tray_durable`` from /state/drift, but
the poll answers for the chip open NOW (``_active_ctx``): a tab showing chip
A, after another window opened chip B, took B's answer for A's tray -- a
durable B could silence "Leave site?" over A's failing checkpoint. The poll
now names the chip its answer is about (``tray_chip``) and a tab applies it
only when that is the chip it shows (``window.__chipKey``).

Pinned on the REAL page and the REAL /state/drift answers of two chips under
jsdom (tests/v151_r4_tray_chip_selfcheck.cjs).
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


def _chip(folder: Path) -> Path:
    folder.mkdir(parents=True)
    (folder / "state.json").write_text(json.dumps(
        {"qubits": {"q1": {"id": "q1", "T1": 1e-5}}, "qubit_pairs": {},
         "active_qubit_names": ["q1"]}), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")
    return folder


def test_a_tab_takes_tray_durable_only_for_its_own_chip(tmp_path, monkeypatch):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    a = _chip(tmp_path / "chip_a" / "quam_state")
    b = _chip(tmp_path / "chip_b" / "quam_state")
    routes._quam_cache.clear()
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(a)}).status_code in (200, 302)
    # chip A's checkpoint writes fail: its tab must keep asking on leave
    real = safe_io.atomic_write_json

    def refuse(path, *args, **kw):
        if ".pending_tray" in str(path):
            raise PermissionError(13, "denied", str(path))
        return real(path, *args, **kw)
    monkeypatch.setattr(safe_io, "atomic_write_json", refuse)
    assert c.post("/field/edit", data={"dot_path": "qubits.q1.T1", "value": "2e-05"}).get_json()["ok"]
    p = app.config["contexts"][app.config["active_context"]]["_pending_tray"]
    end = time.time() + 10
    while not p.failing and time.time() < end:
        time.sleep(0.05)
    assert p.failing, "fixture: chip A's checkpoint writer reports the failure"
    page_a = c.get("/help").get_data(as_text=True)
    assert 'data-tray-durable="1"' not in page_a and "window.__chipKey" in page_a
    drift_a = c.get("/state/drift").get_json()
    assert drift_a["tray_durable"] is False and drift_a["tray_chip"], drift_a
    monkeypatch.undo()
    # another window opens chip B, whose rows are checkpointed
    assert c.post("/load", data={"folder": str(b)}).status_code in (200, 302)
    assert c.post("/field/edit", data={"dot_path": "qubits.q1.T1", "value": "3e-05"}).get_json()["ok"]
    pending_tray.flush_all()
    drift_b = c.get("/state/drift").get_json()
    assert drift_b["tray_durable"] is True and drift_b["tray_chip"] not in ("", drift_a["tray_chip"]), drift_b
    fx = tmp_path / "fixture.json"
    fx.write_text(json.dumps({"page": page_a, "drift_a": drift_a, "drift_b": drift_b}), encoding="utf-8")
    r = subprocess.run([node, str(ROOT / "tests" / "v151_r4_tray_chip_selfcheck.cjs"), str(fx)],
                       capture_output=True, text=True, encoding="utf-8", timeout=180, cwd=str(ROOT))
    if r.returncode == 2:
        pytest.skip("jsdom not installed")
    assert r.returncode == 0 and "DONE" in r.stdout, r.stdout[-3000:] + r.stderr[-3000:]
