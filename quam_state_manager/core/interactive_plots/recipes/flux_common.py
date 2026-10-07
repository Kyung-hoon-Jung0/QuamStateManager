"""Shared helpers for the two flux-long-distortion recipes (19a / 19b)."""
from __future__ import annotations

import numpy as np

from .. import fitcheck, plotbuild as pb


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


def fit_result(fit_results, qname) -> dict:
    res = (fit_results or {}).get(qname) or {}
    return res if isinstance(res, dict) else {}


def rms_mismatch(res, time, y, curve) -> str:
    """A note when the fitter's stored ``rms_error`` contradicts SM's curve, else ''.

    The long-distortion fitters store the RMS of their own residual. The global
    fitter computes it over the points it saw (finite, t > 0, inside
    ``fit_t_min_ns``..``fit_t_max_ns`` when recorded); the legacy sequential
    fitter over every point. Agreement with either proves the curve is the
    fitted one (docs/300; every archived row agrees to 1e-6).
    """
    stored = res.get("rms_error")
    try:
        stored = float(stored)
    except (TypeError, ValueError):
        return ""
    if not np.isfinite(stored):
        return ""
    t = np.asarray(time, dtype=float)
    y = np.asarray(y, dtype=float)
    f = np.asarray(curve, dtype=float)
    if t.shape != y.shape or y.shape != f.shape:
        return ""
    window = np.isfinite(y) & (t > 0)
    lo, hi = res.get("fit_t_min_ns"), res.get("fit_t_max_ns")
    try:
        if lo is not None and hi is not None and np.isfinite(float(lo)) and np.isfinite(float(hi)):
            window &= (t >= float(lo)) & (t <= float(hi))
    except (TypeError, ValueError):
        pass
    every = np.isfinite(y) & np.isfinite(f)
    for m in (window & np.isfinite(f), every):
        if not m.any():
            continue
        rms = float(np.sqrt(np.mean((y[m] - f[m]) ** 2)))
        if abs(rms - stored) <= 1e-4 * max(abs(stored), 1e-12 * max(1.0, float(np.nanmax(np.abs(y[m]))))):
            return ""
    return "Fit curve withheld: SM's reconstruction does not reproduce the fitter's stored RMS error."


def note_figure(figure, text):
    """Attach a fit note to a ``fitted_two_panel`` figure (left panel)."""
    if text:
        figure["layout"]["annotations"] = [pb.note(text)]
    return figure


def fitted_two_panel(time, y, fit_curve, ylabel="flux response [V]"):
    """Two-panel (linear | log-time) data+fit overlay figure dict.

    ``fit_curve=None`` draws the data alone (a withheld fit).
    """
    if fit_curve is None:
        data = [
            pb.line(time, y, name="data", color="#4e79a7", mode="lines+markers"),
            pb.line(time, y, color="#4e79a7", mode="lines+markers",
                    xaxis="x2", yaxis="y2", showlegend=False),
        ]
        return {"data": data, "layout": _two_panel_layout(ylabel)}
    data = [
        pb.line(time, y, name="data", color="#4e79a7", mode="lines+markers"),
        pb.line(time, fit_curve, name="fit", color=pb.FIT_COLOR),
        pb.line(time, y, color="#4e79a7", mode="lines+markers",
                xaxis="x2", yaxis="y2", showlegend=False),
        pb.line(time, fit_curve, name="fit", color=pb.FIT_COLOR, xaxis="x2", yaxis="y2",
                showlegend=False),
    ]
    layout = _two_panel_layout(ylabel)
    if fitcheck.runs_away(fit_curve, y):
        hold = fitcheck.data_range(y)
        if hold is not None:
            layout["yaxis"].update(range=hold, autorange=False)
            layout["yaxis2"].update(range=hold, autorange=False)
            layout["annotations"] = [pb.note(fitcheck.RUNAWAY_NOTE)]
    return {"data": data, "layout": layout}


def _two_panel_layout(ylabel):
    return {
        "xaxis": {"title": {"text": "time [ns]"}, "domain": [0.0, 0.46]},
        "yaxis": {"title": {"text": ylabel}},
        "xaxis2": {"title": {"text": "time [ns] (log)"}, "domain": [0.54, 1.0],
                   "type": "log", "anchor": "y2"},
        "yaxis2": {"title": {"text": ylabel}, "anchor": "x2"},
        "margin": {"l": 60, "r": 20, "t": 50, "b": 50}, "hovermode": "closest",
    }


def positive_log_x(x):
    """Plotly log axes drop ≤0; report whether any positive x exists."""
    x = np.asarray(x, dtype=float)
    return bool(np.any(np.isfinite(x) & (x > 0)))
