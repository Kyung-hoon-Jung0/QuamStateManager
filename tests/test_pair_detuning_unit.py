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


def _chip(tmp_path) -> Path:
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
    return chip


@pytest.fixture
def client(tmp_path):
    chip = _chip(tmp_path)
    app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
    c = app.test_client()
    c.post("/load", data={"folder": str(chip)})
    return c


def test_the_pairs_page_shows_the_detuning_in_volts(client):
    html = client.get("/pairs").get_data(as_text=True)
    assert 'Detuning <span class="unit">(V)</span>' in html
    assert "(MHz)</span></th>" not in html.split("Detuning", 1)[1][:60]
    assert "<td>-0.1659</td>" in html


# Review follow-up: the Pairs table said "Detuning (V) -0.1659" while the pair
# inspector on the same page still printed the row as "-0.00 MHz" (raw Hz) --
# two units for one value on one screen. Every pair-level surface now looks
# the unit up through units.pair_field_key.

def test_pair_field_key_maps_only_the_pairs_own_detuning():
    from quam_state_manager.core import units
    assert units.pair_field_key("detuning") == "pair_detuning"
    assert units.pair_field_key("mutual_flux_bias") == "mutual_flux_bias"
    assert units.pair_field_key("cz_length") == "cz_length"
    # the Hz `detuning` everywhere else is untouched
    assert units.format_quantity(1.5e6, "detuning") == ("1.50", "MHz")


def _detuning_row(html: str) -> str:
    return html.split('value="qubit_pairs.q1-2.detuning"', 1)[1].split("</tr>", 1)[0]


def test_the_pair_inspector_shows_the_detuning_in_volts(client):
    html = client.get("/pair/q1-2", headers={"HX-Request": "true"}).get_data(as_text=True)
    row = _detuning_row(html)
    assert ">-0.1659 V</span>" in row, row
    assert "MHz" not in row
    assert "(raw Hz)" not in row and 'stored raw in Hz' not in row
    assert "(raw V)" in row


def test_the_full_pair_page_says_it_too(client):
    row = _detuning_row(client.get("/pair/q1-2").get_data(as_text=True))
    assert ">-0.1659 V</span>" in row and "MHz" not in row


def test_qsm_show_prints_the_pair_detuning_in_volts(tmp_path):
    from typer.testing import CliRunner

    from quam_state_manager.cli import app
    chip = _chip(tmp_path)
    r = CliRunner().invoke(app, ["show", "q1-2", "-f", str(chip)])
    assert r.exit_code == 0, r.output
    line = next(ln for ln in r.output.splitlines() if "detuning" in ln)
    assert "-0.1659 V" in line, line
    assert "MHz" not in line
