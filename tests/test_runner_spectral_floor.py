"""The spectral presence floor is per-family (docs/78 §27).

`_SPECTRAL_RATIO_MIN = 50.0` was one module constant for every span-mode
family, and on a real 08 -> 08b -> 09 chain it rejected FOUR of five targets
the node accepted. The figure settled it: one of them carries an unmistakable
bright parabolic arc that the node's own fit follows exactly, and it scored 13.

The cause is structural, not a mis-tuned number. Span mode reduces a cube to
its most-structured ROW; a 1-D oscillation concentrates its power there while a
2-D flux arc spreads it over every row. Measured over every span-mode family:

    1-D  T1 min 45 (n=36) | echo min 79 (n=14) | ramsey min 18 (n=262)
    2-D  qubit-vs-flux min 4 (n=185) | resonator-vs-flux min 6 (n=160)

At 50 the 2-D families lost 122/185 and 17/160 accepted targets. One constant
cannot serve both shapes.
"""
from __future__ import annotations

import numpy as np
import pytest

from quam_state_manager.core.autofit import families as fam_mod
from quam_state_manager.core.autofit import gates as gates_mod


def _fam(k):
    f = fam_mod.family_for(k)
    assert f is not None, k
    return f


class TestTheFloorIsDeclaredWhereMeasured:
    @pytest.mark.parametrize("key,expected", [
        ("qubit_spectroscopy_vs_flux", 3.0),
        ("resonator_spectroscopy_vs_flux", 4.5),
    ])
    def test_the_two_d_flux_families_lower_it(self, key, expected):
        assert _fam(key).feature_check.spectral_min == expected

    @pytest.mark.parametrize("key", ["ramsey", "echo", "T1"])
    def test_the_one_d_families_keep_the_default(self, key):
        """Their accepted runs sit at 18-79, comfortably above 50; re-deriving
        them needs the population split docs/78 §22.4 still calls for."""
        assert _fam(key).feature_check.spectral_min is None

    def test_every_declared_floor_sits_below_its_measured_accepted_minimum(self):
        """The project's rule: a floor may only sit BELOW the accepted minimum.
        Measured minima are 4 (qubit-vs-flux) and 6 (resonator-vs-flux)."""
        assert _fam("qubit_spectroscopy_vs_flux").feature_check.spectral_min < 4.0
        assert _fam("resonator_spectroscopy_vs_flux").feature_check.spectral_min < 6.0


class TestTheResolver:
    def test_a_declared_floor_wins(self):
        fc = fam_mod.FeatureCheck(var="I", axis_var="x", mode="span",
                                  spectral_min=3.0)
        assert gates_mod._spectral_floor(fc) == 3.0

    def test_an_undeclared_floor_falls_back_to_the_module_default(self):
        fc = fam_mod.FeatureCheck(var="I", axis_var="x", mode="span")
        assert gates_mod._spectral_floor(fc) == gates_mod._SPECTRAL_RATIO_MIN

    @pytest.mark.parametrize("bad", [None, True, "3.0"])
    def test_a_non_number_falls_back_rather_than_crashing(self, bad):
        fc = fam_mod.FeatureCheck(var="I", axis_var="x", mode="span")
        object.__setattr__(fc, "spectral_min", bad)
        assert gates_mod._spectral_floor(fc) == gates_mod._SPECTRAL_RATIO_MIN


class TestItChangesTheVerdict:
    """A trace whose ratio lands between the two floors: rejected under the
    shared constant, accepted under the family's own."""

    @staticmethod
    def _trace(n=256, amp=0.35, seed=7):
        rng = np.random.default_rng(seed)
        x = np.arange(n)
        return amp * np.sin(2 * np.pi * x / 40.0) + rng.normal(0, 1.0, n)

    def test_the_same_trace_flips_with_the_family_floor(self):
        y = self._trace()
        y0 = y - y.mean()
        psd = np.abs(np.fft.rfft(y0)) ** 2 / y0.size
        ratio = float(np.max(psd[1:])) / float(np.median(psd[1:]))
        # the synthetic sits in the gap the measurement opened up
        assert 3.0 <= ratio < gates_mod._SPECTRAL_RATIO_MIN, ratio
        lo = fam_mod.FeatureCheck(var="I", axis_var="x", mode="span",
                                  spectral_min=3.0)
        hi = fam_mod.FeatureCheck(var="I", axis_var="x", mode="span")
        assert ratio >= gates_mod._spectral_floor(lo)
        assert ratio < gates_mod._spectral_floor(hi)
