"""N-D dataset viewer core — the "cube-to-client" engine.

Reads a run's xarray-flavoured data file in EITHER on-disk format behind one
reader adapter (``_open_reader``): netCDF4-style HDF5 (written by h5netcdf)
with plain h5py, resolving real dimension names via ``DIMENSION_LIST`` object
references (the files carry NO ``_ARRAY_DIMENSIONS`` — the legacy pipeline's
length-guessing was wrong on every same-size dim); or NetCDF-classic
(``CDF\\x01``/``CDF\\x02`` magic — a runner env without netCDF4/h5netcdf makes
xarray fall back to its scipy engine, which writes NetCDF3 bytes under the
same ``ds_*.h5`` names; the whole 2026-07-29+ Lab3 archive is this) with
scipy.io.netcdf_file, where dimension names are native per-variable metadata.
Then classifies each dimension, infers a sensible default view, decimates
oversized arrays, and returns a JSON-ready *cube*: data + coordinates +
semantics. The client (ndview.js) builds the Plotly traces — every interaction
after the single cube fetch (slider, axis swap, facet/overlay toggle, theme)
is client-side.

Design contract (audited):
  * NEVER raises to the caller — every failure is a classified fallback dict
    (``{"ok": False, "error": ..., "fallback": {...}}``) so the route always
    answers HTTP 200 and the UI always has something honest to show.
  * Dim classification is name/dtype-based, never positional (real files have
    inconsistent dim order between sibling variables).
  * Decimation keeps REAL points (index subsampling; kept indices shipped) so
    a click always maps to a true data point; heatmap coarsening ships bin
    means for z but FULL coords for click-snapping.
  * Cube cache is keyed on (path, mtime, var) and holds the SERIALIZED JSON
    bytes (a warm hit is a memcpy, never a re-dump); run archives are
    write-once — if a lab ever rewrites analysis in place, mtime moves and the
    key heals. The LRU is bounded by entry count AND total bytes, and any cube
    whose JSON would exceed ~4 MB is re-decimated (peak-preserving) to fit.

Grounded in an empirical survey of 2,485 real HDF5 files across 167
experiment families (see the interactive-plot-v2 design notes).
"""

from __future__ import annotations

import json
import logging
import threading
import warnings
from collections import OrderedDict
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import h5py
import numpy as np

from quam_state_manager.core.dataset import _h5_lock_for

logger = logging.getLogger(__name__)

# Total elements shipped per cube (post-decimation) — the FIRST-pass budget.
# Elements are a poor proxy for wire size (285k elements measured 6.5 MB JSON),
# so a second, BYTE-aware pass re-decimates any cube whose serialized JSON
# exceeds _CUBE_BYTE_TARGET (see ``_build_cube_bytes_uncached``).
_CUBE_ELEMENT_BUDGET = 500_000
# Serialized-JSON size target per cube. A cube over this is rebuilt with
# proportionally tighter (still peak-preserving) per-dim budgets; only cubes
# with NO decimatable sweep dim can exceed it.
_CUBE_BYTE_TARGET = 4 * 1024 * 1024
_BYTE_SHRINK_ROUNDS = 3
# Ship a decimated dim's full-resolution coord (``coord_full``) only up to this
# many points — beyond it the full coord dominates the payload (~19 B/float).
_COORD_FULL_MAX = 20_000
# Per-sweep-dim point budget for line plots (index-subsampled, peaks kept).
_LINE_POINT_BUDGET = 2_000
# Per-axis pixel budget for heatmap coarsening.
_HEATMAP_AXIS_BUDGET = 512
# Never even open a variable bigger than this raw (matches h5reader._MAX_ELEMENTS).
_MAX_RAW_ELEMENTS = 50_000_000

# Entity dims: the qubit/pair selectors — never plotted on an axis.
_ENTITY_DIM_NAMES = frozenset({"qubit", "qubit_pair", "pair", "spec_qubit"})
# Repetition ("shot") dims: an index over IDENTICAL repeats of the same
# measurement. Its ordering carries no physics — shot 7 is not "after" shot 6
# in any sense the plot can show — so it is the WRONG default x-axis, and it is
# usually the biggest dim in the file, which is exactly how the size-ordered
# default used to pick it (a readout_power_optimization run plotted I against
# n_runs=2000 instead of against amp_prefactor=10).
#
# A handful of ~50 node types in the real archive save one at all: the
# single-shot nodes (readout_power_optimization, iq_blobs, iq_blobs_gef,
# confusion_matrix) and the two-qubit RB family — every other node averages on
# the OPX and ships the average. Names below are the ones those nodes emit
# (n_runs 1836×, average/repeat 41× each, shots 30×, n 4×) plus the obvious
# spellings of the same QUA loop variable.
#
# The line is IDENTICAL repeats vs DISTINCT realizations: nothing tells shot 7
# from shot 6, so averaging them loses nothing — whereas `sequence`,
# `sequence_index` and `nb_of_sequences` index different circuits (an RB random
# sequence, an all_xy gate pair), and those stay plottable.
_SHOT_DIM_NAMES = frozenset({
    "n_runs", "n_shots", "n_avg", "n_averages", "n",
    "shot", "shots", "average", "averages", "repeat", "repeats", "repetition",
    "repetitions",
})
# A cat dim this small defaults to overlaid curves instead of a slider.
_OVERLAY_MAX = 4
# netCDF placeholder NAME attr on dimension scales that carry no real coord.
_NC_PLACEHOLDER = b"This is a netCDF dimension but not a netCDF variable"

# I/Q sibling pairing: plain I/Q and state-suffixed Ig/Qg, Ie/Qe, If/Qf
# (the ONLY conventions in the real archive — numeric-suffix twins are raw
# stream copies, not pairs).
_IQ_SUFFIXES = ("", "g", "e", "f")

# Case-insensitive (macOS default FS preserves but ignores case; tools emit
# .H5/.HDF5 too) and .hdf5 included — aligns with dataset._resolve_fit_ref.
_ALLOWED_H5_SUFFIXES = (".h5", ".hdf5")


# ──────────────────────────────────────────────────────────────────────────
# Cube cache (SERIALIZED JSON bytes — the 9p answer, without the re-dump tax)
#
# Entries are the final UTF-8 JSON bytes of the cube object, not the Python
# dict: a warm hit on a 6.5 MB cube used to cost ~100 ms because every hit
# re-serialized the dict; bytes make a hit a pure memcpy. The per-request
# click/uid block rides OUTSIDE the cube (the route splices it in at the byte
# level), so caching bytes never bakes anything stale. The LRU is bounded BOTH
# by entry count and by total cached bytes.
# ──────────────────────────────────────────────────────────────────────────

# value = (serialized_cube_bytes, small_meta) with
# small_meta = {"ok": bool, "default_view": dict | None} — just enough for the
# route to attach click-candidates without parsing the payload back.
_cube_cache: OrderedDict[tuple, tuple[bytes, dict]] = OrderedDict()
_cube_cache_lock = threading.Lock()
_CUBE_CACHE_MAX = 24
_CUBE_CACHE_MAX_BYTES = 64 * 1024 * 1024
_cube_cache_total = 0   # bytes; guarded by _cube_cache_lock


def _cache_get(key: tuple) -> tuple[bytes, dict] | None:
    with _cube_cache_lock:
        hit = _cube_cache.get(key)
        if hit is not None:
            _cube_cache.move_to_end(key)
        return hit


def _cache_put(key: tuple, value: tuple[bytes, dict]) -> None:
    global _cube_cache_total
    with _cube_cache_lock:
        old = _cube_cache.pop(key, None)
        if old is not None:
            _cube_cache_total -= len(old[0])
        _cube_cache[key] = value
        _cube_cache_total += len(value[0])
        while _cube_cache and (len(_cube_cache) > _CUBE_CACHE_MAX
                               or _cube_cache_total > _CUBE_CACHE_MAX_BYTES):
            _, evicted = _cube_cache.popitem(last=False)
            _cube_cache_total -= len(evicted[0])


def _cache_clear() -> None:
    """Test/HARNESS helper — also resets the byte accounting."""
    global _cube_cache_total
    with _cube_cache_lock:
        _cube_cache.clear()
        _cube_cache_total = 0


def _serialize_cube(cube: dict) -> bytes:
    """Compact UTF-8 JSON. All numeric paths already NaN→None via
    ``_nan_to_none_list``; ``allow_nan=False`` is the tripwire, with a
    defensive re-dump (old jsonify behavior) so the route never 500s."""
    try:
        return json.dumps(cube, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except ValueError:                                       # pragma: no cover
        logger.warning("ndview cube contained non-finite floats post-sanitize")
        return json.dumps(cube, separators=(",", ":")).encode("utf-8")


# ──────────────────────────────────────────────────────────────────────────
# HDF5 structure reading (h5py + manual DIMENSION_LIST resolution)
# ──────────────────────────────────────────────────────────────────────────


def _decode(x: Any) -> Any:
    if isinstance(x, bytes):
        return x.decode("utf-8", "replace")
    return x


def _attr(ds, name: str) -> Any:
    try:
        return _decode(ds.attrs.get(name))
    except Exception:
        return None


def _is_dimension_scale(ds) -> bool:
    try:
        return ds.attrs.get("CLASS") == b"DIMENSION_SCALE"
    except Exception:
        return False


def _is_placeholder_scale(ds) -> bool:
    """A netCDF 'dimension without variable' — a dummy all-zeros scale whose
    values are meaningless (it would otherwise parse as a plausible coord)."""
    try:
        name_attr = ds.attrs.get("NAME")
        return isinstance(name_attr, bytes) and name_attr.startswith(_NC_PLACEHOLDER)
    except Exception:
        return False


def _dim_names_for(f: h5py.File, ds) -> list[dict]:
    """Resolve each axis of *ds* to ``{"name", "has_coord"}`` via DIMENSION_LIST.

    ``has_coord`` is False for placeholder scales (synthetic index axes)."""
    out: list[dict] = []
    dim_list = None
    try:
        dim_list = ds.attrs.get("DIMENSION_LIST")
    except Exception:
        pass
    for axis in range(ds.ndim):
        name = None
        has_coord = False
        if dim_list is not None and axis < len(dim_list):
            try:
                refs = dim_list[axis]
                if len(refs):
                    scale = f[refs[0]]
                    name = scale.name.rsplit("/", 1)[-1]
                    has_coord = not _is_placeholder_scale(scale)
            except Exception:
                name = None
        if name is None:
            name = f"dim_{axis}"
        out.append({"name": name, "has_coord": has_coord})
    return out


def _read_coord(f: h5py.File, name: str) -> np.ndarray | None:
    try:
        ds = f.get(name)
        if ds is None or not isinstance(ds, h5py.Dataset):
            return None
        if ds.ndim != 1 or ds.size > 1_000_000:
            return None
        arr = ds[()]
        if arr.dtype.kind in ("S", "O"):
            arr = np.array([_decode(x) for x in arr], dtype=object)
        return arr
    except Exception:
        return None


# ──────────────────────────────────────────────────────────────────────────
# Reader adapter — one surface over the two on-disk formats.
#
# The pipeline below (classification, decimation, byte budget, serialization)
# is pure numpy and must never care which library produced the arrays. The
# h5py side DELEGATES verbatim to the module helpers above so HDF5 behavior
# stays byte-identical (pinned by the corpus-invariant tests over the real
# archives); the NetCDF-classic side mirrors the proven pattern in
# interactive_plots/h5reader.py. Callers hold ``_h5_lock_for(path)`` around
# ``_open_reader`` — the lock is a plain non-reentrant Lock, so the adapter
# itself must never re-acquire it.
# ──────────────────────────────────────────────────────────────────────────


class _H5Reader:
    """h5py-backed adapter. Handles ARE ``h5py.Dataset`` objects (they carry
    .shape/.ndim/.dtype natively); ``get`` folds the isinstance checks the
    call sites used to repeat, returning None for groups and absentees."""

    def __init__(self, f: h5py.File):
        self._f = f

    def keys(self) -> list:
        return list(self._f.keys())

    def get(self, name: str):
        ds = self._f.get(name)
        return ds if isinstance(ds, h5py.Dataset) else None

    def dim_meta(self, h) -> list[dict]:
        return _dim_names_for(self._f, h)

    def read(self, h):
        return h[()]

    def read_coord(self, name: str):
        return _read_coord(self._f, name)

    def var_attr(self, h, name: str):
        return _attr(h, name)

    def coord_attr(self, name: str, attr: str):
        ds = self._f.get(name)
        return _attr(ds, attr) if isinstance(ds, h5py.Dataset) else None

    def is_dim_coord(self, key: str, h) -> bool:
        return _is_dimension_scale(h)

    def is_placeholder(self, h) -> bool:
        return _is_placeholder_scale(h)

    def root_attrs(self) -> dict:
        out = {}
        for k in self._f.attrs:
            try:
                out[str(k)] = _decode(self._f.attrs[k])
            except Exception:   # noqa: BLE001 — one bad attr must not kill probe
                continue
        return out


class _NcVar:
    """Handle over a scipy ``netcdf_variable`` exposing the .shape/.ndim/.dtype
    triple the pipeline reads (scipy's object has only .shape). Attributes are
    read via ``_attributes.get`` ONLY — scipy injects NC attrs into the
    instance ``__dict__`` after ``data``, so a file attr literally named
    ``data``/``dimensions`` would clobber the field under ``getattr``."""

    __slots__ = ("name", "_v", "shape", "ndim", "dtype")

    def __init__(self, name: str, v):
        self.name = name
        self._v = v
        self.shape = tuple(int(s) for s in v.shape)
        self.ndim = len(self.shape)
        self.dtype = np.dtype(v.data.dtype.newbyteorder("="))   # native-order view


class _NcReader:
    """NetCDF-classic adapter over ``scipy.io.netcdf_file``.

    ``mmap=False`` is an EAGER whole-file read in scipy (arrays are owned
    copies — nothing dangles after close), so a cheap ``st_size`` pre-guard
    stands in for the per-variable element guard that, here, would fire only
    after the RAM was already spent. Dimension names are native
    (``var.dimensions``) — no DIMENSION_LIST dance; a dimension WITHOUT a
    same-named variable is the h5py "placeholder scale" equivalent
    (has_coord=False → classified synthetic)."""

    def __init__(self, path: Path):
        try:
            from scipy.io import netcdf_file
        except ImportError as exc:                      # pragma: no cover
            raise OSError(f"scipy is required to read NetCDF-classic data files: {exc}")
        st_size = path.stat().st_size
        if st_size > _MAX_RAW_ELEMENTS * 8:
            raise OSError(f"NetCDF file too large to load ({st_size:,} bytes)")
        self._f = netcdf_file(str(path), "r", mmap=False)

    def close(self) -> None:
        try:
            self._f.close()
        except Exception:   # noqa: BLE001
            pass

    def keys(self) -> list:
        return list(self._f.variables)

    def get(self, name: str):
        v = self._f.variables.get(name)
        return _NcVar(name, v) if v is not None else None

    def dim_meta(self, h: _NcVar) -> list[dict]:
        return [{"name": str(d), "has_coord": str(d) in self._f.variables}
                for d in h._v.dimensions]

    def read(self, h: _NcVar):
        arr = np.asarray(h._v.data)
        if arr.dtype.byteorder == ">":
            arr = arr.astype(arr.dtype.newbyteorder("="))
        # h5py's ds[()] unwraps 0-d datasets to a numpy SCALAR; [()] is the
        # identity for ndim>0 — keeps the scalar-cube branch format-agnostic.
        return arr[()]

    def read_coord(self, name: str):
        v = self._f.variables.get(name)
        if v is None:
            return None
        try:
            a = np.asarray(v.data)
            if a.size > 1_000_000:
                return None
            if a.dtype.kind == "S" and a.ndim == 2:
                # NetCDF3 has no string type — xarray writes string coords as
                # 2-D char matrices (coord × strlen); join each row back.
                vals = [b"".join(bytes(c) for c in row).decode("utf-8", "replace")
                        .rstrip(chr(0)).strip() for row in a]
                return np.array(vals, dtype=object)
            if a.ndim != 1:
                return None
            if a.dtype.kind in ("S", "O"):
                return np.array([_decode(x) for x in a], dtype=object)
            if a.dtype.byteorder == ">":
                a = a.astype(a.dtype.newbyteorder("="))
            return a
        except Exception:   # noqa: BLE001 — mirrors _read_coord's never-raise
            return None

    def _attrs_of(self, v) -> dict:
        return getattr(v, "_attributes", {}) or {}

    def var_attr(self, h: _NcVar, name: str):
        try:
            return _decode(self._attrs_of(h._v).get(name))
        except Exception:   # noqa: BLE001
            return None

    def coord_attr(self, name: str, attr: str):
        v = self._f.variables.get(name)
        if v is None:
            return None
        try:
            return _decode(self._attrs_of(v).get(attr))
        except Exception:   # noqa: BLE001
            return None

    def is_dim_coord(self, key: str, h: _NcVar) -> bool:
        return key in self._f.dimensions

    def is_placeholder(self, h: _NcVar) -> bool:
        return False   # var-less dims never appear in keys() at all

    def root_attrs(self) -> dict:
        out = {}
        for k, v in (getattr(self._f, "_attributes", {}) or {}).items():
            if k == "_NCProperties":
                continue
            try:
                out[_decode(k)] = _decode(v.tolist() if hasattr(v, "tolist") else v)
            except Exception:   # noqa: BLE001
                continue
        return out


def _is_netcdf_classic(path: Path) -> bool:
    """3-byte magic sniff (b"CDF"). Never raises; anything that is NOT
    NetCDF-classic routes to h5py so garbage keeps today's canonical
    "file signature not found" OSError (and HDF5 userblock files, whose
    signature sits at offset 512/1024, are never misrouted)."""
    try:
        with open(path, "rb") as fh:
            return fh.read(3) == b"CDF"
    except OSError:
        return False


@contextmanager
def _open_reader(h5_path: Path):
    """Yield the right adapter for the file's ACTUAL bytes. The caller must
    already hold ``_h5_lock_for(h5_path)``."""
    if _is_netcdf_classic(h5_path):
        r = _NcReader(h5_path)
        try:
            yield r
        finally:
            r.close()
    else:
        with h5py.File(h5_path, "r") as f:
            yield _H5Reader(f)


def probe_file(h5_path: Path) -> dict:
    """List every plottable entry in the file: data variables AND non-dim
    coordinate variables (fit results ride as coords in real files — a
    vars-only viewer hides them). Never raises."""
    out: dict = {"ok": True, "vars": [], "attrs": {}}
    try:
        with _h5_lock_for(str(h5_path)):
            with _open_reader(h5_path) as r:
                # Non-dim coords referenced by any variable's `coordinates` attr.
                coord_names: set[str] = set()
                for key in r.keys():
                    h = r.get(key)
                    if h is not None:
                        c = r.var_attr(h, "coordinates")
                        if isinstance(c, str):
                            coord_names.update(c.split())
                for key in sorted(r.keys()):
                    h = r.get(key)
                    if h is None:
                        continue
                    is_scale = r.is_dim_coord(key, h)
                    if is_scale and key not in coord_names:
                        continue   # plain dim coord — an axis, not a plottable
                    if is_scale and r.is_placeholder(h):
                        continue
                    # Coord-var = a dimension scale OR any var referenced by a
                    # sibling's `coordinates` attr (aux 2-D coords like
                    # full_freq/amp_full are plain datasets, not scales).
                    is_coord = is_scale or key in coord_names
                    dims = r.dim_meta(h)
                    out["vars"].append({
                        "name": key,
                        "shape": list(h.shape),
                        "ndim": h.ndim,
                        "dtype": str(h.dtype),
                        "dims": [d["name"] for d in dims],
                        "units": r.var_attr(h, "units"),
                        "long_name": r.var_attr(h, "long_name"),
                        "is_coord_var": is_coord,
                        "elements": int(np.prod(h.shape)) if h.ndim else 1,
                    })
                # Data variables first, fit-coord vars after (the shell auto-
                # opens the first card — it should be real data, not a coord).
                out["vars"].sort(key=lambda v: (v["is_coord_var"], v["name"]))
                for k, v in r.root_attrs().items():
                    if isinstance(v, (str, int, float, np.integer, np.floating)):
                        out["attrs"][str(k)] = (float(v) if isinstance(v, (np.integer, np.floating))
                                                else v)
    except OSError as exc:
        return {"ok": False, "error": f"Cannot open the data file: {exc}", "vars": []}
    except Exception as exc:   # noqa: BLE001 — never-crash contract
        logger.warning("ndview probe failed for %s", h5_path, exc_info=True)
        return {"ok": False, "error": f"Unreadable data file ({type(exc).__name__})", "vars": []}
    return out


# ──────────────────────────────────────────────────────────────────────────
# Dim classification + default view
# ──────────────────────────────────────────────────────────────────────────


def _classify_dim(name: str, size: int, coord: np.ndarray | None,
                  has_coord: bool) -> str:
    """'entity' | 'cat' | 'shot' | 'sweep' | 'synthetic' — name/dtype-based,
    never positional."""
    if name in _ENTITY_DIM_NAMES:
        return "entity"
    if coord is not None and coord.dtype == object:      # string coords
        return "entity" if size > _OVERLAY_MAX else "cat"
    if name in _SHOT_DIM_NAMES:
        return "shot"
    if not has_coord or coord is None:
        return "synthetic"
    return "sweep"


# ── axis ordering (docs/122 item 1) ──────────────────────────────────────
# The Raw-data tab used to disagree with the Interactive tab about which sweep
# is x, because it chose by ARRAY SIZE while the interactive recipes orient by
# NAME, following the lab's own plotting modules — and the user confirmed the
# recipes are right (Interactive uses the qualibrate figure's axis scheme).
#
# Measured over 53 executed 2-D runs across 10 families: when the lab's x dim
# happens to be the LARGER array this agrees 30/30, when it is smaller it agrees
# 0/20, equal sizes 3/3. Total separation — ndview's x was literally
# argmax(size) — which is why the transpose looked intermittent to the user.
#
# DERIVED, never invented: 27 constraints extracted from the recipes and from
# the lab's own `x=`/`y=` arguments, topologically sorted with 0 cycles over 21
# names. It must be EXACT SPELLINGS: grouping variants into physical quantities
# creates real cycles (`time > detuning` from 19a/21b against
# `detuning > pulse_duration` from 11b_rabi_chevron; `amp > time` from
# 23_zz_off_jazz against `time > amplitude` from 2Q_19/31_chevron), and
# `qubit_flux > coupler_flux` orders two members of one physical group, which a
# grouped ranking cannot express at all.
#
# `probe_flux` was in the derived order and is deliberately ABSENT. It was the
# one rank placed by name-shape analogy rather than by an observed figure, and
# on 50a/50b_flux_crosstalk_dc run with a probe the lab REDUCES that axis away
# (`isel(probe_flux=k)`) instead of plotting it — a rank would have put it on y
# in place of `detuning`. Leaving it unranked makes that case unchanged from
# today rather than newly wrong.
_AXIS_RANK: dict[str, int] = {
    "amp": 1,                    # 23_zz_off_jazz, 33a leakage, 32b/32c/32d
    "amp_prefactor": 2,          # 11_power_rabi, 29_power_rabi_ef, 15_readout_power_opt
    "alpha_prefactor": 3,        # 13_drag
    "a": 4,                      # 19b/21c reference panel (SM's own figure)
    "idle_time": 5,              # T1/T2echo/T2star_vs_flux, 12_ramsey, 25_T1
    "idle_times": 6,             # 17a, 17b — same quantity, plural spelling
    "time": 7,                   # 19a/19b/20/21b/21c/23, 2Q_19/31_chevron
    "qubit_flux": 8,             # 18a_coupler_zero_point, 30_cz_iswap
    "aggressor_flux": 9,         # 50a/50b_flux_crosstalk_dc
    "flux_bias": 11,             # 02e, 03c, 06, 07, 09, 10, 17a, T1/T2_vs_flux
    "coupler_flux": 12,          # 17b, 18a, 30_cz_iswap
    "amplitude": 13,             # 2Q_19/31_chevron, 38/39/39b SNZ
    "detuning": 14,              # 05, 06, 07, 08, 08b, 09, 10, 02e, 03c, 11b, 14, 50a
    "readout_frequency": 15,     # 29_power_rabi_ef
    "pulse_duration": 16,        # 11b_rabi_chevron
    "t_phi_eff": 17,             # 38/39/39b
    "number_of_operations": 18,  # 33a/33b/33c, 32b
    "N": 19,                     # 32c/32d JAZZ-N
    "nb_of_pulses": 20,          # 11, 13, stark_detuning
    "frame": 21,                 # 19b, 20, 21c, 22
    "power": 22,                 # 05, 08b
}


def _order_sweeps(sweeps: list[dict],
                  all_sweeps: list[dict] | None = None) -> None:
    """Order ``sweeps`` in place so ``sweeps[0]`` is the x axis.

    Size order first — that is the legacy rule and it stays the tie-break and
    the whole rule for anything the ranking does not name. The name ranking is
    applied ONLY when every sweep in the cube is ranked, so a file the table
    does not fully cover keeps today's output byte for byte rather than being
    half-converted on partial evidence. (Measured on the customer archive: of
    1,805 cubes with a y axis, 1,803 have every sweep ranked and 0 are mixed, so
    this gate costs nothing on real data — it is armour for the next node type.)

    ``all_sweeps`` is the gate population: EVERY sweep dim in the cube,
    including short ones diverted to the overlay bucket. The gate used to see
    only the x/y candidates, so an unranked sweep that happened to be short
    slipped past it and the cube was rank-ordered on partial evidence — the
    exact case the docstring above promises cannot happen (docs/124, the
    ndview minor). Callers with no diverted sweeps may omit it.
    """
    sweeps.sort(key=lambda d: d["size"], reverse=True)
    gate = all_sweeps if all_sweeps is not None else sweeps
    if sweeps and gate and all(d["name"] in _AXIS_RANK for d in gate):
        sweeps.sort(key=lambda d: _AXIS_RANK[d["name"]])


def _default_view(dims: list[dict]) -> dict:
    """Assign roles: entity→selector, small dims→overlay, sweeps→x/y/sliders,
    repetition dims→averaged away (``reduced``)."""
    view: dict = {"x": None, "y": None, "entity": None, "overlay": [],
                  "sliders": {}, "reduced": []}
    sweeps: list[dict] = []
    shots: list[dict] = []
    small_sweeps: list[dict] = []      # sweeps diverted to overlay — reclaimable
    for d in dims:
        if d["size"] == 1:
            continue   # squeezed client-side
        kind = d["kind"]
        if kind == "entity" and view["entity"] is None:
            view["entity"] = d["name"]
        elif kind in ("cat",) or (kind == "entity"):
            # second entity / small cat → overlay when tiny, else extra selector
            if d["size"] <= _OVERLAY_MAX and len(view["overlay"]) < 2:
                view["overlay"].append(d["name"])
            else:
                view["sliders"][d["name"]] = 0
        elif kind == "shot":
            # Never an overlay and never a slider: 2,000 identical repeats are
            # 2,000 indistinguishable curves / 2,000 selector chips.
            shots.append(d)
        elif kind in ("sweep", "synthetic"):
            if d["size"] <= _OVERLAY_MAX and len(view["overlay"]) < 2:
                view["overlay"].append(d["name"])
                small_sweeps.append(d)
            else:
                sweeps.append(d)
    _order_sweeps(sweeps, sweeps + small_sweeps)
    shots.sort(key=lambda d: d["size"], reverse=True)
    if not sweeps and shots:
        if small_sweeps:
            # A real quantity that was only diverted to overlay because it is
            # short still beats a shot index as the x-axis.
            best = max(small_sweeps, key=lambda z: z["size"])
            view["overlay"].remove(best["name"])
            sweeps = [best]
        else:
            # Nothing to plot the repeats AGAINST — then the repeats ARE the
            # view. This is the iq_blobs case (Ig(qubit, n_runs)): the per-shot
            # scatter is the whole point of the node, so averaging it away
            # would leave an empty plot. (In the real archive this is the ONLY
            # way a shot axis reaches x: of 502 such cubes, none had any other
            # plottable dim.) The largest repetition dim becomes x; any others
            # still average.
            sweeps = [shots.pop(0)]
    if sweeps:
        view["x"] = sweeps[0]["name"]
    if len(sweeps) >= 2:
        view["y"] = sweeps[1]["name"]
    for extra in sweeps[2:]:
        view["sliders"][extra["name"]] = 0
    view["reduced"] = [{"name": d["name"], "size": d["size"], "op": "mean"}
                       for d in shots]
    return view


def _reduce_dims(data: np.ndarray, dims: list[dict],
                 names: list[str]) -> tuple[np.ndarray, list[dict]]:
    """Average ``data`` over the named dims and drop them from ``dims``.

    Repetition axes are averaged rather than sliced because the mean over
    identical repeats is precisely what the other ~47 node types already ship
    (they average on the OPX and never save the shot axis) — so the default
    view of a single-shot node matches the default view of every other node.
    Showing shot #0 instead would be one noisy trace of a 2,000-shot average.
    """
    axes = tuple(i for i, d in enumerate(dims) if d["name"] in names)
    if not axes or len(axes) >= data.ndim:
        return data, dims                       # nothing to do / would 0-d it
    return _mean_over(data, axes), [d for i, d in enumerate(dims)
                                    if i not in axes]


def _mean_over(arr: np.ndarray, axes: tuple[int, ...]) -> np.ndarray:
    """``nanmean`` over ``axes``, quietly. An all-NaN repeat set is a real (if
    unhappy) run: the mean is NaN, which ``_nan_to_none_list`` already ships as
    null — not a warning worth raising."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        return np.nanmean(arr, axis=axes)


def _iq_partner(name: str, all_names: set[str]) -> str | None:
    """'I'→'Q', 'Ig'→'Qg', … when the partner exists (real-archive conventions)."""
    for suf in _IQ_SUFFIXES:
        if name == f"I{suf}" and f"Q{suf}" in all_names:
            return f"Q{suf}"
        if name == f"Q{suf}" and f"I{suf}" in all_names:
            return f"I{suf}"
    return None


# ──────────────────────────────────────────────────────────────────────────
# Decimation (index-keeping — clicks always land on true points)
# ──────────────────────────────────────────────────────────────────────────


def _minmax_keep_indices(rep: np.ndarray, budget: int) -> np.ndarray:
    """Bucketed min/max index selection over a representative 1-D signal —
    peaks/dips survive (a resonator dip must never be decimated away)."""
    n = rep.shape[0]
    if n <= budget:
        return np.arange(n)
    n_buckets = max(1, budget // 2)
    edges = np.linspace(0, n, n_buckets + 1).astype(int)
    keep: set[int] = {0, n - 1}
    for i in range(n_buckets):
        lo, hi = edges[i], max(edges[i] + 1, edges[i + 1])
        seg = rep[lo:hi]
        if not np.all(np.isnan(seg)):
            keep.add(lo + int(np.nanargmin(seg)))
            keep.add(lo + int(np.nanargmax(seg)))
        else:
            keep.add(lo)
    return np.array(sorted(keep), dtype=int)


def _representative(data: np.ndarray, axis: int) -> np.ndarray:
    """Collapse all other axes (nanmean of |x|) → a 1-D signal along *axis*."""
    other = tuple(i for i in range(data.ndim) if i != axis)
    with np.errstate(all="ignore"):
        rep = np.nanmean(np.abs(data.astype(np.float64, copy=False)), axis=other)
    return np.nan_to_num(rep, nan=0.0)


def _block_mean(data: np.ndarray, axis: int, budget: int) -> tuple[np.ndarray, np.ndarray]:
    """Block-average *axis* down to ≤budget bins; returns (coarse, bin_center_idx)."""
    n = data.shape[axis]
    if n <= budget:
        return data, np.arange(n)
    edges = np.linspace(0, n, budget + 1).astype(int)
    chunks, centers = [], []
    for i in range(budget):
        lo, hi = edges[i], max(edges[i] + 1, edges[i + 1])
        sl = [slice(None)] * data.ndim
        sl[axis] = slice(lo, hi)
        with np.errstate(all="ignore"):
            chunks.append(np.nanmean(data[tuple(sl)], axis=axis, keepdims=True))
        centers.append((lo + hi - 1) // 2)
    return np.concatenate(chunks, axis=axis), np.array(centers, dtype=int)


# ──────────────────────────────────────────────────────────────────────────
# The cube builder
# ──────────────────────────────────────────────────────────────────────────


def _nan_to_none_list(a: np.ndarray) -> list:
    """tolist() with NaN/±inf → None (JSON.parse rejects bare NaN)."""
    if a.dtype.kind == "f":
        bad = ~np.isfinite(a)
        if bad.any():
            obj = a.astype(object)
            obj[bad] = None
            return obj.tolist()
    return a.tolist()


def _table_fallback(data: np.ndarray, dims: list[dict], limit: int = 50) -> dict:
    """An honest raw sample when a variable can't be plotted."""
    flat = data.reshape(-1)
    n = min(limit, flat.shape[0]) if flat.ndim else 0
    vals = []
    for v in flat[:n]:
        v = _decode(v)
        if isinstance(v, (np.integer, np.floating)):
            v = None if (isinstance(v, np.floating) and not np.isfinite(v)) else float(v)
        vals.append(v if isinstance(v, (str, int, float, type(None))) else str(v))
    return {"kind": "table", "dims": [d["name"] for d in dims],
            "shape": [d["size"] for d in dims], "sample": vals,
            "total": int(flat.shape[0]) if flat.ndim else 1}


def _cube_meta(cube: dict) -> dict:
    return {"ok": bool(cube.get("ok")), "default_view": cube.get("default_view")}


def _iq_pair_aligned(cube: dict, pcube: dict) -> bool:
    """True when two sibling cubes are combinable: same dim names+sizes AND the
    same kept-index maps (the element pass guarantees the latter for aligned
    pairs built with equal budgets)."""
    return (bool(cube.get("ok")) and bool(pcube.get("ok"))
            and [(d["name"], d["size"]) for d in (cube.get("dims") or [])]
                == [(d["name"], d["size"]) for d in (pcube.get("dims") or [])]
            and cube.get("kept") == pcube.get("kept"))


def _build_cube_bytes_uncached(h5_path: Path, var: str) -> tuple[bytes, dict, list]:
    """Build + serialize one cube, enforcing the BYTE budget.

    If the first (element-budgeted) build serializes over ``_CUBE_BYTE_TARGET``,
    rebuild with proportionally tighter per-sweep-dim budgets (min/max index
    keeping and bin-mean coarsening both stay peak-preserving) until it fits or
    nothing decimatable remains. A tightened rebuild that comes back broken or
    no smaller never replaces the last good build.

    IQ pairing: the shrink budgets are derived from the measured byte length,
    and I's and Q's lengths differ (float text widths) — so the pair's kept
    maps re-diverged here even after the element pass aligned them, killing
    |IQ|/phase on every over-target cube. When the var has a dim-aligned IQ
    partner and the byte target is in play, build the partner at the SAME
    budgets and drive every shrink decision on the PAIR MAXIMUM length (a
    symmetric statistic), so both sides walk identical rounds to identical
    budgets. The partner's finished bytes are returned as a cache prime (third
    element) so its own request is a warm hit, not a duplicate pair-build.

    Returns ``(raw, meta, primes)`` — primes is ``[(var, raw, meta)]`` for
    opportunistically cacheable sibling builds (empty for solo vars)."""
    cube = _build_cube_uncached(h5_path, var)
    raw = _serialize_cube(cube)

    partner = cube.get("iq_partner") if cube.get("ok") else None
    pcube, praw = None, None
    # Only pay the partner build when the byte target is actually in play:
    # comfortably-under-target pairs (≤90% of target) can't diverge here — the
    # sibling's length differs by float-text widths (a few %), so it fits too
    # and neither side shrinks.
    if partner and len(raw) > int(_CUBE_BYTE_TARGET * 0.9):
        try:
            pc = _build_cube_uncached(h5_path, partner)
            if _iq_pair_aligned(cube, pc):
                pcube, praw = pc, _serialize_cube(pc)
        except Exception:   # noqa: BLE001 — partner trouble → solo shrink
            pcube, praw = None, None

    def _eff_len() -> int:
        return max(len(raw), len(praw)) if praw is not None else len(raw)

    for _ in range(_BYTE_SHRINK_ROUNDS):
        if _eff_len() <= _CUBE_BYTE_TARGET or not cube.get("ok") or cube.get("data") is None:
            break
        dims = cube.get("dims") or []
        dec = [d for d in dims if d.get("kind") in ("sweep", "synthetic")
               and d.get("size", 0) > 1]
        if not dec:
            break   # nothing decimatable (entity/cat-dominated) — ship honestly
        shipped = 1
        for d in dims:
            shipped *= max(1, int(d.get("size", 1)))
        # Element target from the measured bytes/element — the PAIR MAX when
        # paired, so both siblings compute identical budgets.
        target_elems = max(1_000, int(shipped * (_CUBE_BYTE_TARGET / _eff_len()) * 0.85))
        factor = (target_elems / shipped) ** (1.0 / len(dec))
        dim_budgets = {d["name"]: max(16, int(d["size"] * factor)) for d in dec}
        cube2 = _build_cube_uncached(h5_path, var,
                                     element_budget=target_elems,
                                     dim_budgets=dim_budgets)
        if not cube2.get("ok") or cube2.get("data") is None:
            break
        raw2 = _serialize_cube(cube2)
        pcube2, praw2 = None, None
        if praw is not None:
            try:
                pc2 = _build_cube_uncached(h5_path, partner,
                                           element_budget=target_elems,
                                           dim_budgets=dim_budgets)
                if _iq_pair_aligned(cube2, pc2):
                    pcube2, praw2 = pc2, _serialize_cube(pc2)
            except Exception:   # noqa: BLE001
                pcube2, praw2 = None, None
            if praw2 is None:
                break   # pairing broke (shouldn't happen) — keep the last good pair
        new_eff = max(len(raw2), len(praw2)) if praw2 is not None else len(raw2)
        if new_eff >= _eff_len():
            break
        cube, raw = cube2, raw2
        pcube, praw = pcube2, praw2

    primes = ([(partner, praw, _cube_meta(pcube))]
              if praw is not None and pcube is not None else [])
    return raw, _cube_meta(cube), primes


def build_cube_bytes(h5_path: Path, var: str) -> tuple[bytes, dict]:
    """The main entry: variable → serialized JSON cube bytes + small meta.

    NEVER raises. Cached on (path, mtime, var) — bytes, so a warm hit is a
    memcpy (no re-serialization). ``meta = {"ok", "default_view"}`` lets the
    route attach per-request extras without parsing the payload."""
    try:
        mtime = h5_path.stat().st_mtime_ns
    except OSError as exc:
        cube = {"ok": False, "error": f"Data file missing: {exc}", "fallback": None}
        return _serialize_cube(cube), _cube_meta(cube)
    key = (str(h5_path), mtime, var)
    hit = _cache_get(key)
    if hit is not None:
        return hit
    try:
        raw, meta, primes = _build_cube_bytes_uncached(h5_path, var)
    except Exception as exc:   # noqa: BLE001 — the never-crash contract
        logger.warning("ndview cube build failed for %s::%s", h5_path, var, exc_info=True)
        cube = {"ok": False,
                "error": f"Could not read this variable ({type(exc).__name__}: {exc})",
                "fallback": None}
        raw, meta, primes = _serialize_cube(cube), _cube_meta(cube), []
    _cache_put(key, (raw, meta))
    # An IQ pair-shrink already produced the partner's exact bytes — prime its
    # cache slot so the (typically immediate) sibling request is a warm hit
    # instead of a duplicate pair-build.
    for p_var, p_raw, p_meta in primes:
        _cache_put((str(h5_path), mtime, p_var), (p_raw, p_meta))
    return raw, meta


def build_cube(h5_path: Path, var: str) -> dict:
    """Dict view over ``build_cube_bytes`` (compat for tests/older callers).

    Same cache + byte budget; pays one json.loads per call — the hot route
    uses the bytes directly."""
    raw, _meta = build_cube_bytes(h5_path, var)
    return json.loads(raw.decode("utf-8"))


def _build_cube_uncached(h5_path: Path, var: str, *,
                         element_budget: int = _CUBE_ELEMENT_BUDGET,
                         dim_budgets: dict | None = None) -> dict:
    """One un-cached cube build. ``element_budget`` + optional per-dim
    ``dim_budgets`` overrides are the byte-shrink pass's knobs (defaults
    reproduce the plain first-pass build)."""
    with _h5_lock_for(str(h5_path)):
        with _open_reader(h5_path) as r:
            ds = r.get(var)
            if ds is None:
                return {"ok": False, "error": f"No variable named {var!r} in this file.",
                        "fallback": None}
            if ds.ndim and int(np.prod(ds.shape)) > _MAX_RAW_ELEMENTS:
                return {"ok": False,
                        "error": f"{var} is too large to load ({int(np.prod(ds.shape)):,} elements).",
                        "fallback": None}

            dim_meta = r.dim_meta(ds)
            data = r.read(ds)

            # 0-d / string / object → table-style fallback, not a plot.
            if ds.ndim == 0 or ds.dtype.kind in ("S", "O", "U"):
                val = _decode(data if ds.ndim == 0 else None)
                if ds.ndim == 0:
                    if isinstance(val, (np.integer, np.floating)):
                        val = float(val)
                    if isinstance(val, float) and not np.isfinite(val):
                        val = None   # a bare NaN scalar would break JSON.parse
                    return {"ok": True, "var": var, "scalar": val if isinstance(
                        val, (str, int, float, type(None))) else str(val),
                        "dims": [], "data": None, "default_view": None,
                        "units": r.var_attr(ds, "units"),
                        "long_name": r.var_attr(ds, "long_name")}
                return {"ok": False, "error": f"{var} holds text data — shown as a table.",
                        "fallback": _table_fallback(np.array(
                            [_decode(x) for x in data.reshape(-1)], dtype=object),
                            [{"name": d["name"], "size": s}
                             for d, s in zip(dim_meta, ds.shape)])}

            # ints/bools plot fine as numeric.
            data = data.astype(np.float64, copy=False) if data.dtype.kind in ("i", "u", "b") \
                else data
            if data.dtype.kind == "c":   # complex (none in the archive; belt+braces)
                data = np.abs(data)

            # Dim descriptors + coords.
            dims: list[dict] = []
            for axis, (dm, size) in enumerate(zip(dim_meta, ds.shape)):
                coord = r.read_coord(dm["name"]) if dm["has_coord"] else None
                if coord is not None and coord.shape[0] != size:
                    coord = None
                kind = _classify_dim(dm["name"], size, coord, dm["has_coord"])
                dims.append({
                    "name": dm["name"], "size": int(size), "kind": kind,
                    "coord": (_nan_to_none_list(coord) if coord is not None
                              and coord.dtype != object else
                              (list(coord) if coord is not None else None)),
                    "units": (r.coord_attr(dm["name"], "units")
                              if dm["has_coord"] else None),
                    "long_name": (r.coord_attr(dm["name"], "long_name")
                                  if dm["has_coord"] else None),
                    "decimated": False,
                })

            # Aux 2-D coords (full_freq(qubit,detuning)…) as alternative x-axes are
            # NOT built: no client code reads `aux_axes`, and shipping them
            # full-resolution bypassed the cube's byte budget (measured ~8.8 MB) and
            # misaligned them with the decimated dims. Ship an empty list until the
            # alternative-x-axis feature exists client-side (then subsample them with
            # the SAME kept indices as the dims they map to).
            aux_axes: list[dict] = []

            all_names = set(r.keys())
            partner = _iq_partner(var, all_names)

            # Repetition axes are averaged away BEFORE anything is budgeted —
            # a 2,000-shot axis is 2,000× the payload of the view it produces.
            view = _default_view(dims)
            reduce_names = [r["name"] for r in view["reduced"]]
            if reduce_names:
                reduce_axes = tuple(i for i, d in enumerate(dims)
                                    if d["name"] in reduce_names)
                data, dims = _reduce_dims(data, dims, reduce_names)
            else:
                reduce_axes = ()

            # Decimation to budget — sweep dims only, largest first.
            total = int(np.prod(data.shape)) if data.ndim else 1
            kept: dict[str, list[int]] = {}
            if total > element_budget:
                # IQ-shared decimation: min/max index-keeping picks indices from a
                # per-VARIABLE representative, so I and Q used to keep DIFFERENT
                # source indices — the client then (correctly) refuses to combine
                # them and |IQ|/phase became unavailable on any decimated cube.
                # Derive the kept indices from a COMBINED representative
                # (rep(|I|) + rep(|Q|), symmetric in the pair) so both siblings
                # ship IDENTICAL kept sets and mag/phase is computable again.
                # The partner array rides through every slice/bin-mean below so
                # multi-axis decimation stays aligned step by step. Guarded on
                # exact dim-name/shape agreement (real files can order sibling
                # dims differently — then we fall back to solo decimation and
                # the client degrades honestly, exactly as before).
                partner_data = None
                if partner is not None:
                    try:
                        pds = r.get(partner)
                        if (pds is not None and pds.shape == ds.shape
                                and [m["name"] for m in r.dim_meta(pds)]
                                    == [m["name"] for m in dim_meta]):
                            partner_data = r.read(pds)
                            if partner_data.dtype.kind in ("i", "u", "b"):
                                partner_data = partner_data.astype(np.float64, copy=False)
                            if partner_data.dtype.kind == "c":
                                partner_data = np.abs(partner_data)
                            if partner_data.dtype.kind not in ("f",):
                                partner_data = None
                            elif reduce_axes:
                                # The shape check above is against the FILE's
                                # dims (both siblings are still un-reduced
                                # here) — average the partner over the same
                                # axes or the pair walks out of lockstep.
                                partner_data = _mean_over(partner_data,
                                                          reduce_axes)
                    except Exception:   # noqa: BLE001 — partner unreadable → solo
                        partner_data = None
                order = sorted(range(len(dims)), key=lambda i: dims[i]["size"],
                               reverse=True)
                for axis in order:
                    d = dims[axis]
                    if d["kind"] not in ("sweep", "synthetic", "shot"):
                        continue
                    is_heat_axis = d["name"] in (view["x"], view["y"]) and view["y"]
                    budget = _HEATMAP_AXIS_BUDGET if is_heat_axis else _LINE_POINT_BUDGET
                    if dim_budgets and d["name"] in dim_budgets:
                        budget = min(budget, int(dim_budgets[d["name"]]))
                    if d["size"] <= budget:
                        continue
                    if is_heat_axis:
                        data, centers = _block_mean(data, axis, budget)
                        idx = centers
                        d["bin_mean"] = True
                        if partner_data is not None:   # keep the pair in lockstep
                            partner_data, _ = _block_mean(partner_data, axis, budget)
                    else:
                        rep = _representative(data, axis)
                        if partner_data is not None:
                            rep = rep + _representative(partner_data, axis)
                        idx = _minmax_keep_indices(rep, budget)
                        sl = [slice(None)] * data.ndim
                        sl[axis] = idx
                        data = data[tuple(sl)]
                        if partner_data is not None:
                            partner_data = partner_data[tuple(sl)]
                    # coord follows the kept indices. The FULL coord rides
                    # along for click-snap ONLY when it's small: on a 400k-pt
                    # sweep coord_full alone was ~8 MB of JSON — and ``kept``
                    # (real source indices, always shipped) already maps every
                    # kept point back to the true axis. Nothing client-side
                    # reads coord_full today (ndview.js snaps via kept).
                    if d["coord"] is not None:
                        if len(d["coord"]) <= _COORD_FULL_MAX:
                            d["coord_full"] = d["coord"]
                        carr = np.asarray(d["coord"], dtype=object)
                        d["coord"] = list(carr[idx])
                    d["size"] = int(data.shape[axis])
                    d["decimated"] = True
                    kept[d["name"]] = [int(i) for i in idx]
                    total = int(np.prod(data.shape))
                    if total <= element_budget:
                        break
                if total > element_budget:
                    return {"ok": False,
                            "error": (f"{var} is too high-volume to view interactively "
                                      f"even after decimation."),
                            "fallback": _table_fallback(data, dims)}

            return {
                "ok": True,
                "var": var,
                "dtype": str(ds.dtype),
                "units": r.var_attr(ds, "units"),
                "long_name": r.var_attr(ds, "long_name"),
                "dims": dims,
                "data": _nan_to_none_list(data),
                "kept": kept or None,
                "aux_axes": aux_axes,
                "iq_partner": partner,
                "default_view": view,
                "budget": {"shipped": total,
                           "full": int(np.prod(ds.shape)) if ds.ndim else 1},
            }


def list_h5_files(run_folder: Path) -> list[str]:
    """Every *.h5 in the run folder (containment-checked by the caller) — the
    old ds_raw/ds_fit whitelist hid ds_proc/ds_survey files entirely."""
    try:
        return sorted(p.name for p in run_folder.iterdir()
                      if p.suffix.lower() in _ALLOWED_H5_SUFFIXES and p.is_file())
    except OSError:
        return []
