"""Tests for the drag-drop preview, compare, and diagnostics web routes.

The Flask test client bypasses the CSRF origin check in TESTING mode, so we
can POST the JSON bodies the browser drop handler would send.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app


# ---------------------------------------------------------------------------
# Synthetic dicts (port refs use the #/ports/... pointer form)
# ---------------------------------------------------------------------------

def _state():
    return {
        "qubits": {
            "q1": {"id": "q1", "f_01": 5.0e9, "anharmonicity": -2.0e8},
            "q2": {"id": "q2", "f_01": 5.1e9, "anharmonicity": -2.1e8},
        },
        "qubit_pairs": {},
        "ports": {
            "mw_outputs": {"con1": {"1": {"1": {"upconverter_frequency": 6e9},
                                          "3": {"upconverter_frequency": 6e9},
                                          "4": {"upconverter_frequency": 6e9}}}},
            "analog_outputs": {"con2": {"1": {"1": {}, "2": {}}}},
        },
    }


def _wiring():
    return {
        "wiring": {"qubits": {
            "q1": {"xy": {"opx_output": "#/ports/mw_outputs/con1/1/3"},
                   "z": {"opx_output": "#/ports/analog_outputs/con2/1/1"}},
            "q2": {"xy": {"opx_output": "#/ports/mw_outputs/con1/1/4"},
                   "z": {"opx_output": "#/ports/analog_outputs/con2/1/2"}},
        }},
        "network": {"host": "10.0.0.1", "cluster_name": "t"},
    }


def _config():
    return {
        "version": 1,
        "controllers": {"con1": {}},
        "elements": {"q1.xy": {"operations": {"x180": "x180.pulse"}}},
        "pulses": {"x180.pulse": {"waveforms": {"I": "wf_i"}}},
        "waveforms": {"wf_i": {"type": "arbitrary", "samples": [0.1, 0.2]}},
    }


@pytest.fixture
def client(tmp_path):
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    return app.test_client()


# ---------------------------------------------------------------------------
# /instrument/preview
# ---------------------------------------------------------------------------

class TestInstrumentPreview:
    def test_valid(self, client):
        r = client.post("/instrument/preview",
                        json={"state": _state(), "wiring": _wiring(), "label": "MyChip"})
        assert r.status_code == 200
        body = r.get_data(as_text=True)
        assert "renderInstrumentWiring" in body
        assert "preview-banner" in body
        assert "MyChip" in body

    def test_broken_chip_shows_findings(self, client):
        w = _wiring()
        w["wiring"]["qubits"]["q1"]["xy"]["opx_output"] = "#/ports/mw_outputs/con1/9/9"
        r = client.post("/instrument/preview", json={"state": _state(), "wiring": w})
        body = r.get_data(as_text=True)
        assert r.status_code == 200
        assert "does not exist" in body          # the port_missing finding
        assert "_highlightInstrumentPorts" in body
        assert '"fem": "9"' in body              # the broken port is passed to the client for highlighting

    def test_invalid_payload(self, client):
        assert client.post("/instrument/preview", json={"state": "x", "wiring": {}}).status_code == 400
        assert client.post("/instrument/preview", json={}).status_code == 400

    def test_xss_escaped(self, client):
        w = _wiring()
        w["network"]["cluster_name"] = "</script><script>alert(1)</script>"
        r = client.post("/instrument/preview",
                        json={"state": _state(), "wiring": w, "label": "</script><b>x</b>"})
        body = r.get_data(as_text=True)
        assert "</script><script>alert(1)</script>" not in body
        assert "\\u003c" in body          # script_json escaped the wiring value
        assert "&lt;" in body             # autoescape handled the label


# ---------------------------------------------------------------------------
# /config/preview
# ---------------------------------------------------------------------------

class TestConfigPreview:
    def test_valid(self, client):
        r = client.post("/config/preview", json={"config": _config(), "label": "config.json"})
        assert r.status_code == 200
        assert "config-browser" in r.get_data(as_text=True)

    def test_missing_ref_finding(self, client):
        cfg = _config()
        cfg["elements"]["q1.xy"]["operations"]["x180"] = "ghost.pulse"
        body = client.post("/config/preview", json={"config": cfg}).get_data(as_text=True)
        assert "undefined pulse" in body

    def test_invalid_payload(self, client):
        assert client.post("/config/preview", json={"config": 123}).status_code == 400


# ---------------------------------------------------------------------------
# /instrument/compare
# ---------------------------------------------------------------------------

class TestInstrumentCompare:
    def test_two_chips(self, client):
        chips = [{"state": _state(), "wiring": _wiring(), "label": "A"},
                 {"state": _state(), "wiring": _wiring(), "label": "B"}]
        r = client.post("/instrument/compare", json={"chips": chips})
        body = r.get_data(as_text=True)
        assert r.status_code == 200
        assert "wiring-compare-grid" in body
        assert "cmp-diagram-1" in body

    def test_needs_two(self, client):
        chips = [{"state": _state(), "wiring": _wiring(), "label": "A"}]
        assert client.post("/instrument/compare", json={"chips": chips}).status_code == 400


# ---------------------------------------------------------------------------
# /diagnostics (active chip)
# ---------------------------------------------------------------------------

class TestDiagnosticsView:
    def _load_broken(self, client, tmp_path):
        w = _wiring()
        w["wiring"]["qubits"]["q1"]["xy"]["opx_output"] = "#/ports/mw_outputs/con1/9/9"
        folder = tmp_path / "broken"
        folder.mkdir()
        (folder / "state.json").write_text(json.dumps(_state()), encoding="utf-8")
        (folder / "wiring.json").write_text(json.dumps(w), encoding="utf-8")
        client.post("/load", data={"folder": str(folder)})

    def test_no_state(self, client):
        r = client.get("/diagnostics")
        assert r.status_code == 200
        assert "No chip loaded" in r.get_data(as_text=True)

    def test_active_chip_findings_and_jump(self, client, tmp_path):
        self._load_broken(client, tmp_path)
        body = client.get("/diagnostics", headers={"HX-Request": "true"}).get_data(as_text=True)
        assert "does not exist" in body
        assert "goToDiagField" in body            # the jump-to-field button
        assert 'data-jump-path="wiring.qubits.q1.xy.opx_output"' in body

    def test_summary_badge(self, client, tmp_path):
        self._load_broken(client, tmp_path)
        body = client.get("/diagnostics/summary").get_data(as_text=True)
        assert "issue" in body and "diag-header-badge" in body
