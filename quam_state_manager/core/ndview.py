"""N-D dataset viewer core — the "cube-to-client" engine.

Reads a run's xarray-flavoured HDF5 (netCDF4-style, written by h5netcdf) with
PLAIN h5py, resolves real dimension names via ``DIMENSION_LIST`` object
references (the files carry NO ``_ARRAY_DIMENSIONS`` — the legacy pipeline's
length-guessing was wrong on every same-size dim), classifies each dimension,
infers a sensible default view, decimates oversized arrays, and returns a
JSON-ready *cube*: data + coordinates + semantics. The client (ndview.js)
builds the Plotly traces — every interaction after the single cube fetch
(slider, axis swap, facet/overlay toggle, theme) is client-side.

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
from collections import OrderedDict
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
# A cat dim this small defaults to overlaid curves instead of a slider.
_OVERLAY_MAX = 4
# netCDF placeholder NAME attr on dimension scales that carry no real coord.
_NC_PLACEHOLDER = b"This is a netCDF dimension but not a netCDF variable"

# I/Q sibling pairing: plain I/Q and state-suffixed Ig/Qg, Ie/Qe, If/Qf
# (the ONLY conventions in the real archive — numeric-suffix twins are raw
# stream copies, not pairs).
_IQ_SUFFIXES = ("", "g", "e", "f")

_ALLOWED_H5_SUFFIX = ".h5"


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


def probe_file(h5_path: Path) -> dict:
    """List every plottable entry in the file: data variables AND non-dim
    coordinate variables (fit results ride as coords in real files — a
    vars-only viewer hides them). Never raises."""
    out: dict = {"ok": True, "vars": [], "attrs": {}}
    try:
        with _h5_lock_for(str(h5_path)):
            with h5py.File(h5_path, "r") as f:
                # Non-dim coords referenced by any variable's `coordinates` attr.
                coord_names: set[str] = set()
                for key in f.keys():
                    ds = f[key]
                    if isinstance(ds, h5py.Dataset):
                        c = _attr(ds, "coordinates")
                        if isinstance(c, str):
                            coord_names.update(c.split())
                for key in sorted(f.keys()):
                    ds = f[key]
                    if not isinstance(ds, h5py.Dataset):
                        continue
                    is_scale = _is_dimension_scale(ds)
                    if is_scale and key not in coord_names:
                        continue   # plain dim coord — an axis, not a plottable
                    if is_scale and _is_placeholder_scale(ds):
                        continue
                    # Coord-var = a dimension scale OR any var referenced by a
                    # sibling's `coordinates` attr (aux 2-D coords like
                    # full_freq/amp_full are plain datasets, not scales).
                    is_coord = is_scale or key in coord_names
                    dims = _dim_names_for(f, ds)
                    out["vars"].append({
                        "name": key,
                        "shape": list(ds.shape),
                        "ndim": ds.ndim,
                        "dtype": str(ds.dtype),
                        "dims": [d["name"] for d in dims],
                        "units": _attr(ds, "units"),
                        "long_name": _attr(ds, "long_name"),
                        "is_coord_var": is_coord,
                        "elements": int(np.prod(ds.shape)) if ds.ndim else 1,
                    })
                # Data variables first, fit-coord vars after (the shell auto-
                # opens the first card — it should be real data, not a coord).
                out["vars"].sort(key=lambda v: (v["is_coord_var"], v["name"]))
                for k in f.attrs:
                    v = _decode(f.attrs[k])
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
    """'entity' | 'cat' | 'sweep' | 'synthetic' — name/dtype-based, never positional."""
    if name in _ENTITY_DIM_NAMES:
        return "entity"
    if coord is not None and coord.dtype == object:      # string coords
        return "entity" if size > _OVERLAY_MAX else "cat"
    if not has_coord or coord is None:
        return "synthetic"
    return "sweep"


def _default_view(dims: list[dict]) -> dict:
    """Assign roles: entity→selector, small dims→overlay, sweeps→x/y/sliders."""
    view: dict = {"x": None, "y": None, "entity": None, "overlay": [], "sliders": {}}
    sweeps: list[dict] = []
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
        elif kind in ("sweep", "synthetic"):
            if d["size"] <= _OVERLAY_MAX and len(view["overlay"]) < 2:
                view["overlay"].append(d["name"])
            else:
                sweeps.append(d)
    sweeps.sort(key=lambda d: d["size"], reverse=True)
    if sweeps:
        view["x"] = sweeps[0]["name"]
    if len(sweeps) >= 2:
        view["y"] = sweeps[1]["name"]
    for extra in sweeps[2:]:
        view["sliders"][extra["name"]] = 0
    return view


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


def _build_cube_bytes_uncached(h5_path: Path, var: str) -> tuple[bytes, dict]:
    """Build + serialize one cube, enforcing the BYTE budget.

    If the first (element-budgeted) build serializes over ``_CUBE_BYTE_TARGET``,
    rebuild with proportionally tighter per-sweep-dim budgets (min/max index
    keeping and bin-mean coarsening both stay peak-preserving) until it fits or
    nothing decimatable remains. A tightened rebuild that comes back broken or
    no smaller never replaces the last good build."""
    cube = _build_cube_uncached(h5_path, var)
    raw = _serialize_cube(cube)
    for _ in range(_BYTE_SHRINK_ROUNDS):
        if len(raw) <= _CUBE_BYTE_TARGET or not cube.get("ok") or cube.get("data") is None:
            break
        dims = cube.get("dims") or []
        dec = [d for d in dims if d.get("kind") in ("sweep", "synthetic")
               and d.get("size", 0) > 1]
        if not dec:
            break   # nothing decimatable (entity/cat-dominated) — ship honestly
        shipped = 1
        for d in dims:
            shipped *= max(1, int(d.get("size", 1)))
        # Element target from the measured bytes/element of THIS cube.
        target_elems = max(1_000, int(shipped * (_CUBE_BYTE_TARGET / len(raw)) * 0.85))
        factor = (target_elems / shipped) ** (1.0 / len(dec))
        dim_budgets = {d["name"]: max(16, int(d["size"] * factor)) for d in dec}
        cube2 = _build_cube_uncached(h5_path, var,
                                     element_budget=target_elems,
                                     dim_budgets=dim_budgets)
        if not cube2.get("ok") or cube2.get("data") is None:
            break
        raw2 = _serialize_cube(cube2)
        if len(raw2) >= len(raw):
            break
        cube, raw = cube2, raw2
    return raw, _cube_meta(cube)


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
        raw, meta = _build_cube_bytes_uncached(h5_path, var)
    except Exception as exc:   # noqa: BLE001 — the never-crash contract
        logger.warning("ndview cube build failed for %s::%s", h5_path, var, exc_info=True)
        cube = {"ok": False,
                "error": f"Could not read this variable ({type(exc).__name__}: {exc})",
                "fallback": None}
        raw, meta = _serialize_cube(cube), _cube_meta(cube)
    _cache_put(key, (raw, meta))
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
        with h5py.File(h5_path, "r") as f:
            ds = f.get(var)
            if ds is None or not isinstance(ds, h5py.Dataset):
                return {"ok": False, "error": f"No variable named {var!r} in this file.",
                        "fallback": None}
            if ds.ndim and int(np.prod(ds.shape)) > _MAX_RAW_ELEMENTS:
                return {"ok": False,
                        "error": f"{var} is too large to load ({int(np.prod(ds.shape)):,} elements).",
                        "fallback": None}

            dim_meta = _dim_names_for(f, ds)
            data = ds[()]

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
                        "units": _attr(ds, "units"), "long_name": _attr(ds, "long_name")}
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
                coord = _read_coord(f, dm["name"]) if dm["has_coord"] else None
                if coord is not None and coord.shape[0] != size:
                    coord = None
                kind = _classify_dim(dm["name"], size, coord, dm["has_coord"])
                coord_scale = f.get(dm["name"]) if dm["has_coord"] else None
                dims.append({
                    "name": dm["name"], "size": int(size), "kind": kind,
                    "coord": (_nan_to_none_list(coord) if coord is not None
                              and coord.dtype != object else
                              (list(coord) if coord is not None else None)),
                    "units": (_attr(coord_scale, "units")
                              if isinstance(coord_scale, h5py.Dataset) else None),
                    "long_name": (_attr(coord_scale, "long_name")
                                  if isinstance(coord_scale, h5py.Dataset) else None),
                    "decimated": False,
                })

            # Aux 2-D coords (full_freq(qubit,detuning)…): alternative x-axes.
            aux_axes: list[dict] = []
            coords_attr = _attr(ds, "coordinates")
            if isinstance(coords_attr, str):
                dim_names = {d["name"] for d in dims}
                for cname in coords_attr.split():
                    cds = f.get(cname)
                    if (isinstance(cds, h5py.Dataset) and cds.ndim >= 1
                            and cds.dtype.kind == "f"
                            and cds.size <= _CUBE_ELEMENT_BUDGET):
                        cdims = [d["name"] for d in _dim_names_for(f, cds)]
                        if all(cd in dim_names for cd in cdims):
                            aux_axes.append({
                                "name": cname, "dims": cdims,
                                "units": _attr(cds, "units"),
                                "data": _nan_to_none_list(cds[()]),
                            })

            # Decimation to budget — sweep dims only, largest first.
            view = _default_view(dims)
            total = int(np.prod(data.shape)) if data.ndim else 1
            kept: dict[str, list[int]] = {}
            if total > element_budget:
                order = sorted(range(len(dims)), key=lambda i: dims[i]["size"],
                               reverse=True)
                for axis in order:
                    d = dims[axis]
                    if d["kind"] not in ("sweep", "synthetic"):
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
                    else:
                        rep = _representative(data, axis)
                        idx = _minmax_keep_indices(rep, budget)
                        sl = [slice(None)] * data.ndim
                        sl[axis] = idx
                        data = data[tuple(sl)]
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

            all_names = set(f.keys())
            partner = _iq_partner(var, all_names)

            return {
                "ok": True,
                "var": var,
                "dtype": str(ds.dtype),
                "units": _attr(ds, "units"),
                "long_name": _attr(ds, "long_name"),
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
                      if p.suffix == _ALLOWED_H5_SUFFIX and p.is_file())
    except OSError:
        return []
