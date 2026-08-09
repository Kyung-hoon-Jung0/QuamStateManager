"""Chip Status hero map (docs/92 P1) — server-render pin + the jsdom selfcheck.

The hero map is built client-side (chip-status.js buildHeroMap) from the same
topology payload as the card diagram; the server's job is only to mount it.
Pins: the #topo-hero mount exists on /topology and LEADS the section (sits
before the card wrap), and the behavioral selfcheck (honesty modes, edge-colour
parity with the cards, value text on nodes, coincident-cell fan-out) passes
against the real shipped JS.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "chip_status_hero_selfcheck.cjs"


def _client(tmp_path):
    state = {
        "qubits": {
            "qA1": {"id": "qA1", "grid_location": "0,0", "T1": 2.4e-5},
            "qA2": {"id": "qA2", "grid_location": "1,0", "T1": 1.8e-5},
        },
        "qubit_pairs": {
            "qA2-qA1": {
                "id": "qA2-qA1",
                "qubit_control": "#/qubits/qA2",
                "qubit_target": "#/qubits/qA1",
                "macros": {"cz": {"fidelity": {"Bell_State": {"Fidelity": 0.96}}}},
            },
        },
    }
    (tmp_path / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (tmp_path / "wiring.json").write_text(
        json.dumps({"wiring": {"qubits": {}}, "network": {"host": "10.0.0.1"}}),
        encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    client = app.test_client()
    client.post("/load", data={"folder": str(tmp_path)})
    return client


def test_topology_page_mounts_hero_before_cards(tmp_path):
    body = _client(tmp_path).get("/topology").get_data(as_text=True)
    assert 'id="topo-hero"' in body
    # the hero LEADS the topology section — its mount precedes the card wrap
    assert body.index('id="topo-hero"') < body.index('id="topo-html-wrap"')


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_hero_selfcheck_passes():
    r = subprocess.run(
        ["node", str(_SELFCHECK)],
        capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT), timeout=120,
    )
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "all checks passed" in r.stdout, (r.stdout + r.stderr)
