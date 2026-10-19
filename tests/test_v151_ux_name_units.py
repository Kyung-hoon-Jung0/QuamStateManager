"""P1-8 (U29): a key that names its unit is shown in that unit, never rescaled.

``gaussian_filter_frequency_mhz = 200.0`` was read as a Hz "frequency" and
shown as "0.0000 GHz" in the resonator inspector. A unit suffix in the key
(``_mhz``, ``_ns``, ``_dbm`` ...) now wins over the generic name patterns: the
value is shown as stored with the declared unit, or with no hint at all --
never a wrong one.
"""

from __future__ import annotations

import json

import pytest

from quam_state_manager.core import units
from quam_state_manager.web.app import create_app


@pytest.mark.parametrize("key,label", [
    ("gaussian_filter_frequency_mhz", "MHz"),
    ("cutoff_frequency_ghz", "GHz"),
    ("drive_frequency_khz", "kHz"),
    ("notch_frequency_hz", "Hz"),
    ("settle_time_ns", "ns"),
    ("wait_us", units.MICRO + "s"),
    ("timeout_ms", "ms"),
    ("hold_s", "s"),
    ("full_scale_power_dbm", "dBm"),
    ("gain_db", "dB"),
    ("bias_mv", "mV"),
    ("bias_v", "V"),
])
def test_the_named_unit_is_the_hint(key, label):
    assert units.name_unit(key) == label
    assert units.stored_unit_label(key) == label
    shown = units.qty_filter(200.0, key, "full")
    assert shown == f"200 {label}", shown
    assert units.qty_filter(200.0, key, "unit") == label


def test_mhz_frequency_is_not_rescaled_to_ghz():
    key = "gaussian_filter_frequency_mhz"
    assert "GHz" not in units.qty_filter(200.0, key, "full")
    assert units.qty_filter(200.0, key, "num") == "200"
    # the export header already carries the unit: no "_GHz" suffix, no 1e-9
    assert units.export_header(key) == key
    assert units.export_value(key, 200.0) == 200.0
    assert units.display_spec(f"qubits.q1.resonator.{key}") is None


def test_suffix_is_anchored_on_its_underscore():
    assert units.name_unit("x_ghz") == "GHz"          # not "_hz"
    assert units.name_unit("x_ms") == "ms"            # not "_s"
    assert units.name_unit("x_dbm") == "dBm"          # not "_db"
    assert units.name_unit("x_mv") == "mV"            # not "_v"
    for plain in ("status", "params", "shots", "qubits", "rev", "f_01", "ms", "_s"):
        assert units.name_unit(plain) == "", plain


def test_the_generic_patterns_still_apply_without_a_suffix():
    assert units.qty_filter(5.1e9, "f_01", "full") == "5.1000 GHz"
    assert units.qty_filter(1.5e8, "intermediate_frequency", "full") == "150.00 MHz"
    assert units.qty_filter(40, "x_delay_ns", "full") == "40 ns"
    assert units.qty_filter(0.3, "amplitude", "full") == ""


def test_the_inspector_shows_the_named_unit(tmp_path):
    chip = tmp_path / "chip"
    chip.mkdir()
    state = {"qubits": {"q1": {
        "id": "q1", "f_01": 5.1e9,
        "resonator": {"id": "q1.resonator", "frequency_bare": 7.1e9},
        "z": {"id": "q1.z", "joint_offset": 0.0,
              "operations": {"population_loss_probe_flux": {
                  "length": 40, "gaussian_filter_frequency_mhz": 200.0}}},
    }}}
    (chip / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (chip / "wiring.json").write_text("{}", encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(chip)}).status_code < 400
    html = c.get("/qubit/q1", headers={"HX-Request": "true"}).get_data(as_text=True)
    i = html.index("gaussian_filter_frequency_mhz")
    row = html[i:html.index("</tr>", i)]
    assert "GHz" not in row, row
    assert "200 MHz" in row and "raw MHz" in row, row


# --------------------------------------------------------------------------
# R3B M3: a ``*_per_<unit>`` compound has no single unit (a Hz/V slope read
# "4.68844e+09 V"), and the Live State Edit grid + its CSV headers ask the
# key's own unit first (``*_mhz`` was headed "(Hz)" / "[Hz]").
# --------------------------------------------------------------------------
@pytest.mark.parametrize("key", ["slope_control_hz_per_v", "slope_target_hz_per_v", "rate_per_s",
                                 "decay_per_us", "frequency_hz_per_v"])
def test_a_per_unit_compound_names_no_unit(key):
    assert units.name_unit(key) == ""
    assert units.stored_unit_label(key) == ""
    assert units.qty_filter(4688442540.006638, key, "unit") == ""
    assert units.format_quantity(4688442540.006638, key) is None
    from quam_state_manager.core.pair_columns import _unit_of
    assert _unit_of(key, 4688442540.006638) == ""


def test_the_grid_header_asks_the_named_unit_first():
    from quam_state_manager.core.pair_columns import _unit_of
    assert _unit_of("gaussian_filter_frequency_mhz", 200.0) == "MHz"
    assert _unit_of("readout_frequency", 7e9) == "Hz"           # the generic guess still applies
    assert _unit_of("save_to_db", True) == ""                   # a true/false value has no unit


def test_the_live_edit_grid_and_csv_head_the_named_unit(tmp_path):
    import csv
    import io
    import re
    chip = tmp_path / "chip"
    chip.mkdir()
    q = lambda n: {"id": n, "f_01": 5.1e9,  # noqa: E731
                   "z": {"id": f"{n}.z", "operations": {"probe": {
                       "length": 40, "gaussian_filter_frequency_mhz": 200.0}}}}
    state = {"qubits": {"q1": q("q1"), "q2": q("q2")}, "active_qubit_names": ["q1", "q2"],
             "qubit_pairs": {"q1-2": {"id": "q1-2", "qubit_control": "#/qubits/q1",
                                      "qubit_target": "#/qubits/q2",
                                      "extras": {"cz_plan": {"slope_control_hz_per_v": 4688442540.0}}}}}
    (chip / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (chip / "wiring.json").write_text("{}", encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(chip)}).status_code < 400
    html = c.get("/bulk", headers={"HX-Request": "true"}).get_data(as_text=True)
    m = re.search(r'data-col-key="([^"]*gaussian_filter_frequency_mhz)"(.*?)</th>', html, re.S)
    assert m, "no gaussian_filter_frequency_mhz column"
    assert "(MHz)" in m.group(2) and "(Hz)" not in m.group(2), m.group(2)
    rows = list(csv.reader(io.StringIO(c.post("/export", json={"grid": "qubits", "cols": [m.group(1)]})
                                        .get_data(as_text=True).lstrip("\ufeff"))))
    assert rows[0][1] == m.group(1) + " [MHz]", rows[0]
    prs = list(csv.reader(io.StringIO(c.post("/export", json={"grid": "pairs", "cols": []})
                                       .get_data(as_text=True).lstrip("\ufeff"))))
    slope = [h for h in prs[0] if "slope_control_hz_per_v" in h]
    assert slope and all("[" not in h for h in slope), prs[0]
