"""Qubit flux long distortion via Ramsey (1Q_19b).

Mirrors the node's plot_data: raw Ramsey signal heatmap (linear/log time), the
mapped flux-response line (linear/log), the reference amplitude-sweep heatmap,
and the global finite-pulse multi-exponential ``fitted_data`` overlay.

Not clickable: the updated parameter (``z.opx_output.exponential_filter``) comes
from a global fit, with no single-point correspondence.
"""
from __future__ import annotations

import numpy as np

from .. import models, plotbuild as pb
from . import flux_common as fc
from .base import FigureSpec, figure_key, qslice, qubit_index, qubits_of, split_key

FAMILY = ("1Q_19b_qubit_flux_long_distortion_ramsey",
          "1Q_21c_coupler_flux_long_distortion_ramsey")


def _signal_key(present: set) -> str | None:
    for v in ("state", "I"):
        if v in present:
            return v
    return None


def _ref_key(present: set) -> str | None:
    for v in ("state_ref", "I_ref"):
        if v in present:
            return v
    return None


def menu(bundle):
    qubits = qubits_of(bundle) or ["q"]
    multi = len(qubits) > 1
    sig = _signal_key(bundle.raw_vars)
    ref = _ref_key(bundle.raw_vars)
    has_fr = "flux_response" in bundle.fit_vars
    specs = []
    for q in qubits:
        rows = [
            ("raw_data_linear", "Raw Ramsey (linear)", "2d", bool(sig), "no signal"),
            ("raw_data_log", "Raw Ramsey (log)", "2d", bool(sig), "no signal"),
            ("flux_response_linear", "Flux response (linear)", "1d", has_fr, "no flux_response"),
            ("flux_response_log", "Flux response (log)", "1d", has_fr, "no flux_response"),
            ("ref_data", "Reference sweep", "2d", bool(ref), "no reference"),
            ("fitted_data", "Flux response + fit", "1d",
             has_fr and fc.fit_components(bundle.fit_results, q) is not None
             and _settle_time(bundle) is not None, "no fit/settle-time"),
        ]
        for base, title, kind, avail, reason in rows:
            specs.append(FigureSpec(key=figure_key(base, q),
                                    title=title + (f" — {q}" if multi else ""),
                                    kind=kind, available=avail, reason="" if avail else reason))
    return specs


def build(bundle, key):
    base, qname = split_key(key)
    raw, fit = bundle.raw, bundle.fit
    if base.startswith("raw_data"):
        return _raw_data(bundle, key, qname, log=base.endswith("log"))
    if base == "ref_data":
        return _ref_data(bundle, key, qname)
    if base.startswith("flux_response"):
        return _flux_response(bundle, key, qname, log=base.endswith("log"))
    if base == "fitted_data":
        return _fitted(bundle, key, qname)
    return None


def _raw_data(bundle, key, qname, log):
    raw = bundle.raw
    if not raw or not raw.get("vars"):
        return FigureSpec(key=key, title="Raw Ramsey", available=False, reason="no ds_raw")
    qidx = qubit_index(raw, qname)
    sig = _signal_key(set(raw["vars"]))
    if sig is None:
        return FigureSpec(key=key, title="Raw Ramsey", available=False, reason="no signal")
    z, dims = qslice(raw, sig, qidx)              # dims e.g. [frame, time]
    z = np.asarray(z, dtype=float)
    time = np.asarray(raw["coords"].get("time", []), dtype=float)
    frame = np.asarray(raw["coords"].get("frame", []), dtype=float)
    if dims and dims[0] == "time":
        z = z.T                                    # → [frame, time]
    data = [pb.heatmap(time, frame, z, colorbar_title=sig)]
    layout = {"xaxis": pb.axis("Ramsey time [ns]", log=log),
              "yaxis": pb.axis("frame rotation"),
              "margin": {"l": 70, "r": 30, "t": 40, "b": 50}}
    return FigureSpec(key=key, title="Raw Ramsey", kind="2d",
                      figure={"data": data, "layout": layout})


def _ref_data(bundle, key, qname):
    raw = bundle.raw
    if not raw or not raw.get("vars"):
        return FigureSpec(key=key, title="Reference sweep", available=False, reason="no ds_raw")
    qidx = qubit_index(raw, qname)
    ref = _ref_key(set(raw["vars"]))
    if ref is None:
        return FigureSpec(key=key, title="Reference sweep", available=False, reason="no reference")
    z, dims = qslice(raw, ref, qidx)              # dims e.g. [a, frame]
    z = np.asarray(z, dtype=float)
    a = np.asarray(raw["coords"].get("a", []), dtype=float)
    frame = np.asarray(raw["coords"].get("frame", []), dtype=float)
    if dims and dims[0] == "a":
        z = z.T                                    # → [frame, a]
    data = [pb.heatmap(a, frame, z, colorbar_title=ref)]
    layout = {"xaxis": pb.axis("flux amplitude"),
              "yaxis": pb.axis("frame rotation"),
              "margin": {"l": 70, "r": 30, "t": 40, "b": 50}}
    return FigureSpec(key=key, title="Reference sweep", kind="2d",
                      figure={"data": data, "layout": layout})


def _flux_response(bundle, key, qname, log):
    fit = bundle.fit
    if not fit or "flux_response" not in fit.get("vars", {}):
        return FigureSpec(key=key, title="Flux response", available=False, reason="no flux_response")
    qidx = qubit_index(fit, qname)
    fr, _ = qslice(fit, "flux_response", qidx)
    time = np.asarray(fit["coords"].get("time", []), dtype=float)
    data = [pb.line(time, np.asarray(fr, dtype=float), name="flux response",
                    color="#4e79a7", mode="lines+markers")]
    layout = {"xaxis": pb.axis("Ramsey time [ns]", log=log),
              "yaxis": pb.axis("flux response [V]"),
              "margin": {"l": 70, "r": 30, "t": 40, "b": 50}}
    return FigureSpec(key=key, title="Flux response", kind="1d",
                      figure={"data": data, "layout": layout})


def _fitted(bundle, key, qname):
    fit = bundle.fit
    comps = fc.fit_components(bundle.fit_results, qname)
    settle = _settle_time(bundle)
    if not fit or "flux_response" not in fit.get("vars", {}) or comps is None or settle is None:
        return FigureSpec(key=key, title="Flux response + fit",
                          available=False, reason="no fit/settle-time")
    qidx = qubit_index(fit, qname)
    a_dc, components = comps
    fr, _ = qslice(fit, "flux_response", qidx)
    fr = np.asarray(fr, dtype=float)
    time = np.asarray(fit["coords"].get("time", []), dtype=float)
    curve = models.multiexp_finite_pulse(time, a_dc, components, settle)
    figure = fc.fitted_two_panel(time, fr, curve, ylabel="flux response [V]")
    return FigureSpec(key=key, title="Flux response + fit", kind="1d", figure=figure)


def _settle_time(bundle):
    try:
        v = (getattr(bundle.run, "parameters", {}) or {}).get("flux_settle_time_in_ns")
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None
