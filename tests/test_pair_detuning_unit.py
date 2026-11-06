"""Pair detuning uses the declared Hz unit on every surface."""
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
                                 "detuning": 400000000.0, "macros": {}}},
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


def test_the_pairs_page_shows_the_detuning_in_hz(client):
    html = client.get("/pairs").get_data(as_text=True)
    assert 'Detuning <span class="unit">(Hz)</span>' in html
    assert "(MHz)</span></th>" not in html.split("Detuning", 1)[1][:60]
    assert "<td>400000000.0</td>" in html


def test_pair_field_key_maps_only_the_pairs_own_detuning():
    from quam_state_manager.core import units
    assert units.pair_field_key("detuning") == "pair_detuning"
    assert units.pair_field_key("mutual_flux_bias") == "mutual_flux_bias"
    assert units.pair_field_key("cz_length") == "cz_length"
    # the Hz `detuning` everywhere else is untouched
    assert units.format_quantity(1.5e6, "detuning") == ("1.50", "MHz")


def _detuning_row(html: str) -> str:
    return html.split('value="qubit_pairs.q1-2.detuning"', 1)[1].split("</tr>", 1)[0]


def test_the_pair_inspector_shows_the_detuning_in_hz(client):
    html = client.get("/pair/q1-2", headers={"HX-Request": "true"}).get_data(as_text=True)
    row = _detuning_row(html)
    assert ">400000000.0 Hz</span>" in row, row
    assert "MHz" not in row
    assert "(raw V)" not in row
    assert "(raw Hz)" in row


def test_the_full_pair_page_says_it_too(client):
    row = _detuning_row(client.get("/pair/q1-2").get_data(as_text=True))
    assert ">400000000.0 Hz</span>" in row and "MHz" not in row


def test_qsm_show_prints_the_pair_detuning_in_hz(tmp_path):
    from typer.testing import CliRunner

    from quam_state_manager.cli import app
    chip = _chip(tmp_path)
    r = CliRunner().invoke(app, ["show", "q1-2", "-f", str(chip)])
    assert r.exit_code == 0, r.output
    line = next(ln for ln in r.output.splitlines() if "detuning" in ln)
    assert "400000000.0 Hz" in line, line
    assert "MHz" not in line
