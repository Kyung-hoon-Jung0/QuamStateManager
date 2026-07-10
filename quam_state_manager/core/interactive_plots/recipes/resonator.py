"""Resonator spectroscopy (single) — interactive reproduction.

Mirrors ``calibration_utils/resonator_spectroscopy/plotting.py``: amplitude+fit,
raw phase (+group delay), detrended phase, and IQ circle. Handles both ds_fit
schemas: the new Lorentzian schema (``popt``/``f0``) drives the fit overlay; the
old peaks schema (``base_line``/``res_freq``) is detected and the overlay is
drawn from ``base_line`` when present, else traces render without an overlay.

Clickable (amplitude + phase): a clicked frequency (full_freq, displayed in GHz)
sets both ``resonator.f_01`` and ``resonator.RF_frequency`` (×1e9 → Hz), matching
the node's ``update_state``.
"""
from __future__ import annotations

import numpy as np

from .. import models
from ..plotbuild import FIT_COLOR, GROUP_DELAY, clean
from .base import FigureSpec, figure_key, qslice, qubit_index, qubits_of, split_key

FAMILY = ("1Q_03_resonator_spectroscopy",)

_BASE_FIGS = [
    ("amplitude", "Amplitude + fit", "1d"),
    ("amplitude_local", "Amplitude (local)", "1d"),
    ("phase", "Phase + group delay", "1d"),
    ("detrended_phase", "Detrended phase", "1d"),
    ("iq_circle", "IQ circle", "1d"),
]

# Half-window of the local amplitude zoom, in multiples of the fitted FWHM
# (matches the experiment's ``plot_local_amplitude_with_fit`` ±5×FWHM view).
_LOCAL_FWHM_SPAN = 5.0

_FREQ_TARGETS = [
    {"path": "qubits.{q}.resonator.f_01", "scale": 1e9},
    {"path": "qubits.{q}.resonator.RF_frequency", "scale": 1e9},
]


def _freq_click(qname):
    """Clicked frequency (GHz) → resonator.f_01 + RF_frequency (×1e9 → Hz)."""
    return {
        "axis": "x",
        "qubit": qname,
        "qubit_from": "customdata",
        "label": "Set readout frequency",
        "targets": _FREQ_TARGETS,
    }


def menu(bundle):
    qubits = qubits_of(bundle) or ["q"]
    multi = len(qubits) > 1
    raw = bundle.raw_vars
    specs = []
    for q in qubits:
        for base, title, kind in _BASE_FIGS:
            avail, reason = True, ""
            if not raw:
                avail, reason = False, "no ds_raw"
            elif base == "amplitude" and "IQ_abs" not in raw:
                avail, reason = False, "no IQ_abs"
            elif base == "amplitude_local" and not ("IQ_abs" in raw and "popt" in bundle.fit_vars):
                avail, reason = False, "no IQ_abs/fit"
            elif base in ("phase", "detrended_phase") and "phase" not in raw:
                avail, reason = False, "no phase"
            elif base == "detrended_phase" and not (
                    "detuning" in bundle.raw_coords or "RF_frequency" in bundle.raw_coords):
                avail, reason = False, "no frequency axis"
            elif base == "iq_circle" and not ({"I", "Q"} <= raw):
                avail, reason = False, "no I/Q"
            specs.append(FigureSpec(
                key=figure_key(base, q),
                title=title + (f" — {q}" if multi else ""),
                kind=kind, available=avail, reason=reason,
            ))
    return specs


def build(bundle, key):
    base, qname = split_key(key)
    raw = bundle.raw
    if not raw or not raw.get("vars"):
        return FigureSpec(key=key, title=base, available=False, reason="no ds_raw")
    qidx = qubit_index(raw, qname)
    builders = {
        "amplitude": _amplitude,
        "amplitude_local": _amplitude_local,
        "phase": _phase,
        "detrended_phase": _detrended_phase,
        "iq_circle": _iq_circle,
    }
    fn = builders.get(base)
    if fn is None:
        return None
    return fn(bundle, key, qidx, qname or "")


# --- shared helpers -----------------------------------------------------

def _detuning_mhz(raw):
    """Detuning axis in MHz. ``single`` variants store a ``detuning`` coord;
    ``wide`` variants store only ``RF_frequency`` → detuning from its mean."""
    det = np.asarray(raw["coords"].get("detuning", []), dtype=float)
    if det.size:
        return det / 1e6
    rf = np.asarray(raw.get("coords", {}).get("RF_frequency", []), dtype=float)
    if rf.size:
        return (rf - rf.mean()) / 1e6
    return np.asarray([], dtype=float)


def _full_freq_hz(raw, qidx):
    # Single variants store a per-qubit ``full_freq`` var; the ``wide`` variants
    # store a shared 1-D ``RF_frequency`` coordinate instead.
    if "full_freq" in raw.get("vars", {}):
        ff, _ = qslice(raw, "full_freq", qidx)
        return np.asarray(ff, dtype=float)
    rf = raw.get("coords", {}).get("RF_frequency")
    if rf is not None:
        return np.asarray(rf, dtype=float)
    return np.asarray([], dtype=float)


def _twin_detuning_trace(det_mhz, y):
    """Transparent copy of a trace on the top (detuning) x-axis, so xaxis2 renders."""
    return {
        "x": clean(det_mhz), "y": clean(y), "type": "scatter", "mode": "lines",
        "xaxis": "x2", "showlegend": False, "hoverinfo": "skip",
        "line": {"color": "rgba(0,0,0,0)"},
    }


def _popt(bundle, qidx):
    fit = bundle.fit
    if not fit or "popt" not in fit.get("vars", {}):
        return None
    popt, _ = qslice(fit, "popt", qidx)
    popt = np.asarray(popt, dtype=float).ravel()
    if popt.size != 5 or not np.all(np.isfinite(popt)):
        return None
    return popt


# --- figures ------------------------------------------------------------

def _amplitude(bundle, key, qidx, qname):
    raw = bundle.raw
    ff_hz = _full_freq_hz(raw, qidx)
    x_ghz = ff_hz / 1e9
    iq, _ = qslice(raw, "IQ_abs", qidx)
    y_mv = np.asarray(iq, dtype=float) * 1e3
    det_mhz = _detuning_mhz(raw)

    data = [
        {"x": clean(x_ghz), "y": clean(y_mv), "type": "scatter", "mode": "lines",
         "name": qname or "amplitude", "line": {"color": "#4e79a7"},
         "customdata": [qname] * len(x_ghz)},
    ]
    if det_mhz.size:
        data.append(_twin_detuning_trace(det_mhz, y_mv))
    shapes = []
    popt = _popt(bundle, qidx)
    if popt is not None:
        curve_mv = models.lorentzian_dip_linbg(ff_hz, *popt) * 1e3
        data.append({"x": clean(x_ghz), "y": clean(curve_mv), "type": "scatter",
                     "mode": "lines", "name": "fit",
                     "line": {"color": FIT_COLOR, "dash": "dash"}})
        f0, fwhm = float(popt[0]), float(popt[1])
        if np.isfinite(f0) and np.isfinite(fwhm) and fwhm > 0:
            shapes.append({"type": "rect", "xref": "x", "yref": "paper",
                           "x0": (f0 - fwhm / 2) / 1e9, "x1": (f0 + fwhm / 2) / 1e9,
                           "y0": 0, "y1": 1, "fillcolor": "rgba(225,87,89,0.15)",
                           "line": {"width": 0}, "layer": "below"})

    layout = {
        "xaxis": {"title": {"text": "RF frequency [GHz]"}},
        "yaxis": {"title": {"text": "Amplitude [mV]"}},
        "shapes": shapes, "hovermode": "closest",
        "margin": {"l": 60, "r": 30, "t": 50, "b": 50},
    }
    if det_mhz.size:
        layout["xaxis2"] = {"overlaying": "x", "side": "top", "title": {"text": "Detuning [MHz]"}}
    return FigureSpec(key=key, title="Amplitude + fit", kind="1d",
                      figure={"data": data, "layout": layout}, clickable=_freq_click(qname))


def _amplitude_local(bundle, key, qidx, qname):
    """Amplitude + fit zoomed to ±``_LOCAL_FWHM_SPAN``×FWHM around the fitted f0.

    Mirrors the experiment's ``plot_local_amplitude_with_fit`` close-up of the
    resonance peak. Builds the full amplitude figure, then constrains the x/y
    range to the fitted peak window. Falls back to the full view when no fit.
    """
    spec = _amplitude(bundle, key, qidx, qname)
    popt = _popt(bundle, qidx)
    if spec.figure is None or popt is None:
        spec.title = "Amplitude (local)"
        return spec
    f0, fwhm = float(popt[0]), float(popt[1])
    if np.isfinite(f0) and np.isfinite(fwhm) and fwhm > 0:
        x0 = (f0 - _LOCAL_FWHM_SPAN * fwhm) / 1e9
        x1 = (f0 + _LOCAL_FWHM_SPAN * fwhm) / 1e9
        layout = spec.figure["layout"]
        layout.setdefault("xaxis", {})
        layout["xaxis"]["range"] = [x0, x1]
        layout["xaxis"]["autorange"] = False
        # Tighten the y-range to the amplitude inside the window.
        ff_hz = _full_freq_hz(bundle.raw, qidx)
        iq, _ = qslice(bundle.raw, "IQ_abs", qidx)
        x_ghz = ff_hz / 1e9
        y_mv = np.asarray(iq, dtype=float) * 1e3
        m = np.isfinite(x_ghz) & np.isfinite(y_mv) & (x_ghz >= x0) & (x_ghz <= x1)
        if m.any():
            lo, hi = float(np.min(y_mv[m])), float(np.max(y_mv[m]))
            pad = (hi - lo) * 0.1 or 1.0
            layout.setdefault("yaxis", {})
            layout["yaxis"]["range"] = [lo - pad, hi + pad]
            layout["yaxis"]["autorange"] = False
    spec.title = f"Amplitude (local, ±{int(_LOCAL_FWHM_SPAN)}×FWHM)"
    return spec


def _phase(bundle, key, qidx, qname):
    raw = bundle.raw
    ff_hz = _full_freq_hz(raw, qidx)
    x_ghz = ff_hz / 1e9
    phase, _ = qslice(raw, "phase", qidx)
    phase = np.asarray(phase, dtype=float)
    det_mhz = _detuning_mhz(raw)

    data = [
        {"x": clean(x_ghz), "y": clean(phase), "type": "scatter", "mode": "lines",
         "name": qname or "phase", "line": {"color": "#4e79a7"},
         "customdata": [qname] * len(x_ghz)},
    ]
    if det_mhz.size:
        data.append(_twin_detuning_trace(det_mhz, phase))
    # Group delay -dφ/df on the secondary (right) y-axis, in ns. Guard against
    # non-monotonic / duplicated freq samples (wide variants) → NaN→None on clean.
    if ff_hz.size >= 2:
        with np.errstate(divide="ignore", invalid="ignore"):
            tau_ns = -np.gradient(phase, ff_hz) * 1e9
        data.append({"x": clean(x_ghz), "y": clean(tau_ns), "type": "scatter",
                     "mode": "lines", "name": "-dφ/df [ns]", "yaxis": "y2",
                     "line": {"color": GROUP_DELAY, "width": 1}, "opacity": 0.7})

    shapes = []
    popt = _popt(bundle, qidx)
    if popt is not None and np.isfinite(popt[0]):
        shapes.append({"type": "line", "xref": "x", "yref": "paper",
                       "x0": popt[0] / 1e9, "x1": popt[0] / 1e9, "y0": 0, "y1": 1,
                       "line": {"color": FIT_COLOR, "dash": "dash", "width": 1}})

    layout = {
        "xaxis": {"title": {"text": "RF frequency [GHz]"}},
        "yaxis": {"title": {"text": "phase [rad]"}},
        "yaxis2": {"overlaying": "y", "side": "right",
                   "title": {"text": "-dφ/df [ns]"}, "showgrid": False},
        "shapes": shapes, "hovermode": "closest",
        "margin": {"l": 60, "r": 60, "t": 50, "b": 50},
    }
    if det_mhz.size:
        layout["xaxis2"] = {"overlaying": "x", "side": "top", "title": {"text": "Detuning [MHz]"}}
    return FigureSpec(key=key, title="Phase + group delay", kind="1d",
                      figure={"data": data, "layout": layout}, clickable=_freq_click(qname))


def _detrended_phase(bundle, key, qidx, qname):
    raw = bundle.raw
    phase, _ = qslice(raw, "phase", qidx)
    phase = np.asarray(phase, dtype=float)
    det_mhz = _detuning_mhz(raw)

    center = 0.0
    halfwidth = 0.0
    popt = _popt(bundle, qidx)
    if popt is not None:
        f0, fwhm = float(popt[0]), float(popt[1])
        ff_hz = _full_freq_hz(raw, qidx)
        det_hz = np.asarray(raw["coords"].get("detuning", []), dtype=float)
        # Absolute frequency the detuning axis is centered on: the sweep's
        # zero-detuning point (single) or the RF_frequency mean (wide).
        if ff_hz.size and det_hz.size:
            res_center = ff_hz[0] - det_hz[0]
        elif ff_hz.size:
            res_center = float(ff_hz.mean())
        else:
            res_center = None
        if res_center is not None and np.isfinite(f0):
            center = (f0 - res_center) / 1e6
            halfwidth = 3.0 * fwhm / 1e6

    residual = models.detrend_phase_poly(phase, det_mhz, center=center, halfwidth=halfwidth)
    data = [{"x": clean(det_mhz), "y": clean(residual), "type": "scatter",
             "mode": "lines", "name": qname or "residual", "line": {"color": "#4e79a7"}}]
    shapes = [{"type": "line", "xref": "paper", "yref": "y", "x0": 0, "x1": 1,
               "y0": 0, "y1": 0, "line": {"color": "gray", "dash": "dot", "width": 0.5}}]
    if popt is not None:
        shapes.append({"type": "line", "xref": "x", "yref": "paper",
                       "x0": center, "x1": center, "y0": 0, "y1": 1,
                       "line": {"color": FIT_COLOR, "dash": "dash", "width": 1}})
    layout = {
        "xaxis": {"title": {"text": "Detuning [MHz]"}},
        "yaxis": {"title": {"text": "phase residual [rad]"}},
        "shapes": shapes, "hovermode": "closest",
        "margin": {"l": 60, "r": 30, "t": 40, "b": 50},
    }
    return FigureSpec(key=key, title="Detrended phase", kind="1d",
                      figure={"data": data, "layout": layout})


def _iq_circle(bundle, key, qidx, qname):
    raw = bundle.raw
    i_v, _ = qslice(raw, "I", qidx)
    q_v, _ = qslice(raw, "Q", qidx)
    i_mv = np.asarray(i_v, dtype=float) * 1e3
    q_mv = np.asarray(q_v, dtype=float) * 1e3
    det_mhz = _detuning_mhz(raw)

    n = len(i_mv)
    if det_mhz.size == n:
        cvals, cbar = det_mhz, "Detuning [MHz]"
    else:
        cvals, cbar = np.arange(n), "index"
    data = [
        {"x": clean(i_mv), "y": clean(q_mv), "type": "scatter", "mode": "lines",
         "line": {"color": "rgba(128,128,128,0.35)", "width": 1}, "showlegend": False,
         "hoverinfo": "skip"},
        {"x": clean(i_mv), "y": clean(q_mv), "type": "scatter", "mode": "markers",
         "name": qname or "IQ", "marker": {"size": 5, "color": clean(cvals),
         "colorscale": "Viridis", "colorbar": {"title": {"text": cbar}}}},
    ]
    # Red star at the IQ point closest to the fitted resonance.
    popt = _popt(bundle, qidx)
    if popt is not None and np.isfinite(popt[0]):
        ff_hz = _full_freq_hz(raw, qidx)
        if ff_hz.size:
            idx = int(np.argmin(np.abs(ff_hz - popt[0])))
            data.append({"x": [float(i_mv[idx])], "y": [float(q_mv[idx])],
                         "type": "scatter", "mode": "markers", "name": "f₀",
                         "marker": {"symbol": "star", "size": 14, "color": FIT_COLOR}})

    layout = {
        "xaxis": {"title": {"text": "I [mV]"}},
        "yaxis": {"title": {"text": "Q [mV]"}, "scaleanchor": "x", "scaleratio": 1},
        "annotations": _direction_arrows(i_mv, q_mv),
        "hovermode": "closest", "margin": {"l": 60, "r": 30, "t": 40, "b": 50},
    }
    return FigureSpec(key=key, title="IQ circle", kind="1d",
                      figure={"data": data, "layout": layout})


def _direction_arrows(i_mv, q_mv, n=6):
    """Evenly-spaced arrows along the IQ trace to indicate sweep direction."""
    arrows = []
    if len(i_mv) < 2:
        return arrows
    idxs = np.linspace(0, len(i_mv) - 2, n, dtype=int)
    for ai in idxs:
        x0, y0 = float(i_mv[ai]), float(q_mv[ai])
        x1, y1 = float(i_mv[ai + 1]), float(q_mv[ai + 1])
        if not all(np.isfinite([x0, y0, x1, y1])):
            continue
        arrows.append({
            "x": x1, "y": y1, "ax": x0, "ay": y0,
            "xref": "x", "yref": "y", "axref": "x", "ayref": "y",
            "showarrow": True, "arrowhead": 2, "arrowsize": 1.2,
            "arrowwidth": 1, "arrowcolor": "rgba(80,80,80,0.6)", "text": "",
        })
    return arrows
