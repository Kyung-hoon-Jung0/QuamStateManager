"""Shared types + helpers for interactive-plot recipes.

A *recipe* is a module exposing:
    FAMILY : tuple[str, ...]            # node.json metadata.name prefixes it handles
    def menu(bundle) -> list[FigureSpec]    # cheap: figure stubs (figure=None) + availability
    def build(bundle, key) -> FigureSpec    # heavy: one figure with figure={data,layout}

Figures are emitted per qubit (key = ``"<base>::<qubit>"``) so multi-qubit runs
get one figure per qubit; single-qubit runs simply get one.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass
class FigureSpec:
    key: str
    title: str
    kind: str = "1d"  # "1d" | "2d"
    figure: dict | None = None  # {"data": [...traces...], "layout": {...}}
    available: bool = True
    reason: str = ""
    clickable: dict | None = None  # see registry / Click-to-edit design


@dataclass
class Bundle:
    """Everything a recipe needs for one run.

    For the *menu* pass, ``raw``/``fit`` arrays are ``None`` and only the cheap
    ``*_vars`` / ``*_shapes`` probes are populated. For the *build* pass, the
    arrays are loaded too.
    """

    run: Any
    node_meta: dict = field(default_factory=dict)
    fit_results: dict = field(default_factory=dict)
    raw: dict | None = None
    fit: dict | None = None
    raw_vars: set = field(default_factory=set)
    fit_vars: set = field(default_factory=set)
    raw_coords: set = field(default_factory=set)
    fit_coords: set = field(default_factory=set)
    raw_shapes: dict = field(default_factory=dict)
    fit_shapes: dict = field(default_factory=dict)
    qubit_names: list = field(default_factory=list)
    quam_state: dict | None = None
    # Some experiments persist a second dataset alongside ds_raw/ds_fit — e.g.
    # 1Q_15b readout-power-optimization writes its IQ-blob / confusion data to
    # ``ds_iq_blobs.h5`` (same schema as the 1Q_16 iq_blobs ds_fit). Loaded only
    # for recipes that need it; ``iqblobs_vars`` is the cheap menu-pass probe.
    iqblobs: dict | None = None
    iqblobs_vars: set = field(default_factory=set)


def figure_key(base: str, qname: str) -> str:
    return f"{base}::{qname}"


def split_key(key: str) -> tuple[str, str | None]:
    if "::" in key:
        base, qname = key.split("::", 1)
        return base, qname
    return key, None


def qubits_of(bundle: Bundle) -> list[str]:
    """Qubit names for the run (from loaded coords, else node.json qubits list)."""
    for src in (bundle.fit, bundle.raw):
        if src and "qubit" in src.get("coords", {}):
            return [str(q) for q in src["coords"]["qubit"]]
    if bundle.qubit_names:
        return [str(q) for q in bundle.qubit_names]
    return [str(q) for q in (getattr(bundle.run, "qubits", None) or [])]


def qubit_index(src: dict, qname: str | None) -> int:
    """Index of ``qname`` in a loaded source's qubit coord (0 if not found)."""
    if not src or "qubit" not in src.get("coords", {}):
        return 0
    names = [str(q) for q in src["coords"]["qubit"]]
    return names.index(qname) if qname in names else 0


def qslice(src: dict, var: str, qidx: int):
    """Return ``src.vars[var]`` with the qubit axis sliced out (numpy array).

    Falls back to the raw array when the variable has no ``qubit`` dimension.
    Returns ``(array, remaining_dims)``.
    """
    arr = np.asarray(src["vars"][var])
    dims = list(src["dim_order"].get(var, []))
    if "qubit" in dims:
        ax = dims.index("qubit")
        arr = np.take(arr, qidx, axis=ax)
        dims = [d for i, d in enumerate(dims) if i != ax]
    return arr, dims
