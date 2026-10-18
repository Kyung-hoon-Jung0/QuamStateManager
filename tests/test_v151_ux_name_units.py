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
