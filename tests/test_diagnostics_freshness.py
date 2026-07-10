"""Diagnostics FRESHNESS regression — the linter is a safety net, so the badge
(/diagnostics/summary) and the auto error-banner (/diagnostics/banner) must
reflect the CURRENT chip after every state change: a single field edit, a batch
edit, and a revert. This guards the server contract the JS relies on (the
`diagnostics-changed` event re-fetches these two endpoints after every mutation).

The store memoizes lint results on ``mutation_seq``; every edit endpoint bumps it,
so a stale result here would mean the cache key is broken — exactly the
"silently shows pre-edit findings" failure this test exists to catch.
"""

from __future__ import annotations

import json

from quam_state_manager.web.app import create_app

READOUT = "quam.components.pulses.SquareReadoutPulse"
AMP_PATH = "qubits.q1.resonator.operations.readout.amplitude"
RF_PATH = "qubits.q1.xy.RF_frequency"


def _state() -> dict:
    return {
        "qubits": {"q1": {
            "id": "q1",
            "f_01": 5.05e9,
            "xy": {"opx_output": "#/wiring/qubits/q1/xy/opx_output",
                   "RF_frequency": 5.05e9, "operations": {}},
            "resonator": {
                "opx_output": "#/wiring/qubits/q1/rr/opx_output",
                "opx_input": "#/wiring/qubits/q1/rr/opx_input",
                "RF_frequency": 7.0e9,
                "operations": {"readout": {"__class__": READOUT,
                                           "length": 640, "amplitude": 0.3}},
            },
        }},
        "qubit_pairs": {},
        "ports": {
            "mw_outputs": {"con1": {"1": {
                "1": {"__class__": "quam.components.ports.mw_outputs.MWFEMAnalogOutputPort",
                      "band": 2, "upconverter_frequency": 7.0e9, "full_scale_power_dbm": 0},
                "2": {"__class__": "quam.components.ports.mw_outputs.MWFEMAnalogOutputPort",
                      "band": 2, "upconverter_frequency": 5.0e9, "full_scale_power_dbm": 0}}}},
            "analog_outputs": {},
            "mw_inputs": {"con1": {"1": {"1": {}}}},
        },
    }


def _wiring() -> dict:
    return {"wiring": {"qubits": {"q1": {
        "xy": {"opx_output": "#/ports/mw_outputs/con1/1/2"},
        "rr": {"opx_output": "#/ports/mw_outputs/con1/1/1",
               "opx_input": "#/ports/mw_inputs/con1/1/1"},
    }}}, "network": {"host": "x", "cluster_name": "t"}}


def _client(tmp_path):
    (tmp_path / "state.json").write_text(json.dumps(_state()), encoding="utf-8")
    (tmp_path / "wiring.json").write_text(json.dumps(_wiring()), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    client = app.test_client()
    client.post("/load", data={"folder": str(tmp_path)})
    return client


def _summary(client):
    return client.get("/diagnostics/summary").get_data(as_text=True)


def _banner_status(client):
    return client.get("/diagnostics/banner").status_code


def test_clean_chip_starts_healthy(tmp_path):
    client = _client(tmp_path)
    assert "healthy" in _summary(client)
    assert _banner_status(client) == 204


def test_single_edit_introduces_then_clears_error(tmp_path):
    client = _client(tmp_path)
    assert "healthy" in _summary(client)

    # push the readout amplitude past the DAC range → a crash-class error
    r = client.post("/field/edit", data={"dot_path": AMP_PATH, "value": "1.5"})
    assert r.get_json()["ok"] is True
    assert "diag-error" in _summary(client)           # badge now red
    assert _banner_status(client) == 200              # banner pops

    # revert → the linter must clear the error on the very next fetch
    client.post("/field/edit", data={"dot_path": AMP_PATH, "value": "0.3"})
    assert "healthy" in _summary(client)
    assert _banner_status(client) == 204


def test_batch_edit_reflected_in_badge(tmp_path):
    client = _client(tmp_path)
    r = client.post("/field/edit-batch", json={"updates": [
        {"dot_path": AMP_PATH, "value": 2.0}]})
    assert r.get_json()["ok"] is True
    assert "diag-error" in _summary(client)
    assert _banner_status(client) == 200


def test_hardware_freq_check_is_fresh_after_edit(tmp_path):
    # ties the new MW-FEM carrier checks into the freshness contract: moving the
    # drive carrier far past the port LO (|IF| > 500 MHz) is a crash-class error
    # that must appear immediately after the edit, and clear on revert.
    client = _client(tmp_path)
    assert "healthy" in _summary(client)

    client.post("/field/edit", data={"dot_path": RF_PATH, "value": "5.9e9"})  # IF=900MHz
    assert "diag-error" in _summary(client)
    assert _banner_status(client) == 200

    client.post("/field/edit", data={"dot_path": RF_PATH, "value": "5.05e9"})
    assert "healthy" in _summary(client)
    assert _banner_status(client) == 204
