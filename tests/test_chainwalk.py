"""The chain conductor (docs/140): cross-family recency + the parking edge.

Pure tests fabricate walker writes; the integration test walks the REAL
pilot bring-up day (docs/139's q1/q2) and auto-skips when the archive is
absent, pinning the exact numbers that round measured.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from quam_state_manager.core.autofit import chainwalk as CW
from quam_state_manager.core.autofit.chainwalk import Write, conclude


class _V:
    def __init__(self, run_id, date="2026-08-13", run_no=0, outcomes=None):
        self.run_id = run_id
        self.date = date
        self.run_no = run_no
        self.outcomes = outcomes or {"q1": "successful"}


class TestOrdering:
    def test_the_clock_beats_the_run_counter(self):
        # run counters reset mid-day: #97 at 12:52 comes BEFORE #8 at 17:04
        a = _V("#97_08_qubit_spectroscopy_125248", run_no=97)
        b = _V("#8_08_qubit_spectroscopy_170433", run_no=8)
        assert CW.order_key(a) < CW.order_key(b)

    def test_date_beats_time_across_midnight(self):
        a = _V("#172_03_resonator_spectroscopy_single_203622")
        b = _V("#231_08_qubit_spectroscopy_013942", date="2026-08-14")
        assert CW.order_key(a) < CW.order_key(b)


class TestStageViews:
    def test_partition_joint_merge_and_named_skips(self):
        vs = [
            _V("#1_08_qubit_spectroscopy_170433"),
            _V("#2_08b_qubit_spectroscopy_vs_power_171234"),
            _V("#3_03_resonator_spectroscopy_single_101010"),
            _V("#4_02e_resonator_spectroscopy_vs_flux_qdac_101011"),
            _V("#5_11_power_rabi_101012"),      # next chapter, not silence
        ]
        streams = CW.stage_views(vs)
        assert [v.run_id[:2] for v in streams["qubit_joint"]] == ["#1", "#2"]
        assert len(streams["resonator_spectroscopy"]) == 1
        assert len(streams["resonator_spectroscopy_vs_flux"]) == 1
        assert [v.run_id[:2] for v in streams["_out_of_scope"]] == ["#5"]

    def test_streams_are_time_ordered(self):
        vs = [_V("#8_08_qubit_spectroscopy_170433", run_no=8),
              _V("#97_08_qubit_spectroscopy_125248", run_no=97)]
        streams = CW.stage_views(vs)
        assert [v.run_no for v in streams["qubit_joint"]] == [97, 8]


def _w(value, stage, rid, t, date="2026-08-13"):
    return Write(value, stage, rid, (date, t, 0))


class TestConclude:
    """The docs/139 shapes, as pure fixtures."""

    def test_cross_family_recency_latest_flux_write_wins(self):
        # qs map 13:50 says 0.018; the wide res map 17:55 says 0.0579 —
        # the operator's joint_offset IS the later one, never an average
        d = []
        conc, cand = conclude({
            "qubit_spectroscopy_vs_flux": {
                "idle_offset": _w(0.0182763, "qubit_spectroscopy_vs_flux",
                                  "#149", 135030),
                "qubit_frequency": _w(4.699141e9,
                                      "qubit_spectroscopy_vs_flux",
                                      "#149", 135030)},
            "resonator_spectroscopy_vs_flux": {
                "idle_offset": _w(0.0578962, "resonator_spectroscopy_vs_flux",
                                  "#56", 175556)},
        }, d)
        assert conc["flux_idle_offset"].value == pytest.approx(0.0578962)
        assert conc["flux_idle_offset"].run_id == "#56"
        assert [w.run_id for w in cand["flux_idle_offset"]] == ["#149", "#56"]

    def test_the_parking_edge_stales_a_pre_parking_frequency(self):
        d = []
        conc, _ = conclude({
            "qubit_spectroscopy_vs_flux": {
                "qubit_frequency": _w(4.699141e9,
                                      "qubit_spectroscopy_vs_flux",
                                      "#149", 135030)},
            "resonator_spectroscopy_vs_flux": {
                "idle_offset": _w(0.0578962, "resonator_spectroscopy_vs_flux",
                                  "#56", 175556)},
        }, d)
        assert "qubit_frequency" not in conc
        assert any("re-measure 1Q" in x for x in d)

    def test_a_post_parking_frequency_is_vouched(self):
        d = []
        conc, _ = conclude({
            "qubit_joint": {
                "frequency": _w(4.611937e9, "qubit_joint", "#231", 13942,
                                date="2026-08-14")},
            "resonator_spectroscopy_vs_flux": {
                "idle_offset": _w(0.0578962, "resonator_spectroscopy_vs_flux",
                                  "#56", 175556)},
        }, d)
        assert conc["qubit_frequency"].value == pytest.approx(4.611937e9)
        assert not any("re-measure 1Q" in x for x in d)

    def test_parked_with_no_frequency_directs_the_1q_measurement(self):
        d = []
        conc, _ = conclude({
            "resonator_spectroscopy_vs_flux": {
                "idle_offset": _w(0.0578962, "resonator_spectroscopy_vs_flux",
                                  "#56", 175556)},
        }, d)
        assert "qubit_frequency" not in conc
        assert any("measure 1Q at the parked offset" in x for x in d)

    def test_no_park_says_the_endpoint_is_open(self):
        d = []
        conc, _ = conclude({
            "qubit_joint": {
                "frequency": _w(4.286697e9, "qubit_joint", "#140", 133907)},
        }, d)
        assert conc["qubit_frequency"].value == pytest.approx(4.286697e9)
        assert any("no flux point established" in x for x in d)

    def test_resonator_three_windows_latest_wins(self):
        d = []
        conc, cand = conclude({
            "resonator_spectroscopy": {
                "frequency": _w(5.115149e9, "resonator_spectroscopy",
                                "#172", 203622)},
            "resonator_spectroscopy_vs_power": {
                "resonator_frequency": _w(5.115607e9,
                                          "resonator_spectroscopy_vs_power",
                                          "#135", 132801)},
            "resonator_spectroscopy_vs_flux": {
                "resonator_frequency": _w(5.114816e9,
                                          "resonator_spectroscopy_vs_flux",
                                          "#56", 175556),
                "idle_offset": _w(0.0578962, "resonator_spectroscopy_vs_flux",
                                  "#56", 175556)},
        }, d)
        assert conc["resonator_frequency"].run_id == "#172"
        assert len(cand["resonator_frequency"]) == 3


_DAY = Path(r"D:\work\Customer_Codes\CQT\data\2026-08-13")


@pytest.mark.skipif(not _DAY.exists(), reason="pilot archive not on this machine")
class TestChainOnRealArchive:
    """docs/139 end-to-end, now through the shipped conductor."""

    _PROFILE = {
        "two_dip_identity": "purcell_companion",
        "coupler_position": "between_qubit_and_resonator",
        "res_vs_coupler_response": "weak_flat_normal",
        "coupler_parking_rule": "minima_below_anticrossing",
        "res_vs_flux_parking": "resonator_freq_maxima",
        "pair_work_1q_recal": "required_before_after",
    }

    @pytest.fixture(scope="class")
    def day(self):
        from quam_state_manager.core.autofit.pathreplay import load_run
        views = [v for v in (load_run(p) for p in _DAY.iterdir()) if v]
        assert len(views) > 300
        return views

    def test_q2_parks_exactly_and_stales_the_frequency(self, day):
        r = CW.walk_chain(day, "q2", profile=self._PROFILE)
        park = r.conclusions["flux_idle_offset"]
        assert park.value == pytest.approx(0.0578962, rel=1e-4)
        assert park.stage == "resonator_spectroscopy_vs_flux"
        assert "qubit_frequency" not in r.conclusions
        # the operator's own overnight 1Q re-measurement is this directive
        assert any("measure 1Q" in x for x in r.directives)
        rf = r.conclusions["resonator_frequency"]
        assert abs(rf.value - 5.11521e9) < 2e6

    def test_q1_matches_and_names_the_open_endpoint(self, day):
        r = CW.walk_chain(day, "q1", profile=self._PROFILE)
        assert "flux_idle_offset" not in r.conclusions   # the QDAC dead end
        assert any("no flux point established" in x for x in r.directives)
        assert abs(r.conclusions["resonator_frequency"].value
                   - 4.981718e9) < 2e6
        assert abs(r.conclusions["qubit_frequency"].value
                   - 4.286607e9) < 2e6
        assert r.skipped        # the day's rabi/ramsey runs, named not hidden
