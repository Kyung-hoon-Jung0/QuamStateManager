"""Shared component-page chip layout (docs/92 P2/P3) — server pins + selfcheck.

The drawing is client-side (TopoGraph.renderLayout via ComponentMap); the
server's obligations are: mount the map container ABOVE each component table,
leave the tables untouched (rows keep their data-qubit-id / data-pair-id
binding hooks), and ship has_coupler on topology edges (the coupler symbols
key on it). The jsdom selfcheck pins the drawing + binding behaviour against
the real shipped JS.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.core.loader import QuamStore
from quam_state_manager.core.query import QueryEngine
from quam_state_manager.web.app import create_app

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "component_map_selfcheck.cjs"


def _state():
    return {
        "qubits": {
            "qA1": {"id": "qA1", "grid_location": "0,0", "T1": 2.4e-5,
                    "resonator": {"operations": {"readout": {"amplitude": 0.04}}},
                    "z": {"flux_point": "independent"}},
            "qA2": {"id": "qA2", "grid_location": "1,0", "T1": 1.8e-5},
        },
        "qubit_pairs": {
            "qA2-qA1": {
                "id": "qA2-qA1",
                "qubit_control": "#/qubits/qA2",
                "qubit_target": "#/qubits/qA1",
                "coupler": {"decouple_offset": 0.01},
                "macros": {"cz": {"fidelity": {"Bell_State": {"Fidelity": 0.96}}}},
            },
            "qA1-qA2x": {
                "id": "qA1-qA2x",
                "qubit_control": "#/qubits/qA1",
                "qubit_target": "#/qubits/qA2",
                "coupler": None,
            },
        },
    }


def _client(tmp_path):
    (tmp_path / "state.json").write_text(json.dumps(_state()), encoding="utf-8")
    (tmp_path / "wiring.json").write_text(
        json.dumps({"wiring": {"qubits": {}}, "network": {"host": "10.0.0.1"}}),
        encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    client = app.test_client()
    client.post("/load", data={"folder": str(tmp_path)})
    return client


def test_topology_edges_carry_has_coupler():
    eng = QueryEngine(QuamStore.from_dicts(_state(), {"wiring": {"qubits": {}}}))
    edges = {e["pair_id"]: e for e in eng.get_topology()["edges"]}
    # dict-valued coupler counts; explicit null does not (get_pair's convention)
    assert edges["qA2-qA1"]["has_coupler"] is True
    assert edges["qA1-qA2x"]["has_coupler"] is False


# Every component page mounts the map ABOVE its table with its own highlight,
# and the table keeps its binding hooks (rows untouched). P2 shipped Pairs;
# P3 added the remaining four (docs/91 §4).
_PAGES = [
    ("/qubits", "qubits", 'data-qubit-id="qA1"'),
    ("/pairs", "pairs", 'data-pair-id="qA2-qA1"'),
    ("/resonators", "resonators", 'data-qubit-id="qA1"'),
    ("/flux", "flux", 'data-qubit-id="qA1"'),
    ("/couplers", "couplers", 'data-pair-id="qA2-qA1"'),
]


@pytest.mark.parametrize("url,highlight,row_marker", _PAGES)
def test_component_page_mounts_map_above_table(tmp_path, url, highlight, row_marker):
    body = _client(tmp_path).get(url).get_data(as_text=True)
    assert 'id="component-map"' in body, f"{url}: map container missing"
    assert f'data-highlight="{highlight}"' in body, f"{url}: wrong highlight mode"
    assert row_marker in body, f"{url}: table row binding hook missing"
    # additive ABOVE the table: the mount precedes the data table
    assert body.index('id="component-map"') < body.index("data-table"), (
        f"{url}: map must sit above the table")


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_component_map_selfcheck_passes():
    r = subprocess.run(
        ["node", str(_SELFCHECK)],
        capture_output=True, text=True, cwd=str(_ROOT), timeout=120,
    )
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "all checks passed" in r.stdout, (r.stdout + r.stderr)
