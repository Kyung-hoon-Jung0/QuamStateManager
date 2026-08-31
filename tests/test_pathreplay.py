"""Future-blind path replay (docs/129).

The properties that make the benchmark mean anything:

* a replay CANNOT see a run it has not reached — asking raises, so cheating
  is a crash rather than a quietly better score;
* the raw-map reader finds the branch structure a person sees in the figure,
  including the two shapes that broke naive readers on the pilot corpus (a
  sloped background, and a bare branch far deeper than the dressed one);
* the expert rules survive the round trip: fallback writes are reverted,
  floor-pinned powers refused, swapped branch labels corrected from geometry,
  sub-linewidth shifts adopt the dressed value only;
* an abstention never adopts;
* every knob move is a fraction of the CURRENT window, never an absolute —
  the manual's chip-independence rule, made executable.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from quam_state_manager.core.autofit import pathreplay as PR

_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# synthetic maps
# ---------------------------------------------------------------------------

def _write_map(path: Path, qubit: str, freq: np.ndarray, power: np.ndarray,
               z: np.ndarray) -> None:
    h5py = pytest.importorskip("h5py")
    with h5py.File(path, "w") as f:
        f["qubit"] = np.array([qubit.encode()])
        f["IQ_abs"] = z[None, :, :]                 # (1, n_freq, n_power)
        f["power"] = power
        f["full_freq"] = freq[None, :]


def _punchout_map(n_freq=64, n_power=40, cold_px=44, hot_px=18, knee=0.6,
                  slope=3.0, depth_cold=1.0, depth_hot=4.0, width=2.5,
                  noise=0.02, seed=0):
    """A punch-out: dressed dip at *cold_px* below the knee, bare at *hot_px*
    above it, on a sloped background — the shape every pilot map has, with the
    branch-depth asymmetry that is the physics of punch-out."""
    rng = np.random.default_rng(seed)
    f = np.arange(n_freq)
    z = np.zeros((n_freq, n_power))
    for p in range(n_power):
        frac = p / (n_power - 1)
        base = 10.0 + slope * (f / n_freq) + 2.0 * frac
        if frac < knee:
            pos, d = cold_px, depth_cold
        else:
            pos, d = hot_px, depth_hot
        z[:, p] = base - d * np.exp(-0.5 * ((f - pos) / width) ** 2)
    z += rng.normal(0, noise, z.shape)
    freq = 5.0e9 + np.arange(n_freq) * 1.0e5          # 100 kHz/px
    power = np.linspace(-50.0, -10.0, n_power)
    return freq, power, z


def _stationary_map(n_freq=64, n_power=40, pos=30, depth=3.0, **kw):
    rng = np.random.default_rng(1)
    f = np.arange(n_freq)
    z = np.zeros((n_freq, n_power))
    for p in range(n_power):
        base = 10.0 + 3.0 * (f / n_freq) + 2.0 * (p / (n_power - 1))
        z[:, p] = base - depth * np.exp(-0.5 * ((f - pos) / 2.5) ** 2)
    z += rng.normal(0, 0.02, z.shape)
    freq = 5.0e9 + np.arange(n_freq) * 1.0e5
    power = np.linspace(-50.0, -10.0, n_power)
    return freq, power, z


def _run_folder(tmp_path: Path, name: str, qubit: str, cube, *, fit: dict,
                params: dict | None = None, outcome: str = "successful",
                patches: list | None = None, state: dict | None = None) -> Path:
    folder = tmp_path / "2026-08-20" / name
    folder.mkdir(parents=True, exist_ok=True)
    freq, power, z = cube
    _write_map(folder / "ds_raw.h5", qubit, freq, power, z)
    (folder / "data.json").write_text(json.dumps({"fit_results": {qubit: fit}}),
                                      encoding="utf-8")
    (folder / "node.json").write_text(json.dumps({
        "data": {"outcomes": {qubit: outcome},
                 "parameters": {"model": params or {
                     "min_power_dbm": -50, "max_power_dbm": -10,
                     "frequency_span_in_mhz": 6.4,
                     "frequency_step_in_mhz": 0.1, "num_shots": 100}}},
        "patches": patches or [],
    }), encoding="utf-8")
    (folder / "quam_state").mkdir(exist_ok=True)
    (folder / "quam_state" / "state.json").write_text(
        json.dumps(state or {}), encoding="utf-8")
    return folder


# ---------------------------------------------------------------------------

class TestFutureBlindness:
    """The guard is the whole benchmark: without it every convenient shortcut
    is a form of cheating that shows up as a better score."""

    def _sess(self, tmp_path):
        views = []
        for i in range(3):
            folder = _run_folder(tmp_path, f"#{i}_05_resonator_spectroscopy_vs_power_0{i}",
                                 "q1", _punchout_map(),
                                 fit={"resonator_frequency": 5.0044e9,
                                      "success": True})
            views.append(PR.load_run(folder))
        return PR.Session("s", views)

    def test_reveal_stops_at_k(self, tmp_path):
        s = self._sess(tmp_path)
        assert len(s.reveal(0)) == 1
        assert len(s.reveal(2)) == 3

    def test_reaching_past_the_cursor_raises(self, tmp_path):
        s = self._sess(tmp_path)
        with pytest.raises(PR.FutureBlindError):
            s.reveal(3)
        with pytest.raises(PR.FutureBlindError):
            s.at(99)

    def test_negative_index_is_not_a_backdoor(self, tmp_path):
        s = self._sess(tmp_path)
        with pytest.raises(PR.FutureBlindError):
            s.reveal(-1)

    def test_classify_cannot_name_a_later_run(self):
        import inspect
        sig = inspect.signature(PR.classify)
        assert set(sig.parameters) == {"view", "qubit", "geom", "sm_state", "prior"}


class TestReadsTheMapAPersonSees:
    def test_two_branches_with_a_sharp_step_is_c1(self, tmp_path):
        folder = _run_folder(tmp_path, "#1_05_resonator_spectroscopy_vs_power_1", "q1",
                             _punchout_map(),
                             fit={"resonator_frequency": 5.0044e9,
                                  "bare_resonator_frequency": 5.0018e9,
                                  "optimal_power": -30.0, "punchout": True,
                                  "success": True})
        g = PR.measure(folder, "q1")
        assert g is not None
        assert abs(g.cold_pos - 44) <= 2 and abs(g.hot_pos - 18) <= 2
        v = PR.classify(PR.load_run(folder), "q1", g)
        assert v.case == "C1", v.reasons

    def test_a_deeper_bare_branch_does_not_erase_the_dressed_one(self, tmp_path):
        """Punch-out means the bare branch is far stronger. A reader that
        scales its membership bar to the strongest rows drops the dressed
        branch — and then reports a textbook punch-out as one flat line."""
        folder = _run_folder(tmp_path, "#2_05_resonator_spectroscopy_vs_power_2", "q1",
                             _punchout_map(depth_cold=0.6, depth_hot=8.0),
                             fit={"resonator_frequency": 5.0044e9, "success": True})
        g = PR.measure(folder, "q1")
        assert g.separation_px and g.separation_px > g.linewidth_px
        assert abs(g.cold_pos - 44) <= 3

    def test_a_sloped_background_does_not_inflate_the_linewidth(self, tmp_path):
        folder = _run_folder(tmp_path, "#3_05_resonator_spectroscopy_vs_power_3", "q1",
                             _punchout_map(slope=12.0),
                             fit={"resonator_frequency": 5.0044e9, "success": True})
        g = PR.measure(folder, "q1")
        assert g.linewidth_px <= 10, \
            "a global-threshold width counts the slope as dip and merges branches"

    def test_an_empty_window_is_c6_and_never_adopts(self, tmp_path):
        rng = np.random.default_rng(3)
        freq = 5.0e9 + np.arange(64) * 1.0e5
        power = np.linspace(-50.0, -10.0, 40)
        z = 10.0 + rng.normal(0, 0.02, (64, 40))
        folder = _run_folder(tmp_path, "#4_05_resonator_spectroscopy_vs_power_4", "q1",
                             (freq, power, z),
                             fit={"resonator_frequency": 5.003e9, "success": True})
        v = PR.classify(PR.load_run(folder), "q1", PR.measure(folder, "q1"))
        assert v.case == "C6"
        d = PR.decide(PR.load_run(folder), "q1", v)
        assert d.action != "adopt" and not d.adopt

    def test_unreadable_cube_abstains_rather_than_guessing(self, tmp_path):
        folder = tmp_path / "2026-08-20" / "#9_05_resonator_spectroscopy_vs_power_9"
        folder.mkdir(parents=True)
        (folder / "ds_raw.h5").write_bytes(b"not a data file")
        (folder / "data.json").write_text(json.dumps(
            {"fit_results": {"q1": {"resonator_frequency": 5e9, "success": True}}}),
            encoding="utf-8")
        (folder / "node.json").write_text(json.dumps(
            {"data": {"outcomes": {"q1": "successful"},
                      "parameters": {"model": {}}}}), encoding="utf-8")
        view = PR.load_run(folder)
        v = PR.classify(view, "q1", PR.measure(folder, "q1"))
        assert v.case is None
        assert PR.decide(view, "q1", v).action == "abstain"


class TestTheExpertRulesSurviveTheRoundTrip:
    def _c1(self, tmp_path, name="#5_05_resonator_spectroscopy_vs_power_5", **fit):
        base = {"resonator_frequency": 5.0044e9,
                "bare_resonator_frequency": 5.0018e9,
                "optimal_power": -30.0, "punchout": True, "success": True}
        base.update(fit)
        return _run_folder(tmp_path, name, "q1", _punchout_map(), fit=base)

    def test_floor_pinned_power_is_refused_but_the_frequency_is_kept(self, tmp_path):
        folder = self._c1(tmp_path, optimal_power=-49.5)     # window -50..-10
        view = PR.load_run(folder)
        v = PR.classify(view, "q1", PR.measure(folder, "q1"))
        assert "F1" in v.flags
        d = PR.decide(view, "q1", v)
        assert "optimal_power" in d.refused
        assert "optimal_power" not in d.adopt
        assert "resonator_frequency" in d.adopt

    def test_an_out_of_window_power_is_invalid_unconditionally(self, tmp_path):
        folder = self._c1(tmp_path, "#6_05_resonator_spectroscopy_vs_power_6",
                          optimal_power=-73.0)
        v = PR.classify(PR.load_run(folder), "q1", PR.measure(folder, "q1"))
        assert "F1" in v.flags

    def test_fallback_writes_are_reverted_not_carried(self, tmp_path):
        folder = _run_folder(
            tmp_path, "#7_05_resonator_spectroscopy_vs_power_7", "q1",
            _stationary_map(),
            fit={"resonator_frequency": 5.003e9, "punchout": False,
                 "success": True, "target_full_scale_power_dbm": -11},
            patches=[{"op": "replace",
                      "path": "/quam/ports/mw_outputs/con1/1/1/full_scale_power_dbm",
                      "old": -20, "value": -11}])
        view = PR.load_run(folder)
        v = PR.classify(view, "q1", PR.measure(folder, "q1"))
        assert "F2" in v.flags
        d = PR.decide(view, "q1", v)
        assert any("full_scale_power_dbm" in r for r in d.reverts)
        assert d.action != "adopt"

    def test_swapped_branch_labels_are_corrected_from_geometry(self, tmp_path):
        # geometry: dressed (cold) at px 44 = 5.0044 GHz, bare (hot) at px 18
        folder = _run_folder(
            tmp_path, "#8_05_resonator_spectroscopy_vs_power_8", "q1",
            _punchout_map(),
            fit={"resonator_frequency": 5.0018e9,      # swapped
                 "bare_resonator_frequency": 5.0044e9,
                 "optimal_power": -30.0, "punchout": True, "success": True})
        view = PR.load_run(folder)
        v = PR.classify(view, "q1", PR.measure(folder, "q1"))
        assert "F3" in v.flags, v.reasons
        d = PR.decide(view, "q1", v)
        assert d.action == "adopt"
        assert abs(d.adopt["resonator_frequency"] - 5.0044e9) < 3e5, \
            "the corrected dressed value must come from the map, not the label"

    def test_sub_linewidth_shift_adopts_dressed_only(self, tmp_path):
        folder = _run_folder(
            tmp_path, "#10_05_resonator_spectroscopy_vs_power_10", "q1",
            _punchout_map(cold_px=44, hot_px=38, width=3.5),
            fit={"resonator_frequency": 5.0044e9,
                 "bare_resonator_frequency": 5.0041e9,
                 "optimal_power": -30.0, "punchout": True, "success": True})
        view = PR.load_run(folder)
        g = PR.measure(folder, "q1")
        v = PR.classify(view, "q1", g)
        assert v.case == "N3", (v.case, v.reasons)
        d = PR.decide(view, "q1", v)
        assert "resonator_frequency" in d.adopt
        assert "bare_resonator_frequency" not in d.adopt


class TestKnobMovesAreRelativeNeverAbsolute:
    def test_every_move_scales_with_the_current_window(self):
        narrow = {"min_power_dbm": -40, "max_power_dbm": -30,
                  "frequency_span_in_mhz": 5.0, "num_shots": 100}
        wide = {"min_power_dbm": -80, "max_power_dbm": 0,
                "frequency_span_in_mhz": 50.0, "num_shots": 100}
        for case in ("C2", "C3", "N1", "N5", "N6", "N7"):
            a = PR._bounded_moves(case, narrow)
            b = PR._bounded_moves(case, wide)
            assert a and b, case
            assert a != b, f"{case} produced the same numbers for both chips"

    def test_a_paramless_run_yields_no_invented_numbers(self):
        assert PR._bounded_moves("C2", {}) == {}
        assert PR._bounded_moves("N5", {}) == {}


class TestScoringAgainstTheAnswerKey:
    def _result(self, **kw):
        base = dict(session_id="s", qubit="q1", steps=[],
                    final_state={}, terminated_at=None, runs_consumed=2,
                    unresolved=True, unscoreable_proposals=0)
        base.update(kw)
        return PR.ReplayResult(**base)

    def test_a_matching_value_scores_match(self):
        r = self._result(final_state={"resonator_frequency": 5.0e9},
                         terminated_at="#3", unresolved=False)
        s = PR.score(r, {"termination": {"at_run": "#3",
                                         "final_resonator_frequency": 5.0e9 + 5e5},
                         "ideal_length": 2, "actual_length": 5})
        assert s["frequency_verdict"] == "match"
        assert s["runs_saved_vs_operator"] == 3
        assert s["length_vs_ideal"] == 0

    def test_a_wrong_value_is_not_forgiven_by_being_close_ish(self):
        r = self._result(final_state={"resonator_frequency": 5.02e9},
                         terminated_at="#3", unresolved=False)
        s = PR.score(r, {"termination": {"final_resonator_frequency": 5.0e9}})
        assert s["frequency_verdict"] == "wrong_value"

    def test_abstaining_where_the_key_says_unresolved_is_correct(self):
        s = PR.score(self._result(),
                     {"termination": {"unresolved": True}})
        assert s["frequency_verdict"] == "correctly_abstained"

    def test_adopting_where_the_key_says_unresolved_is_an_error(self):
        r = self._result(final_state={"resonator_frequency": 5.0e9},
                         unresolved=False)
        s = PR.score(r, {"termination": {"unresolved": True}})
        assert s["frequency_verdict"] == "adopted_where_key_says_unresolved"

    def test_a_key_without_a_value_is_unscoreable_not_a_pass(self):
        r = self._result(final_state={"resonator_frequency": 5.0e9},
                         unresolved=False)
        s = PR.score(r, {"termination": {"at_run": "#3"}})
        assert s["frequency_verdict"] == "unscoreable_key_has_no_value"


class TestReplayWalksAndStops:
    def test_it_stops_at_the_run_that_answers(self, tmp_path):
        good = {"resonator_frequency": 5.0044e9,
                "bare_resonator_frequency": 5.0018e9,
                "optimal_power": -30.0, "punchout": True, "success": True}
        views = [
            PR.load_run(_run_folder(
                tmp_path, "#1_05_resonator_spectroscopy_vs_power_1", "q1",
                _stationary_map(),
                fit={"resonator_frequency": 5.003e9, "punchout": False,
                     "success": True})),
            PR.load_run(_run_folder(
                tmp_path, "#2_05_resonator_spectroscopy_vs_power_2", "q1",
                _punchout_map(), fit=good)),
            PR.load_run(_run_folder(
                tmp_path, "#3_05_resonator_spectroscopy_vs_power_3", "q1",
                _punchout_map(), fit=good)),
        ]
        res = PR.replay(PR.Session("s", views), "q1")
        assert res.runs_to_first_value == 2, \
            "the efficiency number must count only the runs needed to know"
        assert res.first_value_at.startswith("#2")
        assert abs(res.final_state["resonator_frequency"] - 5.0044e9) < 3e5
        assert res.revisions == [], \
            "a later run that AGREES must not be recorded as the chip moving"

    def test_a_later_map_that_moved_revises_the_held_value(self, tmp_path):
        """R-bias: the chip moving mid-session is exactly what a calibration
        that stops dead at its first answer fails to notice."""
        early = {"resonator_frequency": 5.0044e9,
                 "bare_resonator_frequency": 5.0018e9,
                 "optimal_power": -30.0, "punchout": True, "success": True}
        late = dict(early, resonator_frequency=5.0074e9,
                    bare_resonator_frequency=5.0048e9)
        views = [
            PR.load_run(_run_folder(
                tmp_path, "#1_05_resonator_spectroscopy_vs_power_1", "q1",
                _punchout_map(), fit=early)),
            PR.load_run(_run_folder(
                tmp_path, "#2_05_resonator_spectroscopy_vs_power_2", "q1",
                _punchout_map(cold_px=74, hot_px=48, n_freq=94), fit=late)),
        ]
        res = PR.replay(PR.Session("s", views), "q1")
        assert res.runs_to_first_value == 1
        assert res.revisions and res.revisions[0]["run"].startswith("#2")
        assert abs(res.final_state["resonator_frequency"] - 5.0074e9) < 3e5

    def test_a_qubit_absent_from_a_run_is_skipped_not_guessed(self, tmp_path):
        v1 = PR.load_run(_run_folder(
            tmp_path, "#1_05_resonator_spectroscopy_vs_power_1", "q1",
            _punchout_map(), fit={"resonator_frequency": 5.0044e9, "success": True}))
        s = PR.Session("s", [v1])
        assert s.runs_for("q9") == []
        assert PR.replay(s, "q9").runs_consumed == 0


REAL_2026_08_16 = r"D:\work\Customer_Codes\CQT\data\2026-08-16"


@pytest.mark.skipif(not Path(REAL_2026_08_16).exists(),
                    reason="pilot archive not present on this machine")
class TestOnTheRealArchive:
    """Anchored on maps a human reader classified from the figure (docs/129)."""

    def test_a_textbook_punchout_reads_as_c1(self):
        folder = next(Path(r"D:\work\Customer_Codes\CQT\data\2026-08-16")
                      .glob("#996_05_resonator_spectroscopy_vs_power_*"))
        g = PR.measure(folder, "q20")
        v = PR.classify(PR.load_run(folder), "q20", g)
        assert v.case == "C1", (v.case, v.reasons)

    def test_the_dressed_branch_matches_the_value_that_gets_adopted(self):
        """Anchored on the DRESSED branch, deliberately. That is the value the
        loop writes and the one the human reader confirmed; the record's bare
        is the field the manual trusts least (F3 exists for it, and the expert
        rule does not require it at all). On this very map the high-power rows
        swing from one window edge to the other, so pinning a test to the
        record's bare would assert that a chaotic region is a branch."""
        folder = next(Path(REAL_2026_08_16)
                      .glob("#996_05_resonator_spectroscopy_vs_power_*"))
        g = PR.measure(folder, "q20")
        fit = PR.load_run(folder).fit("q20")
        assert abs(g.freq_at(g.cold_pos) - fit["resonator_frequency"]) < 1e6
        assert g.separation_px > g.linewidth_px


@pytest.mark.skipif(
    not (_ROOT / "tests/golden/calib_paths/resonator_spectroscopy_vs_power"
         / "CQT" / "2026-08-16.json").exists()
    or not Path(REAL_2026_08_16).exists(),
    reason="answer keys or the pilot archive are not present on this machine")
class TestTheBenchmarkDoesNotRegress:
    """The headline numbers of the docs/129 benchmark, pinned as a floor.

    These are measured against hindsight-authored answer keys over 50 real
    (session, qubit) calibrations on two chips. They are a FLOOR, not a
    target: raising them by tuning the reader against these very keys would
    be overfitting the benchmark, which is why the bars sit below the
    measured values rather than on them.
    """

    @staticmethod
    def _score_all():
        import collections
        from quam_state_manager.core.autofit import knowledge
        G = (_ROOT / "tests/golden/calib_paths/resonator_spectroscopy_vs_power")
        archives = {"AS": Path(r"D:\work\dataset\AS_10TQ9TC"),
                    "CQT": Path(r"D:\work\Customer_Codes\CQT\data")}
        pack = knowledge.load_family("resonator_spectroscopy_vs_power")
        rows = []
        for gf in sorted(G.rglob("2026-*.json")):
            doc = json.loads(gf.read_text(encoding="utf-8"))
            chip = "AS" if gf.parent.name.startswith("AS") else "CQT"
            day = archives[chip] / gf.stem
            if not day.exists():
                continue
            folders = sorted(day.glob("#*_05_resonator_spectroscopy_vs_power_*"))
            views = [v for v in (PR.load_run(f) for f in folders) if v is not None]
            if not views:
                continue
            session = PR.Session(f"{chip}_{gf.stem}", views)
            skip = {e.get("qubit") for e in doc.get("exclusions", [])}
            for qkey in doc["qubits"]:
                if qkey["qubit"] in skip:
                    continue
                rows.append(PR.score(PR.replay(session, qkey["qubit"], pack=pack),
                                     qkey))
        return rows

    def test_it_reaches_the_right_answer_on_most_calibrations(self):
        rows = self._score_all()
        assert len(rows) >= 45, f"only {len(rows)} keys resolved to archives"
        good = [r for r in rows if r["frequency_verdict"] in
                ("match", "correctly_abstained")]
        assert len(good) / len(rows) >= 0.80, \
            f"{len(good)}/{len(rows)} correct — measured 46/50 when written"

    def test_it_almost_never_adopts_a_wrong_frequency(self):
        rows = self._score_all()
        wrong = [r for r in rows if r["frequency_verdict"] == "wrong_value"]
        assert len(wrong) <= 3, [r["qubit"] for r in wrong]

    def test_it_abstains_where_the_key_says_no_value_existed(self):
        rows = self._score_all()
        bad = [r for r in rows
               if r["frequency_verdict"] == "adopted_where_key_says_unresolved"]
        assert len(bad) <= 2, [(r["session"], r["qubit"]) for r in bad]

    def test_it_is_not_slower_than_the_ideal_path(self):
        rows = self._score_all()
        lens = [r["length_vs_ideal"] for r in rows
                if isinstance(r.get("length_vs_ideal"), int)]
        assert lens
        at_or_under = sum(1 for x in lens if x <= 0)
        assert at_or_under / len(lens) >= 0.65, \
            f"{at_or_under}/{len(lens)} within the ideal length"

    def test_the_poison_gap_is_recorded_not_forgotten(self):
        """SM refuses about two thirds of the writes the keys call poisoned.
        That is the honest current number and the one real weakness the
        benchmark exposes — pinned so it cannot quietly get worse."""
        rows = self._score_all()
        named = sum(r["poisoned_fields_in_key"] for r in rows)
        adopted = sum(len(r["poison_adopted"]) for r in rows)
        assert named >= 40
        assert adopted / named <= 0.45, \
            f"adopted {adopted}/{named} poisoned fields (measured 21/61)"
