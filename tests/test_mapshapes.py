"""The shared map reader and the four extra family manuals (docs/131).

What is pinned here is mostly a list of ways the obvious statistic was wrong,
each found by measuring the real corpus:

* a per-slice polynomial background cannot separate a broad band that TRACES
  an arch from the map's own transmission shape — subtracting the median
  across the sweep can, and the difference was 18% vs 100% of slices tracked
  on a textbook flux arch;
* a feature only the STATIC background can see did not move, which is how a
  flat line is told from an empty window instead of guessed at;
* raw sign changes in a noisy ridge are not turns: counting them called a
  single clean arch "multi-period" on 105 of 149 real maps;
* a second LINE is a resolved feature of comparable width, not any bump that
  clears the bar (126 of 217 real 1-D targets read as multi-feature before
  the width requirement);
* the labs do not share an axis convention, and the cube axis ORDER differs
  by family — assuming either silently transposes half the corpus.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from quam_state_manager.core.autofit import knowledge
from quam_state_manager.core.autofit import mapcases as MC
from quam_state_manager.core.autofit import mapshapes as MS

_ROOT = Path(__file__).resolve().parent.parent
_FAMS = ("qubit_spectroscopy", "qubit_spectroscopy_vs_flux",
         "resonator_spectroscopy_vs_flux",
         "resonator_spectroscopy_vs_coupler_flux",
         "resonator_spectroscopy",
         "qubit_spectroscopy_vs_power")


def _write(path: Path, target: str, freq, sweep, z, *, sweep_name="flux_bias",
           sweep_first=False):
    """Write a cube. ``sweep_first`` stores it (target, sweep, freq) — the
    order the resonator-flux families really use."""
    h5py = pytest.importorskip("h5py")
    with h5py.File(path, "w") as f:
        f["qubit"] = np.array([target.encode()])
        arr = z.T if sweep_first else z
        f["IQ_abs"] = arr[None, ...]
        f["full_freq"] = np.asarray(freq)[None, :]
        if sweep is not None:
            f[sweep_name] = np.asarray(sweep)


def _arch(n_freq=60, n_sweep=41, depth=6.0, width=4.0, amp=18.0, noise=0.25,
          slope=6.0, seed=0, centre=30.0):
    """A broad band tracing one arch on a strongly sloped background — the
    shape every real flux map has."""
    rng = np.random.default_rng(seed)
    f = np.arange(n_freq)
    z = np.zeros((n_freq, n_sweep))
    x = np.linspace(-1.0, 1.0, n_sweep)
    for p in range(n_sweep):
        pos = centre + amp * (1.0 - x[p] ** 2) - amp / 2.0
        base = 10.0 + slope * (f / n_freq)
        z[:, p] = base - depth * np.exp(-0.5 * ((f - pos) / width) ** 2)
    z += rng.normal(0, noise, z.shape)
    return f.astype(float) * 1e5 + 5e9, np.linspace(-0.5, 0.5, n_sweep), z


def _flat_band(n_freq=60, n_sweep=41, pos=30.0, depth=6.0, noise=0.25, seed=1):
    rng = np.random.default_rng(seed)
    f = np.arange(n_freq)
    z = np.zeros((n_freq, n_sweep))
    for p in range(n_sweep):
        z[:, p] = 10.0 + 6.0 * (f / n_freq) - depth * np.exp(
            -0.5 * ((f - pos) / 4.0) ** 2)
    z += rng.normal(0, noise, z.shape)
    return f.astype(float) * 1e5 + 5e9, np.linspace(-0.5, 0.5, n_sweep), z


class TestReadingACube:
    def test_both_axis_orders_read_identically(self, tmp_path):
        freq, sweep, z = _arch()
        a, b = tmp_path / "a.h5", tmp_path / "b.h5"
        (a.parent / "fa").mkdir(exist_ok=True)
        (a.parent / "fb").mkdir(exist_ok=True)
        _write(a.parent / "fa" / "ds_raw.h5", "q1", freq, sweep, z)
        _write(a.parent / "fb" / "ds_raw.h5", "q1", freq, sweep, z,
               sweep_first=True)
        ca = MS.read_cube(a.parent / "fa", "q1")
        cb = MS.read_cube(a.parent / "fb", "q1")
        assert ca is not None and cb is not None
        assert ca.z.shape == cb.z.shape == (freq.size, sweep.size)
        assert np.allclose(ca.z, cb.z)

    def test_a_missing_target_is_not_guessed(self, tmp_path):
        freq, sweep, z = _arch()
        d = tmp_path / "f"
        d.mkdir()
        _write(d / "ds_raw.h5", "q1", freq, sweep, z)
        assert MS.read_cube(d, "q9") is None

    def test_the_sweep_axis_is_sorted(self, tmp_path):
        freq, sweep, z = _arch()
        d = tmp_path / "f"
        d.mkdir()
        _write(d / "ds_raw.h5", "q1", freq, sweep[::-1], z[:, ::-1])
        c = MS.read_cube(d, "q1")
        assert np.all(np.diff(c.sweep) > 0)


class TestSignificanceCarriesTheLookElsewhereTerm:
    def test_the_bar_grows_with_trace_length(self):
        assert MS.z_bar(400) > MS.z_bar(64) > MS.Z_TRACE

    def test_pure_noise_does_not_clear_it(self):
        rng = np.random.default_rng(5)
        over = 0
        for _ in range(40):
            col = rng.normal(0, 1.0, 400)
            _j, d, _w, _o, _t = MS.slice_features(col)
            over += d >= MS.z_bar(400)
        assert over <= 4, f"{over}/40 noise traces read as a feature"


class TestTrackingARidge:
    def _folder(self, tmp_path, cube):
        d = tmp_path / "f"
        d.mkdir(exist_ok=True)
        _write(d / "ds_raw.h5", "q1", *cube)
        return d

    def test_a_broad_band_on_a_slope_is_tracked_end_to_end(self, tmp_path):
        cube = MS.read_cube(self._folder(tmp_path, _arch()), "q1")
        tr = MS.track_ridge(cube, sign=MS.orient(cube))
        assert tr.coverage >= 0.9, \
            "the moving background is what makes a broad band trackable"

    def test_the_arch_is_measured_as_an_arch(self, tmp_path):
        cube = MS.read_cube(self._folder(tmp_path, _arch()), "q1")
        sh = MS.shape_curve(cube, MS.track_ridge(cube, sign=MS.orient(cube)))
        assert sh.moves and sh.extremum_inside
        assert sh.turns == 1, f"one arch is one turn, got {sh.turns}"

    def test_a_stationary_band_is_flat_not_empty(self, tmp_path):
        cube = MS.read_cube(self._folder(tmp_path, _flat_band()), "q1")
        tr = MS.track_ridge(cube, sign=MS.orient(cube))
        sh = MS.shape_curve(cube, tr)
        assert tr.n_traceable > 0, "a flat line is not an empty window"
        assert not sh.moves
        assert tr.background == "static", \
            "a feature the moving background cannot see did not move"

    def test_noise_only_map_is_empty(self, tmp_path):
        rng = np.random.default_rng(7)
        freq = np.arange(60) * 1e5 + 5e9
        sweep = np.linspace(-0.5, 0.5, 41)
        z = 10.0 + rng.normal(0, 0.25, (60, 41))
        cube = MS.read_cube(self._folder(tmp_path, (freq, sweep, z)), "q1")
        tr = MS.track_ridge(cube, sign=MS.orient(cube))
        assert tr.coverage < 0.2


class TestOneDimensionalLines:
    def _line(self, tmp_path, n=400, depth=25.0, width=6.0, pos=200.0,
              noise=1.0, second=None, seed=3):
        rng = np.random.default_rng(seed)
        f = np.arange(n)
        y = 10.0 + 2.0 * (f / n) - depth * np.exp(-0.5 * ((f - pos) / width) ** 2)
        if second:
            p2, d2, w2 = second
            y -= d2 * np.exp(-0.5 * ((f - p2) / w2) ** 2)
        y += rng.normal(0, noise, n)
        d = tmp_path / "f"
        d.mkdir(exist_ok=True)
        _write(d / "ds_raw.h5", "q1", f.astype(float) * 1e5 + 4e9, None, y)
        return MS.read_cube(d, "q1")

    def test_a_single_broad_noisy_peak_is_not_multi_feature(self, tmp_path):
        cube = self._line(tmp_path, depth=14.0, width=9.0, noise=1.4)
        ln = MS.shape_line(cube, sign=MS.orient(cube))
        assert ln.n_significant == 1, \
            "noise spikes beside a broad line are not a second transition"

    def test_a_genuine_second_line_is_seen(self, tmp_path):
        cube = self._line(tmp_path, second=(300.0, 20.0, 6.0))
        ln = MS.shape_line(cube, sign=MS.orient(cube))
        assert ln.n_significant >= 2

    def test_the_feature_sign_is_measured_not_assumed(self, tmp_path):
        n = 400
        rng = np.random.default_rng(9)
        f = np.arange(n)
        y = 10.0 + 20.0 * np.exp(-0.5 * ((f - 200.0) / 6.0) ** 2) \
            + rng.normal(0, 1.0, n)          # an upward line
        d = tmp_path / "f"
        d.mkdir()
        _write(d / "ds_raw.h5", "q1", f.astype(float) * 1e5 + 4e9, None, y)
        cube = MS.read_cube(d, "q1")
        assert MS.orient(cube) == 1
        ln = MS.shape_line(cube, sign=1)
        assert ln.pos_px is not None and abs(ln.pos_px - 200) <= 3


class TestSignalsAndTheManualsVocabulary:
    def test_every_family_maps_every_signal_to_a_real_case(self):
        for fam in _FAMS:
            pack = knowledge.load_family(fam)
            assert pack is not None, fam
            ids = {c["id"] for c in pack["cases"]}
            smap = pack.get("signal_map") or {}
            assert smap, fam
            for signal, case_id in smap.items():
                assert case_id in ids, (fam, signal, case_id)

    def test_the_reader_never_emits_a_signal_the_maps_lack(self):
        """Every semantic key the reader can produce must be nameable by the
        family that uses that shape — otherwise a real map falls through to
        no case and the ledger says nothing."""
        line_keys = {MC.LINE_CLEAN, MC.LINE_EMPTY, MC.LINE_EDGE,
                     MC.LINE_MULTI, MC.LINE_WEAK, MC.LINE_SPLIT}
        curve_keys = {MC.CURVE_ARCH, MC.CURVE_FULL_SWING, MC.CURVE_MONOTONIC,
                      MC.CURVE_FLAT, MC.CURVE_PARTIAL, MC.CURVE_BROKEN,
                      MC.CURVE_MULTI, MC.CURVE_EMPTY}
        # a DRIVE-POWER second axis has its own vocabulary: what a power ridge
        # does along the axis is not a shape the flux words can describe
        power_keys = {MC.POWER_PLATEAU, MC.POWER_TWO_RIDGES, MC.POWER_NO_ANCHOR,
                      MC.POWER_TOP_ONLY, MC.POWER_EMPTY}
        for fam in _FAMS:
            smap = set((knowledge.load_family(fam).get("signal_map") or {}))
            if fam in ("qubit_spectroscopy", "resonator_spectroscopy"):
                want = line_keys
            elif fam.endswith("_vs_power"):
                want = power_keys
            else:
                want = curve_keys
            assert want <= smap, (fam, sorted(want - smap))

    # Blind agreement is NOT uniform across families, and flattening it into
    # one bar would hide the most useful thing it measures. Four families
    # reach 0.8; the 1-D readout family reaches 0.58, and every disagreement
    # there is the clean-notch / Fano-asymmetric boundary — a real judgement
    # call about one lineshape rather than a reading error. Pinned per family
    # so the difference stays visible instead of being averaged away.
    _BLIND_FLOOR = {"resonator_spectroscopy": 0.55}
    # this family's pack reports agreement two ways; the exact-match number is
    # the one the floor applies to

    def test_packs_carry_their_blind_verification_and_lab_span(self):
        for fam in _FAMS:
            pack = knowledge.load_family(fam)
            bv = pack.get("blind_verification") or {}
            assert bv.get("n", 0) >= 5, fam
            assert bv["agreed"] / bv["n"] >= self._BLIND_FLOOR.get(fam, 0.7), \
                (fam, bv)
            assert len(pack.get("labs") or []) >= 2, \
                f"{fam}: a manual taught from one lab is a manual about one chip"

    def test_the_family_with_weak_agreement_says_so_in_its_pack(self):
        """A number that low has to be explained where a reader meets it, not
        only in a document they may never open."""
        bv = knowledge.load_family("resonator_spectroscopy")["blind_verification"]
        assert bv["agreed"] / bv["n"] < 0.7
        assert len(bv.get("note") or "") > 60

    def test_the_lint_is_load_bearing_not_decorative(self):
        """Four packs pass it clean; the fifth does not, and that is the point
        — one freshly authored case named an absolute linewidth and was
        dropped at load rather than shipped. A lint that has never refused
        anything is not evidence that nothing needed refusing."""
        dropped = {fam: knowledge.load_family(fam)["lint_dropped"]
                   for fam in _FAMS + ("resonator_spectroscopy_vs_power",)}
        assert sum(len(v) for v in dropped.values()) >= 1,             "the lint has stopped catching anything — check it still runs"
        for fam, d in dropped.items():
            assert len(d) <= 2, (fam, d)
        for fam in _FAMS + ("resonator_spectroscopy_vs_power",):
            pack = knowledge.load_family(fam)
            for c in pack["cases"]:
                assert knowledge._lint_violation(c["geometry"]) is None
                assert knowledge._lint_violation(c["prescription"]) is None


class TestExemplarsForEveryFamily:
    def test_each_family_renders_exemplars_from_more_than_one_lab(self):
        for fam in _FAMS + ("resonator_spectroscopy_vs_power",):
            idx_path = (knowledge.pack_path(fam).parent / "exemplars"
                        / "index.json")
            assert idx_path.exists(), fam
            idx = json.loads(idx_path.read_text(encoding="utf-8"))
            assert len(idx["rendered"]) >= 40, fam
            assert len({r["chip"] for r in idx["rendered"]}) >= 2, fam
            # a refusal is the renderer working; a flood of them is not
            assert len(idx["missing"]) <= 0.1 * len(idx["rendered"]), (
                fam, len(idx["missing"]))

    def test_the_flux_families_render_sweep_on_the_x_axis(self):
        """The labs plot flux horizontally and frequency vertically, the
        opposite of the punch-out family. A judge trained on one orientation
        misreads the other."""
        for fam in ("qubit_spectroscopy_vs_flux",
                    "resonator_spectroscopy_vs_flux",
                    "resonator_spectroscopy_vs_coupler_flux"):
            idx = json.loads((knowledge.pack_path(fam).parent / "exemplars"
                              / "index.json").read_text(encoding="utf-8"))
            two_d = [r for r in idx["rendered"] if "sweep_on_x" in r]
            assert two_d and all(r["sweep_on_x"] for r in two_d), fam
        idx = json.loads((knowledge.pack_path("resonator_spectroscopy_vs_power")
                          .parent / "exemplars" / "index.json")
                         .read_text(encoding="utf-8"))
        two_d = [r for r in idx["rendered"] if "sweep_on_x" in r]
        assert two_d and not any(r["sweep_on_x"] for r in two_d)


class TestJudgingARunAgainstItsOwnIntent:
    """Two checks that need no invented constant, because the node states the
    thing they compare against."""

    def _line_folder(self, tmp_path, *, n=400, depth=25.0, width=6.0,
                     pos=200.0, companion=None, seed=3):
        rng = np.random.default_rng(seed)
        f = np.arange(n)
        y = 10.0 + 2.0 * (f / n) - depth * np.exp(-0.5 * ((f - pos) / width) ** 2)
        if companion:
            cp, ch, cw = companion
            y += ch * np.exp(-0.5 * ((f - cp) / cw) ** 2)
        y += rng.normal(0, 1.0, n)
        d = tmp_path / "f"
        d.mkdir(exist_ok=True)
        _write(d / "ds_raw.h5", "q1", f.astype(float) * 1e5 + 4e9, None, y)
        return d, f.astype(float) * 1e5 + 4e9

    def test_a_line_far_wider_than_the_node_asked_for_is_flagged(self, tmp_path):
        d, _ = self._line_folder(tmp_path)
        sig = MC.signal_for("qubit_spectroscopy", d, "q1",
                            fit={"frequency": 4.02e9, "fwhm": 30e6,
                                 "success": True},
                            params={"target_peak_width": 3e6})
        assert MC.FLAG_OVER_BROADENED in sig.flags
        assert sig.measured["fwhm_over_target"] == 10.0

    def test_a_line_at_the_asked_width_is_not_flagged(self, tmp_path):
        d, _ = self._line_folder(tmp_path)
        sig = MC.signal_for("qubit_spectroscopy", d, "q1",
                            fit={"frequency": 4.02e9, "fwhm": 3.4e6,
                                 "success": True},
                            params={"target_peak_width": 3e6})
        assert MC.FLAG_OVER_BROADENED not in sig.flags

    def test_without_a_declared_target_nothing_is_asserted(self, tmp_path):
        d, _ = self._line_folder(tmp_path)
        sig = MC.signal_for("qubit_spectroscopy", d, "q1",
                            fit={"frequency": 4.02e9, "fwhm": 30e6},
                            params={})
        assert MC.FLAG_OVER_BROADENED not in sig.flags, \
            "the yardstick is the node's own parameter, never a constant"

    def test_the_resonator_sign_comes_from_physics_not_measurement(self, tmp_path):
        """A readout resonator in |I+iQ| is a notch. On a Fano trace the
        companion peak is the taller thing, and measuring the sign puts the
        reader on it."""
        d, freq = self._line_folder(tmp_path, depth=20.0,
                                    companion=(170.0, 40.0, 6.0))
        sig = MC.signal_for("resonator_spectroscopy", d, "q1",
                            fit={"frequency": float(freq[200])})
        assert sig.measured["feature"] == "dip"
        assert sig.measured["sign_source"] == "physics"
        assert sig.key == MC.LINE_FANO, sig.reasons

    def test_a_fit_on_the_companion_is_named_and_corrected(self, tmp_path):
        d, freq = self._line_folder(tmp_path, depth=20.0,
                                    companion=(170.0, 40.0, 6.0))
        sig = MC.signal_for("resonator_spectroscopy", d, "q1",
                            fit={"frequency": float(freq[170])})
        assert MC.FLAG_FIT_ON_WRONG_SIDE in sig.flags
        assert abs(sig.corrected["frequency"] - freq[200]) < 3e5

    def test_a_rotated_projection_keeps_measuring_its_sign(self, tmp_path):
        """The companion check belongs only to the families whose direction is
        fixed by physics; on a rotated projection a nearby opposite excursion
        is ordinary background, and calling it Fano made clean qubit lines
        unnameable."""
        d, freq = self._line_folder(tmp_path, depth=20.0,
                                    companion=(170.0, 40.0, 6.0))
        sig = MC.signal_for("qubit_spectroscopy", d, "q1",
                            fit={"frequency": float(freq[200])})
        assert sig.measured["sign_source"] == "measured"
        assert sig.key != MC.LINE_FANO

    def test_the_rf_frequency_axis_spelling_is_read(self, tmp_path):
        """One lab names the absolute frequency axis RF_frequency; without it
        that lab's entire 1-D readout set read as unreadable."""
        h5py = pytest.importorskip("h5py")
        d = tmp_path / "g"
        d.mkdir()
        y = 10.0 - 20.0 * np.exp(-0.5 * ((np.arange(300) - 150.0) / 5.0) ** 2)
        with h5py.File(d / "ds_raw.h5", "w") as f:
            f["qubit"] = np.array([b"qA1"])
            f["IQ_abs"] = y[None, :]
            f["RF_frequency"] = np.arange(300).astype(float) * 1e5 + 7e9
        cube = MS.read_cube(d, "qA1")
        assert cube is not None and cube.n_freq == 300


# ---------------------------------------------------------------------------
# the drive-power axis (docs/133)
# ---------------------------------------------------------------------------

def _write_power(path: Path, target: str, freq, power, z):
    """A power cube, stored the way the real ones are: (qubit, freq, power)
    with the value on the ROTATED projection."""
    h5py = pytest.importorskip("h5py")
    with h5py.File(path, "w") as f:
        f["qubit"] = np.array([target.encode()])
        f["I_rot"] = z[None, ...]
        f["full_freq"] = np.asarray(freq)[None, :]
        f["power"] = np.asarray(power)


def _power_map(n_freq=300, n_sweep=50, onset=18, bend=38, pos=150.0,
               height=9.0, width=3.0, noise=0.3, drift=14.0, broaden=4.0,
               partner_px=None, partner_from=None, seed=3):
    """A textbook qubit-vs-power map.

    Nothing below the onset; a narrow stationary line just above it; then the
    line drifts and broadens as the drive saturates the transition. A partner
    line can be planted at a fixed offset, appearing only at high drive —
    which is how the two-photon transition actually shows up.
    """
    rng = np.random.default_rng(seed)
    f = np.arange(n_freq, dtype=float)
    z = np.zeros((n_freq, n_sweep))
    for p in range(n_sweep):
        col = np.zeros(n_freq)
        if p >= onset:
            grow = 0.0 if p < bend else (p - bend) / max(1, n_sweep - 1 - bend)
            c = pos + drift * grow
            w = width * (1.0 + broaden * grow)
            col += height * np.exp(-0.5 * ((f - c) / w) ** 2)
            if partner_px is not None and p >= (partner_from or bend):
                col += height * 1.1 * np.exp(
                    -0.5 * ((f - (pos + partner_px)) / width) ** 2)
        z[:, p] = col
    z += rng.normal(0, noise, z.shape)
    return f * 1e6 + 4.5e9, np.linspace(-55.0, 0.0, n_sweep), z


class TestThePowerAxis:
    """A power ridge is not a flux arch: the answer is the frequency of the
    stationary low-power stretch, and the brightest part is the wrong place."""

    def test_it_finds_the_onset_and_the_stationary_stretch(self, tmp_path):
        freq, power, z = _power_map()
        _write_power(tmp_path / "ds_raw.h5", "q0", freq, power, z)
        cube = MS.read_cube(tmp_path, "q0", value_vars=("I_rot",))
        ps = MS.shape_power(cube, MS.track_ridge(cube, sign=+1), sign=+1)
        assert ps.block_lo is not None and 14 <= ps.block_lo <= 22
        assert not ps.onset_at_floor and not ps.top_only
        assert ps.plateau_len >= MC.MIN_PLATEAU_SLICES
        assert abs(ps.plateau_freq - (4.5e9 + 150e6)) <= 3e6

    def test_the_line_broadens_and_drifts_by_the_top(self, tmp_path):
        freq, power, z = _power_map()
        _write_power(tmp_path / "ds_raw.h5", "q0", freq, power, z)
        cube = MS.read_cube(tmp_path, "q0", value_vars=("I_rot",))
        ps = MS.shape_power(cube, MS.track_ridge(cube, sign=+1), sign=+1)
        assert ps.width_ratio is not None and ps.width_ratio > 1.5
        assert ps.drift_hz is not None and ps.drift_hz > 5e6

    def test_a_feature_only_at_the_very_top_is_not_a_plateau(self, tmp_path):
        freq, power, z = _power_map(onset=47, bend=47)
        _write_power(tmp_path / "ds_raw.h5", "q0", freq, power, z)
        cube = MS.read_cube(tmp_path, "q0", value_vars=("I_rot",))
        ps = MS.shape_power(cube, MS.track_ridge(cube, sign=+1), sign=+1)
        assert ps.top_only

    def test_present_at_the_lowest_power_means_the_onset_was_never_bracketed(
            self, tmp_path):
        freq, power, z = _power_map(onset=0, bend=30)
        _write_power(tmp_path / "ds_raw.h5", "q0", freq, power, z)
        cube = MS.read_cube(tmp_path, "q0", value_vars=("I_rot",))
        ps = MS.shape_power(cube, MS.track_ridge(cube, sign=+1), sign=+1)
        assert ps.onset_at_floor

    def test_an_empty_map_yields_nothing_rather_than_a_frequency(self, tmp_path):
        rng = np.random.default_rng(0)
        freq = np.arange(300, dtype=float) * 1e6 + 4.5e9
        z = rng.normal(0, 0.3, (300, 50))
        _write_power(tmp_path / "ds_raw.h5", "q0", freq,
                     np.linspace(-55.0, 0.0, 50), z)
        cube = MS.read_cube(tmp_path, "q0", value_vars=("I_rot",))
        ps = MS.shape_power(cube, MS.track_ridge(cube, sign=+1), sign=+1)
        assert ps.plateau_freq is None or ps.plateau_len < MC.MIN_PLATEAU_SLICES


class TestTheTwoPhotonPartner:
    """The 0->2 transition sits half an anharmonicity BELOW the fundamental
    and grows faster with drive, so at high power it can be the strongest
    thing in the map."""

    def _sig(self, tmp_path, partner_px, anh_hz, record=None):
        freq, power, z = _power_map(partner_px=partner_px)
        _write_power(tmp_path / "ds_raw.h5", "q0", freq, power, z)
        fit = {"anharmonicity_stored": anh_hz,
               "frequency": record if record is not None else 4.5e9 + 150e6}
        return MC.signal_for("qubit_spectroscopy_vs_power", tmp_path, "q0",
                             fit=fit)

    def test_a_partner_below_confirms_the_identification(self, tmp_path):
        sig = self._sig(tmp_path, -100, 200e6)
        assert sig.key == MC.POWER_TWO_RIDGES
        assert MC.FLAG_TWO_PHOTON_PRIMARY not in sig.flags
        assert abs(sig.corrected["frequency"] - (4.5e9 + 150e6)) <= 3e6

    def test_a_partner_above_means_the_tracked_line_is_the_two_photon(
            self, tmp_path):
        sig = self._sig(tmp_path, +100, 200e6)
        assert MC.FLAG_TWO_PHOTON_PRIMARY in sig.flags
        # the value handed on is the PARTNER, not the line that was brightest
        assert sig.corrected["frequency"] > 4.5e9 + 200e6

    def test_the_offset_is_read_against_the_runs_own_anharmonicity(
            self, tmp_path):
        # same map, a run that reports a different anharmonicity: the partner
        # no longer sits at half of it, so no two-photon claim is made
        sig = self._sig(tmp_path, +100, 400e6)
        assert MC.FLAG_TWO_PHOTON_PRIMARY not in sig.flags

    def test_a_record_at_the_untouched_centre_of_an_empty_sweep_is_flagged(
            self, tmp_path):
        rng = np.random.default_rng(1)
        freq = np.arange(300, dtype=float) * 1e6 + 4.5e9
        z = rng.normal(0, 0.3, (300, 50))
        _write_power(tmp_path / "ds_raw.h5", "q0", freq,
                     np.linspace(-55.0, 0.0, 50), z)
        centre = 0.5 * (freq.min() + freq.max())
        sig = MC.signal_for("qubit_spectroscopy_vs_power", tmp_path, "q0",
                            fit={"frequency": centre, "success": True})
        assert MC.FLAG_RECORD_AT_SWEEP_CENTRE in sig.flags
        assert "frequency" not in sig.corrected


class TestTheJointFamilysOwnPack:
    """The power pack is the first that names a companion, because its cases
    are only half the story without the 1-D manual beside it."""

    def _pack(self):
        return knowledge.load_family("qubit_spectroscopy_vs_power")

    def test_it_names_its_companion_both_ways(self):
        assert self._pack()["companion_family"] == "qubit_spectroscopy"
        other = knowledge.load_family("qubit_spectroscopy")
        assert other["companion_family"] == "qubit_spectroscopy_vs_power"

    def test_the_joint_cases_live_in_both_manuals(self):
        a = {c["id"] for c in self._pack()["cases"]
             if c.get("kind") == "joint_case"}
        b = {c["id"] for c in knowledge.load_family("qubit_spectroscopy")["cases"]
             if c.get("kind") == "joint_case"}
        assert a and a == b

    def test_the_joint_cases_change_no_score(self):
        """They are documentation for a reader, not dispatch: a case only
        changes a replay if the family's signal_map names it."""
        for fam in ("qubit_spectroscopy", "qubit_spectroscopy_vs_power"):
            pack = knowledge.load_family(fam)
            named = set((pack.get("signal_map") or {}).values())
            joint = {c["id"] for c in pack["cases"]
                     if c.get("kind") == "joint_case"}
            assert not (named & joint), fam

    def test_it_records_what_the_nodes_own_flags_are_worth(self):
        lim = self._pack()["measured_limits"]
        assert "182 of 182" in lim["node_success_is_uninformative"]
        assert lim["two_photon_prevalence"]

    def test_the_two_photon_offset_is_never_a_number_in_the_manual(self):
        """Clause B by hand as well as by lint: the offset must be expressed
        against the anharmonicity the RUN reports."""
        import re
        pack = self._pack()
        text = " ".join(f"{c.get('geometry','')} {c.get('prescription','')}"
                        for c in pack["cases"])
        assert not re.search(r"\d+(\.\d+)?\s*(MHz|GHz|dBm)", text, re.I)
        assert "anharmonicity" in text.lower()
