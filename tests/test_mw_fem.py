"""Tests for the OPX1000 MW-FEM band + LO-sharing model (core/mw_fem.py)."""

from __future__ import annotations

from quam_state_manager.core import mw_fem


def test_band_ranges():
    assert mw_fem.BANDS == {1: (50e6, 5.5e9), 2: (4.5e9, 7.5e9), 3: (6.5e9, 10.5e9)}


def test_in_band():
    assert mw_fem.in_band(5.0e9, 2) is True
    assert mw_fem.in_band(9.0e9, 2) is False           # out of band 2
    assert mw_fem.in_band(9.0e9, 3) is True
    assert mw_fem.in_band(5.0e9, None) is True          # unknown band → no false alarm
    assert mw_fem.in_band("x", 2) is True               # non-numeric → no false alarm


def test_bands_of_overlap():
    assert set(mw_fem.bands_of(5.0e9)) == {1, 2}         # 5 GHz is in both band 1 and 2
    assert mw_fem.bands_of(9.0e9) == [3]
    assert mw_fem.bands_of(40e6) == []                   # below everything


def test_bands_compatible():
    assert mw_fem.bands_compatible(2, 2) is True
    assert mw_fem.bands_compatible(1, 3) is True         # 1 & 3 compatible
    assert mw_fem.bands_compatible(3, 1) is True
    assert mw_fem.bands_compatible(2, 1) is False        # band 2 only with 2
    assert mw_fem.bands_compatible(2, 3) is False


def test_lo_peer_pairs():
    assert mw_fem.lo_peer("mw_outputs", 2) == ("mw_outputs", 3)
    assert mw_fem.lo_peer("mw_outputs", 3) == ("mw_outputs", 2)
    assert mw_fem.lo_peer("mw_outputs", 4) == ("mw_outputs", 5)
    assert mw_fem.lo_peer("mw_outputs", 6) == ("mw_outputs", 7)
    assert mw_fem.lo_peer("mw_outputs", 1) == ("mw_inputs", 1)
    assert mw_fem.lo_peer("mw_outputs", 8) == ("mw_inputs", 2)
    assert mw_fem.lo_peer("mw_inputs", 1) == ("mw_outputs", 1)
    assert mw_fem.lo_peer("mw_inputs", 2) == ("mw_outputs", 8)
    assert mw_fem.lo_peer("mw_outputs", 99) is None


def test_port_of_resolved():
    assert mw_fem.port_of_resolved("ports.mw_outputs.con1.1.2.band") == ("mw_outputs", "con1", 1, 2, "band")
    assert mw_fem.port_of_resolved("ports.mw_inputs.con1.1.1.downconverter_frequency") == \
        ("mw_inputs", "con1", 1, 1, "downconverter_frequency")
    assert mw_fem.port_of_resolved("qubits.qA1.f_01") is None
    assert mw_fem.port_of_resolved(None) is None


def test_freq_field():
    assert mw_fem.freq_field("mw_outputs") == "upconverter_frequency"
    assert mw_fem.freq_field("mw_inputs") == "downconverter_frequency"
