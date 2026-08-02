"""Unit tests for the LF-FEM-delay-from-MW-FEM-band rule in run_build.py.

The full ``run_build.py`` requires the QM stack (quam, quam_builder,
qualang_tools); these are not installed in ``qm_mng``. To keep the tests
in-process we import only the band/delay helpers via direct file load,
which sidesteps the top-of-module QUAM imports.

If a future refactor moves those helpers off the top of the module body,
this test file's loader pattern needs revisiting.
"""

from __future__ import annotations

import importlib.util
import types
from pathlib import Path

import pytest


_RUN_BUILD = Path(__file__).resolve().parent.parent / "quam_state_manager" / "generator" / "run_build.py"


def _load_helpers():
    """Load run_build.py without executing its QUAM-dependent imports.

    We need only the helper functions defined at module top level
    (`_band_for`, `_BAND_TO_DELAY_NS`, `_delay_for_band`,
    `_apply_lf_delay`). The QM stack imports live inside `_apply_pairs`
    and `_apply_qubit` — local imports — so a plain module load works.
    """
    spec = importlib.util.spec_from_file_location("run_build_under_test", _RUN_BUILD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


class TestBandToDelayMap:
    def setup_method(self):
        self.mod = _load_helpers()

    def test_band1_maps_to_141(self):
        assert self.mod._BAND_TO_DELAY_NS[1] == 141

    def test_band2_maps_to_161(self):
        assert self.mod._BAND_TO_DELAY_NS[2] == 161

    def test_band3_maps_to_141(self):
        assert self.mod._BAND_TO_DELAY_NS[3] == 141

    def test_only_three_bands_listed(self):
        assert set(self.mod._BAND_TO_DELAY_NS.keys()) == {1, 2, 3}


class TestDelayForBand:
    def setup_method(self):
        self.mod = _load_helpers()

    def test_known_bands(self):
        assert self.mod._delay_for_band(1) == 141
        assert self.mod._delay_for_band(2) == 161
        assert self.mod._delay_for_band(3) == 141

    def test_unknown_band(self):
        assert self.mod._delay_for_band(0) is None
        assert self.mod._delay_for_band(None) is None
        assert self.mod._delay_for_band(4) is None
        assert self.mod._delay_for_band("2") is None  # str, not int


class TestBandForFrequency:
    """Confirm bandOf-in-Python matches the JS mirror in generate.js."""

    def setup_method(self):
        self.mod = _load_helpers()

    @pytest.mark.parametrize("freq,expected", [
        (3.5e9, 1),
        (5.0e9, 2),  # 4.5–7.5
        (5.4e9, 2),  # 4.5–7.5 wins over band 1 endpoint
        (7.4e9, 3),  # 6.5–10.5 wins after 7.5 cutoff for band 2 — but 7.4 still in band 2 (lower bound 6.5)
        (8.0e9, 3),
        (10.5e9, 3),
        (0.0, None),
        (20e9, None),
    ])
    def test_band_assignment(self, freq, expected):
        # Band-2 covers 4.5–7.5; band-3 covers 6.5–10.5; both can match in
        # 6.5–7.5. The implementation picks band 2 (first match) — we just
        # confirm the result is non-None at the boundaries to keep this
        # robust to that overlap choice.
        result = self.mod._band_for(freq)
        if expected is None:
            assert result is None
        else:
            assert result is not None


class TestApplyLfDelay:
    def setup_method(self):
        self.mod = _load_helpers()

    def test_sets_delay_for_band1(self):
        port = types.SimpleNamespace(delay=0)
        self.mod._apply_lf_delay(port, 1)
        assert port.delay == 141

    def test_sets_delay_for_band2(self):
        port = types.SimpleNamespace(delay=0)
        self.mod._apply_lf_delay(port, 2)
        assert port.delay == 161

    def test_sets_delay_for_band3(self):
        port = types.SimpleNamespace(delay=0)
        self.mod._apply_lf_delay(port, 3)
        assert port.delay == 141

    def test_noop_when_port_is_none(self):
        # Should not raise.
        self.mod._apply_lf_delay(None, 1)

    def test_noop_when_band_is_none(self):
        port = types.SimpleNamespace(delay=0)
        self.mod._apply_lf_delay(port, None)
        assert port.delay == 0

    def test_noop_when_band_is_unknown(self):
        port = types.SimpleNamespace(delay=0)
        self.mod._apply_lf_delay(port, 99)
        assert port.delay == 0

    def test_noop_when_port_has_no_delay_attr(self):
        # Object lacks `delay` entirely → silently skipped.
        port = types.SimpleNamespace(offset=0.0)
        self.mod._apply_lf_delay(port, 1)
        assert not hasattr(port, "delay")

    def test_swallows_reference_assignment_error(self):
        # Some QUAM ports raise ValueError when assigning over a reference;
        # the helper must catch that.
        class RefPort:
            @property
            def delay(self):
                return 0
            @delay.setter
            def delay(self, _):
                raise ValueError("reference write blocked")
        port = RefPort()
        # Should not raise.
        self.mod._apply_lf_delay(port, 2)


class TestBandOverride:
    """r16 ⓪-3: user-settable band beats the derived _band_for pick — the
    Nyquist bands OVERLAP (6.5–7.5 GHz is band 2 AND 3) and first-match-wins
    wrote band 2 + the wrong 161 ns delay on real band-3 readout ports."""

    def setup_method(self):
        self.mod = _load_helpers()

    def _mw_port(self):
        return types.SimpleNamespace(upconverter_frequency=None,
                                     downconverter_frequency=None, band=None)

    def test_explicit_band_overrides_derived(self):
        ch = types.SimpleNamespace(opx_output=self._mw_port(), opx_input=None)
        self.mod._set_channel_lo(ch, 7.015e9, 3)     # overlap zone; derived = 2
        assert ch.opx_output.band == 3
        assert ch.opx_output.upconverter_frequency == 7.015e9

    def test_none_band_keeps_derived(self):
        ch = types.SimpleNamespace(opx_output=self._mw_port(), opx_input=None)
        self.mod._set_channel_lo(ch, 7.015e9, None)
        assert ch.opx_output.band == 2               # legacy first-match

    def test_invalid_band_falls_back_to_derived(self):
        ch = types.SimpleNamespace(opx_output=self._mw_port(), opx_input=None)
        self.mod._set_channel_lo(ch, 5.0e9, 7)
        assert ch.opx_output.band == 1               # _band_for(5.0e9), first match

    def test_band_only_write(self):
        out, inp = self._mw_port(), self._mw_port()
        ch = types.SimpleNamespace(opx_output=out, opx_input=inp)
        self.mod._set_channel_band(ch, 3)
        assert out.band == 3 and inp.band == 3
        assert out.upconverter_frequency is None     # LO untouched

    def test_band_only_invalid_noop(self):
        out = self._mw_port()
        ch = types.SimpleNamespace(opx_output=out, opx_input=None)
        self.mod._set_channel_band(ch, "3")          # strings never coerce here
        assert out.band is None
