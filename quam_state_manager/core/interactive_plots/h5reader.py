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
