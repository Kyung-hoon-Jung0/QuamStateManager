"""r10: NetCDF-classic fallback for the interactive-plot reader.

A runner env missing netCDF4/h5netcdf makes xarray fall back to its scipy
engine, which writes NetCDF-classic bytes ("CDF…") under the same
``ds_*.h5`` names. h5py refuses those ("file signature not found"), which
silently degraded the WHOLE Interactive tab to static PNGs for every such
run (the entire 2026-07-29 Lab3 day — reported via the EF power-rabi node).
SM ships scipy, so the reader now sniffs the magic and reads them natively."""

from __future__ import annotations

import json
from types import SimpleNamespace

import numpy as np
import pytest

from quam_state_manager.core.interactive_plots import h5reader, registry

nc = pytest.importorskip("scipy.io.netcdf", reason="scipy required")


def _write_cdf(path, *, with_rf=False):
    """A minimal xarray-scipy-shaped EF power-rabi ds_raw: char qubit coord,
    numeric sweep coords, I + full_amp data vars with real dim names."""
    f = nc.netcdf_file(str(path), "w")
    try:
        f.createDimension("qubit", 1)
        f.createDimension("string3", 3)
        f.createDimension("amp_prefactor", 5)
        q = f.createVariable("qubit", "c", ("qubit", "string3"))
        q[:] = np.frombuffer(b"qA2", dtype="S1").reshape(1, 3)
        amp = f.createVariable("amp_prefactor", "d", ("amp_prefactor",))
        amp[:] = np.linspace(0.5, 1.5, 5)
        dims = ("qubit", "amp_prefactor")
        shape = (1, 5)
        if with_rf:
            f.createDimension("readout_frequency", 3)
            rf = f.createVariable("readout_frequency", "d", ("readout_frequency",))
            rf[:] = np.array([-1e6, 0.0, 1e6])
            dims = ("qubit", "readout_frequency", "amp_prefactor")
            shape = (1, 3, 5)
        sig = f.createVariable("I", "d", dims)
        sig[:] = np.arange(np.prod(shape), dtype=float).reshape(shape)
        sig.units = b"V"
        sig.long_name = b"I quadrature"
        fa = f.createVariable("full_amp", "d", ("qubit", "amp_prefactor"))
        fa[:] = (np.linspace(0.5, 1.5, 5) * 0.2).reshape(1, 5)
        f.history = b"test"
    finally:
        f.close()


def _run(tmp_path):
    return SimpleNamespace(folder_path=tmp_path, experiment_name="29_power_rabi_ef",
                           parameters={"operation": "EF_x180"}, fit_results={},
                           qubits=["qA2"])


class TestNetcdfReader:
    def test_probe_reads_structure_and_qubits(self, tmp_path):
        _write_cdf(tmp_path / "ds_raw.h5")
        out = h5reader.probe_vars(_run(tmp_path), "ds_raw")
        assert out is not None
        assert out["qubits"] == ["qA2"]
        assert out["coords"] == {"qubit": 1, "amp_prefactor": 5}
        assert out["vars"]["I"] == [1, 5]
        assert out["vars"]["full_amp"] == [1, 5]

    def test_load_carries_true_dim_names_and_attrs(self, tmp_path):
        _write_cdf(tmp_path / "ds_raw.h5", with_rf=True)
        out = h5reader.load_dataset(_run(tmp_path), "ds_raw")
        assert out is not None
        assert out["dim_order"]["I"] == ["qubit", "readout_frequency",
                                         "amp_prefactor"]
        assert out["vars"]["I"].shape == (1, 3, 5)
        assert out["attrs"]["I"]["units"] == "V"
        assert out["coords"]["qubit"] == ["qA2"]
        assert out["coords"]["readout_frequency"] == [-1e6, 0.0, 1e6]
        assert out["root_attrs"]["history"] == "test"

    def test_oversized_var_skipped_not_crashed(self, tmp_path):
        _write_cdf(tmp_path / "ds_raw.h5")
        out = h5reader.load_dataset(_run(tmp_path), "ds_raw", max_elements=3)
        assert out is not None
        assert "I" not in out["vars"]
        assert out["attrs"]["I"]["oversized"] is True

    def test_hdf5_files_still_use_h5py_path(self, tmp_path):
        h5py = pytest.importorskip("h5py")
        with h5py.File(tmp_path / "ds_raw.h5", "w") as f:
            f["I"] = np.zeros((2, 2))
        out = h5reader.probe_vars(_run(tmp_path), "ds_raw")
        assert out is not None and out["vars"]["I"] == [2, 2]


class TestEfRabiInteractiveEndToEnd:
    """The reported symptom: an EF power-rabi run saved as NetCDF-classic
    must render an INTERACTIVE tile whose click targets EF_x180 (never the
    GE x180/x90 pair)."""

    def _folder(self, tmp_path, with_rf):
        _write_cdf(tmp_path / "ds_raw.h5", with_rf=with_rf)
        (tmp_path / "node.json").write_text(json.dumps(
            {"metadata": {"name": "29_power_rabi_ef"},
             "data": {"parameters": {"model": {"operation": "EF_x180"}}}}),
            encoding="utf-8")
        return tmp_path

    def test_menu_offers_interactive_tile(self, tmp_path):
        run = _run(self._folder(tmp_path, with_rf=False))
        specs = registry.list_interactive_figures(run)
        tile = next(s for s in specs if s["key"].startswith("amplitude"))
        assert tile["available"] is True and tile["static"] is False

    @pytest.mark.parametrize("with_rf,kind", [(False, "1d"), (True, "2d")])
    def test_build_clickable_targets_ef_x180(self, tmp_path, with_rf, kind):
        run = _run(self._folder(tmp_path, with_rf))
        out = registry.build_interactive_figure(run, "amplitude::qA2")
        assert out is not None and out["kind"] == kind
        targets = out["clickable"]["targets"]
        assert targets == [{"path": "qubits.{q}.xy.operations.EF_x180.amplitude",
                            "scale": 1e-3}]
