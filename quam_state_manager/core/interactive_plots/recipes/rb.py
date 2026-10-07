"""Single-qubit randomized benchmarking (1Q_27) — interactive reproduction.

1D survival vs Clifford depth with the node's exponential-decay fit. View-only
(reports error-per-Clifford/gate; no figure-point edit).

The fit curve (docs/300). ``fit_data`` is a row of fit PARAMETERS, never a
curve -- the previous overlay plotted that row against the depth axis (12
numbers: three parameters and their covariance). Parameters are now picked by
their own coordinate labels and the node's model is evaluated:

* ``fit_vals`` = a, offset, decay, ... (``fit_decay_exp``): qualibration_libs
  ``decay_exp`` (see ``models.decay_exp``), which the legacy figure draws as
  ``decay_exp(`` ``fit.depths,`` ``fit.fit_data.sel(fit_vals="a"),`` ...
  (calibration_utils/single_qubit_randomized_benchmarking/plotting.py, lines 70-75);
* ``param`` = p, A, B (the power-law generation, signal ``data_mean``):
  ``A * (p**x) + B`` (calibration_utils/common_utils/fitting_tools.py,
  lines 67-68: ``def power_law(x, p, A, B):`` / ``return A * (p**x) + B``).

Unknown labels draw no curve, with a note.
"""
from __future__ import annotations

import numpy as np

from .. import fitcheck, models
from ..plotbuild import FIT_COLOR, line, note
from .base import FigureSpec, figure_key, qslice, qubit_index, qubits_of, split_key

FAMILY = ("1Q_27_single_qubit_randomized_benchmarking",
          "1Q_27b_single_qubit_randomized_benchmarking_interleaved")

_SIGNALS = ("averaged_data", "data_mean")


def _signal(vars_: set):
    return next((v for v in _SIGNALS if v in vars_), None)


def menu(bundle):
    qubits = qubits_of(bundle) or ["q"]
    multi = len(qubits) > 1
    have = _signal(bundle.raw_vars | bundle.fit_vars) is not None
    return [FigureSpec(figure_key("amplitude", q),
                       "Randomized benchmarking" + (f" — {q}" if multi else ""), "1d",
                       available=have, reason="" if have else "no data")
            for q in qubits]


def build(bundle, key):
    base, qname = split_key(key)
    fit_sig = _signal(set((bundle.fit or {}).get("vars", {})))
    src = bundle.fit if fit_sig else bundle.raw
    sig = fit_sig or _signal(set((bundle.raw or {}).get("vars", {})))
    if not src or sig is None:
        return FigureSpec(key=key, title="Randomized benchmarking", available=False, reason="no data")
    qidx = qubit_index(src, qname)
    depths = np.asarray(src["coords"].get("depths", []), dtype=float)
    y, _ = qslice(src, sig, qidx)
    data = [line(depths, np.asarray(y, dtype=float), name=qname or "data",
                 color="#4e79a7", mode="markers")]
    notes = []
    curve_x, curve = _node_fit_curve(bundle.fit, qname, depths)
    hold = None
    if curve is not None:
        data.append(line(curve_x, curve, name="fit", color=FIT_COLOR, dash="dash"))
        if fitcheck.runs_away(curve, y):
            hold = fitcheck.data_range(y)
            notes.append(fitcheck.RUNAWAY_NOTE)
    elif bundle.fit and "fit_data" in bundle.fit.get("vars", {}):
        notes.append("No fit curve: the saved fit parameters are not a known RB model's.")
    layout = {"xaxis": {"title": {"text": "Number of Cliffords"}},
              "yaxis": {"title": {"text": "P(survival)"}}, "hovermode": "closest",
              "margin": {"l": 60, "r": 30, "t": 40, "b": 50}}
    if hold is not None:
        layout["yaxis"].update(range=hold, autorange=False)
    if notes:
        layout["annotations"] = [note(notes)]
    return FigureSpec(key=key, title="Randomized benchmarking", kind="1d",
                      figure={"data": data, "layout": layout})


def _node_fit_curve(fit, qname, depths):
    """``(x, y)`` of the node's fit on a smooth depth grid, or ``(None, None)``."""
    if not fit or "fit_data" not in fit.get("vars", {}) or depths.size < 2:
        return None, None
    try:
        row, dims = qslice(fit, "fit_data", qubit_index(fit, qname))
    except Exception:  # noqa: BLE001
        return None, None
    row = np.asarray(row, dtype=float).ravel()
    labels = [str(v) for v in fit.get("coords", {}).get(dims[0], [])] if len(dims) == 1 else []
    if len(labels) != row.size:
        return None, None
    p = dict(zip(labels, row))
    x = np.linspace(float(np.nanmin(depths)), float(np.nanmax(depths)), 200)
    if {"a", "offset", "decay"} <= p.keys():
        y = models.decay_exp(x, p["a"], p["offset"], p["decay"])
    elif {"p", "A", "B"} <= p.keys():
        y = models.power_law(x, p["p"], p["A"], p["B"])
    else:
        return None, None
    if not np.any(np.isfinite(y)):
        return None, None
    return x, y
