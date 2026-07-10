"""Qubit flux long distortion via qubit spectroscopy (1Q_19a).

Mirrors the node's plot_data: IQ-abs heatmaps (linear/log time), a phase
heatmap, center-frequency-vs-time and flux-response-vs-time line plots (linear/
log), and the global multi-exponential ``fitted_data`` overlay. Everything reads
from ds_fit (the node persists processed + derived arrays there).

Not clickable: the updated parameter (``z.opx_output.exponential_filter``) comes
from a global multi-exponential fit, with no single-point correspondence.
"""
from __future__ import annotations

import numpy as np

from .. import models, plotbuild as pb
from . import flux_common as fc
from .base import FigureSpec, figure_key, qslice, qubit_index, qubits_of, split_key

FAMILY = ("1Q_19a_qubit_flux_long_distortion_qubitspec",
          "1Q_21b_coupler_flux_long_distortion_qubitspec")

_FIGS = [
    ("iq_abs_linear", "|IQ| vs time (linear)", "2d", "IQ_abs"),
    ("iq_abs_log", "|IQ| vs time (log)", "2d", "IQ_abs"),
    ("phase", "Phase vs time", "2d", "phase"),
    ("center_freq_linear", "Center frequency (linear)", "1d", "center_freqs"),
    ("center_freq_log", "Center frequency (log)", "1d", "center_freqs"),
    ("flux_response_linear", "Flux response (linear)", "1d", "flux_response"),
    ("flux_response_log", "Flux response (log)", "1d", "flux_response"),
    ("fitted_data", "Flux response + fit", "1d", "flux_response"),
]


def menu(bundle):
    qubits = qubits_of(bundle) or ["q"]
    multi = len(qubits) > 1
    fitv = bundle.fit_vars
    specs = []
    for q in qubits:
        for base, title, kind, need in _FIGS:
            avail = need in fitv
            reason = "" if avail else f"no {need}"
            if base == "fitted_data" and avail and not fc.fit_components(bundle.fit_results, q):
                avail, reason = False, "no fit components"
            specs.append(FigureSpec(key=figure_key(base, q),
                                    title=title + (f" — {q}" if multi else ""),
                                    kind=kind, available=avail, reason=reason))
    return specs


def build(bundle, key):
    base, qname = split_key(key)
    fit = bundle.fit
    if not fit or not fit.get("vars"):
        return FigureSpec(key=key, title=base, available=False, reason="no ds_fit")
    qidx = qubit_index(fit, qname)
    time = np.asarray(fit["coords"].get("time", []), dtype=float)

    if base in ("iq_abs_linear", "iq_abs_log"):
        return _iq_heatmap(bundle, key, qidx, time, log=base.endswith("log"))
    if base == "phase":
        return _phase_heatmap(bundle, key, qidx, time)
    if base in ("center_freq_linear", "center_freq_log"):
        return _center_freq(bundle, key, qname, qidx, time, log=base.endswith("log"))
    if base in ("flux_response_linear", "flux_response_log"):
        return _flux_response(bundle, key, qidx, time, log=base.endswith("log"))
    if base == "fitted_data":
        return _fitted(bundle, key, qname, qidx, time)
    return None


def _freq_ghz(fit, qidx):
    var = "freq_full" if "freq_full" in fit["vars"] else "full_freq"
    ff, _ = qslice(fit, var, qidx)
    return np.asarray(ff, dtype=float) / 1e9


def _iq_heatmap(bundle, key, qidx, time, log):
    fit = bundle.fit
    z, dims = qslice(fit, "IQ_abs", qidx)         # dims e.g. [detuning, time]
    z = np.asarray(z, dtype=float)
    freq_ghz = _freq_ghz(fit, qidx)
    # Orient z as [y=freq(detuning), x=time].
    if dims and dims[0] == "time":
        z = z.T
    data = [pb.heatmap(time, freq_ghz, z, colorbar_title="|IQ|")]
    layout = {"xaxis": pb.axis("Flux pulse duration [ns]", log=log),
              "yaxis": pb.axis("Frequency [GHz]"),
              "margin": {"l": 70, "r": 30, "t": 40, "b": 50}}
    return FigureSpec(key=key, title="|IQ| vs time", kind="2d",
                      figure={"data": data, "layout": layout})


def _phase_heatmap(bundle, key, qidx, time):
    fit = bundle.fit
    z, dims = qslice(fit, "phase", qidx)          # dims e.g. [time, detuning]
    z = np.asarray(z, dtype=float)
    freq_ghz = _freq_ghz(fit, qidx)
    if dims and dims[0] == "time":
        z = z.T                                    # → [detuning, time]
    data = [pb.heatmap(time, freq_ghz, z, colorscale="RdBu", zmin=-np.pi, zmax=np.pi,
                       colorbar_title="phase [rad]")]
    layout = {"xaxis": pb.axis("Flux pulse duration [ns]"),
              "yaxis": pb.axis("Frequency [GHz]"),
              "margin": {"l": 70, "r": 30, "t": 40, "b": 50}}
    return FigureSpec(key=key, title="Phase vs time", kind="2d",
                      figure={"data": data, "layout": layout})


def _center_freq(bundle, key, qname, qidx, time, log):
    fit = bundle.fit
    cf, _ = qslice(fit, "center_freqs", qidx)
    cf = np.asarray(cf, dtype=float)
    rf = fc.qubit_rf_hz(bundle.quam_state, qname)
    finite = cf[np.isfinite(cf)]
    looks_relative = finite.size and float(np.nanmedian(np.abs(finite))) < 1e9
    if rf is not None and looks_relative:
        y = (cf + rf) / 1e9
        ylabel, title = "Qubit frequency [GHz]", "Center frequency"
    elif looks_relative:
        y = cf / 1e6
        ylabel, title = "Detuning [MHz]", "Center frequency (relative)"
    else:
        y = cf / 1e9
        ylabel, title = "Qubit frequency [GHz]", "Center frequency"
    data = [pb.line(time, y, name=qname or "center", color="#4e79a7", mode="lines+markers")]
    layout = {"xaxis": pb.axis("Flux pulse duration [ns]", log=log),
              "yaxis": pb.axis(ylabel), "margin": {"l": 70, "r": 30, "t": 40, "b": 50}}
    return FigureSpec(key=key, title=title, kind="1d",
                      figure={"data": data, "layout": layout})


def _flux_response(bundle, key, qidx, time, log):
    fit = bundle.fit
    fr, _ = qslice(fit, "flux_response", qidx)
    data = [pb.line(time, np.asarray(fr, dtype=float), name="flux response",
                    color="#4e79a7", mode="lines+markers")]
    layout = {"xaxis": pb.axis("Flux pulse duration [ns]", log=log),
              "yaxis": pb.axis("flux response [V]"),
              "margin": {"l": 70, "r": 30, "t": 40, "b": 50}}
    return FigureSpec(key=key, title="Flux response", kind="1d",
                      figure={"data": data, "layout": layout})


def _fitted(bundle, key, qname, qidx, time):
    fit = bundle.fit
    comps = fc.fit_components(bundle.fit_results, qname)
    if comps is None:
        return FigureSpec(key=key, title="Flux response + fit",
                          available=False, reason="no fit components")
    a_dc, components = comps
    fr, _ = qslice(fit, "flux_response", qidx)
    fr = np.asarray(fr, dtype=float)
    curve = models.multiexp_decay(time, a_dc, components)
    figure = fc.fitted_two_panel(time, fr, curve, ylabel="flux response [V]")
    return FigureSpec(key=key, title="Flux response + fit", kind="1d", figure=figure)
