"""Two unit defects on the pair and readout surfaces.

gl-6na: a pair ``detuning`` too large to be volts (a frequency stored in a
volt field) printed as "509318255.4322 V" in the pair inspector. Every pair
detuning surface now goes through the one fallback in ``core.units``
(``pair_detuning_inferred`` / ``pair_detuning_display``): Hz units, the stored
number, and a note that the unit was inferred. A plausible volt value renders
exactly as before.

gl-f4h: Chip Status > Frequencies showed ``resonator.f_01`` although the
resonator plays its ``RF_frequency`` (reached through pointers). The topology
node carries ``readout_rf_frequency`` (the played value, else f_01 with the
source named) and the panel reads it; ``readout_frequency`` keeps holding
f_01 because history, trends and exports are keyed on it.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.core import units
from quam_state_manager.web.app import create_app

_ROOT = Path(__file__).resolve().parent.parent
BIG = 509318255.4322012


def _chip(tmp_path: Path, detuning, resonator: dict) -> Path:
    chip = tmp_path / "quam_state"
    chip.mkdir()
    (chip / "state.json").write_text(json.dumps({
        "qubits": {
            "q1": {"id": "q1", "resonator": resonator},
            "q2": {"id": "q2", "resonator": {"f_01": 7.2e9, "RF_frequency": 7.2e9}},
        },
        "qubit_pairs": {"q1-2": {"id": "q1-2", "qubit_control": "#/qubits/q1",
                                 "qubit_target": "#/qubits/q2",
                                 "detuning": detuning,
                                 "macros": {"cz": {"detuning": 1.5e6}}}},
        "active_qubit_names": ["q1", "q2"]}), encoding="utf-8")
    (chip / "wiring.json").write_text(json.dumps(
        {"network": {"host": "1.2.3.4"}, "wiring": {"qubits": {}}}), encoding="utf-8")
    return chip


def _client(tmp_path, detuning=BIG, resonator=None):
    resonator = resonator if resonator is not None else {
        "f_01": 7.6266e9, "LO_frequency": 7.4e9, "intermediate_frequency": 2.1027e8,
        "RF_frequency": "#./inferred_RF_frequency"}
    chip = _chip(tmp_path, detuning, resonator)
    c = create_app(testing=True, instance_path=str(tmp_path / "_i")).test_client()
    c.post("/load", data={"folder": str(chip)})
    return c


def _detuning_row(html: str) -> str:
    return html.split('value="qubit_pairs.q1-2.detuning"', 1)[1].split("</tr>", 1)[0]


# --- gl-6na: the shared fallback -------------------------------------------

def test_the_shared_fallback_renders_hz_units_with_the_inferred_note():
    assert units.pair_detuning_inferred(BIG)
    assert units.pair_detuning_display(BIG) == (
        "509.318 MHz (stored 509318255.4322012, unit inferred as Hz: too large for V)")
    assert units.qty_filter(BIG, "pair_detuning", "full") == units.pair_detuning_display(BIG)
    assert units.qty_filter(BIG, "pair_detuning") == "509.318 MHz"
    assert units.pair_detuning_cell(BIG) == ("509.318 MHz", True)


def test_a_plausible_volt_detuning_renders_exactly_as_before():
    v = -0.16586175268952874
    assert not units.pair_detuning_inferred(v)
    assert units.qty_filter(v, "pair_detuning", "full") == "-0.1659 V"
    assert units.qty_filter(v, "pair_detuning") == "-0.1659"
    assert units.pair_detuning_display(v) == "-0.1659 V"
    assert units.pair_detuning_cell(v) == ("-0.1659", False)
    assert units.format_quantity(v, "pair_detuning") == ("-0.1659", "V")


def test_the_pair_inspector_goes_through_the_fallback(tmp_path):
    c = _client(tmp_path)
    row = _detuning_row(c.get("/pair/q1-2", headers={"HX-Request": "true"}).get_data(as_text=True))
    assert "509.318 MHz" in row, row
    assert "unit inferred as Hz" in row
    assert "509318255.4322 V" not in row
    assert "(raw Hz)" in row and "(raw V)" not in row


def test_the_pair_inspector_keeps_a_plausible_volt_value(tmp_path):
    c = _client(tmp_path, detuning=-0.16586175268952874)
    row = _detuning_row(c.get("/pair/q1-2", headers={"HX-Request": "true"}).get_data(as_text=True))
    assert ">-0.1659 V</span>" in row and "(raw V)" in row and "inferred" not in row


def test_the_pairs_table_uses_the_same_rendering(tmp_path):
    html = _client(tmp_path).get("/pairs").get_data(as_text=True)
    assert "509.318 MHz (unit inferred)" in html
    assert units.pair_detuning_display(BIG) in html      # the cell's tooltip
    assert "(raw, unit?)" not in html


def test_qsm_show_uses_the_fallback(tmp_path):
    from typer.testing import CliRunner

    from quam_state_manager.cli import app
    chip = _chip(tmp_path, BIG, {"f_01": 7.2e9})
    r = CliRunner().invoke(app, ["show", "q1-2", "-f", str(chip)])
    assert r.exit_code == 0, r.output
    out = " ".join(r.output.split())
    assert "509.318 MHz" in out and "509318255.4322 V" not in out


# --- the unit-map audit ------------------------------------------------------

def test_a_frequency_shift_is_mhz_not_ghz():
    assert units.format_quantity(-700000, "GEF_frequency_shift") == ("-0.70", "MHz")
    # absolute frequencies are unchanged
    assert units.format_quantity(7.4e9, "upconverter_frequency") == ("7.4000", "GHz")
    assert units.format_quantity(2.055e8, "anharmonicity") == ("205.50", "MHz")
    assert units.format_quantity(450000.0, "detuning_excited_hz") == ("450000", "Hz")


def test_the_pair_grid_labels_the_pairs_own_detuning_in_volts():
    from quam_state_manager.core import pair_columns as pc
    own = pc._leaf("q1-2", ["detuning"], ["detuning"], -0.16)
    macro = pc._leaf("q1-2", ["macros", "cz", "detuning"], ["macros", "cz", "detuning"], 1.5e6)
    assert own["unit"] == "V"
    assert macro["unit"] == "Hz"


# --- gl-f4h: the played readout frequency ------------------------------------

def _node(c, q):
    from quam_state_manager.web import routes
    with c.application.test_request_context():
        topo = routes._engine().get_topology()
    return next(n for n in topo["nodes"] if n["id"] == q)


def test_the_topology_carries_the_played_rf_through_pointers(tmp_path):
    c = _client(tmp_path)
    c.get("/topology")
    n = _node(c, "q1")
    assert n["readout_rf_frequency"] == 7.61027e9
    assert n["readout_rf_source"] == "RF_frequency"
    assert n["readout_frequency"] == 7.6266e9        # the history key is unchanged
    assert n["metrics"]["readout_rf_frequency"]["value"] == 7.61027e9


def test_an_absolute_pointer_rf_is_followed(tmp_path):
    c = _client(tmp_path, resonator={"f_01": 7.6266e9, "RF_frequency": "#/qubits/q2/resonator/f_01"})
    n = _node(c, "q1")
    assert n["readout_rf_frequency"] == 7.2e9 and n["readout_rf_source"] == "RF_frequency"


def test_rf_equal_to_f01_is_one_value(tmp_path):
    n = _node(_client(tmp_path), "q2")
    assert n["readout_rf_frequency"] == n["readout_frequency"] == 7.2e9
    assert n["readout_rf_source"] == "RF_frequency"


def test_no_rf_falls_back_to_f01_and_says_so(tmp_path):
    n = _node(_client(tmp_path, resonator={"f_01": 7.3e9}), "q1")
    assert n["readout_rf_frequency"] == 7.3e9
    assert n["readout_rf_source"] == "f_01"


def test_the_panel_shows_the_played_rf_with_f01_as_a_secondary_line():
    if shutil.which("node") is None:
        pytest.skip("node not on PATH")
    r = subprocess.run(["node", str(_ROOT / "tests" / "chip_status_played_rf_selfcheck.cjs")],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       cwd=str(_ROOT), timeout=120)
    if r.returncode == 2 and "jsdom not installed" in (r.stderr or ""):
        pytest.skip("jsdom not installed")
    assert r.returncode == 0, (r.stdout + r.stderr)
