"""DRAG calibration (1Q_13) — interactive reproduction.

2D heatmap (number-of-pulses × DRAG α) of the signal, plus the 1D averaged
curve, with the optimal α marked. Clickable: a clicked α → `xy.operations.<op>.alpha`.
"""
from __future__ import annotations

import numpy as np

from .. import plotbuild as pb
from .base import FigureSpec, figure_key, qslice, qubit_index, qubits_of, split_key

FAMILY = ("1Q_13_drag_calibration_180_minus_180",)


def _op(bundle):
    try:
        return str((getattr(bundle.run, "parameters", {}) or {}).get("operation", "x180")) or "x180"
    except Exception:  # noqa: BLE001
        return "x180"


def _click(bundle, qname, *, axis_is_absolute: bool):
    """Absolute-alpha ASSIGN — ONLY when the plotted x-axis is the persisted
    absolute ``alpha`` var. A dimensionless ``alpha_prefactor`` axis MUST stay
    view-only: assigning a clicked prefactor (≈±1.2) verbatim would be wrong by
    a factor of alpha_pre (the 05b-class relative-vs-absolute bug) — and
    alpha_pre is NOT the frozen snapshot (post-update) nor even patches[].old
    when the node ran with an alpha_setpoint override."""
    if not axis_is_absolute:
        return None
    return {"axis": "x", "qubit": qname, "label": "Set DRAG α",
            "targets": [{"path": "qubits.{q}.xy.operations.%s.alpha" % _op(bundle), "scale": 1}]}


def menu(bundle):
    qubits = qubits_of(bundle) or ["q"]
    multi = len(qubits) > 1
    src_vars = bundle.raw_vars | bundle.fit_vars
    have = "I" in src_vars or "averaged_data" in src_vars
    specs = []
    for q in qubits:
        suffix = f" — {q}" if multi else ""
        specs.append(FigureSpec(figure_key("amplitude", q), "DRAG (pulses × α)" + suffix,
                                "2d", available="I" in src_vars, reason="" if "I" in src_vars else "no I"))
        specs.append(FigureSpec(figure_key("averaged", q), "DRAG (averaged)" + suffix,
                                "1d", available="averaged_data" in src_vars,
                                reason="" if "averaged_data" in src_vars else "no averaged_data"))
    return specs


def build(bundle, key):
    base, qname = split_key(key)
    src = bundle.raw if (bundle.raw and bundle.raw.get("vars")) else bundle.fit
    if not src or not src.get("vars"):
        return FigureSpec(key=key, title="DRAG", available=False, reason="no data")
    qidx = qubit_index(src, qname)
    alpha, _ = qslice(src, "alpha", qidx) if "alpha" in src["vars"] else (None, None)
    alpha = np.asarray(alpha, dtype=float) if alpha is not None else \
        np.asarray(src["coords"].get("alpha_prefactor", []), dtype=float)
    opt = _scalar(src, "optimal_alpha", qidx)

    if base == "averaged":
        if "averaged_data" not in src["vars"]:
            return FigureSpec(key=key, title="DRAG (averaged)", available=False, reason="no averaged_data")
        y, _ = qslice(src, "averaged_data", qidx)
        data = [pb.line(alpha, np.asarray(y, dtype=float), name=qname or "data",
                        color="#4e79a7", mode="lines+markers", customdata=[qname] * len(alpha))]
        shapes = [pb.vline(opt)] if opt is not None and np.isfinite(opt) else []
        layout = {"xaxis": {"title": {"text": "DRAG α"}}, "yaxis": {"title": {"text": "signal"}},
                  "shapes": shapes, "hovermode": "closest", "margin": {"l": 60, "r": 30, "t": 40, "b": 50}}
        return FigureSpec(key=key, title="DRAG (averaged)", kind="1d",
                          figure={"data": data, "layout": layout}, clickable=_click(bundle, qname, axis_is_absolute="alpha" in src["vars"]))

    # 2D heatmap
    if "I" not in src["vars"]:
        return FigureSpec(key=key, title="DRAG", available=False, reason="no I")
    z, dims = qslice(src, "I", qidx)
    z = np.asarray(z, dtype=float)
    nb = np.asarray(src["coords"].get("nb_of_pulses", []), dtype=float)
    if dims and dims[0] != "nb_of_pulses":
        z = z.T
    data = [pb.heatmap(alpha, nb, z, colorbar_title="I")]
    shapes = [pb.vline(opt)] if opt is not None and np.isfinite(opt) else []
    layout = {"xaxis": {"title": {"text": "DRAG α"}}, "yaxis": {"title": {"text": "Number of pulses"}},
              "shapes": shapes, "margin": {"l": 60, "r": 30, "t": 50, "b": 50}}
    return FigureSpec(key=key, title="DRAG (pulses × α)", kind="2d",
                      figure={"data": data, "layout": layout}, clickable=_click(bundle, qname, axis_is_absolute="alpha" in src["vars"]))


def _scalar(src, var, qidx):
    if var not in src.get("vars", {}):
        return None
    try:
        return float(np.asarray(qslice(src, var, qidx)[0]).ravel()[0])
    except Exception:  # noqa: BLE001
        return None
