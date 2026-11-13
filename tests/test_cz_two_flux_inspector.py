"""A both-movers CZ macro carries a second flux pulse, ``flux_pulse_target``.

The pair inspector listed only ``flux_pulse_qubit``, so the second qubit's
amplitude and lengths were not on screen at all. They are listed now, every
field the pulse holds, under ``<gate>_target_``. The CZ gate plan marks an
IRB above 1 as unphysical rather than printing it as a plain number.
"""
from __future__ import annotations

import json
import re

import pytest

from quam_state_manager.web.app import create_app


def _snz(amp, pid):
    return {"__class__": "lab.two_flux_gate.SNZTwoFluxPulse", "id": pid, "amplitude": amp,
            "length": "#./inferred_length", "flat_length": 88, "t_phi": 0, "b_over_a": 0.0,
            "neg_offset_v": 0.0, "padding": 20, "axis_angle": None, "digital_marker": None}


def _state(irb=None):
    fid = {"InterleavedRB": irb} if irb is not None else {}
    return {
        "qubits": {"q1": {"f_01": 5.2e9, "anharmonicity": -200e6, "z": {"flux_point": "joint"}},
                   "q2": {"f_01": 5.0e9, "anharmonicity": -200e6, "z": {"flux_point": "joint"}}},
        "qubit_pairs": {"q1-q2": {
            "qubit_control": "#/qubits/q1", "qubit_target": "#/qubits/q2",
            "moving_qubit": "control",
            "macros": {
                "cz_snz": {"__class__": "lab.two_flux_gate.CZGateTwoFlux",
                           "flux_pulse_qubit": _snz(0.1905, "cz_snz_q1"),
                           "flux_pulse_target": _snz(0.0123, "cz_snz_q2"),
                           "phase_shift_control": 0.4047, "phase_shift_target": 0.4350,
                           "fidelity": fid},
                "cz": "#./cz_snz"}}},
        "active_qubit_names": ["q1", "q2"],
    }


def _client(tmp_path, state):
    live = tmp_path / "chips" / "c1"
    live.mkdir(parents=True)
    (live / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (live / "wiring.json").write_text(json.dumps({"network": {"host": "1.1.1.1"}}), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    c.post("/load", data={"folder": str(live)})
    return c


def _row(html, key):
    m = re.search(r">" + re.escape(key) + r"</code>\s*</td>\s*<td[^>]*>(.*?)</td>", html, re.S)
    return m and " ".join(m.group(1).split())


class TestSecondFluxPulse:
    def test_the_target_pulse_is_listed_field_by_field(self, tmp_path):
        html = _client(tmp_path, _state()).get("/pair/q1-q2", headers={"HX-Request": "true"}).get_data(as_text=True)
        assert "cz_snz_target_pulse_class" in html and "SNZTwoFluxPulse" in _row(html, "cz_snz_target_pulse_class")
        for field, path in (("amplitude", "0.0123"), ("flat_length", "88"), ("padding", "20")):
            assert f"cz_snz_target_{field}" in html, field
            assert f"qubit_pairs.q1-q2.macros.cz_snz.flux_pulse_target.{field}" in html, field
        assert 'value="0.0123"' in html            # the second qubit's own amplitude
        assert 'value="0.1905"' in html            # the first one is still there

    def test_a_single_mover_gate_gets_no_target_rows(self, tmp_path):
        st = _state()
        del st["qubit_pairs"]["q1-q2"]["macros"]["cz_snz"]["flux_pulse_target"]
        html = _client(tmp_path, st).get("/pair/q1-q2", headers={"HX-Request": "true"}).get_data(as_text=True)
        assert "_target_" not in html


class TestPlanIrbCell:
    def test_an_irb_above_one_is_marked_unphysical(self, tmp_path):
        html = _client(tmp_path, _state(irb=1.5345)).get("/cz-plan", headers={"HX-Request": "true"}).get_data(as_text=True)
        assert "1.5345" in html and 'data-fid-unphysical="1"' in html

    def test_a_physical_irb_is_not(self, tmp_path):
        html = _client(tmp_path, _state(irb=0.9763)).get("/cz-plan", headers={"HX-Request": "true"}).get_data(as_text=True)
        assert "0.9763" in html and "data-fid-unphysical" not in html
