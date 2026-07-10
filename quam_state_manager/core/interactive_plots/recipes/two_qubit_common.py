"""Shared helpers for two-qubit (``qubit_pair``-keyed) interactive recipes.

The 1Q recipes slice a ``qubit`` axis (see ``base.qslice``); the 2Q experiments
key their arrays by ``qubit_pair`` (e.g. ``qA2-qA1``) instead. These mirror the
1Q helpers for that axis, and are shared by ``chevron``, ``two_qubit_rb``,
``cz_phase`` and ``cz_2d_maps``.
"""
from __future__ import annotations

import numpy as np


def pairs_of(bundle) -> list[str]:
    """Qubit-pair names: from loaded coords (build pass), else fit_results (menu)."""
    for src in (bundle.fit, bundle.raw):
        if src and "qubit_pair" in src.get("coords", {}):
            return [str(p) for p in src["coords"]["qubit_pair"]]
    if bundle.fit_results:
        return [str(k) for k in bundle.fit_results]
    return [str(q) for q in (bundle.qubit_names or [])] or ["pair"]


def pair_index(src: dict, pname) -> int:
    if not src or "qubit_pair" not in src.get("coords", {}):
        return 0
    names = [str(p) for p in src["coords"]["qubit_pair"]]
    return names.index(pname) if pname in names else 0


def pslice(src: dict, var: str, pidx: int):
    """``src.vars[var]`` with the ``qubit_pair`` axis sliced out. (array, dims).

    Tolerates a source with no ``dim_order`` map (older/lean loads)."""
    arr = np.asarray(src["vars"][var])
    dims = list(src.get("dim_order", {}).get(var, []))
    if "qubit_pair" in dims:
        ax = dims.index("qubit_pair")
        arr = np.take(arr, pidx, axis=ax)
        dims = [d for i, d in enumerate(dims) if i != ax]
    return arr, dims


def pair_scalar(src: dict, var: str, pidx: int):
    """A scalar variable (e.g. ``optimal_amplitude``) for one pair, or ``None``.

    Non-finite (NaN/inf — failed fits) also returns ``None`` so callers can
    treat "missing" and "unusable" identically."""
    if not src or var not in src.get("vars", {}):
        return None
    try:
        v = float(np.asarray(pslice(src, var, pidx)[0]).ravel()[0])
        return v if np.isfinite(v) else None
    except Exception:  # noqa: BLE001
        return None


def star(x, y, name, color):
    """A single starred marker (fitted optimum) trace."""
    return {"x": [x], "y": [y], "type": "scatter", "mode": "markers", "name": name,
            "marker": {"color": color, "size": 13, "symbol": "star",
                       "line": {"color": "#fff", "width": 1}}}
