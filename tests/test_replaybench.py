"""The generalized future-blind replay (docs/131).

The punch-out family has its own reader; every other spectroscopy family
shares one, and this is the loop around it. What matters is that the
properties which make the punch-out benchmark meaningful survive
generalisation:

* future-blindness is STRUCTURAL — the same session guard, so reaching past
  the cursor raises rather than quietly scoring better;
* the case decision and the numbers stay apart — the reader returns a
  semantic signal, the manual names it, the knob arithmetic is bounded and
  relative;
* a shape the reader cannot vouch for never adopts, and a value the sweep
  could not actually see is refused rather than written.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from quam_state_manager.core.autofit import knowledge
from quam_state_manager.core.autofit import mapcases as MC
from quam_state_manager.core.autofit import replaybench as RB
from quam_state_manager.core.autofit.pathreplay import (
    FutureBlindError, Session, load_run)

_ROOT = Path(__file__).resolve().parent.parent
_FAM_FLUX = "resonator_spectroscopy_vs_flux"
_FAM_LINE = "qubit_spectroscopy"


def _cube_folder(tmp_path: Path, name: str, target: str, freq, sweep, z, *,
                 fit: dict, params: dict, outcome="successful",
                 patches=None) -> Path:
    h5py = pytest.importorskip("h5py")
    d = tmp_path / "2026-08-20" / name
    d.mkdir(parents=True, exist_ok=True)
    with h5py.File(d / "ds_raw.h5", "w") as f:
        f["qubit"] = np.array([target.encode()])
        f["IQ_abs"] = z[None, ...]
        f["full_freq"] = np.asarray(freq)[None, :]
        if sweep is not None:
            f["flux_bias"] = np.asarray(sweep)
    (d / "data.json").write_text(json.dumps({"fit_results": {target: fit}}),
                                 encoding="utf-8")
    (d / "node.json").write_text(json.dumps({
        "data": {"outcomes": {target: outcome},
                 "parameters": {"model": params}},
        "patches": patches or []}), encoding="utf-8")
    (d / "quam_state").mkdir(exist_ok=True)
    (d / "quam_state" / "state.json").write_text("{}", encoding="utf-8")
    return d


def _arch(n_freq=60, n_sweep=41, amp=18.0, centre=30.0, seed=0, noise=0.25):
    rng = np.random.default_rng(seed)
    f = np.arange(n_freq)
    x = np.linspace(-1.0, 1.0, n_sweep)
    z = np.zeros((n_freq, n_sweep))
    for p in range(n_sweep):
        pos = centre + amp * (1.0 - x[p] ** 2) - amp / 2.0
        z[:, p] = 10.0 + 6.0 * (f / n_freq) - 6.0 * np.exp(
            -0.5 * ((f - pos) / 4.0) ** 2)
    z += rng.normal(0, noise, z.shape)
    return f.astype(float) * 1e5 + 5e9, np.linspace(-0.5, 0.5, n_sweep), z


def _noise_map(n_freq=60, n_sweep=41, seed=4):
    rng = np.random.default_rng(seed)
    z = 10.0 + rng.normal(0, 0.25, (n_freq, n_sweep))
    return (np.arange(n_freq).astype(float) * 1e5 + 5e9,
            np.linspace(-0.5, 0.5, n_sweep), z)


_FLUX_PARAMS = {"min_flux_offset_in_v": -0.5, "max_flux_offset_in_v": 0.5,
                "frequency_span_in_mhz": 6.0, "frequency_step_in_mhz": 0.1,
                "num_shots": 100}


class TestFutureBlindnessSurvivesGeneralisation:
    def test_the_same_guard_is_used(self, tmp_path):
        freq, sweep, z = _arch()
        views = [load_run(_cube_folder(
            tmp_path, f"#{i}_06_resonator_spectroscopy_vs_flux_{i}", "q1",
            freq, sweep, z, fit={"resonator_frequency": 5.003e9,
                                 "idle_offset": 0.0, "success": True},
            params=_FLUX_PARAMS)) for i in range(2)]
        s = Session("s", views)
        with pytest.raises(FutureBlindError):
            s.reveal(2)

    def test_replay_signature_takes_no_future(self):
        import inspect
        sig = inspect.signature(RB.replay)
        assert set(sig.parameters) == {"family", "session", "target", "pack",
                                       "signal_fn"}


class TestTheReaderOnlyVouchesForWhatItMeasured:
    def _one(self, tmp_path, cube, fit, name="#1_06_resonator_spectroscopy_vs_flux_1"):
        freq, sweep, z = cube
        v = load_run(_cube_folder(tmp_path, name, "q1", freq, sweep, z,
                                  fit=fit, params=_FLUX_PARAMS))
        return RB.replay(_FAM_FLUX, Session("s", [v]), "q1")

    def test_a_readable_arch_adopts_both_values(self, tmp_path):
        res = self._one(tmp_path, _arch(),
                        {"resonator_frequency": 5.0032e9, "idle_offset": 0.0,
                         "success": True})
        assert res.steps[0].action == "adopt"
        assert "resonator_frequency" in res.final_state
        assert "idle_offset" in res.final_state

    def test_an_empty_map_never_adopts(self, tmp_path):
        res = self._one(tmp_path, _noise_map(),
                        {"resonator_frequency": 5.003e9, "idle_offset": 0.0,
                         "success": True})
        assert res.steps[0].action != "adopt"
        assert res.final_state == {}
        assert res.unresolved

    def test_a_sweep_value_at_the_edge_is_refused_not_written(self, tmp_path):
        res = self._one(tmp_path, _arch(),
                        {"resonator_frequency": 5.0032e9,
                         "idle_offset": -0.5,        # the very edge swept
                         "success": True})
        assert "idle_offset" in res.steps[0].refused
        assert "idle_offset" not in res.final_state
        assert "resonator_frequency" in res.final_state, \
            "refusing the operating point must not throw away the frequency"

    def test_a_sweep_value_outside_the_swept_range_is_refused(self, tmp_path):
        res = self._one(tmp_path, _arch(),
                        {"resonator_frequency": 5.0032e9, "idle_offset": 3.0,
                         "success": True})
        assert "idle_offset" in res.steps[0].refused

    def test_every_signal_reaches_a_named_case(self, tmp_path):
        for cube in (_arch(), _noise_map()):
            res = self._one(tmp_path, cube,
                            {"resonator_frequency": 5.003e9, "success": True})
            st = res.steps[0]
            assert st.signal is not None
            assert st.case, f"signal {st.signal} reached no case"


class TestKnobMovesAreBoundedAndRelative:
    def test_a_flat_map_widens_the_sweep_around_its_own_centre(self):
        out = RB._moves(MC.CURVE_FLAT, _FLUX_PARAMS)
        assert out["min_flux_offset_in_v"] == pytest.approx(-1.5)
        assert out["max_flux_offset_in_v"] == pytest.approx(1.5)

    def test_the_same_case_gives_different_numbers_on_different_chips(self):
        narrow = dict(_FLUX_PARAMS, min_flux_offset_in_v=-0.05,
                      max_flux_offset_in_v=0.05)
        a = RB._moves(MC.CURVE_FLAT, _FLUX_PARAMS)
        b = RB._moves(MC.CURVE_FLAT, narrow)
        assert a != b, "a knob move fixed in absolute terms is a chip-specific rule"

    def test_a_paramless_run_invents_nothing(self):
        assert RB._moves(MC.CURVE_FLAT, {}) == {}
        assert RB._moves(MC.LINE_EMPTY, {}) == {}

    def test_recentring_is_declared_not_invented(self):
        out = RB._moves(MC.LINE_EDGE, {"frequency_span_in_mhz": 50.0})
        assert out == {"recenter_on_feature": True}, \
            "the reader must not invent a frequency to re-centre on"


class TestScoring:
    def _res(self, **kw):
        base = dict(family=_FAM_FLUX, session_id="s", target="q1", steps=[],
                    final_state={}, first_value={}, first_value_at=None,
                    runs_to_first_value=0, runs_consumed=3, unresolved=True,
                    revisions=[], unscoreable_proposals=0)
        base.update(kw)
        return RB.Result(**base)

    def test_a_matching_frequency_scores_match(self):
        r = self._res(final_state={"resonator_frequency": 5.0e9},
                      unresolved=False, runs_to_first_value=2)
        s = RB.score(r, {"termination": {"final_frequency": 5.0e9 + 5e5,
                                         "unresolved": False},
                         "ideal_length": 2, "actual_length": 5})
        assert s["frequency_verdict"] == "match"
        assert s["length_vs_ideal"] == 0
        assert s["runs_saved_vs_operator"] == 3

    def test_abstaining_where_the_key_says_unresolved_is_correct(self):
        s = RB.score(self._res(), {"termination": {"unresolved": True}})
        assert s["frequency_verdict"] == "correctly_abstained"

    def test_adopting_where_the_key_says_unresolved_is_an_error(self):
        r = self._res(final_state={"resonator_frequency": 5.0e9},
                      unresolved=False)
        s = RB.score(r, {"termination": {"unresolved": True}})
        assert s["frequency_verdict"] == "adopted_where_key_says_unresolved"

    def test_a_key_without_a_value_is_unscoreable_not_a_pass(self):
        r = self._res(final_state={"resonator_frequency": 5.0e9},
                      unresolved=False)
        s = RB.score(r, {"termination": {"unresolved": False}})
        assert s["frequency_verdict"] == "unscoreable_key_has_no_value"


class TestOnTheRealArchives:
    """Anchored on maps whose shape was confirmed by a human reader."""

    AS = Path(r"D:\work\dataset\AS_10TQ9TC\2026-08-10")
    CQT = Path(r"D:\work\Customer_Codes\CQT\data\2026-08-13")

    @pytest.mark.skipif(not AS.exists(), reason="AS archive absent")
    def test_a_textbook_flux_arch_is_read_as_one(self):
        folder = next(self.AS.glob("#308_06_resonator_spectroscopy_vs_flux_*"))
        sig = MC.signal_for(_FAM_FLUX, folder, "q7")
        assert sig.key in (MC.CURVE_ARCH, MC.CURVE_FULL_SWING), sig.reasons
        assert sig.measured["coverage"] >= 0.9

    @pytest.mark.skipif(not CQT.exists(), reason="CQT archive absent")
    def test_a_parabola_fitted_onto_noise_is_read_as_empty(self):
        """The node drew a parabola and marked two sweet spots on a map that
        carries no ridge at all — the exact shape the loop must refuse."""
        folder = next(self.CQT.glob("#13_09_qubit_spectroscopy_vs_flux_*"))
        sig = MC.signal_for("qubit_spectroscopy_vs_flux", folder, "q2",
                            fit={"success": True})
        assert sig.key == MC.CURVE_EMPTY, sig.reasons
        assert MC.FLAG_ACCEPTED_EMPTY in sig.flags


_KEYS = _ROOT / "tests" / "golden" / "calib_paths"
_ARCHIVES = {"CQT": Path(r"D:\work\Customer_Codes\CQT\data"),
             "AS_10TQ9TC": Path(r"D:\work\dataset\AS_10TQ9TC"),
             "SNU_1Q": Path(r"D:\work\dataset\SNU_1Q"),
             "IQCC_QOP37": Path(r"D:\work\dataset\IQCC_QOP37"),
             "KRISS_CR": Path(r"D:\work\dataset\KRISS_CR")}
_NODES = {"qubit_spectroscopy": "08_qubit_spectroscopy",
          "qubit_spectroscopy_vs_flux": "09_qubit_spectroscopy_vs_flux",
          "resonator_spectroscopy_vs_flux": "06_resonator_spectroscopy_vs_flux",
          "resonator_spectroscopy_vs_coupler_flux":
              "07_resonator_spectroscopy_vs_coupler_flux"}
_FAMS4 = tuple(_NODES)


def _score_all():
    """Replay exactly the runs each key was authored from.

    Not "every run of that family on that date": a key has no opinion about a
    run its author never looked at, and scoring against those let the system
    adopt from unexamined runs — worth a 12-point swing between two harnesses
    reading the same archive.
    """
    rows = []
    for fam in _FAMS4:
        base = _KEYS / fam
        if not base.exists():
            continue
        pack = knowledge.load_family(fam)
        for kf in sorted(base.rglob("2026-*.json")):
            doc = json.loads(kf.read_text(encoding="utf-8"))
            lab, root = kf.parent.name, _ARCHIVES.get(kf.parent.name)
            want = doc.get("runs") or []
            if root is None or not want:
                continue
            day = root / kf.stem
            if not day.exists():
                continue
            views = [v for v in (load_run(day / r) for r in want
                                 if (day / r).exists()) if v]
            if len(views) < len(want):
                continue                      # partial archive: skip, not guess
            session = Session(f"{fam}__{lab}__{kf.stem}", views)
            for qkey in doc.get("qubits") or []:
                q = qkey["qubit"]
                if not session.runs_for(q):
                    continue
                rows.append(RB.score(RB.replay(fam, session, q, pack=pack),
                                     qkey))
    return rows


@pytest.mark.skipif(not (_KEYS / "qubit_spectroscopy").exists()
                    or not _ARCHIVES["CQT"].exists(),
                    reason="answer keys or archives are not on this machine")
class TestTheFourFamilyBenchmarkDoesNotRegress:
    """Headline numbers of the docs/131 benchmark, pinned as a FLOOR.

    They are markedly weaker than the punch-out family's (46/50): one shared
    reader across four families, twelve hindsight-authored sessions on five
    chips. The bars sit below the measured values on purpose — raising them by
    tuning the reader against these very keys would be overfitting the
    benchmark, which is the one thing a benchmark cannot survive.
    """

    def test_it_scores_a_meaningful_number_of_targets(self):
        rows = _score_all()
        assert len(rows) >= 55, f"only {len(rows)} targets resolved"

    def test_more_than_half_of_the_outcomes_are_right(self):
        rows = _score_all()
        scoreable = [r for r in rows
                     if r["frequency_verdict"] != "unscoreable_key_has_no_value"]
        good = [r for r in scoreable if r["frequency_verdict"] in
                ("match", "correctly_abstained")]
        assert len(good) / len(scoreable) >= 0.60, \
            f"{len(good)}/{len(scoreable)} correct — measured 51/73 when written"

    def test_every_family_contributes(self):
        rows = _score_all()
        fams = {r["family"] for r in rows}
        assert fams == set(_FAMS4), sorted(set(_FAMS4) - fams)

    def test_it_is_not_slower_than_the_ideal_path(self):
        rows = _score_all()
        lens = [r["length_vs_ideal"] for r in rows
                if isinstance(r.get("length_vs_ideal"), int)]
        assert lens
        assert sum(1 for x in lens if x <= 0) / len(lens) >= 0.75

    def test_the_over_adoption_gap_is_recorded_not_forgotten(self):
        """The honest weakness: it adopts a value on some targets whose keys
        say nothing trustworthy existed. Pinned so it cannot quietly grow."""
        rows = _score_all()
        bad = [r for r in rows
               if r["frequency_verdict"] == "adopted_where_key_says_unresolved"]
        assert len(bad) / max(1, len(rows)) <= 0.25, \
            f"{len(bad)}/{len(rows)} over-adopted (measured 12/73)"
