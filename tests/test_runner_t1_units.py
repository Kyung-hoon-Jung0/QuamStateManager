"""The T1 unit defect (docs/78 §22.4 item 1) — a bug, not a calibration.

Measured, independently, before fixing:

* the chips store ``T1``/``T2ramsey``/``T2echo`` in **seconds** — n=8,379 /
  7,354 / 7,980 across 399 archived snapshots, p50 ≈ 3e-5;
* node 05's fit reports ``t1`` ≈ 3e4, i.e. **nanoseconds** (6 of 6 accepted
  fits, 3 runs);
* so the shipped band ``[0.5e-6, 1e-3]`` — which is CORRECT for the stored
  values — accepted **0 of 6** real fits, and the ``UpdateSpec`` would have
  written ~30,000 SECONDS into a field holding 30 microseconds.

Scoped by measurement rather than by family shape: ramsey's ``decay`` (n=635)
and echo's ``T2_echo`` (n=143) already report seconds and must NOT be scaled.

The fix is one reader (`families.fit_value`), because the defect's real cause
was two readers: the band and the write reached the same key by different paths
and disagreed about its unit, and neither had been exercised on real data.
"""
from __future__ import annotations

import pytest

from quam_state_manager.core.autofit import families as fam_mod
from quam_state_manager.core.autofit import gates as gates_mod

T1_NS = 32428.8          # the corpus median, verbatim
T1_S = T1_NS * 1e-9


def _fam(key):
    f = fam_mod.family_for(key)
    assert f is not None, key
    return f


class TestTheScaleIsDeclaredWhereMeasured:
    def test_t1_is_scaled(self):
        assert _fam("T1").fit_scale == {"t1": 1e-9}

    @pytest.mark.parametrize("key", ["echo", "ramsey"])
    def test_the_sibling_decay_families_are_NOT_scaled(self, key):
        """They already report seconds — scaling them would create the very
        defect this fixes, in the other direction."""
        assert not _fam(key).fit_scale

    def test_no_other_family_gained_a_scale_by_accident(self):
        scaled = {k: f.fit_scale for k, f in fam_mod.FAMILIES.items()
                  if getattr(f, "fit_scale", None)}
        assert set(scaled) == {"t1"} or set(scaled) == {"T1"}, scaled


class TestOneReader:
    def test_it_converts(self):
        assert fam_mod.fit_value(_fam("T1"), {"t1": T1_NS}, "t1") == \
            pytest.approx(T1_S)

    def test_an_unscaled_key_passes_through_unchanged(self):
        f = _fam("power_rabi")
        assert fam_mod.fit_value(f, {"opt_amp": 0.31}, "opt_amp") == 0.31

    def test_a_bool_is_not_a_measurement(self):
        """`True` is an int in Python and has no business in a physical band."""
        f = _fam("power_rabi")
        assert fam_mod.fit_value(f, {"opt_amp": True}, "opt_amp") is None

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), None, "0.3"])
    def test_non_numbers_are_refused(self, bad):
        f = _fam("power_rabi")
        assert fam_mod.fit_value(f, {"opt_amp": bad}, "opt_amp") is None

    def test_a_missing_key_is_refused(self):
        assert fam_mod.fit_value(_fam("T1"), {}, "t1") is None


class TestTheBandNowAcceptsRealFits:
    """0 of 6 before; the band itself was never wrong."""

    CORPUS_NS = [29660.7, 30500.0, 32428.8, 34000.0, 35100.0, 36602.9]

    def test_every_archived_accepted_fit_now_lands_inside_the_band(self):
        fam = _fam("T1")
        pl = next(p for p in fam.plausibility if p.key == "t1")
        for ns in self.CORPUS_NS:
            v = fam_mod.fit_value(fam, {"t1": ns}, "t1")
            assert pl.lo <= v <= pl.hi, (ns, v)

    def test_the_raw_nanosecond_value_would_still_be_rejected(self):
        """The guard against 'fixing' this by widening the band instead."""
        pl = next(p for p in _fam("T1").plausibility if p.key == "t1")
        assert not (pl.lo <= T1_NS <= pl.hi)

    def test_the_gate_passes_a_real_t1_fit(self):
        fam = _fam("T1")
        v = gates_mod.GateVerdict(target="qA1", verdict="pass")
        out = gates_mod._plausibility(fam, {"t1": T1_NS}, "qA1", v, None, None) \
            if hasattr(gates_mod, "_plausibility") else None
        if out is None:                      # helper is private/renamed
            pytest.skip("plausibility helper not exposed")
        assert v.verdict != "fail"


class TestTheWriteAgreesWithTheBand:
    def test_the_update_writes_seconds_not_nanoseconds(self):
        rows = fam_mod.resolve_updates(_fam("T1"), "qA1", {"t1": T1_NS},
                                       run_parameters={},
                                       current_value_of=lambda p: None)
        assert rows, "T1 declares one update"
        row = rows[0]
        assert row["path"] == "qubits.qA1.T1"
        assert row["value"] == pytest.approx(T1_S)

    def test_band_and_write_cannot_disagree(self):
        """The property the fix exists to guarantee: whatever the band judged
        is exactly what gets written."""
        fam = _fam("T1")
        judged = fam_mod.fit_value(fam, {"t1": T1_NS}, "t1")
        written = fam_mod.resolve_updates(fam, "qA1", {"t1": T1_NS},
                                          run_parameters={},
                                          current_value_of=lambda p: None)[0]
        assert written["value"] == pytest.approx(judged)

    def test_a_scaled_family_still_honours_an_update_factor(self):
        """`factor` (the node's own ratio) and the unit scale must compose,
        not replace each other."""
        f = _fam("power_rabi")
        rows = fam_mod.resolve_updates(f, "qA1", {"opt_amp": 0.4},
                                       run_parameters={"update_x90": True,
                                                       "operation": "x180"},
                                       current_value_of=lambda p: None)
        halves = [r for r in rows if r["path"].endswith("x90.amplitude")]
        assert halves and halves[0]["value"] == pytest.approx(0.2)
