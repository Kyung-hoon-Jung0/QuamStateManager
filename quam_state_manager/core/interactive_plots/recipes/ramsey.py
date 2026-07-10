"""Ramsey (1Q_12) — interactive reproduction (view-only).

1D I-quadrature vs idle time, one trace per detuning sign (±), with a damped-
oscillation overlay reconstructed from the saved fit params. View-only: the
node updates a global frequency offset / T2*, not a single figure point.
"""
from __future__ import annotations

import numpy as np

from .. import models
from ..plotbuild import COLORWAY, FIT_COLOR, clean, line
from .base import FigureSpec, figure_key, qslice, qubit_index, qubits_of, split_key

FAMILY = ("1Q_12_ramsey",)


def menu(bundle):
    qubits = qubits_of(bundle) or ["q"]
    multi = len(qubits) > 1
    have = ("I" in bundle.raw_vars) or ("state" in bundle.raw_vars)
    return [FigureSpec(figure_key("amplitude", q),
                       "Ramsey" + (f" — {q}" if multi else ""), "1d",
                       available=have, reason="" if have else "no data")
            for q in qubits]


def build(bundle, key):
    base, qname = split_key(key)
    raw, fit = bundle.raw, bundle.fit
    if not raw or not raw.get("vars"):
        return FigureSpec(key=key, title="Ramsey", available=False, reason="no ds_raw")
    qidx = qubit_index(raw, qname)
    sig = "state" if "state" in raw["vars"] else "I"
    t = np.asarray(raw["coords"].get("idle_time", []), dtype=float)
    signs = list(raw["coords"].get("detuning_signs", [0]))

    arr, dims = qslice(raw, sig, qidx)          # dims e.g. [idle_time, detuning_signs]
    arr = np.asarray(arr, dtype=float)
    sign_axis = dims.index("detuning_signs") if "detuning_signs" in dims else None
    scale = 1e3 if sig != "state" else 1.0
    ylabel = "I [mV]" if sig != "state" else "state"

    fit_arr = fit_labels = None
    if fit and "fit" in fit.get("vars", {}):
        try:
            fit_arr, _ = qslice(fit, "fit", qidx)   # [detuning_signs, fit_vals]
            fit_arr = np.asarray(fit_arr, dtype=float)
            fit_labels = [str(v) for v in fit["coords"].get("fit_vals", [])]
        except Exception:  # noqa: BLE001
            fit_arr = None

    data = []
    for si, sgn in enumerate(signs):
        y = arr[:, si] if sign_axis == 1 else (arr[si] if sign_axis == 0 else arr)
        y = np.asarray(y, dtype=float) * scale
        color = COLORWAY[si % len(COLORWAY)]
        data.append(line(t, y, name=f"Δ={sgn:+g}", color=color, mode="lines+markers"))
        if fit_arr is not None and fit_labels and si < fit_arr.shape[0]:
            curve = _osc_curve(t, fit_arr[si], fit_labels)
            if curve is not None:
                data.append(line(t, curve * scale, name=f"fit Δ={sgn:+g}",
                                 color=color, dash="dash"))
    layout = {"xaxis": {"title": {"text": "idle time [ns]"}},
              "yaxis": {"title": {"text": ylabel}}, "hovermode": "closest",
              "margin": {"l": 60, "r": 30, "t": 40, "b": 50}}
    return FigureSpec(key=key, title="Ramsey", kind="1d",
                      figure={"data": data, "layout": layout})


def _osc_curve(t, vals, labels):
    try:
        fv = {lab: float(vals[i]) for i, lab in enumerate(labels) if i < len(vals)}
        if {"a", "f", "phi", "offset", "decay"} <= set(fv):
            return models.osc_decay(t, fv["a"], fv["f"], fv["phi"], fv["offset"], fv["decay"])
    except Exception:  # noqa: BLE001
        pass
    return None
