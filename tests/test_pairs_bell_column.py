"""docs/301 (F21): the Pairs table's Bell fidelity reads the gate that has one.

The column was wired to ``cz_flattop`` alone, so a chip whose Bell state lives
under another CZ macro (``cz_unipolar`` here) showed "-" on every row while
Chip Status Trends charted the same values."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app


def _pair(macros):
    return {"id": "q1-2", "qubit_control": "#/qubits/q1", "qubit_target": "#/qubits/q2", "macros": macros}


def _cz(bell=None, amp=0.2):
    gate = {"flux_pulse_qubit": {"amplitude": amp, "length": 48}, "phase_shift_control": 0.1,
            "phase_shift_target": 0.2}
    if bell is not None:
        gate["fidelity"] = {"Bell_State": {"Fidelity": bell, "Purity": 0.97}}
    return gate


def _client(tmp_path, macros):
    chip = tmp_path / "quam_state"
    chip.mkdir()
    (chip / "state.json").write_text(json.dumps({
        "qubits": {"q1": {"id": "q1"}, "q2": {"id": "q2"}},
        "qubit_pairs": {"q1-2": _pair(macros)},
        "active_qubit_names": ["q1", "q2"]}), encoding="utf-8")
    (chip / "wiring.json").write_text(json.dumps({"network": {}, "wiring": {"qubits": {}}}), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
    c = app.test_client()
    c.post("/load", data={"folder": str(chip)})
    return c


def _bell_cell(html):
    row = re.search(r'<tr class="clickable-row[^"]*" data-pair-id="q1-2".*?</tr>', html, re.S).group(0)
    return re.findall(r"<td[^>]*>.*?</td>", row, re.S)[6]


def test_another_gates_bell_fidelity_is_shown_and_named(tmp_path):
    c = _client(tmp_path, {"cz_unipolar": _cz(bell=0.9748)})
    cell = _bell_cell(c.get("/pairs").get_data(as_text=True))
    assert "0.9748" in cell and "cz_unipolar" in cell, cell


def test_cz_flattop_keeps_its_place(tmp_path):
    c = _client(tmp_path, {"cz_flattop": _cz(bell=0.91), "cz_unipolar": _cz(bell=0.99)})
    cell = _bell_cell(c.get("/pairs").get_data(as_text=True))
    assert "0.9100" in cell and "cz_unipolar 0.9900" in cell, cell


def test_no_bell_anywhere_is_a_dash(tmp_path):
    c = _client(tmp_path, {"cz_unipolar": _cz()})
    cell = _bell_cell(c.get("/pairs").get_data(as_text=True))
    assert re.sub(r"\s+", "", re.sub(r"<[^>]+>", "", cell)) == "-", cell


@pytest.mark.parametrize("nonfinite", [float("nan"), float("inf"), -float("inf")])
@pytest.mark.parametrize("preferred", [False, True])
def test_nonfinite_bell_values_do_not_hide_a_finite_alternative(tmp_path, nonfinite, preferred):
    macros = {"cz_unipolar": _cz(bell=nonfinite), "cz_flattop_erf": _cz(bell=0.99)}
    if preferred:
        macros["cz_flattop"] = _cz(bell=nonfinite)
    c = _client(tmp_path, macros)
    cell = _bell_cell(c.get("/pairs").get_data(as_text=True))
    assert "0.9900" in cell and "cz_flattop_erf" in cell, cell
    assert not re.search(r"\b(?:nan|inf)\b", cell, re.I), cell


@pytest.mark.parametrize("nonfinite", [float("nan"), float("inf"), -float("inf")])
def test_only_nonfinite_bell_values_render_empty(tmp_path, nonfinite):
    c = _client(tmp_path, {"cz_unipolar": _cz(bell=nonfinite), "cz_flattop": _cz(bell=nonfinite)})
    cell = _bell_cell(c.get("/pairs").get_data(as_text=True))
    assert re.sub(r"\s+", "", re.sub(r"<[^>]+>", "", cell)) == "-", cell
