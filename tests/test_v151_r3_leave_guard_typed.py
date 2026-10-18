"""v1.5.1 R3 MINOR 4 + MINOR 5: the leave guard's two blind spots.

MINOR 4: ``data-tray-durable`` was decided when the tray RENDERED -- for a
commit, before the background checkpoint write even ran. A write that then
failed never reached the page (the drift poll's signature did not move), so
"Leave site?" stayed silent for exactly the case it is kept for. The drift
poll now carries ``tray_durable`` and the page follows it.

MINOR 5: since SB-P2-8 a durable tray lets a page go -- but a value typed and
not committed in the inspector forms (qubit / pair / pulse fields) or the
Json Tree editors had only the tray's guard. It is asked about now, tray or
not.

Pinned on the REAL full page, the real qubit and pulse inspectors and the real
drift answer under jsdom (tests/v151_r3_leave_guard_typed_selfcheck.cjs).
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
from tests.test_pulses_routes import _make_state, _make_wiring

ROOT = Path(__file__).resolve().parent.parent


def test_leaving_asks_for_a_failing_checkpoint_and_for_typed_values(tmp_path, monkeypatch):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    live = tmp_path / "chip" / "quam_state"
    live.mkdir(parents=True)
    st = _make_state()
    (live / "state.json").write_text(json.dumps(st), encoding="utf-8")
    (live / "wiring.json").write_text(json.dumps(_make_wiring()), encoding="utf-8")
    routes._quam_cache.clear()
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    q = sorted(st["qubits"])[0]
    leaf = next(k for k, v in st["qubits"][q].items() if isinstance(v, (int, float)) and not isinstance(v, bool))
    assert c.post("/field/edit", data={"dot_path": f"qubits.{q}.{leaf}",
                                       "value": str(st["qubits"][q][leaf] * 1.01)}).get_json()["ok"]
    pending_tray.flush_all()
    page = c.get("/help").get_data(as_text=True)
    assert 'data-tray-durable="1"' in page
    inspector = c.get(f"/qubit/{q}").get_data(as_text=True)
    assert "inline-edit" in inspector and "edit-input" in inspector
    pulse = c.get(f"/pulse/detail?path=qubits.{q}.xy.operations.x180_DragCosine").get_data(as_text=True)
    assert "pulse-edit-form" in pulse

    # the checkpoint writes start failing after the page was drawn
    real = safe_io.atomic_write_json

    def refuse(path, *a, **k):
        if ".pending_tray" in str(path):
            raise PermissionError(13, "denied", str(path))
        return real(path, *a, **k)

    monkeypatch.setattr(safe_io, "atomic_write_json", refuse)
    assert c.post("/field/edit", data={"dot_path": f"qubits.{q}.{leaf}",
                                       "value": str(st["qubits"][q][leaf] * 1.02)}).get_json()["ok"]
    p = app.config["contexts"][app.config["active_context"]]["_pending_tray"]
    end = time.time() + 10
    while not p.failing and time.time() < end:
        time.sleep(0.05)
    assert p.failing, "fixture: the checkpoint writer reports the failure"
    drift = c.get("/state/drift").get_json()
    assert drift.get("tray_durable") is False, drift
    monkeypatch.undo()

    fx = tmp_path / "fixture.json"
    fx.write_text(json.dumps({"page": page, "inspector": inspector, "pulse": pulse,
                              "drift": drift}), encoding="utf-8")
    r = subprocess.run([node, str(ROOT / "tests" / "v151_r3_leave_guard_typed_selfcheck.cjs"), str(fx)],
                       capture_output=True, text=True, encoding="utf-8", timeout=180, cwd=str(ROOT))
    if r.returncode == 2:
        pytest.skip("jsdom not installed")
    assert r.returncode == 0 and "DONE" in r.stdout, r.stdout[-3000:] + r.stderr[-3000:]
