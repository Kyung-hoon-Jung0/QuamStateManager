"""Repetition ("shot") axes are averaged, never plotted (docs/82).

Reported from the field: opening ``ds_raw.h5`` of a
``readout_power_optimization`` run plotted I against **n_runs** — the shot
index — instead of against ``amp_prefactor``, the thing the node actually
sweeps. ``_default_view`` ordered sweeps by SIZE, and the shot axis (2,000) is
always the biggest dim in the file.

A shot index carries no physics: nothing distinguishes shot 7 from shot 6, so
its ordering is not a quantity anything can be plotted against. The fix mirrors
what the other ~47 node types already do — they average on the OPX and only
ever save the average — so the default view of a single-shot node now matches
the default view of every other node.

Two invariants pull against each other and both are pinned here:
  * a repetition axis is averaged away whenever there is anything else to plot
    against, and
  * it is KEPT when there is not — ``iq_blobs`` is nothing BUT per-shot points,
    and averaging it away would leave an empty plot.

Shapes below are the real archive's, measured: readout_power_optimization
``I(state=2, qubit=2, n_runs=2000, amp_prefactor=10)``, iq_blobs
``Ig(qubit, n_runs)``, two-qubit RB ``state(qubit_pair, circuit_depth, average,
repeat)``.
"""
from __future__ import annotations

import json

import numpy as np
import pytest

h5py = pytest.importorskip("h5py")

from quam_state_manager.core import ndview


def _write(path, dims, arrays):
    """netCDF4-style file: ``dims`` = [(name, coord | None)], ``arrays`` =
    {var: ndarray} sharing those dims in order."""
    with h5py.File(path, "w") as f:
        scales = []
        for name, coord in dims:
            if coord is None:
                scales.append(None)
                continue
            c = np.array([s.encode() for s in coord]) if isinstance(coord[0], str) \
                else np.asarray(coord)
            d = f.create_dataset(name, data=c)
            d.attrs["CLASS"] = np.bytes_("DIMENSION_SCALE")
            scales.append(d)
        for var, data in arrays.items():
            ds = f.create_dataset(var, data=data)
            for axis, sc in enumerate(scales):
                if sc is not None:
                    ds.dims[axis].attach_scale(sc)
    return path


def _readout_power(tmp_path, n_runs=200, n_amp=10, var="I"):
    """The reported shape: (state, qubit, n_runs, amp_prefactor)."""
    # Value model with a KNOWN mean: shot k contributes k, so the mean over
    # n_runs is (n_runs-1)/2 plus the per-(state, qubit, amp) offset.
    shots = np.arange(n_runs, dtype=np.float64)
    base = np.arange(2 * 2 * n_amp, dtype=np.float64).reshape(2, 2, 1, n_amp) * 1000.0
    data = base + shots.reshape(1, 1, n_runs, 1)
    return _write(tmp_path / "ds_raw.h5",
                  [("state", [0, 1]), ("qubit", ["q0", "q1"]),
                   ("n_runs", np.arange(n_runs)),
                   ("amp_prefactor", np.linspace(0.1, 1.0, n_amp))],
                  {var: data}), base, shots


class TestTheReportedCase:
    def test_the_real_sweep_is_the_x_axis(self, tmp_path):
        p, _, _ = _readout_power(tmp_path)
        v = ndview.build_cube(p, "I")["default_view"]
        assert v["x"] == "amp_prefactor", "the shot index is not a quantity"
        assert v["entity"] == "qubit"
        assert v["overlay"] == ["state"]

    def test_the_shot_axis_is_classified_as_a_repeat(self, tmp_path):
        p, _, _ = _readout_power(tmp_path)
        # Built WITHOUT the shot dim, so classification is checked at the source.
        assert ndview._classify_dim("n_runs", 2000, np.arange(2000), True) == "shot"
        assert ndview.build_cube(p, "I")["default_view"]["reduced"] == [
            {"name": "n_runs", "size": 200, "op": "mean"}]

    def test_the_reduced_dim_leaves_the_cube(self, tmp_path):
        """It must not survive in ``dims`` — the client flattens ``data``
        against exactly those sizes."""
        p, _, _ = _readout_power(tmp_path)
        cube = ndview.build_cube(p, "I")
        assert [d["name"] for d in cube["dims"]] == \
            ["state", "qubit", "amp_prefactor"]
        shape = [d["size"] for d in cube["dims"]]
        assert int(np.prod(shape)) == np.asarray(cube["data"]).size

    def test_the_value_shown_is_the_mean_of_every_shot(self, tmp_path):
        """Not shot #0, not a sample — the average, like every averaged node."""
        p, base, shots = _readout_power(tmp_path)
        cube = ndview.build_cube(p, "I")
        expected = base[:, :, 0, :] + shots.mean()
        np.testing.assert_allclose(np.asarray(cube["data"]), expected)

    def test_the_payload_shrinks_by_the_repeat_count(self, tmp_path):
        p, _, _ = _readout_power(tmp_path, n_runs=200)
        cube = ndview.build_cube(p, "I")
        assert cube["budget"]["full"] == 2 * 2 * 200 * 10
        assert cube["budget"]["shipped"] == 2 * 2 * 10


class TestWhenTheShotsAreThePoint:
    def test_iq_blobs_keeps_its_shot_axis(self, tmp_path):
        """``Ig(qubit, n_runs)`` — averaging it away would leave nothing."""
        p = _write(tmp_path / "ds_iq_blobs.h5",
                   [("qubit", ["q0", "q1"]), ("n_runs", np.arange(500))],
                   {"Ig": np.random.default_rng(0).normal(size=(2, 500))})
        v = ndview.build_cube(p, "Ig")["default_view"]
        assert v["x"] == "n_runs" and v["reduced"] == []
        assert v["entity"] == "qubit"

    def test_a_kept_shot_axis_still_decimates(self, tmp_path):
        """It stops being a 'sweep' — the decimation gate must still take it,
        or a 6,000-shot blob would ship in full."""
        n = 6_000
        p = _write(tmp_path / "ds_iq_blobs.h5",
                   [("qubit", ["q0"]), ("n_runs", np.arange(n))],
                   {"Ig": np.random.default_rng(0).normal(size=(1, n)) * 1.0})
        # Force the element budget down so this fixture stays small but the
        # gate is still exercised end to end.
        cube = ndview._build_cube_uncached(p, "Ig", element_budget=3_000)
        assert cube["ok"]
        shot = next(d for d in cube["dims"] if d["name"] == "n_runs")
        assert shot["decimated"] and shot["size"] < n

    def test_a_short_real_sweep_is_reclaimed_before_a_shot_axis(self, tmp_path):
        """A sweep of 3 points goes to the overlay bucket, which would leave the
        shot index as the only x candidate. A real quantity — even a short one —
        beats a shot index, so it is pulled back onto the axis."""
        p = _write(tmp_path / "ds_raw.h5",
                   [("state", [0, 1]), ("amp_prefactor", [0.1, 0.5, 0.9]),
                    ("n_runs", np.arange(80))],
                   {"I": np.ones((2, 3, 80))})
        v = ndview.build_cube(p, "I")["default_view"]
        assert v["x"] == "amp_prefactor"           # the bigger of the two
        assert v["overlay"] == ["state"]           # the other stays overlaid
        assert [r["name"] for r in v["reduced"]] == ["n_runs"]

    def test_only_the_largest_is_promoted_when_all_dims_are_repeats(self, tmp_path):
        p = _write(tmp_path / "ds_raw.h5",
                   [("n_runs", np.arange(50)), ("repeat", np.arange(6))],
                   {"I": np.ones((50, 6))})
        v = ndview.build_cube(p, "I")["default_view"]
        assert v["x"] == "n_runs"
        assert [r["name"] for r in v["reduced"]] == ["repeat"]
        assert v["y"] is None and v["sliders"] == {}


class TestTheOtherRepeatNames:
    def test_rb_averages_both_of_its_repeat_axes(self, tmp_path):
        """Real two-qubit RB: (qubit_pair, circuit_depth, average, repeat).
        The size order used to put ``average``=100 on x and the depth on y —
        a heatmap of a decay curve."""
        p = _write(tmp_path / "ds_raw.h5",
                   [("qubit_pair", ["qA1-qA2"]),
                    ("circuit_depth", [1, 2, 4, 8, 16, 32, 64]),
                    ("average", np.arange(100)), ("repeat", np.arange(5))],
                   {"state": np.zeros((1, 7, 100, 5))})
        v = ndview.build_cube(p, "state")["default_view"]
        assert v["x"] == "circuit_depth" and v["y"] is None
        assert sorted(r["name"] for r in v["reduced"]) == ["average", "repeat"]

    def test_a_small_repeat_axis_never_becomes_an_overlay(self, tmp_path):
        """``repeat``=4 is under the overlay cap — four indistinguishable
        curves is not a legend."""
        p = _write(tmp_path / "ds_raw.h5",
                   [("detuning", np.linspace(-1e6, 1e6, 20)), ("repeat", np.arange(4))],
                   {"I": np.ones((20, 4))})
        v = ndview.build_cube(p, "I")["default_view"]
        assert v["x"] == "detuning" and v["overlay"] == []
        assert [r["name"] for r in v["reduced"]] == ["repeat"]

    @pytest.mark.parametrize("name", ["sequence", "sequence_index", "nb_of_sequences"])
    def test_distinct_realizations_are_not_repeats(self, tmp_path, name):
        """An RB random sequence and an all_xy gate pair name DIFFERENT
        circuits — index 7 is not index 6 repeated, so they stay plottable."""
        p = _write(tmp_path / f"ds_{name}.h5",
                   [("qubit", ["q0"]), (name, np.arange(21))],
                   {"state": np.ones((1, 21))})
        v = ndview.build_cube(p, "state")["default_view"]
        assert v["x"] == name and v["reduced"] == []


class TestTheAverageIsSafe:
    def test_all_nan_repeats_average_to_null_not_a_crash(self, tmp_path):
        """A failed acquisition is a real (if unhappy) run: nanmean over an
        all-NaN set is NaN, which must ship as JSON null like any other NaN."""
        data = np.ones((6, 10))
        data[1, :] = np.nan
        p = _write(tmp_path / "ds_raw.h5",
                   [("detuning", np.linspace(-1e6, 1e6, 6)), ("n_runs", np.arange(10))],
                   {"I": data})
        cube = ndview.build_cube(p, "I")
        assert cube["ok"] and cube["data"][1] is None
        assert "NaN" not in json.dumps(cube)

    def test_partial_nan_repeats_average_over_the_rest(self, tmp_path):
        data = np.full((6, 4), 10.0)
        data[0, :2] = np.nan                       # half the shots lost
        p = _write(tmp_path / "ds_raw.h5",
                   [("detuning", np.linspace(-1e6, 1e6, 6)), ("n_runs", np.arange(4))],
                   {"I": data})
        assert ndview.build_cube(p, "I")["data"] == [10.0] * 6

    def test_iq_siblings_reduce_identically(self, tmp_path):
        """|IQ| and phase are computed client-side from the two cubes — they
        must come out of the reduction on the same grid."""
        rng = np.random.default_rng(3)
        dims = [("detuning", np.linspace(-1e6, 1e6, 30)), ("n_runs", np.arange(40))]
        p = _write(tmp_path / "ds_raw.h5", dims,
                   {"I": rng.normal(size=(30, 40)), "Q": rng.normal(size=(30, 40)) * 3})
        ci, cq = ndview.build_cube(p, "I"), ndview.build_cube(p, "Q")
        assert [(d["name"], d["size"]) for d in ci["dims"]] == \
               [(d["name"], d["size"]) for d in cq["dims"]]
        assert ci["default_view"]["reduced"] == cq["default_view"]["reduced"]
        assert ci["iq_partner"] == "Q"

    def test_iq_siblings_stay_in_lockstep_when_decimation_also_fires(self, tmp_path):
        """The partner array is read at the FILE's shape inside the decimation
        branch — it must be averaged over the same axes before it is used as a
        decimation representative, or the pair walks out of step and |IQ|
        silently disappears."""
        rng = np.random.default_rng(4)
        n_sweep, n_shot = 8_000, 3
        dims = [("detuning", np.linspace(-1e6, 1e6, n_sweep)), ("n_runs", np.arange(n_shot))]
        p = _write(tmp_path / "ds_raw.h5", dims,
                   {"I": rng.normal(size=(n_sweep, n_shot)),
                    "Q": rng.normal(size=(n_sweep, n_shot)) * 3})
        ci = ndview._build_cube_uncached(p, "I", element_budget=3_000)
        cq = ndview._build_cube_uncached(p, "Q", element_budget=3_000)
        assert ci["ok"] and cq["ok"]
        assert any(d["decimated"] for d in ci["dims"])
        assert (ci["kept"] or {}) == (cq["kept"] or {}), \
            "reduced siblings must still ship identical kept sets"
