"""Tests for /field/peek and /field/edit-batch — the routes that power the
Plotly click-confirmation popup.

/field/peek is a tiny read-only "what is the current value of this dot-path?"
endpoint. /field/edit-batch wraps modifier.batch_set and reports per-path
success/failure with atomic rollback.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app


def _make_state() -> dict:
    return {
        "qubits": {
            "qA1": {
                "id": "qA1",
                "f_01": 6.25e9,
                "T1": 8834.0,
                "T2ramsey": 1.5e-6,
                "anharmonicity": -220e6,
                "xy": {
                    "RF_frequency": 6.25e9,
                    "operations": {
                        "x180_DragCosine": {"amplitude": 0.115, "length": 40},
                    },
                },
                "resonator": {
                    "f_01": 7.64e9,
                    "time_of_flight": 380,
                    "operations": {"readout": {"amplitude": 0.042}},
                },
            },
        },
        "qubit_pairs": {},
        "active_qubit_names": ["qA1"],
    }


def _make_wiring() -> dict:
    return {
        "wiring": {
            "qubits": {"qA1": {"xy": {"opx_output": "MW-FEM/1/2"}}},
        },
        "network": {"host": "10.1.1.18"},
    }


@pytest.fixture
def synth_folder(tmp_path: Path) -> Path:
    (tmp_path / "state.json").write_text(json.dumps(_make_state(), indent=2), encoding="utf-8")
    (tmp_path / "wiring.json").write_text(json.dumps(_make_wiring(), indent=2), encoding="utf-8")
    return tmp_path


@pytest.fixture
def client(tmp_path: Path, synth_folder: Path):
    app = create_app(testing=True, instance_path=str(tmp_path / "_app_instance"))
    client = app.test_client()
    client.post("/load", data={"folder": str(synth_folder)})
    return client


class TestFieldPeek:
    def test_single_path_returns_current_value(self, client):
        resp = client.get("/field/peek?dot_path=qubits.qA1.f_01")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert body["values"]["qubits.qA1.f_01"] == 6.25e9
        assert body["errors"] == {}

    def test_multiple_paths_in_one_call(self, client):
        resp = client.get(
            "/field/peek?dot_path=qubits.qA1.f_01"
            "&dot_path=qubits.qA1.T1"
            "&dot_path=qubits.qA1.resonator.time_of_flight"
        )
        body = resp.get_json()
        assert body["ok"] is True
        assert body["values"] == {
            "qubits.qA1.f_01": 6.25e9,
            "qubits.qA1.T1": 8834.0,
            "qubits.qA1.resonator.time_of_flight": 380,
        }

    def test_missing_path_is_null_other_paths_still_returned(self, client):
        resp = client.get(
            "/field/peek?dot_path=qubits.qA1.f_01"
            "&dot_path=qubits.qA1.does_not_exist"
        )
        body = resp.get_json()
        assert body["ok"] is True
        assert body["values"]["qubits.qA1.f_01"] == 6.25e9
        assert body["values"]["qubits.qA1.does_not_exist"] is None
        assert "qubits.qA1.does_not_exist" in body["errors"]

    def test_no_paths_returns_empty_values(self, client):
        resp = client.get("/field/peek")
        body = resp.get_json()
        assert body == {"ok": True, "values": {}, "errors": {}, "resolved": {}}

    def test_no_active_context_returns_400(self, tmp_path):
        app = create_app(testing=True, instance_path=str(tmp_path / "_app_instance"))
        c = app.test_client()
        # No /load → no active context.
        resp = c.get("/field/peek?dot_path=qubits.qA1.f_01")
        assert resp.status_code == 400
        assert resp.get_json()["ok"] is False


class TestFieldEditBatch:
    def test_happy_path_applies_all(self, client):
        resp = client.post(
            "/field/edit-batch",
            json={
                "updates": [
                    {"dot_path": "qubits.qA1.f_01", "value": "6.30e9"},
                    {"dot_path": "qubits.qA1.T1", "value": "9000"},
                ]
            },
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert "tray_html" in body
        applied = [r for r in body["results"] if r["applied"]]
        assert len(applied) == 2

        # The new values are visible through /field/peek now.
        peek = client.get(
            "/field/peek?dot_path=qubits.qA1.f_01&dot_path=qubits.qA1.T1"
        ).get_json()
        assert peek["values"]["qubits.qA1.f_01"] == 6.30e9
        assert peek["values"]["qubits.qA1.T1"] == 9000

    def test_form_encoded_input_also_works(self, client):
        from werkzeug.datastructures import MultiDict
        md = MultiDict()
        md.add("dot_path", "qubits.qA1.f_01")
        md.add("value", "6.40e9")
        md.add("dot_path", "qubits.qA1.T1")
        md.add("value", "12000")
        resp = client.post("/field/edit-batch", data=md)
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True

    def test_rollback_on_invalid_path(self, client):
        # Apply 1 valid, 1 invalid (nonexistent path). The valid one must
        # NOT persist after rollback.
        resp = client.post(
            "/field/edit-batch",
            json={
                "updates": [
                    {"dot_path": "qubits.qA1.f_01", "value": "9.99e9"},
                    {"dot_path": "qubits.qA1.does_not_exist", "value": "1"},
                ]
            },
        )
        assert resp.status_code == 400
        body = resp.get_json()
        assert body["ok"] is False
        # results includes both rows with their statuses.
        by_path = {r["dot_path"]: r for r in body["results"]}
        assert by_path["qubits.qA1.does_not_exist"]["applied"] is False
        assert "error" in by_path["qubits.qA1.does_not_exist"]
        # Rolled-back row also marked applied=false.
        assert by_path["qubits.qA1.f_01"]["applied"] is False

        # Confirm the f_01 change did NOT persist.
        peek = client.get("/field/peek?dot_path=qubits.qA1.f_01").get_json()
        assert peek["values"]["qubits.qA1.f_01"] == 6.25e9

    def test_response_includes_tray_html(self, client):
        resp = client.post(
            "/field/edit-batch",
            json={"updates": [{"dot_path": "qubits.qA1.T1", "value": "7777"}]},
        )
        body = resp.get_json()
        assert body["ok"] is True
        assert "pending-tray" in body["tray_html"]

    def test_empty_updates_400s(self, client):
        resp = client.post("/field/edit-batch", json={"updates": []})
        assert resp.status_code == 400
        assert resp.get_json()["ok"] is False

    def test_no_active_context_400s(self, tmp_path):
        app = create_app(testing=True, instance_path=str(tmp_path / "_app_instance"))
        c = app.test_client()
        resp = c.post(
            "/field/edit-batch",
            json={"updates": [{"dot_path": "qubits.qA1.f_01", "value": "1"}]},
        )
        assert resp.status_code == 400
        assert resp.get_json()["ok"] is False


# ======================================================================
# Pointer-aware click-to-edit: peek `resolved` block + edit auto-resolve
# ======================================================================

def _pointer_state() -> dict:
    return {
        "qubits": {
            "qA1": {
                "id": "qA1",
                "f_01": 6.25e9,
                "xy": {
                    "RF_frequency": 6.25e9,
                    "operations": {
                        "x180": "#./x180_DragCosine",
                        "y180": "#./y180_DragCosine",
                        "x180_DragCosine": {"amplitude": 0.115, "length": 40},
                        "y180_DragCosine": {
                            "amplitude": "#../x180_DragCosine/amplitude",
                            "length": 40,
                        },
                    },
                },
                "resonator": {"f_01": 7.64e9, "operations": {"readout": {"amplitude": 0.042}}},
            },
        },
        "qubit_pairs": {},
        "active_qubit_names": ["qA1"],
    }


@pytest.fixture
def pointer_client(tmp_path: Path):
    folder = tmp_path / "ptr_state"
    folder.mkdir()
    (folder / "state.json").write_text(json.dumps(_pointer_state(), indent=2), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(_make_wiring(), indent=2), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_app_instance"))
    client = app.test_client()
    client.post("/load", data={"folder": str(folder)})
    return client


class TestPointerAwarePeek:
    _ALIAS = "qubits.qA1.xy.operations.x180.amplitude"
    _LITERAL = "qubits.qA1.xy.operations.x180_DragCosine.amplitude"

    def test_values_stay_raw_back_compat(self, pointer_client):
        body = pointer_client.get("/field/peek", query_string={"dot_path": self._ALIAS}).get_json()
        # Raw value is the pointer string (or null) — unchanged behavior.
        assert "values" in body and self._ALIAS in body["values"]

    def test_resolved_block_follows_pointer(self, pointer_client):
        body = pointer_client.get("/field/peek", query_string={"dot_path": self._ALIAS}).get_json()
        r = body["resolved"][self._ALIAS]
        assert r["is_pointer"] is True and r["resolvable"] is True
        assert r["resolved_path"] == self._LITERAL
        assert r["resolved_value"] == 0.115
        assert len(r["chain"]) == 1
        assert "y180" in r["shared_by"]

    def test_resolved_non_pointer_trivial(self, pointer_client):
        body = pointer_client.get("/field/peek", query_string={"dot_path": "qubits.qA1.f_01"}).get_json()
        r = body["resolved"]["qubits.qA1.f_01"]
        assert r["is_pointer"] is False
        assert r["candidates"][0]["path"] == "qubits.qA1.f_01"


class TestPointerAwareEdit:
    _ALIAS = "qubits.qA1.xy.operations.x180.amplitude"
    _LITERAL = "qubits.qA1.xy.operations.x180_DragCosine.amplitude"

    def test_edit_auto_resolves_alias(self, pointer_client):
        resp = pointer_client.post("/field/edit", data={"dot_path": self._ALIAS, "value": "0.5"})
        assert resp.get_json()["ok"] is True
        # The literal is updated; the alias pointer string is left intact.
        peek = pointer_client.get("/field/peek", query_string={"dot_path": self._LITERAL}).get_json()
        assert peek["values"][self._LITERAL] == 0.5
        raw = pointer_client.get("/field/peek", query_string={"dot_path": "qubits.qA1.xy.operations.x180"}).get_json()
        assert raw["values"]["qubits.qA1.xy.operations.x180"] == "#./x180_DragCosine"

    def test_edit_batch_auto_resolves_alias(self, pointer_client):
        resp = pointer_client.post("/field/edit-batch",
                                   json={"updates": [{"dot_path": self._ALIAS, "value": "0.33"}]})
        body = resp.get_json()
        assert body["ok"] is True
        assert body["results"][0]["applied"] is True
        peek = pointer_client.get("/field/peek", query_string={"dot_path": self._LITERAL}).get_json()
        assert peek["values"][self._LITERAL] == 0.33

    def test_edit_literal_path_unchanged_behavior(self, pointer_client):
        # Editing the already-literal path works exactly as before (no resolution).
        resp = pointer_client.post("/field/edit", data={"dot_path": self._LITERAL, "value": "0.9"})
        assert resp.get_json()["ok"] is True
        peek = pointer_client.get("/field/peek", query_string={"dot_path": self._LITERAL}).get_json()
        assert peek["values"][self._LITERAL] == 0.9
