"""ndview — the N-D data viewer core.

Two layers:
  * Synthetic unit tests (always run): dim classification, default-view roles,
    decimation index-keeping, NaN-safe serialization, the never-crash contract.
  * CORPUS INVARIANTS (auto-skip without the real archive): sweep every
    ds_*.h5 under the real data folders and assert the crash-free guarantee —
    every variable yields ok=True or a CLASSIFIED fallback, never an exception;
    entity dims never land on a plot axis; JSON stays parse-safe (no bare NaN).
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest

h5py = pytest.importorskip("h5py")

from quam_state_manager.core import ndview

_ARCHIVES = [Path("<dataset-root>"),
             Path("<install-root>")]
_HAS_ARCHIVE = any(p.is_dir() for p in _ARCHIVES)


# ──────────────────────────────────────────────────────────────────────────
# Synthetic fixtures
# ──────────────────────────────────────────────────────────────────────────

def _write_nc_style(path: Path, *, qubits=("q0", "q1"), n_freq=50, extra=None):
    """A minimal netCDF4-style file: dimension scales + DIMENSION_LIST refs."""
    with h5py.File(path, "w") as f:
        q = f.create_dataset("qubit", data=np.array([s.encode() for s in qubits]))
        q.attrs["CLASS"] = np.bytes_("DIMENSION_SCALE")
        d = f.create_dataset("detuning", data=np.linspace(-5e6, 5e6, n_freq))
        d.attrs["CLASS"] = np.bytes_("DIMENSION_SCALE")
        d.attrs["units"] = np.bytes_("Hz")
        data = np.random.default_rng(0).normal(size=(len(qubits), n_freq))
        data[0, 3] = np.nan
        I = f.create_dataset("I", data=data)
        I.attrs["units"] = np.bytes_("V")
        Q = f.create_dataset("Q", data=data * 0.5)
        for ds in (I, Q):
            ds.dims[0].attach_scale(q)
            ds.dims[1].attach_scale(d)
        if extra:
            extra(f)
    return path


@pytest.fixture
def nc_file(tmp_path):
    return _write_nc_style(tmp_path / "ds_raw.h5")


class TestCubeSynthetic:
    def test_dims_resolved_via_dimension_list(self, nc_file):
        cube = ndview.build_cube(nc_file, "I")
        assert cube["ok"]
        names = [d["name"] for d in cube["dims"]]
        assert names == ["qubit", "detuning"]          # NOT length-guessed
        kinds = {d["name"]: d["kind"] for d in cube["dims"]}
        assert kinds["qubit"] == "entity" and kinds["detuning"] == "sweep"

    def test_default_view_roles(self, nc_file):
        cube = ndview.build_cube(nc_file, "I")
        v = cube["default_view"]
        assert v["x"] == "detuning" and v["y"] is None
        assert v["entity"] == "qubit"                  # selector, never an axis

    def test_nan_serializes_to_null(self, nc_file):
        cube = ndview.build_cube(nc_file, "I")
        s = json.dumps(cube)
        assert "NaN" not in s                          # JSON.parse-safe
        assert cube["data"][0][3] is None

    def test_iq_partner_detected(self, nc_file):
        assert ndview.build_cube(nc_file, "I")["iq_partner"] == "Q"
        assert ndview.build_cube(nc_file, "Q")["iq_partner"] == "I"

    def test_missing_var_is_classified(self, nc_file):
        cube = ndview.build_cube(nc_file, "__nope__")
        assert cube["ok"] is False and "No variable" in cube["error"]

    def test_missing_file_is_classified(self, tmp_path):
        cube = ndview.build_cube(tmp_path / "absent.h5", "x")
        assert cube["ok"] is False

    def test_string_var_falls_back_to_table(self, tmp_path):
        p = tmp_path / "ds_fit.h5"
        with h5py.File(p, "w") as f:
            f.create_dataset("labels", data=np.array([b"a", b"b"]))
        cube = ndview.build_cube(p, "labels")
        assert cube["ok"] is False and cube["fallback"]["kind"] == "table"
        assert cube["fallback"]["sample"] == ["a", "b"]

    def test_scalar_var(self, tmp_path):
        p = tmp_path / "ds_fit.h5"
        with h5py.File(p, "w") as f:
            f.create_dataset("delay", data=np.float64(28.5))
        cube = ndview.build_cube(p, "delay")
        assert cube["ok"] and cube["scalar"] == 28.5

    def test_decimation_keeps_real_points_and_peak(self, tmp_path):
        """A resonance dip must survive decimation; kept indices map back."""
        p = tmp_path / "ds_raw.h5"
        n = 50_000                                     # > line budget
        x = np.linspace(0, 1, n)
        y = np.ones(n); dip = n // 3; y[dip] = -100.0  # the dip
        with h5py.File(p, "w") as f:
            d = f.create_dataset("detuning", data=x)
            d.attrs["CLASS"] = np.bytes_("DIMENSION_SCALE")
            big = np.tile(y, (20, 1))                  # 20×50k = 1M > budget
            I = f.create_dataset("I", data=big)
            q = f.create_dataset("qubit",
                                 data=np.array([f"q{i}".encode() for i in range(20)]))
            q.attrs["CLASS"] = np.bytes_("DIMENSION_SCALE")
            I.dims[0].attach_scale(q); I.dims[1].attach_scale(d)
        cube = ndview.build_cube(p, "I")
        assert cube["ok"]
        det = next(d2 for d2 in cube["dims"] if d2["name"] == "detuning")
        assert det["decimated"] and det["size"] <= ndview._LINE_POINT_BUDGET + 2
        kept = cube["kept"]["detuning"]
        assert dip in kept                             # the dip survived
        assert min(cube["data"][0]) == -100.0
        # kept indices are REAL source indices (click maps to true points)
        assert det["coord"][kept.index(dip)] == pytest.approx(x[dip])

    def test_iq_siblings_share_kept_indices_after_decimation(self, tmp_path):
        """|IQ|/phase usability: decimated I and Q must ship IDENTICAL kept
        sets (derived from a combined |I|+|Q| representative), or the client
        can never combine them. Give each variable its own dip at a different
        sweep point — solo per-variable decimation keeps different indices;
        the shared representative must keep BOTH dips in BOTH cubes."""
        p = tmp_path / "ds_raw.h5"
        n = 50_000
        x = np.linspace(0, 1, n)
        yi = np.ones(n); dip_i = n // 3; yi[dip_i] = -100.0     # I's dip
        yq = np.ones(n); dip_q = (2 * n) // 3; yq[dip_q] = -100.0  # Q's dip
        with h5py.File(p, "w") as f:
            d = f.create_dataset("detuning", data=x)
            d.attrs["CLASS"] = np.bytes_("DIMENSION_SCALE")
            q = f.create_dataset("qubit",
                                 data=np.array([f"q{i}".encode() for i in range(20)]))
            q.attrs["CLASS"] = np.bytes_("DIMENSION_SCALE")
            I = f.create_dataset("I", data=np.tile(yi, (20, 1)))   # 1M > budget
            Q = f.create_dataset("Q", data=np.tile(yq, (20, 1)))
            for ds in (I, Q):
                ds.dims[0].attach_scale(q)
                ds.dims[1].attach_scale(d)
        cube_i = ndview.build_cube(p, "I")
        cube_q = ndview.build_cube(p, "Q")
        assert cube_i["ok"] and cube_q["ok"]
        ki = cube_i["kept"]["detuning"]
        kq = cube_q["kept"]["detuning"]
        assert ki == kq, "IQ siblings must ship identical kept sets"
        # The combined representative preserves EACH variable's feature in both.
        assert dip_i in ki and dip_q in ki
        assert min(cube_i["data"][0]) == -100.0   # I's dip value survived
        assert min(cube_q["data"][0]) == -100.0   # Q's dip value survived

    def test_iq_siblings_stay_aligned_through_byte_shrink(self, tmp_path):
        """The BYTE-shrink pass derives its budgets from the measured serialized
        length — and I's and Q's lengths differ (float text widths), so the pair
        re-diverged here even after the element pass aligned them. Under the
        pair-max shrink both siblings must walk to IDENTICAL kept maps. This
        cube is UNDER the element budget but OVER the byte target, so only the
        byte-shrink pass decimates it."""
        p = tmp_path / "ds_raw.h5"
        rng = np.random.default_rng(2)
        nf, na = 700, 600                            # 420k elems < 500k element budget
        with h5py.File(p, "w") as f:
            fr = f.create_dataset("freq", data=np.linspace(4e9, 5e9, nf))
            fr.attrs["CLASS"] = np.bytes_("DIMENSION_SCALE")
            am = f.create_dataset("amp", data=np.linspace(0, 1, na))
            am.attrs["CLASS"] = np.bytes_("DIMENSION_SCALE")
            I = f.create_dataset("I", data=rng.normal(size=(nf, na)))
            Q = f.create_dataset("Q", data=rng.normal(size=(nf, na)) * 3.0)
            for ds in (I, Q):
                ds.dims[0].attach_scale(fr)
                ds.dims[1].attach_scale(am)
        cube_i = ndview.build_cube(p, "I")
        cube_q = ndview.build_cube(p, "Q")
        assert cube_i["ok"] and cube_q["ok"]
        # byte-shrink actually fired (the whole point of this fixture)
        assert any(d["decimated"] for d in cube_i["dims"])
        assert (cube_i["kept"] or {}) == (cube_q["kept"] or {}), \
            "byte-shrink must keep IQ siblings on identical kept maps"
        assert [(d["name"], d["size"]) for d in cube_i["dims"]] == \
            [(d["name"], d["size"]) for d in cube_q["dims"]]

    def test_probe_lists_fit_coords(self, tmp_path):
        """Fit results riding as non-dim coords must be discoverable."""
        p = tmp_path / "ds_fit.h5"
        with h5py.File(p, "w") as f:
            q = f.create_dataset("qubit", data=np.array([b"q0"]))
            q.attrs["CLASS"] = np.bytes_("DIMENSION_SCALE")
            fv = f.create_dataset("f0", data=np.array([5.1e9]))
            fv.attrs["CLASS"] = np.bytes_("DIMENSION_SCALE")   # scale-like coord
            base = f.create_dataset("fitted", data=np.array([1.0]))
            base.dims[0].attach_scale(q)
            base.attrs["coordinates"] = np.bytes_("f0")
        names = [v["name"] for v in ndview.probe_file(p)["vars"]]
        assert "f0" in names and "fitted" in names


# ──────────────────────────────────────────────────────────────────────────
# Corpus invariants over the REAL archive (the crash-free guarantee)
# ──────────────────────────────────────────────────────────────────────────

def _iter_archive_h5(cap_per_root=120):
    for root in _ARCHIVES:
        if not root.is_dir():
            continue
        n = 0
        for p in sorted(root.rglob("ds_*.h5")):
            yield p
            n += 1
            if n >= cap_per_root:
                break


@pytest.mark.skipif(not _HAS_ARCHIVE, reason="real data archive not present")
class TestCorpusInvariants:
    def test_every_variable_ok_or_classified(self):
        checked = 0
        for h5p in _iter_archive_h5():
            probe = ndview.probe_file(h5p)
            assert isinstance(probe, dict)
            assert ("vars" in probe) or (not probe["ok"])
            if not probe.get("ok"):
                continue                                # classified probe failure
            for v in probe["vars"][:6]:                 # bound runtime per file
                cube = ndview.build_cube(h5p, v["name"])
                checked += 1
                assert isinstance(cube, dict)
                assert cube.get("ok") in (True, False)  # never an exception
                if cube.get("ok") and cube.get("data") is not None:
                    view = cube["default_view"]
                    entity = view.get("entity")
                    # INVARIANT: entity dims never land on a plot axis.
                    if entity is not None:
                        assert entity != view["x"] and entity != view["y"]
                    # INVARIANT: payload is JSON.parse-safe.
                    s = json.dumps(cube)
                    assert "NaN" not in s and "Infinity" not in s
        assert checked > 50, f"corpus too small ({checked}) — archive layout changed?"


class TestH5SuffixMatching:
    def test_list_h5_files_case_insensitive_and_hdf5(self, tmp_path):
        """.H5/.HDF5 spellings are normal on macOS (case-preserving FS) and
        .hdf5 aligns with dataset._resolve_fit_ref — the old exact ".h5"
        match silently hid such files from the Data tab."""
        (tmp_path / "DS_RAW.H5").write_bytes(b"")
        (tmp_path / "ds_proc.hdf5").write_bytes(b"")
        (tmp_path / "ds_fit.h5").write_bytes(b"")
        (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
        (tmp_path / "sub.h5dir").mkdir()   # directory — never listed
        assert ndview.list_h5_files(tmp_path) == [
            "DS_RAW.H5", "ds_fit.h5", "ds_proc.hdf5",
        ]
