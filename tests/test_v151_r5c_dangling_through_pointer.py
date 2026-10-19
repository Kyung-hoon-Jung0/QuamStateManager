"""R4 B1: header Search says "dangling pointer to" only for a pointer the
pointer resolver cannot resolve -- never for a valid pointer whose path
crosses another pointer.

R3B MINOR 1 decided "dangling" by walking the raw dicts along the pointer's
target path. That walk stopped at the first pointer on the way, so every
cross-resonance channel's ``LO_frequency``
(``#/qubits/<q>/xy/opx_output/upconverters/2/frequency``, where ``xy.opx_output``
is a pointer to the wiring, itself a pointer to a shared MW-FEM port) was
called dangling: 54 of 44,072 pointers on the real states, all of them valid
wiring. The chip below copies that real shape (state.json ``ports``, the
wiring's ``opx_output`` pointer, a port's ``upconverters``) and adds two that
truly dangle: through a pointer to a port that is not there, and to an
upconverter the port does not have.
"""
from __future__ import annotations

import json
import re

from quam_state_manager.web.app import create_app


def _chip(folder):
    port = {"controller_id": "con1", "fem_id": 1, "port_id": 2, "band": 3,
            "upconverters": {"1": {"frequency": 8600000000.0}, "2": {"frequency": 9095000000}},
            "full_scale_power_dbm": 16,
            "__class__": "quam.components.ports.analog_outputs.MWFEMAnalogOutputPort"}
    qubits = {q: {"id": q, "xy": {"RF_frequency": 8.5e9,
                                  "opx_output": f"#/wiring/qubits/{q}/xy/opx_output"}}
              for q in ("q0", "q1", "q2")}

    def cr(q, up=2):
        return {"cross_resonance": {"id": f"cr_{q}",
                                    "LO_frequency": f"#/qubits/{q}/xy/opx_output/upconverters/{up}/frequency"}}
    state = {"qubits": qubits,
             "qubit_pairs": {"q0-1": cr("q0"),        # valid: through two pointers to the port
                             "q1-0": cr("q1"),        # dangling: q1's port is not there
                             "q2-0": cr("q2", up=3)},  # dangling: the port has no upconverter 3
             "active_qubit_names": ["q0", "q1", "q2"],
             "ports": {"mw_outputs": {"con1": {"1": {"2": port}}}}}
    wiring = {"wiring": {"qubits": {
        "q0": {"xy": {"opx_output": "#/ports/mw_outputs/con1/1/2"}},
        "q1": {"xy": {"opx_output": "#/ports/mw_outputs/con1/1/7"}},
        "q2": {"xy": {"opx_output": "#/ports/mw_outputs/con1/1/2"}}}},
        "network": {"host": "1.1.1.1", "cluster_name": "C1"}}
    folder.mkdir()
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(wiring), encoding="utf-8")


def test_a_pointer_through_a_pointer_is_not_dangling(tmp_path):
    _chip(tmp_path / "chip")
    c = create_app(testing=True, instance_path=str(tmp_path / "_inst")).test_client()
    assert c.post("/load", data={"folder": str(tmp_path / "chip")}).status_code < 400
    html = c.get("/search?q=LO_frequency", headers={"HX-Request": "true"}).get_data(as_text=True)
    by = dict(re.findall(r'<tr class="clickable-row" data-dot-path="([^"]+)"(.*?)</tr>', html, re.S))
    ok = by["qubit_pairs.q0-1.cross_resonance.LO_frequency"]
    assert "points to <code>qubits.q0.xy.opx_output.upconverters.2.frequency</code>" in ok, ok
    assert "dangling" not in ok, "a valid wiring through a pointer was called dangling: " + ok
    for p in ("qubit_pairs.q1-0.cross_resonance.LO_frequency",
              "qubit_pairs.q2-0.cross_resonance.LO_frequency"):
        assert "dangling pointer to <code>" in by[p], (p, by[p])
        assert "points to" not in by[p].replace("dangling pointer to", ""), (p, by[p])
