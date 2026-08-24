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
        # "profile" is NOT future information: it is the chip's declared
        # design facts (extras.sm_profile), written by a human before any
        # run exists and never derived from later runs (docs/135)
        assert set(sig.parameters) == {"family", "session", "target", "pack",
                                       "signal_fn", "profile"}


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


class TestClosureWalk:
    """docs/137: the session-end closure pass. Three mechanisms, each pinned
    with the shape that motivated it, plus the dormancy guarantee."""

    _CFAM = "resonator_spectroscopy_vs_coupler_flux"
    _CNODE = "07_resonator_spectroscopy_vs_coupler_flux"

    def test_a_nan_is_not_a_value(self, tmp_path):
        # the reader vouches the SHAPE, the fit failed -> record carries NaN;
        # vouching it made six sessions "resolved" with a non-answer
        freq, sweep, z = _arch()
        v = load_run(_cube_folder(
            tmp_path, "#1_06_resonator_spectroscopy_vs_flux_1", "q1",
            freq, sweep, z,
            fit={"resonator_frequency": float("nan"),
                 "idle_offset": float("nan"), "success": True},
            params=_FLUX_PARAMS))
        res = RB.replay(_FAM_FLUX, Session("s", [v]), "q1")
        assert res.final_state == {}
        assert res.unresolved

    def _coupler_views(self, tmp_path, specs):
        # each run gets its OWN map (arch centred per spec) with a fit at
        # that map's apex — a fit disagreeing with its map is refused by the
        # reader before any closure logic runs, so the disagreement between
        # RUNS must be real, not injected into the record
        views = []
        for i, (centre, mq) in enumerate(specs):
            freq, sweep, z = _arch(centre=centre, seed=i)
            apex = (centre + 9.0) * 1e5 + 5e9      # amp/2 = 9 rows
            params = dict(_FLUX_PARAMS, measure_qubit=mq)
            views.append(load_run(_cube_folder(
                tmp_path, f"#{i}_{self._CNODE}_{i}", "q1-2", freq, sweep, z,
                fit={"resonator_frequency": apex, "idle_offset": 0.0,
                     "success": True},
                params=params)))
        return views

    def test_a_minority_identity_read_never_overwrites_the_majority(
            self, tmp_path):
        # measure_qubit=control is a DIFFERENT resonator: its frequency must
        # not ride the recency ratchet over the target-resonator reads
        views = self._coupler_views(tmp_path, [
            (30, "target"), (30, "target"), (6, "control")])
        res = RB.replay(self._CFAM, Session("s", views), "q1-2")
        assert res.final_state["resonator_frequency"] == pytest.approx(
            39.0 * 1e5 + 5e9, rel=2e-4)
        assert any("identity" in n for n in res.closure_notes)

    def test_contested_never_vouches_when_cl_cluster_ships(self, tmp_path):
        # two same-setting readings disagreeing beyond tolerance: one
        # observation each, no independent confirmation -> no value
        views = self._coupler_views(tmp_path, [(30, "target"), (6, "target")])
        pack = dict(knowledge.load_family(self._CFAM) or {})
        pack["closure_rules"] = list(pack.get("closure_rules") or []) + [
            {"id": "CL-CLUSTER"}]
        res = RB.replay(self._CFAM, Session("s", views), "q1-2", pack=pack)
        assert "resonator_frequency" not in res.final_state
        assert res.unresolved
        assert any("CONTESTED" in n for n in res.closure_notes)

    def test_without_the_rule_the_ratchet_is_byte_identical(self, tmp_path):
        # knowledge stays strictly optional: the shipped coupler pack has no
        # CL-CLUSTER, so the same disagreeing pair keeps the last reading
        views = self._coupler_views(tmp_path, [(30, "target"), (6, "target")])
        res = RB.replay(self._CFAM, Session("s", views), "q1-2")
        assert res.final_state["resonator_frequency"] == pytest.approx(
            15.0 * 1e5 + 5e9, rel=2e-4)
        assert not res.unresolved


class TestQdacAliases:
    """docs/139: the QDAC-bias flux nodes are the SAME measurement through a
    different bias source (params + fit_results schema byte-identical on the
    real archive) — without the alias the chain walk treats a QDAC-biased
    qubit's flux maps as an unknown family and abstains on everything."""

    def test_qdac_flux_nodes_map_to_their_families(self):
        assert RB.pack_family_for(
            "#153_02e_resonator_spectroscopy_vs_flux_qdac_150413")             == "resonator_spectroscopy_vs_flux"
        assert RB.pack_family_for(
            "#437_03c_qubit_spectroscopy_vs_flux_qdac_165721")             == "qubit_spectroscopy_vs_flux"


class TestAbcRetag:
    """docs/136: every step of the 73 targets carries a step_class tag
    derived DETERMINISTICALLY from the key's own correct_decision (rule
    smabc/v1) — reproducible, no fresh adjudication. Re-deriving the rule
    here must reproduce the files exactly; a hand edit that breaks the
    derivation is a schema violation, not a new opinion."""

    @staticmethod
    def _rule(decision, index):
        if decision in ("reject_and_retune", "reconfirm"):
            return "B"
        if decision == "reject_and_stop":
            return "C"
        if decision == "adopt":
            return "A" if index == 0 else "C"
        raise ValueError(decision)

    def test_every_step_is_tagged_and_matches_the_rule(self):
        n = 0
        for fam in _FAMS4:
            for kf in sorted((_KEYS / fam).rglob("2026-*.json")):
                doc = json.loads(kf.read_text(encoding="utf-8"))
                assert doc.get("abc_tagging", {}).get("rule") == "smabc/v1", kf
                for q in doc.get("qubits") or []:
                    for i, st in enumerate(q.get("ideal_path") or []):
                        n += 1
                        assert st.get("step_class") == self._rule(
                            st["correct_decision"], i), (kf.name, q["qubit"], i)
        assert n == 170

    def test_tag_census_is_pinned(self):
        from collections import Counter
        c = Counter()
        for fam in _FAMS4:
            for kf in sorted((_KEYS / fam).rglob("2026-*.json")):
                doc = json.loads(kf.read_text(encoding="utf-8"))
                for q in doc.get("qubits") or []:
                    for st in q.get("ideal_path") or []:
                        c[st["step_class"]] += 1
        assert dict(c) == {"A": 21, "B": 92, "C": 57}


@pytest.mark.skipif(not (_KEYS / "qubit_spectroscopy").exists()
                    or not _ARCHIVES["CQT"].exists(),
                    reason="answer keys or archives are not on this machine")
class TestTwoTierBenchmark:
    """docs/136 floors for the doctrine measurement. Bars sit BELOW the
    measured values on purpose (the docs/131 no-overfit argument): measured
    2026-08-24 — B-direction 64/92, premature first adoption 27/73 (13 of
    the 20 wrong outcomes first adopted at a B-tagged run — concluding
    before closure IS the dominant failure channel), conclusions at
    licensed closure points 42/56."""

    def test_b_direction_agreement_floor(self):
        a = t = 0
        for r in _score_all():
            s = r.get("b_direction_agreement")
            if s and s != "n/a":
                x, y = s.split("/")
                a += int(x)
                t += int(y)
        assert t >= 80, f"only {t} B-steps scored"
        assert a / t >= 0.60, f"B-direction {a}/{t}"

    def test_premature_first_adoption_cannot_quietly_grow(self):
        """First value taken at a B-tagged run = concluded before closure —
        the dangerous direction. Capped so it cannot silently grow."""
        rows = _score_all()
        prem = 0
        for r in rows:
            ta = r.get("terminated_at")
            if not ta:
                continue
            tok = ta.split("_")[0]
            licensed = set(r.get("a_points") or []) | set(r.get("c_points") or [])
            if tok not in licensed:
                prem += 1
        assert prem / len(rows) <= 0.45, f"{prem}/{len(rows)} premature"

    def test_licensed_closure_conclusions_floor(self):
        good = tot = 0
        for r in _score_all():
            if r.get("terminal_class") in ("A", "C"):
                tot += 1
                good += r["frequency_verdict"] in ("match",
                                                   "correctly_abstained")
        assert tot >= 50, f"only {tot} licensed-closure targets"
        assert good / tot >= 0.65, f"licensed conclusions {good}/{tot}"


# ---------------------------------------------------------------------------
# reading the two qubit-spectroscopy node types together (docs/133)
# ---------------------------------------------------------------------------

class TestTheFamilyOfARun:
    """One session can mix node types, so the family is resolved per run."""

    def test_it_names_both_qubit_spectroscopy_node_types(self):
        assert RB.pack_family_for(
            "#724_08b_qubit_spectroscopy_vs_power_165701") \
            == "qubit_spectroscopy_vs_power"
        assert RB.pack_family_for("#693_08_qubit_spectroscopy_162029") \
            == "qubit_spectroscopy"

    def test_the_readout_spellings_all_land_on_one_pack(self):
        for name in ("#1_03_resonator_spectroscopy_single_010101",
                     "#2_02_resonator_spectroscopy_wide_pyloop_010101",
                     "#3_1Q_03_resonator_spectroscopy_wide_python_loop_010101"):
            assert RB.pack_family_for(name) == "resonator_spectroscopy", name


class TestTheTwoPhotonGuard:
    """The trap this round exists for: at too much drive a 1-D fit lands on
    the 0->2 transition half an anharmonicity below the fundamental, and
    nothing inside that single run can tell the two apart."""

    @staticmethod
    def _meta(d: Path, fit: dict, params: dict | None = None) -> None:
        (d / "data.json").write_text(json.dumps({"fit_results": fit}),
                                     encoding="utf-8")
        (d / "node.json").write_text(json.dumps({
            "data": {"outcomes": {q: "successful" for q in fit},
                     "parameters": {"model": params or {}}},
            "patches": []}), encoding="utf-8")
        (d / "quam_state").mkdir(exist_ok=True)
        (d / "quam_state" / "state.json").write_text("{}", encoding="utf-8")

    def _session(self, tmp_path, claim_hz, held_hz, anh_hz=200e6):
        """A power run that establishes the frequency and the anharmonicity,
        then a 1-D run claiming ``claim_hz``."""
        import numpy as np
        h5py = pytest.importorskip("h5py")
        views = []
        # 1) the power run: a clean stationary line at held_hz
        pw = tmp_path / "#1_08b_qubit_spectroscopy_vs_power_000001"
        pw.mkdir()
        n_f, n_p = 300, 50
        f = np.arange(n_f, dtype=float) * 1e6 + (held_hz - 150e6)
        rng = np.random.default_rng(5)
        z = rng.normal(0, 0.3, (n_f, n_p))
        for p in range(14, n_p):
            z[:, p] += 9.0 * np.exp(-0.5 * ((np.arange(n_f) - 150) / 3.0) ** 2)
        with h5py.File(pw / "ds_raw.h5", "w") as h:
            h["qubit"] = np.array([b"q0"])
            h["I_rot"] = z[None, ...]
            h["full_freq"] = f[None, :]
            h["power"] = np.linspace(-55.0, 0.0, n_p)
        self._meta(pw, {"q0": {"frequency": held_hz,
                               "anharmonicity_stored": anh_hz,
                               "optimal_power": -20.0, "success": True}},
                   {"min_power_dbm": -55, "max_power_dbm": 0,
                    "num_power_points": 50})
        views.append(load_run(pw))
        # 2) the 1-D run: one clean line, at whatever it claims
        one = tmp_path / "#2_08_qubit_spectroscopy_000002"
        one.mkdir()
        n = 400
        f1 = np.arange(n, dtype=float) * 1e5 + (claim_hz - 20e6)
        z1 = rng.normal(0, 0.3, n)
        z1 += 9.0 * np.exp(-0.5 * ((np.arange(n) - 200) / 4.0) ** 2)
        with h5py.File(one / "ds_raw.h5", "w") as h:
            h["qubit"] = np.array([b"q0"])
            h["IQ_abs"] = z1[None, :]
            h["full_freq"] = f1[None, :]
        self._meta(one, {"q0": {"frequency": claim_hz, "success": True,
                                "peak_snr": 20.0, "r2": 0.95}},
                   {"frequency_span_in_mhz": 40.0,
                    "frequency_step_in_mhz": 0.1})
        views.append(load_run(one))
        return Session("s", [v for v in views if v])

    def test_a_claim_half_an_anharmonicity_low_is_refused(self, tmp_path):
        held = 4.65e9
        s = self._session(tmp_path, claim_hz=held - 100e6, held_hz=held)
        res = RB.replay(RB.pack_family_for, s, "q0")
        last = res.steps[-1]
        assert last.action != "adopt"
        assert any("two-photon" in r for r in last.reasons)
        assert abs(res.final_state["frequency"] - held) <= 3e6

    def test_a_claim_at_the_established_frequency_is_adopted(self, tmp_path):
        held = 4.65e9
        s = self._session(tmp_path, claim_hz=held + 0.4e6, held_hz=held)
        res = RB.replay(RB.pack_family_for, s, "q0")
        assert res.steps[-1].action == "adopt"

    def test_a_claim_far_from_both_is_left_alone(self, tmp_path):
        held = 4.65e9
        s = self._session(tmp_path, claim_hz=held - 40e6, held_hz=held)
        res = RB.replay(RB.pack_family_for, s, "q0")
        assert res.steps[-1].action == "adopt"

    def test_the_guard_needs_the_runs_own_anharmonicity(self, tmp_path):
        # no anharmonicity reported anywhere: the guard cannot fire, and it
        # invents no number of its own
        held = 4.65e9
        s = self._session(tmp_path, claim_hz=held - 100e6, held_hz=held,
                          anh_hz=0.0)
        res = RB.replay(RB.pack_family_for, s, "q0")
        assert res.steps[-1].action == "adopt"


class TestAMeasuredValueOutranksTheRecord:
    """Which value to use, not merely whether to accept one — but only where
    the reader measured it across many slices."""

    def test_the_relaxation_is_scoped_to_multi_slice_shapes(self):
        # every member is a POWER shape whose value came from many swept
        # powers; no 1-D shape may join, because there the corrected value is
        # one peak of one trace
        assert RB._MEASURED_ACROSS_SLICES == {MC.POWER_PLATEAU,
                                              MC.POWER_TWO_RIDGES,
                                              MC.POWER_LADDER}
        assert all(k.startswith("power_") for k in RB._MEASURED_ACROSS_SLICES)
        assert MC.LINE_CLEAN not in RB._MEASURED_ACROSS_SLICES
        assert MC.LINE_FANO not in RB._MEASURED_ACROSS_SLICES


class TestPhysicalDrivePower:
    """Reading the amplitude without the port gets the SIGN of a change wrong."""

    def _run(self, tmp_path, amp, fsp, factor=None):
        d = tmp_path / "2026-08-20" / "#1_08_qubit_spectroscopy_000001"
        (d / "quam_state").mkdir(parents=True)
        state = {
            "qubits": {"q0": {"xy": {
                "opx_output": "#/wiring/qubits/q0/xy/opx_output",
                "operations": {"saturation": {"amplitude": amp}}}}},
            "ports": {"mw_outputs": {"con1": {"1": {"5": {
                "full_scale_power_dbm": fsp}}}}},
        }
        (d / "quam_state" / "state.json").write_text(json.dumps(state),
                                                     encoding="utf-8")
        (d / "quam_state" / "wiring.json").write_text(json.dumps({"wiring": {
            "qubits": {"q0": {"xy": {
                "opx_output": "#/ports/mw_outputs/con1/1/5"}}}}}),
            encoding="utf-8")
        params = {} if factor is None else {"operation_amplitude_factor": factor}
        (d / "data.json").write_text(json.dumps({"fit_results": {}}),
                                     encoding="utf-8")
        (d / "node.json").write_text(json.dumps({
            "data": {"outcomes": {}, "parameters": {"model": params}},
            "patches": []}), encoding="utf-8")
        return load_run(d)

    def test_it_follows_the_pointer_chain_across_both_files(self, tmp_path):
        v = self._run(tmp_path, 0.03981071705534972, 11)
        assert abs(RB.drive_power_dbm(v, "q0") - (-17.0)) < 0.02

    def test_the_amplitude_factor_counts(self, tmp_path):
        v = self._run(tmp_path, 0.1, 0, factor=2.0)
        # 20*log10(0.2) = -13.98
        assert abs(RB.drive_power_dbm(v, "q0") - (-13.979)) < 0.02

    def test_amplitude_up_but_power_down(self, tmp_path):
        """The real case: the stored amplitude rose by half again while the
        port's full-scale power was written down further, so the drive at the
        qubit FELL. Amplitude alone reports the opposite."""
        a = self._run(tmp_path / "a", 0.10, 11)
        b = self._run(tmp_path / "b", 0.15, 4)
        assert 0.15 > 0.10                                   # amplitude rose
        assert RB.drive_power_dbm(b, "q0") < RB.drive_power_dbm(a, "q0")

    def test_a_run_with_no_port_reports_nothing(self, tmp_path):
        d = tmp_path / "2026-08-20" / "#2_08_qubit_spectroscopy_000002"
        (d / "quam_state").mkdir(parents=True)
        (d / "quam_state" / "state.json").write_text(
            json.dumps({"qubits": {"q0": {"xy": {"operations": {}}}}}),
            encoding="utf-8")
        (d / "data.json").write_text(json.dumps({"fit_results": {}}),
                                     encoding="utf-8")
        (d / "node.json").write_text(json.dumps({
            "data": {"outcomes": {}, "parameters": {"model": {}}},
            "patches": []}), encoding="utf-8")
        assert RB.drive_power_dbm(load_run(d), "q0") is None


class TestHowASessionEnds:
    """Recency or agreement. A ratchet IS recency-following, so which of the
    two a session ends on is a real choice and the caller makes it."""

    def test_the_largest_cluster_wins_not_the_last_value(self):
        assert RB._largest_cluster([4.30e9, 4.3001e9, 4.2999e9, 4.40e9]) \
            == pytest.approx(4.30e9, abs=2e5)

    def test_a_nan_does_not_empty_the_cluster_it_seeds(self):
        # a NaN does not compare equal to itself, so leaving one in used to
        # divide by an empty cluster
        assert RB._largest_cluster([float("nan"), 4.30e9, 4.3001e9]) \
            == pytest.approx(4.30e9, abs=2e5)
        assert RB._largest_cluster([float("nan")]) is None
        assert RB._largest_cluster([]) is None

    def test_ties_still_go_to_the_later_value(self):
        # two clusters of one: recency decides where agreement does not
        assert RB._largest_cluster([4.30e9, 4.40e9]) == pytest.approx(4.40e9)

    def test_recency_is_the_default_so_published_numbers_do_not_move(self):
        import inspect
        sig = inspect.signature(RB.score)
        assert sig.parameters["rule"].default == "recency"

    def test_the_two_rules_can_disagree_on_the_same_result(self):
        res = RB.Result(
            family="qubit_spectroscopy", session_id="s", target="q0", steps=[],
            final_state={"frequency": 4.40e9}, first_value={},
            first_value_at="#1", runs_to_first_value=1, runs_consumed=3,
            unresolved=False, revisions=[], unscoreable_proposals=0,
            adopted_values={"frequency": [4.30e9, 4.3001e9, 4.40e9]},
            consensus_state={"frequency": 4.30005e9})
        key = {"termination": {"final_frequency": 4.30e9}}
        assert RB.score(res, key)["frequency_verdict"] == "wrong_value"
        assert RB.score(res, key, rule="agreement")["frequency_verdict"] == "match"


# ---------------------------------------------------------------------------
# the joint qubit-spectroscopy benchmark (docs/133)
# ---------------------------------------------------------------------------

_JOINT = _KEYS / "qubit_spectroscopy_vs_power"


def _score_joint(rule: str = "recency"):
    """Replay both qubit-spectroscopy node types as ONE session.

    The keys record run NUMBERS rather than folder names because a joint
    session mixes node types and the number is what identifies a run across
    both.
    """
    import re as _re
    rows = []
    if not _JOINT.exists():
        return rows
    for kf in sorted(_JOINT.rglob("2026-*.json")):
        doc = json.loads(kf.read_text(encoding="utf-8"))
        lab, root = kf.parent.name, _ARCHIVES.get(kf.parent.name)
        want = doc.get("runs") or []
        if root is None or not want:
            continue
        day = root / kf.stem
        if not day.exists():
            continue
        by_no = {}
        for d in day.iterdir():
            m = _re.match(r"^#(\d+)_", d.name)
            if m and d.is_dir():
                by_no.setdefault(int(m.group(1)), d)
        folders = [by_no[int(n)] for n in want if int(n) in by_no]
        if len(folders) < len(want):
            continue                          # partial archive: skip, not guess
        views = [v for v in (load_run(f) for f in folders) if v]
        if not views:
            continue
        session = Session(doc.get("session_id") or f"{lab}__{kf.stem}", views)
        for qkey in doc.get("qubits") or []:
            q = qkey["qubit"]
            if not session.runs_for(q):
                continue
            rows.append(RB.score(
                RB.replay(RB.pack_family_for, session, q), qkey, rule=rule))
    return rows


@pytest.mark.skipif(not _JOINT.exists() or not _ARCHIVES["CQT"].exists(),
                    reason="joint answer keys or archives are not on this machine")
class TestTheJointBenchmark:
    """Both node types replayed as one session, scored against keys written
    with hindsight. Bars sit below the measured values on purpose."""

    def test_it_scores_a_meaningful_number_of_targets(self):
        assert len(_score_joint()) >= 12

    def test_both_node_types_are_actually_read(self):
        rows = _score_joint()
        fams = {f for r in rows for f in r["family"].split("+")}
        assert "qubit_spectroscopy" in fams
        assert "qubit_spectroscopy_vs_power" in fams

    def test_most_outcomes_are_right(self):
        rows = _score_joint()
        able = [r for r in rows
                if r["frequency_verdict"] != "unscoreable_key_has_no_value"]
        good = [r for r in able if r["frequency_verdict"] in
                ("match", "correctly_abstained")]
        assert able
        assert len(good) / len(able) >= 0.50, \
            f"{len(good)}/{len(able)} correct"

    def test_the_end_rule_is_reported_so_a_number_names_its_own_rule(self):
        rows = _score_joint(rule="agreement")
        assert rows and all(r["end_rule"] == "agreement" for r in rows)
