"""Ramsey vs flux calibration (1Q_23) — interactive reproduction (view-only).

Raw Ramsey-vs-flux heatmap, parabola fit, and the unfolded qubit-frequency-
vs-flux curve. CLICKABLE with contract-faithful baking (contracts.py): flux
clicks are deltas from the PRE-update parked offset (patches-aware); the
freq-vs-flux curve's y is absolute (RF assign + f_01 lock-step). The quad term
stays fit-only (a curvature is not a point).
"""
from __future__ import annotations

import numpy as np

from .. import plotbuild as pb
from .base import FigureSpec, figure_key, qslice, qubit_index, qubits_of, split_key

FAMILY = ("1Q_23_ramsey_vs_flux_calibration",)


def menu(bundle):
    qubits = qubits_of(bundle) or ["q"]
    multi = len(qubits) > 1
    specs = []
    for q in qubits:
        suffix = f" — {q}" if multi else ""
        specs.append(FigureSpec(figure_key("raw_data", q), "Ramsey vs flux" + suffix, "2d",
                                available="state" in bundle.raw_vars,
                                reason="" if "state" in bundle.raw_vars else "no state"))
        specs.append(FigureSpec(figure_key("qubit_freq_vs_flux", q), "Qubit freq vs flux" + suffix,
                                "1d", available="f_qubit_vs_flux" in bundle.fit_vars,
                                reason="" if "f_qubit_vs_flux" in bundle.fit_vars else "no fit"))
        have_par = {"unfolded_frequency", "quad_term"} <= bundle.fit_vars
        specs.append(FigureSpec(figure_key("parabola_fit", q), "Parabola fit" + suffix,
                                "1d", available=have_par,
                                reason="" if have_par else "no fit"))
    return specs


def build(bundle, key):
    base, qname = split_key(key)
    if base == "raw_data":
        raw = bundle.raw
        if not raw or "state" not in raw.get("vars", {}):
            return FigureSpec(key=key, title="Ramsey vs flux", available=False, reason="no state")
        qidx = qubit_index(raw, qname)
        flux = np.asarray(raw["coords"].get("flux_bias", []), dtype=float)
        t = np.asarray(raw["coords"].get("idle_times", []), dtype=float)
        z, dims = qslice(raw, "state", qidx)
        z = np.asarray(z, dtype=float)
        if dims and dims[0] != "flux_bias" and z.ndim == 2:
            z = z.T
        data = [pb.heatmap(t, flux, z, colorbar_title="state")]
        layout = {"xaxis": {"title": {"text": "idle time [ns]"}},
                  "yaxis": {"title": {"text": "flux bias \u0394 [V]"}},
                  "margin": {"l": 60, "r": 30, "t": 50, "b": 50}}
        # The flux axis is a DELTA around the parked offset — the node does
        # ``offset += flux_offset``. Baked: offset_pre + clicked (patches-aware
        # pre-update provenance; see contracts.flux_delta_targets).
        from .. import contracts as _contracts
        clickable = _contracts.flux_delta_targets(bundle, qname, axis="y")
        return FigureSpec(key=key, title="Ramsey vs flux", kind="2d",
                          figure={"data": data, "layout": layout},
                          clickable=clickable)

    if base == "parabola_fit":
        return _parabola_fit(bundle, key, qname)

    # qubit_freq_vs_flux
    fit = bundle.fit
    if not fit or "f_qubit_vs_flux" not in fit.get("vars", {}):
        return FigureSpec(key=key, title="Qubit freq vs flux", available=False, reason="no fit")
    qidx = qubit_index(fit, qname)
    flux = np.asarray((fit["coords"].get("flux_bias")
                       or (bundle.raw or {}).get("coords", {}).get("flux_bias") or []), dtype=float)
    y, _ = qslice(fit, "f_qubit_vs_flux", qidx)
    data = [pb.line(flux, np.asarray(y, dtype=float), name=qname or "f_qubit",
                    color="#4e79a7", mode="lines+markers")]
    shapes = []
    foff = _scalar(fit, "flux_offset", qidx)
    if foff is not None and np.isfinite(foff):
        shapes.append(pb.vline(foff, color=pb.FIT_COLOR, dash="dash"))
    layout = {"xaxis": {"title": {"text": "flux bias [V]"}},
              "yaxis": {"title": {"text": "qubit frequency [GHz]"}}, "shapes": shapes,
              "hovermode": "closest", "margin": {"l": 60, "r": 30, "t": 40, "b": 50}}
    # x = flux Δ → z offset shift; y = ABSOLUTE qubit RF [GHz] → RF assign +
    # f_01 lock-step shift (the node's own update semantics). One click stages
    # both quantities of the clicked parabola point.
    from .. import contracts as _contracts
    clickable = None
    fx = _contracts.flux_delta_targets(bundle, qname, axis="x")
    fy = _contracts.absolute_freq_targets(bundle, qname, axis="y", axis_scale=1e9)
    if fx or fy:
        clickable = {"qubit": qname, "label": "Set flux offset + qubit frequency",
                     "targets": ((fx or {}).get("targets", [])
                                 + (fy or {}).get("targets", []))}
    return FigureSpec(key=key, title="Qubit freq vs flux", kind="1d",
                      figure={"data": data, "layout": layout},
                      clickable=clickable)


def _parabola_fit(bundle, key, qname):
    """Sweet-spot detuning vs flux: unfolded Ramsey frequency + parabola fit.

    Mirrors ``ramsey_versus_flux_calibration/plotting.py``
    ``plot_individual_parabolas_with_fit``: all y-values are in MHz relative to
    the applied (artificial) detuning, so the parabola's vertex sits at the
    qubit sweet spot. ``flux_offset`` (sweet-spot flux) and the Nyquist-zone
    boundaries are drawn as guide lines.
    """
    fit = bundle.fit
    if not fit or not ({"unfolded_frequency", "quad_term"} <= set(fit.get("vars", {}))):
        return FigureSpec(key=key, title="Parabola fit", available=False, reason="no fit")
    qidx = qubit_index(fit, qname)
    flux = np.asarray(fit["coords"].get("flux_bias", []), dtype=float)
    unfolded, _ = qslice(fit, "unfolded_frequency", qidx)
    unfolded = np.asarray(unfolded, dtype=float)
    det = _scalar(fit, "artifitial_detuning", qidx) or 0.0  # MHz

    data = [pb.line(flux, unfolded * 1e3 - det, name="unfolded freq",
                    color="#4e79a7", mode="lines+markers")]

    # Aliased (raw fitted) frequency for reference — needs fit_results[fit_vals="f"].
    try:
        if "fit_results" in fit["vars"]:
            fit_vals = [str(v) for v in fit["coords"].get("fit_vals", [])]
            if "f" in fit_vals:
                fr, dims = qslice(fit, "fit_results", qidx)
                fr = np.asarray(fr, dtype=float)
                if "fit_vals" in dims:
                    aliased = np.take(fr, fit_vals.index("f"), axis=dims.index("fit_vals"))
                    data.append(pb.line(flux, np.abs(aliased) * 1e3 - det, name="aliased freq",
                                         color="#999999", mode="lines+markers", opacity=0.35))
    except Exception:  # noqa: BLE001 — reference trace is best-effort
        pass

    shapes = []
    quad = _scalar(fit, "quad_term", qidx)
    v0 = _scalar(fit, "flux_offset", qidx)
    f0 = _scalar(fit, "freq_offset", qidx)
    if None not in (quad, v0, f0) and flux.size:
        parabola = (-quad / 1e3) * (flux - v0) ** 2 + (f0 * 1e-3) - det
        data.append(pb.line(flux, parabola, name="parabola fit", color=pb.GROUP_DELAY, width=2))
        shapes.append(pb.vline(v0, color=pb.FIT_COLOR, dash="dash"))
        shapes.append(pb.hline(f0 * 1e-3 - det, color=pb.ACCENT, dash="dash"))

    # Nyquist-zone boundary lines.
    try:
        nz, _ = qslice(fit, "nyquist_zones", qidx)
        f_nyq = _scalar(fit, "f_nyquist", qidx)
        max_zone = int(np.nanmax(np.asarray(nz, dtype=float)))
        if f_nyq is not None and np.isfinite(f_nyq):
            for n in range(1, max_zone + 1):
                shapes.append(pb.hline(n * f_nyq * 1e3 - det, color="gray", dash="dot", width=0.5))
    except Exception:  # noqa: BLE001
        pass

    layout = {"xaxis": {"title": {"text": "flux bias [V]"}},
              "yaxis": {"title": {"text": "sweet-spot detuning [MHz]"}},
              "shapes": shapes, "hovermode": "closest",
              "margin": {"l": 60, "r": 30, "t": 50, "b": 50}}
    from .. import contracts as _contracts
    clickable = _contracts.flux_delta_targets(bundle, qname, axis="x")
    return FigureSpec(key=key, title="Parabola fit", kind="1d",
                      figure={"data": data, "layout": layout},
                      clickable=clickable)


def _scalar(fit, var, qidx):
    if var not in fit.get("vars", {}):
        return None
    try:
        return float(np.asarray(qslice(fit, var, qidx)[0]).ravel()[0])
    except Exception:  # noqa: BLE001
        return None
