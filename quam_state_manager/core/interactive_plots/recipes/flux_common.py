"""Shared helpers for the two flux-long-distortion recipes (19a / 19b)."""
from __future__ import annotations

import numpy as np

from .. import plotbuild as pb


def qubit_rf_hz(quam_state, qname):
    """Absolute qubit drive frequency [Hz] from a run's state.json, or None.

    Tries ``qubits.<q>.xy.RF_frequency`` then ``qubits.<q>.f_01``. JSON-pointer
    strings (unresolved ``#/...`` references) and non-numeric values yield None.
    """
    if not quam_state or not qname:
        return None
    q = (quam_state.get("qubits") or {}).get(qname) or {}
    xy = q.get("xy") or {}
    for v in (xy.get("RF_frequency"), q.get("f_01"), xy.get("f_01")):
        if isinstance(v, (int, float)):
            return float(v)
    return None


def fit_components(fit_results, qname):
    """Return ``(a_dc, [(a_i, τ_i), ...])`` from data.json fit_results, or None."""
    res = (fit_results or {}).get(qname) or {}
    tuples = res.get("a_tau_tuple")
    a_dc = res.get("a_dc")
    if not isinstance(tuples, (list, tuple)) or a_dc is None:
        return None
    comps = []
    for pair in tuples:
        if isinstance(pair, (list, tuple)) and len(pair) == 2:
            try:
                comps.append((float(pair[0]), float(pair[1])))
            except (TypeError, ValueError):
                return None
    if not comps:
        return None
    return float(a_dc), comps


def fitted_two_panel(time, y, fit_curve, ylabel="flux response [V]"):
    """Two-panel (linear | log-time) data+fit overlay figure dict."""
    data = [
        pb.line(time, y, name="data", color="#4e79a7", mode="lines+markers"),
        pb.line(time, fit_curve, name="fit", color=pb.FIT_COLOR),
        pb.line(time, y, color="#4e79a7", mode="lines+markers",
                xaxis="x2", yaxis="y2", showlegend=False),
        pb.line(time, fit_curve, color=pb.FIT_COLOR, xaxis="x2", yaxis="y2", showlegend=False),
    ]
    layout = {
        "xaxis": {"title": {"text": "time [ns]"}, "domain": [0.0, 0.46]},
        "yaxis": {"title": {"text": ylabel}},
        "xaxis2": {"title": {"text": "time [ns] (log)"}, "domain": [0.54, 1.0],
                   "type": "log", "anchor": "y2"},
        "yaxis2": {"title": {"text": ylabel}, "anchor": "x2"},
        "margin": {"l": 60, "r": 20, "t": 50, "b": 50}, "hovermode": "closest",
    }
    return {"data": data, "layout": layout}


def positive_log_x(x):
    """Plotly log axes drop ≤0; report whether any positive x exists."""
    x = np.asarray(x, dtype=float)
    return bool(np.any(np.isfinite(x) & (x > 0)))
