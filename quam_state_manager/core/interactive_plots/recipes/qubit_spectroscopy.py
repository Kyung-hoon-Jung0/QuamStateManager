"""Qubit spectroscopy (1Q_08, 1Q_08_new, 1Q_28 e→f) — interactive reproduction.

1D rotated-I vs frequency with the node's Lorentzian-peak overlay + the fitted
qubit frequency marker. Handles both ds_fit schemas (docs/300):

* ``popt`` = [f0, fwhm, amp, bg0, bg1] (the curve_fit generation): a PEAK on a
  linear background whose reference point is the mean of the fit window, so
  the curve is evaluated on, and drawn over, the window only -- the node
  figure's own rule (``fit_window_half_hz``, else max(4 FWHM, 10 MHz)). The
  previous overlay drew it as a DIP over the whole sweep.
* ``base_line``/``position``/``width``/``amplitude`` (the legacy peak-finder
  generation, no least-squares fit): the legacy figure's curve exactly, a
  Lorentzian of half width ``width/2`` on the MEAN baseline, labelled as a
  peak estimate. Withheld when the library's own peak/dip rule says the
  feature is a dip (that curve would be drawn upside down).

Clickable (ge spectroscopy only): a clicked frequency (full_freq, GHz) sets
`f_01` + `xy.RF_frequency` (×1e9 → Hz). The e→f node updates `anharmonicity`
(a delta), so it is view-only.
"""
from __future__ import annotations

import numpy as np

from .. import models
from .. import fitcheck
from ..plotbuild import FIT_COLOR, clean, note
from .base import FigureSpec, figure_key, qslice, qubit_index, qubits_of, split_key

# Both e→f node-name spellings occur on disk: the older "E_to_F" and the
# current "ef" (the node's name= is "28_Qubit_Spectroscopy_ef").
FAMILY = ("1Q_08_qubit_spectroscopy",
          "1Q_28_Qubit_Spectroscopy_E_to_F", "1Q_28_Qubit_Spectroscopy_ef")


def _is_ef(bundle) -> bool:
    name = (bundle.node_meta.get("metadata") or {}).get("name") \
        or getattr(bundle.run, "experiment_name", "") or ""
    # NORMALIZED gate (P0): the tier-2 matcher routes STANDALONE
    # "28_qubit_spectroscopy_e_to_f" here too — the old raw "1Q_28" prefix
    # missed it and the E→F peak became clickable into f_01 (wrong by the
    # anharmonicity, ~200-300 MHz). Match the semantic marker instead.
    from ..registry import _normalize_node_name
    n = _normalize_node_name(name)
    return ("e_to_f" in n) or n.endswith("_ef") or name.startswith("1Q_28")


def _click(qname):
    return {"axis": "x", "qubit": qname, "label": "Set qubit frequency",
            "targets": [{"path": "qubits.{q}.f_01", "scale": 1e9},
                        {"path": "qubits.{q}.xy.RF_frequency", "scale": 1e9}]}


def menu(bundle):
    qubits = qubits_of(bundle) or ["q"]
    multi = len(qubits) > 1
    have = ("I_rot" in bundle.fit_vars) or ("IQ_abs" in bundle.raw_vars)
    return [FigureSpec(figure_key("amplitude", q),
                       "Qubit spectroscopy" + (f" — {q}" if multi else ""), "1d",
                       available=have, reason="" if have else "no data")
            for q in qubits]


def build(bundle, key):
    base, qname = split_key(key)
    raw, fit = bundle.raw, bundle.fit
    if not raw or not raw.get("vars"):
        return FigureSpec(key=key, title="Qubit spectroscopy", available=False, reason="no ds_raw")
    qidx = qubit_index(fit if (fit and fit.get("vars")) else raw, qname)

    ff, _ = qslice(raw, "full_freq", qidx)
    ff = np.asarray(ff, dtype=float)
    x_ghz = ff / 1e9
    det_hz = np.asarray(raw["coords"].get("detuning", []), dtype=float)
    det_mhz = det_hz / 1e6

    fitted_signal = bool(fit and "I_rot" in fit.get("vars", {}))
    if fitted_signal:
        y, _ = qslice(fit, "I_rot", qidx)
        ylabel = "rotated I [mV]"
    else:
        y, _ = qslice(raw, "IQ_abs", qidx)
        ylabel = "|IQ| [mV]"
    y_v = np.asarray(y, dtype=float)
    y_mv = y_v * 1e3

    data = [
        {"x": clean(x_ghz), "y": clean(y_mv), "type": "scatter", "mode": "lines",
         "name": qname or "signal", "line": {"color": "#4e79a7"},
         "customdata": [qname] * len(x_ghz)},
        {"x": clean(det_mhz), "y": clean(y_mv), "type": "scatter", "mode": "lines",
         "xaxis": "x2", "showlegend": False, "hoverinfo": "skip",
         "line": {"color": "rgba(0,0,0,0)"}},
    ]
    shapes = []
    # The node fitted the rotated I; over |IQ| (no I_rot saved) there is no fit to draw.
    notes = _add_overlay(data, fit, qidx, det_hz, x_ghz, y_v) if fitted_signal else []
    res_hz = _res_freq(fit, qidx)
    if res_hz is not None and np.isfinite(res_hz):
        shapes.append({"type": "line", "xref": "x", "yref": "paper",
                       "x0": res_hz / 1e9, "x1": res_hz / 1e9, "y0": 0, "y1": 1,
                       "line": {"color": FIT_COLOR, "dash": "dash", "width": 1}})

    layout = {
        "xaxis": {"title": {"text": "RF frequency [GHz]"}},
        "xaxis2": {"overlaying": "x", "side": "top", "title": {"text": "Detuning [MHz]"}},
        "yaxis": {"title": {"text": ylabel}},
        "shapes": shapes, "hovermode": "closest",
        "margin": {"l": 60, "r": 30, "t": 50, "b": 50},
    }
    if notes:
        layout["annotations"] = [note(notes)]
    clickable = None if _is_ef(bundle) else _click(qname)
    return FigureSpec(key=key, title="Qubit spectroscopy", kind="1d",
                      figure={"data": data, "layout": layout}, clickable=clickable)


def _scalar(fit, var, qidx):
    if var not in fit.get("vars", {}):
        return None
    try:
        v = float(np.asarray(qslice(fit, var, qidx)[0], dtype=float).ravel()[0])
    except Exception:  # noqa: BLE001
        return None
    return v if np.isfinite(v) else None


def _add_overlay(data, fit, qidx, det_hz, x_ghz, y_v):
    """Append the node's fit curve to ``data``; return notes for the figure."""
    if not fit or not det_hz.size or det_hz.size != y_v.size:
        return []
    fv = fit.get("vars", {})
    try:
        if "popt" in fv:
            return _overlay_popt(data, fit, qidx, det_hz, x_ghz, y_v)
        if "base_line" in fv:
            return _overlay_peak_finder(data, fit, qidx, det_hz, x_ghz, y_v)
    except Exception:  # noqa: BLE001 — an overlay must never break the figure
        return ["No fit curve: the saved fit could not be read."]
    return []


def _overlay_popt(data, fit, qidx, det_hz, x_ghz, y_v):
    popt = np.asarray(qslice(fit, "popt", qidx)[0], dtype=float).ravel()
    if popt.size != 5 or not np.all(np.isfinite(popt)):
        return []                                   # the node produced no curve
    # Window = the node figure's rule (calibration_utils/qubit_spectroscopy/
    # plotting.py, lines 185-188): ``window_half = float(fit_q.fit_window_half_hz.values)``,
    # else ``window_half = max(4.0 * fwhm, 10e6)``; then
    # ``mask = np.abs(detuning_hz - f0_rel) <= window_half``.
    f0 = _scalar(fit, "f0", qidx)
    f0 = popt[0] if f0 is None else f0
    fwhm = _scalar(fit, "fwhm", qidx)
    fwhm = popt[1] if fwhm is None else fwhm
    half = _scalar(fit, "fit_window_half_hz", qidx)
    if half is None:
        half = max(4.0 * fwhm, 10e6)
    mask = np.abs(det_hz - f0) <= half
    if int(mask.sum()) < 4:                         # plotting.py line 189: ``if mask.sum() >= 4:``
        return []
    # lorentzian_peak_linbg references its background to mean(f), so it must
    # be evaluated on the window (the node figure passes ``detuning_hz[mask]``).
    curve = models.lorentzian_peak_linbg(det_hz[mask], *popt)
    peak = popt[2] / (1.0 + ((det_hz[mask] - popt[0]) / (popt[1] / 2.0)) ** 2)
    mirror = curve - 2.0 * peak                     # the same line drawn as a dip
    y_w = y_v[mask]
    if fitcheck.r2(y_w, curve) < fitcheck.r2(y_w, mirror):
        return ["Fit curve withheld: the saved parameters describe a peak, "
                "but the data in the fit window is a dip."]
    data.append({"x": clean(x_ghz[mask]), "y": clean(curve * 1e3), "type": "scatter",
                 "mode": "lines", "name": "fit",
                 "line": {"color": FIT_COLOR, "dash": "dash"}})
    return []


def _overlay_peak_finder(data, fit, qidx, det_hz, x_ghz, y_v):
    base = np.asarray(qslice(fit, "base_line", qidx)[0], dtype=float).ravel()
    pos, wid, amp = (_scalar(fit, v, qidx) for v in ("position", "width", "amplitude"))
    if None in (pos, wid, amp) or not wid > 0 or not np.any(np.isfinite(base)):
        return []                                   # no peak was found
    # The library's peak/dip decision on the same signal (qualibration_libs
    # analysis/feature_detection.py, lines 85-88):
    # ``2.0 * (da.mean(dim=dim) - da.min(dim=dim) < da.max(dim=dim) - da.mean(dim=dim))``.
    ym = y_v[np.isfinite(y_v)]
    if ym.size and not (ym.mean() - ym.min() < ym.max() - ym.mean()):
        return ["No fit curve: the node's peak finder located a dip here, "
                "and the legacy overlay is a peak."]
    # Legacy figure (calibration_utils/qubit_spectroscopy/plotting.py of the
    # peak-finder generation, lines 69-75): ``lorentzian_peak(`` ``ds.detuning,``
    # ``float(fit.amplitude.values),`` ``float(fit.position.values),``
    # ``float(fit.width.values) / 2,`` ``float(fit.base_line.mean().values),``.
    curve = models.lorentzian_peak(det_hz, amp, pos, wid / 2.0, float(np.nanmean(base)))
    data.append({"x": clean(x_ghz), "y": clean(curve * 1e3), "type": "scatter",
                 "mode": "lines", "name": "fit (peak estimate)",
                 "line": {"color": FIT_COLOR, "dash": "dash"}})
    return []


def _res_freq(fit, qidx):
    if not fit:
        return None
    for var in ("res_freq",):
        if var in fit.get("vars", {}):
            try:
                return float(np.asarray(qslice(fit, var, qidx)[0]).ravel()[0])
            except Exception:  # noqa: BLE001
                return None
    return None
