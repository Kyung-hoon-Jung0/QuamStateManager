"""Safe bulk HDF5 reading for the interactive-plot recipes.

Reuses the exact safety envelope of :mod:`quam_state_manager.core.dataset`
(``ds_raw``/``ds_fit`` whitelist + per-file lock; h5py is not thread-safe).
``probe_vars`` reads only structure (cheap, for the figure menu); ``load_dataset``
materializes the requested arrays into numpy (for building a figure).
"""
from __future__ import annotations

import logging
from pathlib import Path

from quam_state_manager.core import safe_io
from quam_state_manager.core.dataset import _H5_WHICH_WHITELIST, _h5_lock_for

logger = logging.getLogger(__name__)

# Skip materializing any single variable larger than this (≈400 MB of float64),
# so a pathological single-shot array degrades one figure instead of OOMing.
_MAX_ELEMENTS = 50_000_000


def _decode(x):
    return x.decode() if isinstance(x, bytes) else x


def _h5_path(run, which: str) -> Path | None:
    if which not in _H5_WHICH_WHITELIST:
        return None
    p = Path(run.folder_path) / f"{which}.h5"
    return p if p.exists() else None


# ---------------------------------------------------------------------------
# NetCDF-classic fallback (r10): a runner env missing netCDF4/h5netcdf makes
# xarray fall back to its scipy engine, which writes NetCDF-classic bytes
# ("CDF\x01"/"CDF\x02") under the same ``ds_*.h5`` names. h5py refuses those
# ("file signature not found"), which silently degraded the whole Interactive
# tab to static PNGs for every such run (the entire 2026-07-29 Lab3 day). SM
# already ships scipy — read them with scipy.io.netcdf_file instead. NetCDF3
# even carries per-variable dimension NAMES natively, so axis truth needs no
# DIMENSION_LIST deref here.
# ---------------------------------------------------------------------------

def _netcdf3_magic(path: Path) -> bool:
    """3-byte sniff for NetCDF-classic ("CDF"). Never raises."""
    try:
        with open(path, "rb") as fh:
            return fh.read(3) == b"CDF"
    except OSError:
        return False


def _nc_coord_values(var) -> list:
    """A coordinate variable's values as a plain list. NetCDF3 has no string
    type — xarray writes string coords as 2-D char arrays (coord × strlen);
    join each row back into one string."""
    import numpy as np

    a = np.asarray(var[()])
    if a.dtype.kind == "S" and a.ndim == 2:
        return [b"".join(bytes(c) for c in row).decode("utf-8", "replace")
                .rstrip("\x00").strip() for row in a]
    if a.dtype.kind == "S":
        return [x.decode("utf-8", "replace") for x in a.tolist()]
    return a.tolist()


def _probe_vars_netcdf(path: Path) -> dict | None:
    """probe_vars, NetCDF-classic edition — same payload shape."""
    try:
        from scipy.io import netcdf_file
    except ImportError:
        return None
    out: dict = {"vars": {}, "coords": {}, "qubits": []}
    with _h5_lock_for(str(path)):
        try:
            f = netcdf_file(str(path), "r", mmap=False)
            try:
                dims = set(f.dimensions)
                for name, var in f.variables.items():
                    if name in dims:        # coordinate variable
                        out["coords"][name] = int(var.shape[0]) if var.shape else 1
                        if name == "qubit":
                            out["qubits"] = _nc_coord_values(var)
                    else:
                        out["vars"][name] = list(var.shape)
            finally:
                f.close()
        except Exception as e:  # noqa: BLE001 — corrupt file → no menu
            logger.warning("netcdf probe failed for %s: %s", path, e)
            return None
    return out


def _load_dataset_netcdf(path: Path, want, max_elements: int) -> dict | None:
    """load_dataset, NetCDF-classic edition — same payload shape."""
    try:
        import numpy as np
        from scipy.io import netcdf_file
    except ImportError:
        return None
    out_vars: dict = {}
    coords: dict = {}
    attrs: dict = {}
    dim_order: dict = {}
    root_attrs: dict = {}
    with _h5_lock_for(str(path)):
        try:
            f = netcdf_file(str(path), "r", mmap=False)
            try:
                for k, v in (getattr(f, "_attributes", {}) or {}).items():
                    if k == "_NCProperties":
                        continue
                    root_attrs[_decode(k)] = _decode(
                        v.tolist() if hasattr(v, "tolist") else v)
                dims = set(f.dimensions)
                coord_names = {n for n in f.variables if n in dims}
                for name in coord_names:
                    coords[name] = _nc_coord_values(f.variables[name])
                for name, var in f.variables.items():
                    if name in coord_names:
                        continue
                    if want is not None and name not in want:
                        continue
                    vdims = [str(d) for d in var.dimensions]
                    n_elem = 1
                    for s in var.shape:
                        n_elem *= int(s)
                    if n_elem > max_elements:
                        logger.warning("skipping oversized var %s (%d elements)",
                                       name, n_elem)
                        attrs[name] = {"oversized": True,
                                       "shape": list(var.shape), "dims": vdims}
                        continue
                    # copy out of scipy's buffer so the file can close
                    out_vars[name] = np.array(var[()])
                    dim_order[name] = vdims
                    va = getattr(var, "_attributes", {}) or {}
                    attrs[name] = {
                        "long_name": _decode(va.get("long_name", "")),
                        "units": _decode(va.get("units", "")),
                        "shape": list(var.shape),
                        "dims": vdims,
                    }
            finally:
                f.close()
        except Exception as e:  # noqa: BLE001
            logger.warning("netcdf load failed for %s: %s", path, e)
            return None
    return {"vars": out_vars, "coords": coords, "attrs": attrs,
            "dim_order": dim_order, "root_attrs": root_attrs}


def probe_vars(run, which: str) -> dict | None:
    """Cheap structure probe: ``{"vars": {name: shape}, "coords": {name: size}}``.

    Does NOT read the (potentially large) data-variable arrays — only shapes +
    the small 1-D coordinate scales. Returns ``None`` if the file is absent.
    """
    try:
        import h5py
    except ImportError:
        return None
    path = _h5_path(run, which)
    if path is None:
        return None
    if _netcdf3_magic(path):
        return _probe_vars_netcdf(path)
    out: dict = {"vars": {}, "coords": {}, "qubits": []}
    with _h5_lock_for(str(path)):
        try:
            with h5py.File(path, "r") as f:
                for name in f:
                    d = f[name]
                    cls = _decode(dict(d.attrs).get("CLASS", b""))
                    if cls == "DIMENSION_SCALE" and len(d.shape) == 1:
                        out["coords"][name] = int(d.shape[0])
                        if name == "qubit":  # cheap: read the small qubit-name list
                            data = d[()]
                            if hasattr(data, "tolist"):
                                data = data.tolist()
                            out["qubits"] = [x.decode() if isinstance(x, bytes) else str(x)
                                             for x in data]
                    else:
                        out["vars"][name] = list(d.shape)
        except Exception as e:  # noqa: BLE001 — corrupt/locked file → no menu
            logger.warning("probe_vars failed for %s: %s", path, e)
            return None
    return out


def load_dataset(run, which: str, vars=None, max_elements: int = _MAX_ELEMENTS) -> dict | None:
    """Load an HDF5 dataset into numpy arrays + coordinates + per-var metadata.

    Returns ``{"vars": {name: np.ndarray}, "coords": {name: list},
    "attrs": {name: {...}}, "dim_order": {name: [dim, ...]}}`` or ``None`` if
    the file is absent / unreadable. Pass ``vars=[...]`` to limit which data
    variables are materialized. Coordinate scales are always loaded.
    """
    try:
        import h5py
        import numpy as np
    except ImportError:
        return None
    path = _h5_path(run, which)
    if path is None:
        return None
    if _netcdf3_magic(path):
        return _load_dataset_netcdf(path, set(vars) if vars is not None else None,
                                    max_elements)

    want = set(vars) if vars is not None else None
    out_vars: dict = {}
    coords: dict = {}
    attrs: dict = {}
    dim_order: dict = {}

    root_attrs: dict = {}
    with _h5_lock_for(str(path)):
        try:
            with h5py.File(path, "r") as f:
                # Dataset-level (root) attrs, e.g. max_amp / max_power_dbm.
                for k, v in f.attrs.items():
                    if k == "_NCProperties":
                        continue
                    root_attrs[k] = _decode(v.tolist() if hasattr(v, "tolist") else v)
                # Pass 1: coordinate scales (small, 1-D).
                coord_names = set()
                for name in f:
                    d = f[name]
                    cls = _decode(dict(d.attrs).get("CLASS", b""))
                    if cls == "DIMENSION_SCALE" and len(d.shape) == 1:
                        coord_names.add(name)
                        data = d[()]
                        if hasattr(data, "tolist"):
                            data = data.tolist()
                        if data and isinstance(data[0], bytes):
                            data = [x.decode() for x in data]
                        coords[name] = data
                # Pass 2: data variables.
                for name in f:
                    if name in coord_names:
                        continue
                    if want is not None and name not in want:
                        continue
                    d = f[name]
                    a = dict(d.attrs)
                    dims = _dim_names(f, d, coords)   # DIMENSION_LIST deref (see fn)
                    n_elem = 1
                    for s in d.shape:
                        n_elem *= int(s)
                    if n_elem > max_elements:
                        logger.warning("skipping oversized var %s (%d elements)", name, n_elem)
                        attrs[name] = {"oversized": True, "shape": list(d.shape), "dims": dims}
                        continue
                    out_vars[name] = np.asarray(d[()])
                    dim_order[name] = dims
                    attrs[name] = {
                        "long_name": _decode(a.get("long_name", "")),
                        "units": _decode(a.get("units", "")),
                        "shape": list(d.shape),
                        "dims": dims,
                    }
        except Exception as e:  # noqa: BLE001
            logger.warning("load_dataset failed for %s: %s", path, e)
            return None

    return {"vars": out_vars, "coords": coords, "attrs": attrs,
            "dim_order": dim_order, "root_attrs": root_attrs}


def _dim_names(f, ds, coords: dict) -> list[str]:
    """Resolve a variable's dimension names.

    DIMENSION_LIST FIRST (real axis truth): every archive file carries it and NONE
    carry _ARRAY_DIMENSIONS, so the old length-equality guess was what actually ran
    — and it mis-assigned axes whenever two coords shared a length (square heatmaps,
    a length-1 sweep colliding with the length-1 qubit coord), transposing the
    heatmap so a click stages the WRONG coordinate's value (this path STAGES
    calibration edits). ndview already deref's DIMENSION_LIST; the recipe path was
    missed (doc 48). Fall back to _ARRAY_DIMENSIONS, then the length guess.
    """
    try:
        dim_list = ds.attrs.get("DIMENSION_LIST")
    except Exception:
        dim_list = None
    if dim_list is not None:
        names: list[str] = []
        ok = True
        for axis in range(ds.ndim):
            nm = None
            try:
                if axis < len(dim_list) and len(dim_list[axis]):
                    nm = f[dim_list[axis][0]].name.rsplit("/", 1)[-1]
            except Exception:
                nm = None
            if nm is None:
                ok = False
                break
            names.append(nm)
        if ok and len(names) == ds.ndim:
            return names

    attrs = dict(ds.attrs)
    ad = attrs.get("_ARRAY_DIMENSIONS")
    if ad is not None:
        if hasattr(ad, "tolist"):
            ad = ad.tolist()
        if isinstance(ad, (list, tuple)):
            return [_decode(x) for x in ad]
        if isinstance(ad, bytes):
            return [ad.decode()]

    dims: list[str] = []
    for i, size in enumerate(ds.shape):
        matched = False
        for cname, cvals in coords.items():
            if len(cvals) == size:
                dims.append(cname)
                matched = True
                break
        if not matched:
            dims.append(f"dim_{i}")
    return dims


def load_quam_state(run) -> dict | None:
    """Load the run's quam_state, merging ``state.json`` + ``wiring.json``.

    Merging lets pointers like ``resonator.opx_output`` (``#/wiring/...`` →
    ``#/ports/...``) resolve through to e.g. ``full_scale_power_dbm``.
    """
    folder = Path(run.folder_path) / "quam_state"
    try:
        state = safe_io.read_json(folder / "state.json")
    except (OSError, ValueError):
        return None
    try:
        wiring = safe_io.read_json(folder / "wiring.json")
    except (OSError, ValueError):
        wiring = None
    if isinstance(wiring, dict):
        merged = dict(state)
        merged.update(wiring)  # adds "wiring" / "ports" / "network" alongside "qubits"
        return merged
    return state
