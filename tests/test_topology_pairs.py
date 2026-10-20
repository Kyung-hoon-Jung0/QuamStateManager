"""Pairs as first-class map objects: the jsdom selfcheck over the shipped JS,
plus the two server-side pieces it relies on (the proven fidelity run and the
CZ gate plan link by pair id)."""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app

ROOT = Path(__file__).resolve().parents[1]


def _chip(tmp_path, fid):
    chip = tmp_path / "chip"
    chip.mkdir()
    state = {
        "qubits": {
            "q1": {"id": "q1", "grid_location": "0,0"},
            "q2": {"id": "q2", "grid_location": "1,0"},
        },
        "qubit_pairs": {
            "q1-q2": {"id": "q1-q2", "qubit_control": "#/qubits/q1",
                      "qubit_target": "#/qubits/q2",
                      "macros": {"cz": {"fidelity": fid}}},
        },
    }
    (chip / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (chip / "wiring.json").write_text('{"wiring":{"qubits":{}}}', encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "instance"))
    client = app.test_client()
    client.post("/load", data={"folder": str(chip)})
    return client


def _edge(client):
    return client.get("/api/topology").get_json()["edges"][0]


def test_fidelity_run_is_named_only_beside_its_own_metric(tmp_path):
    client = _chip(tmp_path, {"InterleavedRB": 0.991, "InterleavedRB_load_id": 412})
    edge = _edge(client)
    assert edge["cz_fidelity"] == pytest.approx(0.991)
    # v1.5.2 (v152b): load id -> unknown; a hint does not prove a write.
    assert edge["cz_fidelity_run"] is None


def test_no_run_when_the_shown_number_came_from_elsewhere(tmp_path):
    # the Bell number is shown; the stored load id belongs to standard RB
    client = _chip(tmp_path, {"Bell_State": {"Fidelity": 0.95}, "StandardRB_load_id": 7})
    edge = _edge(client)
    assert edge["cz_fidelity"] == pytest.approx(0.95)
    assert edge["cz_fidelity_run"] is None


def test_cz_plan_accepts_a_pair_id(tmp_path):
    from tests.test_cz_plan_page import _client

    # v1.5.2 (v152b): HTTP 200 -> second usable pair identity; reject silent fallback.
    client = _client(tmp_path)
    page = client.get("/cz-plan?pair=q3-2", headers={"HX-Request": "true"})
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert 'data-selected="qubit_pairs.q3-2.macros.cz"' in html
    assert 'data-op="qubit_pairs.q1-2.macros.cz_unipolar"' in html
    for pair in ("unknown", '<img src=x onerror="alert(1)">'):
        html = client.get("/cz-plan", query_string={"pair": pair},
                          headers={"HX-Request": "true"}).get_data(as_text=True)
        assert 'data-selected=""' in html and "Unknown pair:" in html
        assert '<img src=x' not in html
        if pair.startswith("<"):
            assert "&lt;img" in html


def test_topology_pairs_selfcheck():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    r = subprocess.run([node, str(ROOT / "tests" / "topology_pairs_selfcheck.cjs")],
                       capture_output=True, text=True, encoding="utf-8", timeout=300,
                       env=dict(os.environ))
    if r.returncode == 2 and "jsdom not installed" in r.stderr:
        pytest.skip("jsdom not installed")
    assert r.returncode == 0, r.stdout + r.stderr
