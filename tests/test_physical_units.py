"""True physical output for amplitudes (docs/109).

`amplitude` is a bare scale factor; the surfaces now say what actually leaves
the instrument: MW channels get dBm via the FSP-compensation identity
``P = FSP + 20*log10(|amp|)``, LF/flux channels get volts (the amplitude IS
volts). Honesty pins: a broken chain, a text value or an MW zero renders
NOTHING — never an invented number. Lengths were already covered (stored ns +
the `qty` filter / `(ns)` headers).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from quam_state_manager.core import physical_units
from quam_state_manager.web.app import create_app

_WIRING = {
    "network": {"host": "1.1.1.1", "cluster_name": "C1"},
    "wiring": {"qubits": {"qA1": {
        "xy": {"opx_output": "#/ports/mw_outputs/con1/1/2"},
        "rr": {"opx_output": "#/ports/mw_outputs/con1/1/1"},
        "z": {"opx_output": "#/ports/analog_outputs/con1/5/1"},
    }}},
}


def _state():
    return {
        "qubits": {"qA1": {
            "id": "qA1", "f_01": 5.0e9,
            "xy": {
                "opx_output": "#/wiring/qubits/qA1/xy/opx_output",
                "operations": {
                    # the real alias shape: op name -> sibling pulse dict
                    "x180": "#./x180_DragCosine",
                    "x180_DragCosine": {"amplitude": 0.1, "length": 100},
                },
            },
            "z": {
                "opx_output": "#/wiring/qubits/qA1/z/opx_output",
                "joint_offset": 0.01,
                "operations": {"const": {"amplitude": 0.012, "length": 200}},
            },
            "resonator": {
                "opx_output": "#/wiring/qubits/qA1/rr/opx_output",
                "operations": {"readout": {"amplitude": 0.1, "length": 800}},
            },
        }},
        "qubit_pairs": {},
        "active_qubit_names": ["qA1"],
        "ports": {
            "mw_outputs": {"con1": {"1": {
                "1": {"band": 1, "full_scale_power_dbm": 0},
                "2": {"band": 2, "full_scale_power_dbm": 0}}}},
            "analog_outputs": {"con1": {"5": {"1": {
                "output_mode": "direct", "offset": None}}}},
        },
    }


def _write_chip(folder: Path, state: dict, wiring: dict | None = None):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(wiring or _WIRING),
                                        encoding="utf-8")


@pytest.fixture
def merged(tmp_path):
    from quam_state_manager.core.loader import QuamStore
    folder = tmp_path / "chip"
    _write_chip(folder, _state())
    return QuamStore(str(folder)).merged


class TestCore:
    def test_mw_dbm_identity(self, merged):
        """The docs/95-pinned example: FSP 0 + amp 0.1 -> -20 dBm."""
        a = physical_units.amp_annotation(
            merged, "qubits.qA1.xy.operations.x180_DragCosine.amplitude", 0.1)
        assert a and a["kind"] == "mw"
        assert a["fsp"] == 0 and abs(a["dbm"] - (-20.0)) < 1e-9
        assert a["text"] == "-20.0 dBm"

    def test_alias_path_resolves_too(self, merged):
        """The pointer-aliased op path (operations.x180 -> #./x180_DragCosine)
        annotates identically — the channel ancestor is the same."""
        a = physical_units.amp_annotation(
            merged, "qubits.qA1.xy.operations.x180.amplitude", 0.1)
        assert a and a["kind"] == "mw" and a["text"] == "-20.0 dBm"

    def test_lf_volts(self, merged):
        a = physical_units.amp_annotation(
            merged, "qubits.qA1.z.operations.const.amplitude", 0.012)
        assert a and a["kind"] == "lf"
        assert a["volts"] == 0.012 and a["text"] == "12 mV"

    def test_lf_volts_formats(self):
        assert physical_units.format_volts(0.5) == "500 mV"
        assert physical_units.format_volts(1.2) == "1.2 V"
        assert physical_units.format_volts(0.0) == "0 V"
        assert physical_units.format_volts(-0.25) == "-250 mV"

    def test_mw_zero_is_blank(self, merged):
        assert physical_units.amp_annotation(
            merged, "qubits.qA1.xy.operations.x180_DragCosine.amplitude", 0) is None

    def test_text_value_is_blank(self, merged):
        assert physical_units.amp_annotation(
            merged, "qubits.qA1.xy.operations.x180_DragCosine.amplitude", "0.1") is None

    def test_non_amplitude_leaf_is_blank(self, merged):
        assert physical_units.amp_annotation(
            merged, "qubits.qA1.xy.operations.x180_DragCosine.length", 100) is None

    def test_broken_chain_is_blank(self, tmp_path):
        """Dangling wiring pointer -> honest blank, never invented."""
        from quam_state_manager.core.loader import QuamStore
        st = _state()
        wr = json.loads(json.dumps(_WIRING))
        wr["wiring"]["qubits"]["qA1"]["xy"]["opx_output"] = "#/ports/mw_outputs/conX/9/9"
        folder = tmp_path / "chipb"
        _write_chip(folder, st, wr)
        m = QuamStore(str(folder)).merged
        assert physical_units.amp_annotation(
            m, "qubits.qA1.xy.operations.x180_DragCosine.amplitude", 0.1) is None

    def test_no_channel_ancestor_is_blank(self, merged):
        assert physical_units.amp_annotation(
            merged, "qubits.qA1.f_01.amplitude", 0.1) is None


class TestSurfaces:
    @pytest.fixture
    def env(self, tmp_path):
        live = tmp_path / "chips" / "live"
        _write_chip(live, _state())
        app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
        c = app.test_client()
        r = c.post("/load", data={"folder": str(live)})
        assert r.status_code in (200, 302)
        return {"app": app, "client": c}

    def test_bulk_grid_annotates_mw_and_lf(self, env):
        html = env["client"].get("/bulk").data.decode("utf-8")
        assert "bulk-phys" in html
        assert "-20.0 dBm" in html            # xy amp through the 2-hop chain
        assert 'data-phys-kind="mw"' in html
        assert 'data-phys-fsp="0' in html     # live-recompute seed

    def test_qubit_inspector_annotates(self, env):
        html = env["client"].get("/qubit/qA1").data.decode("utf-8")
        assert "phys-note" in html
        assert "-20.0 dBm" in html


    def test_settings_offers_unit_toggle(self, env):
        """docs/109 stage 2: the global MW-power unit setting lives in the
        Settings dropdown — dBm / V rms / Both, driven by PhysAmp.setUnit."""
        html = env["client"].get("/qubits").data.decode("utf-8")
        assert 'data-phys-unit="dbm"' in html
        assert 'data-phys-unit="v"' in html
        assert 'data-phys-unit="both"' in html
        assert "PhysAmp.setUnit" in html

    def test_components_tables_carry_p_ro(self, env):
        """The Qubits + Resonators tables gained the P(RO) column: canonical
        dBm text, data-dbm for client reformatting, data-sort so ordering is
        display-unit-independent, and the header unit label follows the
        setting via .phys-unit-label."""
        for page in ("/qubits", "/resonators"):
            html = env["client"].get(page).data.decode("utf-8")
            assert "P(RO)" in html, page
            assert "phys-unit-label" in html, page
            assert "data-sort=" in html, page
            assert "-20.0 dBm" in html, page   # FSP 0 + amp 0.1

    def test_no_ports_chip_stays_blank(self, tmp_path):
        """A chip with no port chain renders ZERO physical annotations —
        the extension-shaped guarantee (existing chips unchanged)."""
        live = tmp_path / "chips" / "bare"
        st = {"qubits": {"qA1": {"id": "qA1", "f_01": 5.0e9,
                                 "xy": {"operations": {"x180": {"amplitude": 0.1}}}}},
              "qubit_pairs": {}, "active_qubit_names": ["qA1"]}
        _write_chip(live, st, {"network": {"host": "1.1.1.1",
                                           "cluster_name": "C1"}})
        app = create_app(testing=True, instance_path=str(tmp_path / "_inst2"))
        c = app.test_client()
        assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
        html = c.get("/bulk").data.decode("utf-8")
        assert "bulk-phys" not in html
        html2 = c.get("/qubit/qA1").data.decode("utf-8")
        assert "phys-note" not in html2
