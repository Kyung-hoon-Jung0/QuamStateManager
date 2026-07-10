"""Audit finding #1: apply-fitted-value must not write a run's fit onto a
DIFFERENT loaded chip that merely reuses the same qubit names.

The fix stamps the run's chip fingerprint token into the dataset detail page and
re-checks it server-side at edit time. These tests cover the token helper and the
``/field/edit[-batch]`` cross-chip guard (the unbypassable server side); the
client-side open-time confirm is exercised in the browser.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from quam_state_manager.core import history
from quam_state_manager.web.app import create_app


def _make_state(f_01: float = 6.25e9) -> dict:
    return {
        "qubits": {"qA1": {"id": "qA1", "f_01": f_01, "T1": 8000}},
        "qubit_pairs": {},
        "active_qubit_names": ["qA1"],
    }


def _make_wiring(host: str = "10.1.1.18") -> dict:
    return {
        "wiring": {"qubits": {"qA1": {"xy": {"opx_output": "MW-FEM/1/2"}}}},
        "network": {"host": host, "cluster_name": "clusterA"},
    }


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    (tmp_path / "state.json").write_text(json.dumps(_make_state()), encoding="utf-8")
    (tmp_path / "wiring.json").write_text(json.dumps(_make_wiring()), encoding="utf-8")
    return tmp_path


@pytest.fixture
def client(tmp_path, folder):
    app = create_app(testing=True, instance_path=str(tmp_path / "_app"))
    c = app.test_client()
    c.post("/load", data={"folder": str(folder)})
    return c


# --- token helper -----------------------------------------------------------

def test_fingerprint_token_is_stable_and_discriminating():
    a = history.fingerprint_from_dicts(_make_state(), _make_wiring())
    a2 = history.fingerprint_from_dicts(_make_state(f_01=9e9), _make_wiring())  # same identity
    b = history.fingerprint_from_dicts(  # different network host → different chip
        _make_state(), _make_wiring(host="10.9.9.99"))
    assert history.fingerprint_token(a) == history.fingerprint_token(a2)  # value change ≠ identity
    assert history.fingerprint_token(a) != history.fingerprint_token(b)
    assert history.fingerprint_token(None) is None


# --- server guard -----------------------------------------------------------

def test_active_token_endpoint(client):
    tok = client.get("/chip/active-token").get_json()
    assert tok["token"]  # non-empty


def test_batch_blocks_mismatched_chip(client):
    before = client.get("/chip/active-token").get_json()["token"]
    resp = client.post("/field/edit-batch", json={
        "updates": [{"dot_path": "qubits.qA1.f_01", "value": 1.0e9}],
        "expect_chip": "deadbeefdeadbeef",  # not the loaded chip
    })
    assert resp.status_code == 409
    assert resp.get_json()["chip_mismatch"] is True
    # The value was NOT written.
    assert client.get("/chip/active-token").get_json()["token"] == before
    review = client.get("/changes").data.decode()
    assert "1,000,000,000" not in review and "1000000000" not in review


def test_batch_allows_matching_chip(client):
    tok = client.get("/chip/active-token").get_json()["token"]
    resp = client.post("/field/edit-batch", json={
        "updates": [{"dot_path": "qubits.qA1.f_01", "value": 1.0e9}],
        "expect_chip": tok,
    })
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True


def test_batch_force_overrides_mismatch(client):
    resp = client.post("/field/edit-batch", json={
        "updates": [{"dot_path": "qubits.qA1.f_01", "value": 1.0e9}],
        "expect_chip": "deadbeefdeadbeef", "force_chip": True,
    })
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True


def test_single_field_edit_guard(client):
    resp = client.post("/field/edit", data={
        "dot_path": "qubits.qA1.f_01", "value": "1.0e9",
        "expect_chip": "deadbeefdeadbeef"})
    assert resp.status_code == 409
    assert resp.get_json()["chip_mismatch"] is True


def test_no_token_means_no_gate(client):
    # A generic edit (no expect_chip) is never gated — unchanged behaviour.
    resp = client.post("/field/edit-batch", json={
        "updates": [{"dot_path": "qubits.qA1.f_01", "value": 1.0e9}]})
    assert resp.status_code == 200
