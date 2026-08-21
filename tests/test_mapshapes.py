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
         "resonator_spectroscopy_vs_coupler_flux")


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
        for fam in _FAMS:
            smap = set((knowledge.load_family(fam).get("signal_map") or {}))
            want = line_keys if fam == "qubit_spectroscopy" else curve_keys
            assert want <= smap, (fam, sorted(want - smap))

    def test_packs_carry_their_blind_verification_and_lab_span(self):
        for fam in _FAMS:
            pack = knowledge.load_family(fam)
            bv = pack.get("blind_verification") or {}
            assert bv.get("n", 0) >= 5, fam
            assert bv["agreed"] / bv["n"] >= 0.7, (fam, bv)
            assert len(pack.get("labs") or []) >= 2, \
                f"{fam}: a manual taught from one lab is a manual about one chip"

    def test_every_pack_survives_the_clause_b_lint(self):
        for fam in _FAMS + ("resonator_spectroscopy_vs_power",):
            pack = knowledge.load_family(fam)
            assert pack["lint_dropped"] == [], (fam, pack["lint_dropped"])


class TestExemplarsForEveryFamily:
    def test_each_family_renders_exemplars_from_more_than_one_lab(self):
        for fam in _FAMS + ("resonator_spectroscopy_vs_power",):
            idx_path = (knowledge.pack_path(fam).parent / "exemplars"
                        / "index.json")
            assert idx_path.exists(), fam
            idx = json.loads(idx_path.read_text(encoding="utf-8"))
            assert idx["missing"] == [], (fam, idx["missing"][:3])
            assert len(idx["rendered"]) >= 40, fam
            assert len({r["chip"] for r in idx["rendered"]}) >= 2, fam

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
