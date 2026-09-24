"""QA F-24: a qubit pair's ``detuning`` is a flux amplitude in volts.

quam_builder's FluxTunableTransmonPair, verbatim: "detuning (Optional[float]):
Flux amplitude required to bring the qubits to the same energy in V". SM read
it by leaf name as the Hz ``detuning`` of pulses / the ZZ drive / the XY-detuned
channel, so the rig chip's q1-2 value -0.16586 printed as "-0.0 MHz" in the
Chip Status pair popup and "-0.00" under "Detuning (MHz)" on the Pairs page and
in the printed report. The popup is pinned in chip_status_hero_selfcheck.cjs,
the report in test_chip_report.py, the unit key in test_units.py; this pins the
Pairs page.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app


@pytest.fixture
def client(tmp_path):
    chip = tmp_path / "quam_state"
    chip.mkdir()
    (chip / "state.json").write_text(json.dumps({
        "qubits": {"q1": {"id": "q1"}, "q2": {"id": "q2"}},
        "qubit_pairs": {"q1-2": {"id": "q1-2", "qubit_control": "#/qubits/q1",
                                 "qubit_target": "#/qubits/q2",
                                 "detuning": -0.16586175268952874, "macros": {}}},
        "active_qubit_names": ["q1", "q2"]}), encoding="utf-8")
    (chip / "wiring.json").write_text(json.dumps(
        {"network": {"host": "1.2.3.4"}, "wiring": {"qubits": {}}}), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
    c = app.test_client()
    c.post("/load", data={"folder": str(chip)})
    return c


def test_the_pairs_page_shows_the_detuning_in_volts(client):
    html = client.get("/pairs").get_data(as_text=True)
    assert 'Detuning <span class="unit">(V)</span>' in html
    assert "(MHz)</span></th>" not in html.split("Detuning", 1)[1][:60]
    assert "<td>-0.1659</td>" in html
