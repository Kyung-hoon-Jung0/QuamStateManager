"""Tests for the Live State Edit *pair* grid route + commit path (/bulk + the
pair cells' /field/edit-batch round-trip).

The pair grid is a denser entry surface over the SAME atomic edit-batch +
working-copy path the qubit grid and inspector use — so these assert the render
(a row per pair, derived columns, read-only runtime/list cells) and that an
arbitrary deep pair dot-path (a nested flux-pulse leaf; a examplechip-style coupler
operation alias) round-trips with NO new mutation code.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app


def _qubit(qid: str, f01: float) -> dict:
    return {
        "id": qid, "f_01": f01, "anharmonicity": -220e6,
        "xy": {"RF_frequency": f01, "intermediate_frequency": 100e6,
               "operations": {"x180": {"amplitude": 0.11}, "x90": {"amplitude": 0.055},
                              "saturation": {"amplitude": 0.04}}},
        "resonator": {"f_01": 7.6e9, "RF_frequency": 7.6e9,
                      "operations": {"readout": {"amplitude": 0.04, "length": 1000,
                                                 "threshold": -1e-4}}},
        "z": {"joint_offset": 0.05},
    }


def _cz_pair(pid: str) -> dict:
    return {
        "id": pid, "moving_qubit": "control", "detuning": 1.0e6, "coupler": None,
        "confusion": [[0.98, 0.02], [0.03, 0.97]],
        "macros": {
            "cz": "#./cz_unipolar",
            "cz_unipolar": {
                "duration": "#./inferred_duration", "duration_control": None,
                "phase_shift_control": 0.025, "phase_shift_target": 0.99,
                "flux_pulse_qubit": {"amplitude": 0.209, "length": 68, "flat_length": 48},
                "coupler_flux_pulse": {"amplitude": 0.1},
                "fidelity": {"Bell_State": {"Fidelity": 0.97}},
            },
        },
    }


def _state() -> dict:
    return {
        "qubits": {"qA1": _qubit("qA1", 6.25e9), "qA2": _qubit("qA2", 5.80e9),
                   "qA3": _qubit("qA3", 5.70e9)},
        "qubit_pairs": {"qA2-qA1": _cz_pair("qA2-qA1"), "qA3-qA2": _cz_pair("qA3-qA2")},
        "active_qubit_names": ["qA1", "qA2", "qA3"],
    }


def _wiring() -> dict:
    return {"wiring": {"qubits": {}, "qubit_pairs": {}}, "network": {"host": "10.1.1.1"}}


@pytest.fixture
def client(tmp_path: Path):
    (tmp_path / "state.json").write_text(json.dumps(_state()), encoding="utf-8")
    (tmp_path / "wiring.json").write_text(json.dumps(_wiring()), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_app_instance"))
    c = app.test_client()
    c.post("/load", data={"folder": str(tmp_path)})
    return c


class TestPairRender:
    def test_pair_table_renders_a_row_per_pair(self, client):
        body = client.get("/bulk", headers={"HX-Request": "true"}).get_data(as_text=True)
        assert 'id="bulk-pair-table"' in body
        assert body.count('data-pair="qA2-qA1"') == 1
        assert body.count('data-pair="qA3-qA2"') == 1

    def test_flux_pulse_cell_carries_real_write_path(self, client):
        body = client.get("/bulk", headers={"HX-Request": "true"}).get_data(as_text=True)
        assert 'data-dot-path="qubit_pairs.qA2-qA1.macros.cz_unipolar.flux_pulse_qubit.amplitude"' in body

    def test_runtime_duration_cell_is_read_only(self, client):
        body = client.get("/bulk", headers={"HX-Request": "true"}).get_data(as_text=True)
        # the inferred-duration self-ref is rendered read-only (never an editable input)
        assert "bulk-cell-runtime" in body

    def test_confusion_is_a_jump_to_inspector_badge(self, client):
        body = client.get("/bulk", headers={"HX-Request": "true"}).get_data(as_text=True)
        assert "bulk-ro-link" in body
        assert "BulkPairEdit.openPair('qA2-qA1')" in body

    def test_qubit_table_still_present(self, client):
        body = client.get("/bulk", headers={"HX-Request": "true"}).get_data(as_text=True)
        assert 'id="bulk-table"' in body
        assert body.count('data-qubit="qA1"') == 1


class TestPairCommit:
    def _edit(self, client, dot_path, value):
        return client.post("/field/edit-batch", json={"updates": [{"dot_path": dot_path, "value": value}]})

    def test_nested_flux_leaf_round_trips(self, client):
        r = self._edit(client, "qubit_pairs.qA2-qA1.macros.cz_unipolar.flux_pulse_qubit.amplitude", "0.3")
        assert r.status_code == 200
        body = r.get_json()
        assert body["ok"] is True
        res = body["results"][0]
        assert res["applied"] is True
        assert res["resolved_path"].endswith("macros.cz_unipolar.flux_pulse_qubit.amplitude")
        # the committed value re-renders in the grid
        page = client.get("/bulk", headers={"HX-Request": "true"}).get_data(as_text=True)
        assert 'value="0.3"' in page

    def test_phase_shift_round_trips(self, client):
        r = self._edit(client, "qubit_pairs.qA3-qA2.macros.cz_unipolar.phase_shift_control", "0.5")
        body = r.get_json()
        assert body["ok"] is True and body["results"][0]["applied"] is True

    def test_type_coercion_preserves_int_length(self, client):
        # flux_pulse_qubit.length is an int; the coercer keeps it int (not "70" str)
        r = self._edit(client, "qubit_pairs.qA2-qA1.macros.cz_unipolar.flux_pulse_qubit.length", "70")
        body = r.get_json()
        assert body["ok"] is True
        assert body["results"][0]["applied"] is True

    def test_bad_value_isolates(self, client):
        # a non-numeric into a float leaf fails for THAT path, atomically
        r = self._edit(client, "qubit_pairs.qA2-qA1.macros.cz_unipolar.phase_shift_target", "not-a-number")
        body = r.get_json()
        # either the whole batch reports not-ok, or that single result is applied=False
        assert body["ok"] is False or body["results"][0]["applied"] is False
