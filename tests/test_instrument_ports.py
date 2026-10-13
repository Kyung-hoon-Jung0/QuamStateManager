"""Every physical analog output of an OPX1000 FEM is drawn, used or not.

The rack diagram used to size each controller's port rows from the HIGHEST
port the chip happened to use, so a FEM wired on outputs 1..7 lost its port 8
entirely: the drawing was cut off below the last used port, and the empty
slot a user would move a line to was nowhere to be seen. A FEM is hardware
with a fixed port count (8 analog outputs on both the LF-FEM and the MW-FEM,
QM docs), so the row count is floored at that number; a port used above it
(a hand-edited wiring) still extends the rows, never truncated.
"""
from __future__ import annotations

from quam_state_manager.core.loader import QuamStore
from quam_state_manager.core.query import QueryEngine


def _wiring(lines: dict, ports: dict) -> dict:
    return {"wiring": {"qubits": {"q1": lines}}, "ports": ports}


def _max_out(state: dict, wiring: dict, ctrl: str = "con1") -> int:
    out = QueryEngine(QuamStore.from_dicts(state, wiring)).get_instrument_wiring()
    return out["controllers"][ctrl]["max_output_port"]


class TestEveryFemPortIsDrawn:
    def test_lf_fem_using_port_1_draws_all_8(self):
        wiring = _wiring(
            {"z": {"opx_output": "#/ports/analog_outputs/con1/5/1"}},
            {"analog_outputs": {"con1": {"5": {"1": {}}}}},
        )
        assert _max_out({"qubits": {"q1": {}}}, wiring) == 8

    def test_mw_fem_using_ports_1_to_3_draws_all_8(self):
        wiring = _wiring(
            {"xy": {"opx_output": "#/ports/mw_outputs/con1/1/3"},
             "rr": {"opx_output": "#/ports/mw_outputs/con1/1/1"}},
            {"mw_outputs": {"con1": {"1": {"1": {"band": 1}, "3": {"band": 1}}}}},
        )
        assert _max_out({"qubits": {"q1": {}}}, wiring) == 8

    def test_a_port_above_the_floor_still_extends_the_rows(self):
        wiring = _wiring(
            {"z": {"opx_output": "#/ports/analog_outputs/con1/5/10"}},
            {"analog_outputs": {"con1": {"5": {"10": {}}}}},
        )
        assert _max_out({"qubits": {"q1": {}}}, wiring) == 10

    def test_unused_ports_stay_empty(self):
        """The floor adds rows, never invented assignments."""
        wiring = _wiring(
            {"z": {"opx_output": "#/ports/analog_outputs/con1/5/1"}},
            {"analog_outputs": {"con1": {"5": {"1": {}}}}},
        )
        out = QueryEngine(QuamStore.from_dicts({"qubits": {"q1": {}}}, wiring)
                          ).get_instrument_wiring()
        assert list(out["controllers"]["con1"]["fems"]["5"]["output_ports"]) == ["1"]
