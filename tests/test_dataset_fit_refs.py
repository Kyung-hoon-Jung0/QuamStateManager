"""Tests for DatasetStore's npz/h5 fit-result reference resolution.

``data.json`` sometimes stores a fitted value as a relative reference into a
companion array file (e.g. run 475's
``"./arrays.npz#fit_results.qA2-qA1.control_phase_correction"``). The dataset
detail / Results tab resolves these to the real value via
``DatasetStore._resolve_fit_ref`` instead of showing the raw string.
"""
from __future__ import annotations

import pytest

from quam_state_manager.core.dataset import DatasetStore

np = pytest.importorskip("numpy")


def _store(tmp_path):
    # An empty / non-dataset folder is fine — we call the resolver directly.
    return DatasetStore(tmp_path)


def test_resolve_npz_scalar(tmp_path):
    np.savez(tmp_path / "arrays.npz",
             **{"fit_results.qA2-qA1.control_phase_correction": np.array(0.8895903839688349)})
    ds = _store(tmp_path)
    val = ds._resolve_fit_ref(
        tmp_path, "./arrays.npz#fit_results.qA2-qA1.control_phase_correction")
    assert isinstance(val, float)
    assert abs(val - 0.8895903839688349) < 1e-12


def test_resolve_npz_size_one_array(tmp_path):
    np.savez(tmp_path / "arrays.npz", **{"k": np.array([0.5])})
    ds = _store(tmp_path)
    assert ds._resolve_fit_ref(tmp_path, "./arrays.npz#k") == 0.5


def test_resolve_npz_multi_element_array_summary(tmp_path):
    np.savez(tmp_path / "arrays.npz", **{"k": np.zeros((3, 4))})
    ds = _store(tmp_path)
    out = ds._resolve_fit_ref(tmp_path, "./arrays.npz#k")
    assert isinstance(out, str) and out.startswith("[array ") and "(3, 4)" in out


def test_resolve_npz_nonfinite_becomes_none(tmp_path):
    np.savez(tmp_path / "arrays.npz", **{"k": np.array(np.nan)})
    ds = _store(tmp_path)
    assert ds._resolve_fit_ref(tmp_path, "./arrays.npz#k") is None


def test_resolve_missing_key_returns_raw(tmp_path):
    np.savez(tmp_path / "arrays.npz", **{"k": np.array(1.0)})
    ds = _store(tmp_path)
    ref = "./arrays.npz#nope"
    assert ds._resolve_fit_ref(tmp_path, ref) == ref


def test_resolve_missing_file_returns_raw(tmp_path):
    ds = _store(tmp_path)
    ref = "./arrays.npz#k"
    assert ds._resolve_fit_ref(tmp_path, ref) == ref


def test_resolve_path_escape_blocked(tmp_path):
    # File OUTSIDE the run folder; a ../ ref must be refused (returns raw).
    np.savez(tmp_path / "evil.npz", **{"k": np.array(1.0)})
    run_folder = tmp_path / "run"
    run_folder.mkdir()
    ds = _store(tmp_path)
    ref = "./../evil.npz#k"
    assert ds._resolve_fit_ref(run_folder, ref) == ref


def test_resolve_no_hash_returns_raw(tmp_path):
    ds = _store(tmp_path)
    assert ds._resolve_fit_ref(tmp_path, "./arrays.npz") == "./arrays.npz"


def test_resolve_fit_refs_only_touches_dotslash_refs(tmp_path):
    np.savez(tmp_path / "arrays.npz", **{"fit_results.q.m": np.array(0.7)})
    ds = _store(tmp_path)

    class _R:
        folder_path = tmp_path
        fit_results = {"q": {"m": "./arrays.npz#fit_results.q.m",
                             "n": 1.23,         # plain number, untouched
                             "s": "passband"}}  # plain string, untouched

    out = ds._resolve_fit_refs(_R())
    assert out["q"]["m"] == 0.7
    assert out["q"]["n"] == 1.23
    assert out["q"]["s"] == "passband"


def test_resolve_fit_refs_passthrough_non_dict(tmp_path):
    ds = _store(tmp_path)

    class _R:
        folder_path = tmp_path
        fit_results = {"q": "scalar-not-a-dict"}

    assert ds._resolve_fit_refs(_R()) == {"q": "scalar-not-a-dict"}
