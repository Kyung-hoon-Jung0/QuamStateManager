"""PaneState (docs/110 #10-A) — a tab keeps its state when you return.

Server half: the tray carries ``data-seq`` (store.mutation_seq) — PaneState's
freshness beacon (the tray renders on every page and OOB-swaps on every
mutation, so the CURRENT tray always carries the current seq). Client half:
``tests/pane_state_selfcheck.cjs`` (park/restore, stale-seq refetch + soft
re-apply, chip-switch refetch, stateRestored clear, same-route passthrough).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "pane_state_selfcheck.cjs"

_WIRING = {"network": {"host": "1.1.1.1", "cluster_name": "C1"}}
_STATE = {"qubits": {"qA1": {"id": "qA1", "f_01": 5.0e9}},
          "qubit_pairs": {}, "active_qubit_names": ["qA1"]}


@pytest.fixture
def env(tmp_path):
    live = tmp_path / "chips" / "live"
    live.mkdir(parents=True)
    (live / "state.json").write_text(json.dumps(_STATE), encoding="utf-8")
    (live / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    return {"app": app, "client": c}


class TestSeqBeacon:
    def test_tray_carries_mutation_seq(self, env):
        c = env["client"]
        html = c.get("/state/tray").data.decode("utf-8")
        assert 'data-seq="' in html
        seq0 = html.split('data-seq="')[1].split('"')[0]
        assert seq0 != ""
        # a mutation moves the beacon
        r = c.post("/field/edit-batch", json={
            "updates": [{"dot_path": "qubits.qA1.f_01", "value": "5.01e9"}],
            "expect_chip": ""})
        assert r.status_code == 200 and r.get_json()["ok"]
        html2 = c.get("/state/tray").data.decode("utf-8")
        seq1 = html2.split('data-seq="')[1].split('"')[0]
        assert seq1 != seq0, "an edit must move the freshness beacon"

    def test_no_chip_tray_still_renders(self, tmp_path):
        app = create_app(testing=True, instance_path=str(tmp_path / "_i2"))
        c = app.test_client()
        r = c.get("/state/tray")
        assert r.status_code == 200   # empty-seq tray must never 500


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_pane_state_client_selfcheck():
    proc = subprocess.run(
        ["node", str(_SELFCHECK)], capture_output=True, text=True,
        cwd=str(_ROOT), timeout=120)
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
