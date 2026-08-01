"""r13: NetCDF-classic support for the Raw Data tab (core/ndview.py).

Since 2026-07-29 every IQCC run's ``ds_*.h5`` is NetCDF-classic (CDF-2) bytes —
xarray's scipy fallback engine writing under the misleading ``.h5`` name — and
ndview (h5py-only, a gap docs/48 recorded as deliberate) answered "Cannot open
the data file: … file signature not found" for all of them. ndview now routes
file access through a reader adapter (``_open_reader``): b"CDF" magic → scipy
``netcdf_file``; everything else → h5py exactly as before (garbage keeps the
canonical h5py OSError; HDF5 userblock files are never misrouted).

Fixtures mirror the REAL archive shapes verified on the IQCC runs: char-matrix
qubit names, int dim coords with units, aux ``coordinates`` variables
(full_freq/current), and — crucially — MIXED per-variable dim order (the real
2-D files store phase as (qubit, flux_bias, detuning) while I/Q are
(qubit, detuning, flux_bias)).
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from quam_state_manager.core import ndview

nc = pytest.importorskip("scipy.io.netcdf", reason="scipy required")


def _chars(names):
    n = max(len(s) for s in names)
    out = np.zeros((len(names), n), dtype="S1")
    for i, s in enumerate(names):
        row = np.frombuffer(s.encode(), dtype="S1")
        out[i, :len(row)] = row
    return out


def _write_cdf_1d(path, n_det=400):
    """1-qubit spectroscopy shape (real #517): I/Q/IQ_abs/phase over detuning,
    full_freq aux coord, char qubit name, int detuning coord with units."""
    f = nc.netcdf_file(str(path), "w")
    try:
        f.createDimension("qubit", 1)
        f.createDimension("string3", 3)
        f.createDimension("detuning", n_det)
        q = f.createVariable("qubit", "c", ("qubit", "string3"))
        q[:] = _chars(["qD2"])
        det = f.createVariable("detuning", "i", ("detuning",))
        det[:] = np.linspace(-50e6, 50e6, n_det).astype(np.int32)
        det.units = b"Hz"
        det.long_name = b"readout frequency"
        rng = np.random.default_rng(7)
        for name in ("I", "Q", "IQ_abs", "phase"):
            v = f.createVariable(name, "d", ("qubit", "detuning"))
            arr = rng.normal(0.0, 1e-4, (1, n_det))
            arr[0, 3] = np.nan                       # real files carry NaN
            v[:] = arr
            v.coordinates = b"full_freq"
        ff = f.createVariable("full_freq", "d", ("qubit", "detuning"))
        ff[:] = 5.0e9 + np.linspace(-50e6, 50e6, n_det).reshape(1, n_det)
        ff.units = b"Hz"
        t0 = f.createVariable("t_offset", "d", ())
        t0.data[()] = 28.5      # 0-d scalar var (assignValue is broken for 0-d)
    finally:
        f.close()


def _write_cdf_2d(path, flux_coord=True):
    """3-qubit spectroscopy-vs-flux shape (real #419): I/Q/IQ_abs are
    (qubit, detuning, flux_bias) but phase is (qubit, flux_bias, detuning) —
    mixed dim order inside ONE file. current/attenuated_current ride as aux
    coords on flux_bias; ``flux_coord=False`` drops the flux_bias coord var
    (h5py-placeholder equivalent → classified synthetic)."""
    f = nc.netcdf_file(str(path), "w")
    try:
        f.createDimension("qubit", 3)
        f.createDimension("string3", 3)
        f.createDimension("detuning", 21)
        f.createDimension("flux_bias", 11)
        q = f.createVariable("qubit", "c", ("qubit", "string3"))
        q[:] = _chars(["qB1", "qB2", "qB3"])
        det = f.createVariable("detuning", "i", ("detuning",))
        det[:] = np.linspace(-10e6, 10e6, 21).astype(np.int32)
        if flux_coord:
            fb = f.createVariable("flux_bias", "d", ("flux_bias",))
            fb[:] = np.linspace(-0.1, 0.1, 11)
            fb.units = b"V"
        cur = f.createVariable("current", "d", ("flux_bias",))
        cur[:] = np.linspace(-1e-3, 1e-3, 11)
        acur = f.createVariable("attenuated_current", "d", ("flux_bias",))
        acur[:] = np.linspace(-1e-4, 1e-4, 11)
        rng = np.random.default_rng(11)
        for name in ("I", "Q", "IQ_abs"):
            v = f.createVariable(name, "d", ("qubit", "detuning", "flux_bias"))
            v[:] = rng.normal(0.0, 1e-4, (3, 21, 11))
            v.coordinates = b"attenuated_current current full_freq"
        ph = f.createVariable("phase", "d", ("qubit", "flux_bias", "detuning"))
        ph[:] = rng.normal(0.0, 1.0, (3, 11, 21))
        ph.coordinates = b"attenuated_current current full_freq"
        ff = f.createVariable("full_freq", "d", ("qubit", "detuning"))
        ff[:] = 5e9 + np.tile(np.linspace(-10e6, 10e6, 21), (3, 1))
    finally:
        f.close()


class TestProbe:
    def test_1d_cards_match_real_archive_semantics(self, tmp_path):
        p = tmp_path / "ds_raw.h5"
        _write_cdf_1d(p)
        probe = ndview.probe_file(p)
        assert probe["ok"] is True
        by_name = {v["name"]: v for v in probe["vars"]}
        # Data vars + the aux coord + the 0-d scalar; dim coords (qubit,
        # detuning) and the string3 helper dim never surface as cards.
        assert set(by_name) == {"I", "Q", "IQ_abs", "phase", "full_freq",
                                "t_offset"}
        assert by_name["full_freq"]["is_coord_var"] is True
        assert by_name["I"]["is_coord_var"] is False
        # Data vars sort before coord vars (the shell auto-opens the first).
        names = [v["name"] for v in probe["vars"]]
        assert names.index("I") < names.index("full_freq")
        # dtype normalized to NATIVE spelling (scipy reads big-endian ">f8").
        assert by_name["I"]["dtype"] == "float64"
        assert by_name["I"]["dims"] == ["qubit", "detuning"]
        assert by_name["I"]["shape"] == [1, 400]

    def test_2d_mixed_dim_order_preserved(self, tmp_path):
        p = tmp_path / "ds_raw.h5"
        _write_cdf_2d(p)
        probe = ndview.probe_file(p)
        by_name = {v["name"]: v for v in probe["vars"]}
        assert by_name["I"]["dims"] == ["qubit", "detuning", "flux_bias"]
        assert by_name["phase"]["dims"] == ["qubit", "flux_bias", "detuning"]
        assert by_name["current"]["is_coord_var"] is True
        assert by_name["attenuated_current"]["is_coord_var"] is True


class TestCube:
    def test_1d_cube_entity_join_units_and_nan(self, tmp_path):
        p = tmp_path / "ds_raw.h5"
        _write_cdf_1d(p)
        cube = ndview.build_cube(p, "IQ_abs")
        assert cube["ok"] is True
        dims = {d["name"]: d for d in cube["dims"]}
        # Char-matrix qubit names joined into the entity coord.
        assert dims["qubit"]["kind"] == "entity"
        assert dims["qubit"]["coord"] == ["qD2"]
        assert dims["detuning"]["kind"] == "sweep"
        assert dims["detuning"]["units"] == "Hz"
        assert isinstance(dims["detuning"]["coord"][0], int)   # int coord stays int
        # NaN sanitized (JSON.parse-safe) — the injected NaN became null.
        s = json.dumps(cube)
        assert "NaN" not in s and "Infinity" not in s
        assert cube["data"][0][3] is None
        assert cube["default_view"]["x"] == "detuning"

    def test_iq_partner_detected(self, tmp_path):
        p = tmp_path / "ds_raw.h5"
        _write_cdf_1d(p)
        assert ndview.build_cube(p, "I")["iq_partner"] == "Q"
        assert ndview.build_cube(p, "IQ_abs")["iq_partner"] is None

    def test_2d_per_variable_dim_order_and_entity_off_axes(self, tmp_path):
        p = tmp_path / "ds_raw.h5"
        _write_cdf_2d(p)
        ci = ndview.build_cube(p, "I")
        cp = ndview.build_cube(p, "phase")
        assert [d["name"] for d in ci["dims"]] == ["qubit", "detuning", "flux_bias"]
        assert [d["name"] for d in cp["dims"]] == ["qubit", "flux_bias", "detuning"]
        for cube in (ci, cp):
            view = cube["default_view"]
            assert view["entity"] == "qubit"
            assert view["entity"] not in (view["x"], view["y"])
            np_data = np.asarray(
                [[[x for x in row] for row in q] for q in cube["data"]], dtype=object)
            assert np_data.shape == tuple(d["size"] for d in cube["dims"])

    @pytest.mark.parametrize("flux_coord,kind", [(True, "sweep"), (False, "synthetic")])
    def test_dim_without_coord_var_is_synthetic(self, tmp_path, flux_coord, kind):
        p = tmp_path / "ds_raw.h5"
        _write_cdf_2d(p, flux_coord=flux_coord)
        cube = ndview.build_cube(p, "I")
        dims = {d["name"]: d for d in cube["dims"]}
        assert dims["flux_bias"]["kind"] == kind

    def test_scalar_variable_parity(self, tmp_path):
        # h5py's ds[()] unwraps 0-d to a numpy scalar; the NC reader must
        # match ([()] after asarray) or the scalar cube ships a STRING.
        p = tmp_path / "ds_raw.h5"
        _write_cdf_1d(p)
        cube = ndview.build_cube(p, "t_offset")
        assert cube["ok"] is True
        assert cube["scalar"] == 28.5
        assert isinstance(cube["scalar"], float)

    def test_iq_lockstep_decimation_on_cdf(self, tmp_path):
        # Decimated I and Q must keep IDENTICAL source indices (the client
        # refuses |IQ|/phase otherwise) — the pair-representative logic is
        # format-agnostic but only if the NC read path feeds it equally.
        # Mirrors test_ndview's HDF5 twin: genuinely over-budget data (20
        # qubits x 50k > _CUBE_ELEMENT_BUDGET), each sibling with its own dip.
        p = tmp_path / "ds_raw.h5"
        n = 50_000
        yi = np.ones(n); dip_i = n // 3; yi[dip_i] = -100.0
        yq = np.ones(n); dip_q = (2 * n) // 3; yq[dip_q] = -100.0
        f = nc.netcdf_file(str(p), "w")
        try:
            f.createDimension("qubit", 20)
            f.createDimension("string3", 3)
            f.createDimension("detuning", n)
            q = f.createVariable("qubit", "c", ("qubit", "string3"))
            q[:] = _chars([f"q{i:02d}" for i in range(20)])
            det = f.createVariable("detuning", "d", ("detuning",))
            det[:] = np.linspace(0, 1, n)
            vi = f.createVariable("I", "d", ("qubit", "detuning"))
            vi[:] = np.tile(yi, (20, 1))
            vq = f.createVariable("Q", "d", ("qubit", "detuning"))
            vq[:] = np.tile(yq, (20, 1))
        finally:
            f.close()
        ndview._cache_clear()
        ci = ndview.build_cube(p, "I")
        cq = ndview.build_cube(p, "Q")
        assert ci["ok"] and cq["ok"]
        di = {d["name"]: d for d in ci["dims"]}
        assert di["detuning"]["decimated"] is True
        ki = ci["kept"]["detuning"]
        assert ki == cq["kept"]["detuning"], "IQ siblings must ship identical kept sets"
        assert dip_i in ki and dip_q in ki           # both features survive in both

    def test_oversized_variable_classified(self, tmp_path, monkeypatch):
        p = tmp_path / "ds_raw.h5"
        _write_cdf_1d(p)
        ndview._cache_clear()
        monkeypatch.setattr(ndview, "_MAX_RAW_ELEMENTS", 100)
        # The open-time st_size pre-guard fires first (mmap=False is an eager
        # whole-file read — a per-variable guard would be too late), so the
        # whole file classifies as unopenable, never an exception.
        probe = ndview.probe_file(p)
        assert probe["ok"] is False
        assert probe["error"].startswith("Cannot open the data file")
        cube = ndview.build_cube(p, "I")
        assert cube["ok"] is False and "error" in cube


class TestCorruptAndCache:
    def test_garbage_keeps_canonical_h5py_message(self, tmp_path):
        # Non-CDF garbage routes to h5py exactly as before this feature.
        p = tmp_path / "ds_raw.h5"
        p.write_bytes(b"this is not a data file at all")
        probe = ndview.probe_file(p)
        assert probe["ok"] is False
        assert probe["error"].startswith("Cannot open the data file")

    def test_truncated_cdf_classified(self, tmp_path):
        p = tmp_path / "ds_raw.h5"
        p.write_bytes(b"CDF\x02" + b"\x00" * 16)     # scipy raises Type/ValueError
        probe = ndview.probe_file(p)
        assert probe["ok"] is False
        assert "vars" in probe and probe["vars"] == []
        raw, meta = ndview.build_cube_bytes(p, "I")
        assert meta["ok"] is False
        assert json.loads(raw.decode("utf-8"))["ok"] is False

    def test_empty_file_classified(self, tmp_path):
        p = tmp_path / "ds_raw.h5"
        p.write_bytes(b"")
        probe = ndview.probe_file(p)
        assert probe["ok"] is False

    def test_cache_warm_hit_and_rebuild_identical(self, tmp_path):
        p = tmp_path / "ds_raw.h5"
        _write_cdf_1d(p)
        ndview._cache_clear()
        raw1, meta1 = ndview.build_cube_bytes(p, "IQ_abs")
        raw2, meta2 = ndview.build_cube_bytes(p, "IQ_abs")
        assert raw1 is raw2                          # warm hit = same bytes object
        ndview._cache_clear()
        raw3, _ = ndview.build_cube_bytes(p, "IQ_abs")
        assert raw1 == raw3                          # deterministic rebuild
        assert meta1["ok"] is True and meta2["ok"] is True


class TestHdf5PathUnchanged:
    def test_hdf5_still_routes_through_h5py(self, tmp_path):
        h5py = pytest.importorskip("h5py")
        p = tmp_path / "ds_raw.h5"
        with h5py.File(p, "w") as f:
            f["I"] = np.arange(12.0).reshape(3, 4)
        probe = ndview.probe_file(p)
        assert probe["ok"] is True
        assert {v["name"] for v in probe["vars"]} == {"I"}
        cube = ndview.build_cube(p, "I")
        assert cube["ok"] is True
        # No DIMENSION_LIST in this minimal file → synthetic dim_N names,
        # exactly the pre-adapter behavior.
        assert [d["name"] for d in cube["dims"]] == ["dim_0", "dim_1"]
