"""Qubit flux short distortion / cryoscope (1Q_20) — interactive (view-only).

Reproduces the figures whose arrays are persisted in ds_fit: raw heatmap,
unwrapped phase, cryoscope frequency (lin/log), flux response (lin/log), and the
IIR multi-exponential fit overlay. The FIR-diagnostic figures and ramsey_curve
are NOT persisted → unavailable. View-only (updates a global filter).
"""
from __future__ import annotations

import numpy as np

from .. import models, plotbuild as pb
from . import flux_common as fc
from .base import FigureSpec, figure_key, qslice, qubit_index, qubits_of, split_key

FAMILY = ("1Q_20_qubit_flux_short_distortion",)


def _time(bundle, qidx):
    for src in (bundle.fit, bundle.raw):
        if src and "time" in src.get("coords", {}):
            return np.asarray(src["coords"]["time"], dtype=float)
    return None


def _components(fit_results, qname):
    res = (fit_results or {}).get(qname) or {}
    comps, a_dc = res.get("components"), res.get("a_dc")
    if not isinstance(comps, (list, tuple)) or a_dc is None:
        return None
    pairs = []
    for c in comps:
        if isinstance(c, (list, tuple)) and len(c) == 2:
            try:
                pairs.append((float(c[0]), float(c[1])))
            except (TypeError, ValueError):
                return None
    return (float(a_dc), pairs) if pairs else None


def menu(bundle):
    qubits = qubits_of(bundle) or ["q"]
    multi = len(qubits) > 1
    fv = bundle.fit_vars
    rows = [
        ("raw", "Raw (frame × time)", "2d", "I" in bundle.raw_vars, "no I"),
        ("unwrapped_phase", "Unwrapped phase", "1d", "phase" in fv, "no phase"),
        ("cryoscope_freq_linear", "Cryoscope freq (linear)", "1d", "freq" in fv, "no freq"),
        ("cryoscope_freq_log", "Cryoscope freq (log)", "1d", "freq" in fv, "no freq"),
        ("flux_response_linear", "Flux response (linear)", "1d", "flux" in fv, "no flux"),
        ("flux_response_log", "Flux response (log)", "1d", "flux" in fv, "no flux"),
        ("iir_fitted_data", "Flux response + IIR fit", "1d",
         "flux" in fv and _components(bundle.fit_results, qubits[0]) is not None, "no fit components"),
    ]
    specs = []
    for q in qubits:
        for base, title, kind, avail, reason in rows:
            specs.append(FigureSpec(figure_key(base, q), title + (f" — {q}" if multi else ""),
                                    kind=kind, available=avail, reason="" if avail else reason))
    return specs


def build(bundle, key):
    base, qname = split_key(key)
    raw, fit = bundle.raw, bundle.fit
    if base == "raw":
        if not raw or "I" not in raw.get("vars", {}):
            return FigureSpec(key=key, title="Raw", available=False, reason="no I")
        qidx = qubit_index(raw, qname)
        t = np.asarray(raw["coords"].get("time", []), dtype=float)
        frame = np.asarray(raw["coords"].get("frame", []), dtype=float)
        z, dims = qslice(raw, "I", qidx)
        z = np.asarray(z, dtype=float)
        if dims and dims[0] != "frame" and z.ndim == 2:
            z = z.T
        data = [pb.heatmap(t, frame, z, colorbar_title="I")]
        layout = {"xaxis": {"title": {"text": "Cryoscope pulse duration [ns]"}},
                  "yaxis": {"title": {"text": "frame rotation"}},
                  "margin": {"l": 60, "r": 30, "t": 50, "b": 50}}
        return FigureSpec(key=key, title="Raw (frame × time)", kind="2d",
                          figure={"data": data, "layout": layout})

    if not fit or not fit.get("vars"):
        return FigureSpec(key=key, title=base, available=False, reason="no ds_fit")
    qidx = qubit_index(fit, qname)
    t = _time(bundle, qidx)

    if base == "iir_fitted_data":
        comps = _components(bundle.fit_results, qname)
        if "flux" not in fit["vars"] or comps is None or t is None:
            return FigureSpec(key=key, title="IIR fit", available=False, reason="no fit components")
        a_dc, components = comps
        flux, _ = qslice(fit, "flux", qidx)
        curve = models.multiexp_decay(t, a_dc, components)
        figure = fc.fitted_two_panel(t, np.asarray(flux, dtype=float), curve, ylabel="flux response")
        return FigureSpec(key=key, title="Flux response + IIR fit", kind="1d", figure=figure)

    var = {"unwrapped_phase": "phase", "cryoscope_freq_linear": "freq",
           "cryoscope_freq_log": "freq", "flux_response_linear": "flux",
           "flux_response_log": "flux"}.get(base)
    if var is None or var not in fit["vars"] or t is None:
        return FigureSpec(key=key, title=base, available=False, reason="not reproducible")
    y, _ = qslice(fit, var, qidx)
    ylabel = {"phase": "phase [rad]", "freq": "frequency", "flux": "flux response"}[var]
    log = base.endswith("log")
    data = [pb.line(t, np.asarray(y, dtype=float), name=var, color="#4e79a7", mode="lines+markers")]
    layout = {"xaxis": pb.axis("Cryoscope pulse duration [ns]", log=log),
              "yaxis": pb.axis(ylabel), "hovermode": "closest",
              "margin": {"l": 60, "r": 30, "t": 40, "b": 50}}
    return FigureSpec(key=key, title=base.replace("_", " ").title(), kind="1d",
                      figure={"data": data, "layout": layout})
