"""R6e: action names and true/false values do not declare decibels."""
import pytest
from quam_state_manager.core import units, pair_columns

@pytest.mark.parametrize("name", ["save_to_db", "export_to_db", "extras.save_to_db"])
def test_action_suffix_is_not_a_stored_unit(name):
    assert units.stored_unit_label(name) == ""
    assert pair_columns._unit_of(name) == ""
    assert pair_columns._unit_of(name, True) == ""
    assert pair_columns._unit_of(name, False) == ""

@pytest.mark.parametrize("name,label", [("gain_db", "dB"), ("power_dbm", "dBm"), ("frequency_mhz", "MHz")])
def test_physical_suffix_still_has_its_unit(name, label):
    assert units.stored_unit_label(name) == label
    assert pair_columns._unit_of(name, 2.0) == label
    assert pair_columns._unit_of(name, True) == ""
