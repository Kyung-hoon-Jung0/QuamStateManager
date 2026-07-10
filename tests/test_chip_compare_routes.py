"""Tests for the Chip Compare web routes (/chip-compare/*).

Chip Compare lets the user pick 2+ quam_state folders and renders them
side-by-side. Distinct from /compare (dataset-checkbox-driven) — works on
plain quam_state folders that may not have node.json/data.json.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app


# ---------------------------------------------------------------------------
# Minimal synth — copied from test_web.py's _make_state / _make_wiring so this
# file stays self-contained.
# ---------------------------------------------------------------------------


def _make_state(f_01: float = 6.25e9, t1: float = 8834) -> dict:
    return {
        "qubits": {
            "qA1": {
                "id": "qA1",
                "f_01": f_01,
                "T1": t1,
                "T2ramsey": 1.5e-6,
                "T2echo": None,
                "anharmonicity": -220e6,
                "chi": -5.2e6,
                "grid_location": "0,2",
                "xy": {
                    "RF_frequency": f_01,
                    "opx_output": "#/wiring/qubits/qA1/xy/opx_output",
                    "operations": {
                        "x180_DragCosine": {"amplitude": 0.115, "length": 40, "alpha": -1.75},
                        "x90_DragCosine": {"amplitude": 0.057},
                        "saturation": {"amplitude": 0.04},
                    },
                },
                "resonator": {
                    "f_01": 7.64e9,
                    "RF_frequency": 7.64e9,
                    "operations": {
                        "readout": {"amplitude": 0.042, "length": 1000, "threshold": -1e-4},
                    },
                    "confusion_matrix": [[0.91, 0.09], [0.12, 0.88]],
                    "time_of_flight": 380,
                },
                "z": {"joint_offset": 0.081, "independent_offset": 0.0, "flux_point": "joint"},
                "gate_fidelity": {"averaged": 0.991, "x180": 0.986, "x90": 0.986},
            },
        },
        "qubit_pairs": {},
        "active_qubit_names": ["qA1"],
    }


def _make_wiring() -> dict:
    return {
        "wiring": {
            "qubits": {
                "qA1": {
                    "xy": {"opx_output": "MW-FEM/1/2"},
                    "rr": {"opx_output": "MW-FEM/1/1", "opx_input": "MW-FEM/1/1"},
                    "z": {"opx_output": "LF-FEM/5/1"},
                },
            },
        },
        "network": {"host": "10.1.1.18"},
    }


def _write_quam(folder: Path, *, f_01: float, t1: float) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(_make_state(f_01, t1), indent=2), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(_make_wiring(), indent=2), encoding="utf-8")
    return folder


@pytest.fixture
def two_chips(tmp_path: Path) -> list[str]:
    """Two standalone <chip>/quam_state folders — no node.json."""
    a = _write_quam(tmp_path / "chip_a" / "quam_state", f_01=6.25e9, t1=8834)
    b = _write_quam(tmp_path / "chip_b" / "quam_state", f_01=6.30e9, t1=12000)
    return [str(a), str(b)]


@pytest.fixture
def two_chips_with_node(tmp_path: Path) -> list[str]:
    """Two experiment-style folders that do have node.json/data.json."""
    out = []
    for i, freq in enumerate([6.25e9, 6.30e9]):
        exp_dir = tmp_path / f"exp_{i}"
        qs = exp_dir / "quam_state"
        _write_quam(qs, f_01=freq, t1=8834 + i * 1000)
        node = {
            "metadata": {
                "name": f"03_resonator_spec_{i}",
                "status": "finished",
                "run_start": f"2026-02-19T16:3{i}:00",
                "run_end": f"2026-02-19T16:3{i + 1}:00",
            },
            "data": {
                "parameters": {"model": {"num_shots": 100 + i * 50}},
                "outcomes": {"qA1": "successful"},
            },
            "id": i + 10,
        }
        (exp_dir / "node.json").write_text(json.dumps(node), encoding="utf-8")
        data = {"fit_results": {"qA1": {"frequency": freq + 1e6, "success": True}}}
        (exp_dir / "data.json").write_text(json.dumps(data), encoding="utf-8")
        out.append(str(qs))
    return out


@pytest.fixture
def client(tmp_path):
    app = create_app(testing=True, instance_path=str(tmp_path / "_app_instance"))
    return app.test_client()


class TestChipCompareRedirect:
    """Legacy /chip-compare → Compare hub (docs/49 P4). The picker/tabs
    surface is replaced by the hub; the topology/diff tab FRAGMENTS below
    stay served (and tested) until the redirect soaks."""

    def test_get_redirects_to_hub(self, client):
        resp = client.get("/chip-compare")
        assert resp.status_code == 302
        # bare landings carry from= so the hub explains where the page went
        assert resp.headers["Location"] == "/compare-hub?from=chip-compare"

    def test_post_translates_paths_to_src_tokens(self, client, two_chips):
        resp = client.post("/chip-compare", data={"paths": two_chips})
        assert resp.status_code == 302
        loc = resp.headers["Location"]
        assert loc.startswith("/compare-hub?")
        assert loc.count("src=") == 2
        # plain folders → ws: tokens (archive-run layouts get run:)
        assert "src=ws%3A" in loc or "src=ws:" in loc

    def test_htmx_post_uses_hx_redirect(self, client, two_chips):
        """A7 — htmx follows a 302 and swaps it into the pane; HTMX
        requests must get HX-Redirect instead."""
        resp = client.post("/chip-compare", data={"paths": two_chips},
                           headers={"HX-Request": "true"})
        assert resp.status_code == 200
        assert resp.headers["HX-Redirect"].startswith("/compare-hub?")

    def test_single_path_still_redirects(self, client, two_chips):
        resp = client.post("/chip-compare", data={"paths": two_chips[0]})
        assert resp.status_code == 302
        assert resp.headers["Location"].count("src=") == 1

    def test_sidebar_has_no_chip_compare_entry(self, client):
        html = client.get("/compare-hub").data.decode()
        assert 'href="/chip-compare"' not in html
        assert 'href="/compare-hub"' in html




class TestChipCompareTopologyTab:
    def test_topology_tab_lists_chips(self, client, two_chips):
        qs = "&".join(f"paths={p}" for p in two_chips)
        resp = client.get(f"/chip-compare/topology?{qs}")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "chip-compare-grid" in html
        assert "chip_a" in html
        assert "chip_b" in html
        # Each chip card has the per-qubit grid.
        assert "chip-compare-qubit" in html
        # qA1 is in both chips → both cards mention it.
        assert html.count("qA1") >= 2

    def test_topology_marks_reference_chip(self, client, two_chips):
        qs = "&".join(f"paths={p}" for p in two_chips)
        resp = client.get(f"/chip-compare/topology?ref=0&{qs}")
        html = resp.data.decode()
        # The ref chip gets a REF badge.
        assert "REF" in html
        assert "chip-compare-card-ref" in html

    def test_topology_highlights_qubits_that_differ(self, client, two_chips):
        # f_01 differs across chip_a and chip_b → qA1 should be flagged on
        # the non-reference card.
        qs = "&".join(f"paths={p}" for p in two_chips)
        resp = client.get(f"/chip-compare/topology?ref=0&{qs}")
        html = resp.data.decode()
        assert "chip-compare-qubit-diff" in html

    def test_topology_too_few_paths_warns(self, client, two_chips):
        resp = client.get(f"/chip-compare/topology?paths={two_chips[0]}")
        assert resp.status_code == 200
        assert b"at least 2" in resp.data


class TestChipCompareDiffTab:
    def test_diff_tab_shows_state_diff(self, client, two_chips):
        qs = "&".join(f"paths={p}" for p in two_chips)
        resp = client.get(f"/chip-compare/diff?{qs}")
        assert resp.status_code == 200
        html = resp.data.decode()
        # f_01 differs across the two chips.
        assert "f_01" in html
        assert "cell-diff" in html

    def test_diff_tab_with_experiment_data_shows_params(self, client, two_chips_with_node):
        qs = "&".join(f"paths={p}" for p in two_chips_with_node)
        resp = client.get(f"/chip-compare/diff?{qs}")
        html = resp.data.decode()
        # node.json's num_shots differs across the two runs.
        assert "Parameter Differences" in html
        assert "num_shots" in html

    def test_diff_tab_without_node_json_omits_param_section(self, client, two_chips):
        qs = "&".join(f"paths={p}" for p in two_chips)
        resp = client.get(f"/chip-compare/diff?{qs}")
        html = resp.data.decode()
        # No node.json → no Parameter Differences section header.
        assert "Parameter Differences" not in html
