"""Power Rabi (1Q_11_power_rabi) — interactive reproduction.

2D amplitude × number-of-pulses heatmap (with a twin amplitude-prefactor axis),
or a 1D amplitude sweep with an oscillation overlay. The primary x-axis is the
absolute pulse amplitude in mV (``full_amp``), so a clicked point maps directly
to the stored amplitude in Volts.

Clickable: a clicked amplitude (mV) sets ``xy.operations.<op>.amplitude``
(÷1e3 → V); for ``op == "x180"`` it also sets ``x90.amplitude`` (= ½), matching
the node's ``update_state``.

The 1D fit overlay is qualibration_libs ``oscillation`` -- a COSINE in the
amplitude prefactor (see ``models.oscillation``; the previous overlay used a
sine, a quarter period off). Where the run stores the node's ``osc_amp_snr``
(computed from this very curve), SM recomputes it and withholds the curve
with a note when the two disagree (docs/300).
"""
from __future__ import annotations

import numpy as np

from .. import fitcheck, models, plotbuild as pb
from .base import FigureSpec, figure_key, qslice, qubit_index, qubits_of, split_key

FAMILY = ("1Q_11_power_rabi", "1Q_29_power_rabi_ef")

# Preference order for the signal variable to plot.
_SIGNAL_VARS = ("state", "IQ_abs", "I")


def _signal_var(present: set) -> str | None:
    for v in _SIGNAL_VARS:
        if v in present:
            return v
    return None


def _click(bundle, qname):
    op = "x180"
    try:
        op = str((getattr(bundle.run, "parameters", {}) or {}).get("operation", "x180")) or "x180"
    except Exception:  # noqa: BLE001
        op = "x180"
    targets = [{"path": "qubits.{q}.xy.operations.%s.amplitude" % op, "scale": 1e-3}]
    if op == "x180":
        targets.append({"path": "qubits.{q}.xy.operations.x90.amplitude", "scale": 5e-4})
    return {"axis": "x", "qubit": qname, "label": "Set π-pulse amplitude", "targets": targets}


def menu(bundle):
    qubits = qubits_of(bundle) or ["q"]
    multi = len(qubits) > 1
    sig = _signal_var(bundle.raw_vars) or _signal_var(bundle.fit_vars)
    specs = []
    for q in qubits:
        avail, reason = (True, "") if sig else (False, "no signal variable")
        # Base "amplitude" matches the experiment's saved-figure name so the
        # static-PNG fallback dedups against this interactive tile.
        specs.append(FigureSpec(
            key=figure_key("amplitude", q),
            title="Power Rabi" + (f" — {q}" if multi else ""),
            kind="2d", available=avail, reason=reason,
        ))
    return specs


def build(bundle, key):
    base, qname = split_key(key)
    src = bundle.raw if (bundle.raw and bundle.raw.get("vars")) else bundle.fit
    if not src or not src.get("vars"):
        return FigureSpec(key=key, title="Power Rabi", available=False, reason="no data")
    qidx = qubit_index(src, qname)
    sig = _signal_var(set(src["vars"]))
    if sig is None:
        return FigureSpec(key=key, title="Power Rabi", available=False, reason="no signal variable")

    z, dims = qslice(src, sig, qidx)          # dims minus qubit
    z = np.asarray(z, dtype=float)
    amp_v, _ = qslice(src, "full_amp", qidx)  # absolute amplitude per prefactor [V]
    x_mv = np.asarray(amp_v, dtype=float) * 1e3
    pref = np.asarray(src["coords"].get("amp_prefactor", []), dtype=float)

    # A single-pulse sweep still carries nb_of_pulses as a length-1 axis. The node
    # drops it and fits the 1D trace (calibration_utils/power_rabi/analysis.py, line 523:
    # ``ds_fit = ds.isel(nb_of_pulses=0, drop=True) if "nb_of_pulses" in ds.dims else ds``),
    # so draw it as that 1D trace with its fit, not as a one-row heatmap without one
    # (docs/300).
    if "nb_of_pulses" in dims and z.ndim == 2 and z.shape[dims.index("nb_of_pulses")] == 1:
        z = np.take(z, 0, axis=dims.index("nb_of_pulses"))
        dims = [d for d in dims if d != "nb_of_pulses"]

    if "nb_of_pulses" in dims and z.ndim == 2:
        return _heatmap(bundle, key, qname, src, qidx, sig, z, dims, x_mv, pref)
    if "readout_frequency" in dims and z.ndim == 2:
        return _heatmap_rf(bundle, key, qname, src, qidx, sig, z, dims, x_mv, pref)
    return _line(bundle, key, qname, src, qidx, sig, z, x_mv, pref)


def _heatmap_rf(bundle, key, qname, src, qidx, sig, z, dims, x_mv, pref):
    """EF Rabi: amplitude × readout-frequency heatmap (1Q_29)."""
    rf = np.asarray(src["coords"].get("readout_frequency", []), dtype=float)
    if dims and dims[0] != "readout_frequency":
        z = z.T  # → [readout_frequency, amp]
    data = [pb.heatmap(x_mv, rf, z, colorbar_title=sig)]
    if pref.size and rf.size:
        data.append({"x": [float(pref.min()), float(pref.max())],
                     "y": [float(rf[0]), float(rf[0])], "xaxis": "x2",
                     "type": "scatter", "mode": "markers",
                     "marker": {"opacity": 0}, "showlegend": False, "hoverinfo": "skip"})
    shapes = _opt_amp_vline(bundle, qidx)
    fit = bundle.fit
    if fit and "best_readout_frequency" in fit.get("vars", {}):
        try:
            best = float(np.asarray(qslice(fit, "best_readout_frequency", qidx)[0]).ravel()[0])
            if np.isfinite(best):
                shapes.append(pb.hline(best, color=pb.ACCENT, dash="dash", width=1.5))
        except Exception:  # noqa: BLE001
            pass
    layout = {
        "xaxis": pb.axis("Pulse amplitude [mV]"),
        "xaxis2": {"overlaying": "x", "side": "top", "title": {"text": "amplitude prefactor"}},
        "yaxis": pb.axis("Readout freq detuning [Hz]"),
        "shapes": shapes, "margin": {"l": 70, "r": 30, "t": 50, "b": 50},
    }
    return FigureSpec(key=key, title="Power Rabi (EF)", kind="2d",
                      figure={"data": data, "layout": layout}, clickable=_click(bundle, qname))


def _heatmap(bundle, key, qname, src, qidx, sig, z, dims, x_mv, pref):
    nb = np.asarray(src["coords"].get("nb_of_pulses", []), dtype=float)
    # Orient z as [y=nb_of_pulses, x=amp].
    if dims and dims[0] != "nb_of_pulses":
        z = z.T
    data = [pb.heatmap(x_mv, nb, z, colorbar_title=sig)]
    # Twin amplitude-prefactor axis (transparent marker sets xaxis2 range).
    if pref.size and nb.size:
        data.append({"x": [float(pref.min()), float(pref.max())],
                     "y": [float(nb[0]), float(nb[0])], "xaxis": "x2",
                     "type": "scatter", "mode": "markers",
                     "marker": {"opacity": 0}, "showlegend": False, "hoverinfo": "skip"})
    shapes = _opt_amp_vline(bundle, qidx)
    layout = {
        "xaxis": pb.axis("Pulse amplitude [mV]"),
        "xaxis2": {"overlaying": "x", "side": "top", "title": {"text": "amplitude prefactor"}},
        "yaxis": pb.axis("Number of pulses"),
        "shapes": shapes, "margin": {"l": 60, "r": 30, "t": 50, "b": 50},
    }
    return FigureSpec(key=key, title="Power Rabi", kind="2d",
                      figure={"data": data, "layout": layout}, clickable=_click(bundle, qname))


def _line(bundle, key, qname, src, qidx, sig, z, x_mv, pref):
    y = z * 1e3 if sig != "state" else z
    data = [pb.line(x_mv, y, name=qname or sig, color="#4e79a7",
                    customdata=[qname] * len(x_mv))]
    # Oscillation overlay from 1D fit params, when present.
    fit = bundle.fit
    notes = []
    if fit and "fit" in fit.get("vars", {}) and pref.size:
        try:
            farr, fdims = qslice(fit, "fit", qubit_index(fit, qname))
            labels = [str(v) for v in fit["coords"].get("fit_vals", [])]
            fv = {lab: float(np.asarray(farr).ravel()[i]) for i, lab in enumerate(labels)}
            if {"a", "f", "phi", "offset"} <= set(fv):
                curve = models.oscillation(pref, fv["a"], fv["f"], fv["phi"], fv["offset"])
                why = _snr_mismatch(fit, qname, np.asarray(z, dtype=float).ravel(), curve, fv["a"])
                if why:
                    notes.append(why)
                else:
                    curve = curve * 1e3 if sig != "state" else curve
                    data.append(pb.line(x_mv, curve, name="fit", color=pb.FIT_COLOR, dash="dash"))
        except Exception:  # noqa: BLE001
            pass
    ylabel = "Signal [mV]" if sig != "state" else "State population"
    layout = {"xaxis": pb.axis("Pulse amplitude [mV]"),
              "xaxis2": {"overlaying": "x", "side": "top", "title": {"text": "amplitude prefactor"}},
              "yaxis": pb.axis(ylabel), "shapes": _opt_amp_vline(bundle, qidx),
              "margin": {"l": 60, "r": 30, "t": 50, "b": 50}}
    if notes:
        layout["annotations"] = [pb.note(notes)]
    # Twin prefactor axis range.
    if pref.size:
        data.append({"x": [float(pref.min()), float(pref.max())], "y": [None, None],
                     "xaxis": "x2", "type": "scatter", "mode": "markers",
                     "marker": {"opacity": 0}, "showlegend": False, "hoverinfo": "skip"})
    return FigureSpec(key=key, title="Power Rabi", kind="1d",
                      figure={"data": data, "layout": layout}, clickable=_click(bundle, qname))


def _snr_mismatch(fit, qname, y, curve, a):
    """A note when the run's stored ``osc_amp_snr`` contradicts SM's curve, else ''."""
    if "osc_amp_snr" not in fit.get("vars", {}):
        return ""
    try:
        stored = float(np.asarray(qslice(fit, "osc_amp_snr", qubit_index(fit, qname))[0],
                                  dtype=float).ravel()[0])
    except Exception:  # noqa: BLE001
        return ""
    if not np.isfinite(stored) or y.shape != np.shape(curve):
        return ""
    recomputed = fitcheck.osc_amp_snr(y, curve, a)
    if fitcheck.agrees_rel(recomputed, stored, fitcheck.SNR_REL_TOL):
        return ""
    return (f"Fit curve withheld: SM's reconstruction gives oscillation SNR {recomputed:.3g}, "
            f"the node stored {stored:.3g}.")


def _opt_amp_vline(bundle, qidx):
    """Green vertical line at the fitted π-pulse amplitude (opt_amp, in mV)."""
    fit = bundle.fit
    if not fit:
        return []
    if "success" in fit.get("vars", {}):
        try:
            ok, _ = qslice(fit, "success", qidx)
            if not bool(np.asarray(ok).ravel()[0]):
                return []
        except Exception:  # noqa: BLE001
            pass
    if "opt_amp" not in fit.get("vars", {}):
        return []
    try:
        opt, _ = qslice(fit, "opt_amp", qidx)
        opt_mv = float(np.asarray(opt).ravel()[0]) * 1e3
        if np.isfinite(opt_mv):
            return [pb.vline(opt_mv)]
    except Exception:  # noqa: BLE001
        pass
    return []
