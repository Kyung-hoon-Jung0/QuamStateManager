"""Ramsey (1Q_12) — interactive reproduction (view-only).

1D I-quadrature vs idle time, one trace per detuning sign (±), with the node's
damped-oscillation fit redrawn from the saved fit params. View-only: the node
updates a global frequency offset / T2*, not a single figure point.

The fit curve (docs/300):
  * model = qualibration_libs ``oscillation_decay_exp``, i.e.
    ``a * exp(-t * decay) * cos(2 pi f t + phi) + offset`` with ``decay`` a
    positive rate (see ``models.oscillation_decay_exp`` for the quoted line);
  * a qubit whose node adopted the mixed Gaussian+exponential envelope
    (``used_mixed``) has its curves stored verbatim in ``fit_curve`` -- drawn
    as stored, exactly as the node's own figure does;
  * per-run proof: where the run stores ``fit_r2`` (min over detuning signs of
    the node's R^2) or ``t2_star`` (= 1e-9 * mean(1/decay)), SM recomputes it
    from its reconstruction; a mismatch withholds the curve with a note;
  * a branch whose decay rate is <= 0 is the node's own runaway fit: it is
    still the node's curve, so it is drawn, but the y axis is held to the data
    (the node figure's rule) and a note says so.
"""
from __future__ import annotations

import numpy as np

from .. import fitcheck, models
from ..plotbuild import COLORWAY, line, note
from .base import FigureSpec, figure_key, qslice, qubit_index, qubits_of, split_key

FAMILY = ("1Q_12_ramsey",)

_PARAMS = ("a", "f", "phi", "offset", "decay")


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

    ys = []
    for si in range(len(signs)):
        y = arr[:, si] if sign_axis == 1 else (arr[si] if sign_axis == 0 else arr)
        ys.append(np.asarray(y, dtype=float))

    curves, notes, diverged, params = _fit_curves(fit, qname, t, ys, signs)
    # docs/301: the model curve is drawn on a fine grid. Evaluated on the
    # data's own points (about six per period here) the fit was a polyline
    # through the same places as the data line, same colour and width, and
    # could not be told apart from it. The data are drawn as points, as the
    # node's own figure does. A stored curve exists only on the data points.
    t_fit = t
    if params is not None and t.size >= 2 and np.all(np.isfinite(t)):
        t_fit = np.linspace(float(t.min()), float(t.max()), max(10 * t.size, 400))
        curves = [None if c is None else
                  models.oscillation_decay_exp(t_fit, pp["a"], pp["f"], pp["phi"],
                                               pp["offset"], pp["decay"])
                  for pp, c in zip(params, curves)]

    data = []
    for si, sgn in enumerate(signs):
        color = COLORWAY[si % len(COLORWAY)]
        data.append(line(t, ys[si] * scale, name=f"Δ={sgn:+g}", color=color, mode="markers"))
        if curves is not None and si < len(curves) and curves[si] is not None:
            data.append(line(t_fit, curves[si] * scale, name=f"fit Δ={sgn:+g}",
                             color=color, width=1.5))
    layout = {"xaxis": {"title": {"text": "idle time [ns]"}},
              "yaxis": {"title": {"text": ylabel}}, "hovermode": "closest",
              "margin": {"l": 60, "r": 30, "t": 40, "b": 50}}
    if diverged:
        rng = fitcheck.data_range(*[y * scale for y in ys])
        if rng is not None:
            layout["yaxis"]["range"] = rng
            layout["yaxis"]["autorange"] = False
    if notes:
        layout["annotations"] = [note(notes)]
    return FigureSpec(key=key, title="Ramsey", kind="1d",
                      figure={"data": data, "layout": layout})


def _fit_curves(fit, qname, t, ys, signs):
    """``(curves | None, notes, diverged, params | None)`` for one qubit.

    ``curves[i]`` is the node's curve for detuning-sign index ``i`` (same units
    as the data, i.e. before the mV scale), or ``None`` when there is nothing
    to draw for that branch. ``params`` are the model parameters the curves
    were evaluated from, or ``None`` when the node's stored curve is drawn.
    """
    if not fit:
        return None, [], False, None
    fidx = qubit_index(fit, qname)
    mixed = _scalar(fit, "used_mixed", fidx)
    if mixed is not None and bool(mixed):
        fsigns = list(fit["coords"].get("detuning_signs", []))
        stored = _stored_curves(fit, fidx, len(fsigns), t.size)
        why = "No fit curve: the node used the mixed model; its curve was not saved or usable."
        if stored is None:
            return None, [why], False, None
        order = _sign_order(fsigns, len(stored), signs)
        curves, notes = [], []
        for sgn, si in zip(signs, order):
            if si is None:
                curves.append(None)
                notes.append(f"No fit curve for detuning sign {sgn:+g}: the node saved no fit row.")
            elif not np.all(np.isfinite(stored[si])):
                curves.append(None)
                notes.append(f"Detuning sign {sgn:+g}: {why}")
            else:
                curves.append(stored[si])
        if any(c is not None for c in curves):
            notes.append("Fit: the node's stored mixed-envelope curve.")
        return curves, notes, False, None
    if "fit" not in fit.get("vars", {}):
        return None, [], False, None
    labels = [str(v) for v in fit["coords"].get("fit_vals", [])]
    if not set(_PARAMS) <= set(labels):
        return None, ["No fit curve: the saved fit parameters are not the known Ramsey model's."], False, None
    try:
        farr, fdims = qslice(fit, "fit", fidx)          # [detuning_signs, fit_vals]
        farr = np.asarray(farr, dtype=float)
        if fdims and fdims[0] == "fit_vals":
            farr = farr.T
    except Exception:  # noqa: BLE001
        return None, [], False, None
    # Rows follow ds_fit's own detuning_signs coordinate; pair them with the
    # data traces BY SIGN VALUE, never by position.
    fsigns = list(fit["coords"].get("detuning_signs", []))
    order = _sign_order(fsigns, farr.shape[0], signs)
    params = []
    notes = []
    for sgn, si in zip(signs, order):
        if si is None:
            params.append(None)
            notes.append(f"No fit curve for detuning sign {sgn:+g}: the node saved no fit row.")
        else:
            params.append({lab: float(farr[si][labels.index(lab)]) for lab in _PARAMS})

    exp_curves = [models.oscillation_decay_exp(t, p["a"], p["f"], p["phi"], p["offset"], p["decay"])
                  if p is not None and all(np.isfinite(list(p.values()))) else None for p in params]

    # Per-run proof that the reconstruction is the node's curve.
    # Aggregate checks require all saved rows to be paired with raw traces.
    why = (_mismatch(fit, fidx, params, exp_curves, ys)
           if all(p is not None for p in params) and len(params) == farr.shape[0] else "")
    if why:
        return None, notes + [why], False, None

    diverged = any(p is not None and not (p["decay"] > 0) for p in params)
    if diverged:
        notes.append("Fit decay rate ≤ 0 (the node's own fit runs away): y axis held to the data.")
    return exp_curves, notes, diverged, params


def _sign_order(fsigns, n_rows, signs):
    fsigns = list(map(float, fsigns))
    return [fsigns.index(float(s)) if float(s) in fsigns
            and fsigns.index(float(s)) < n_rows else None for s in signs]


def _mismatch(fit, fidx, params, curves, ys):
    """A note when the run's own numbers contradict SM's reconstruction, else ''."""
    vars_ = fit.get("vars", {})
    # The node's fit_r2 is the MINIMUM over detuning signs of the R^2 of exactly
    # this model on exactly this signal (calibration_utils/ramsey/analysis.py,
    # line 99: ``rec["r2"] = float(min(r2s))``).
    stored_r2 = _scalar(fit, "fit_r2", fidx) if "fit_r2" in vars_ else None
    if stored_r2 is not None and np.isfinite(stored_r2):
        r2s = [fitcheck.r2(y, c) for y, c in zip(ys, curves) if c is not None]
        recomputed = min(r2s) if r2s else float("nan")
        if not fitcheck.agrees_abs(recomputed, stored_r2, fitcheck.R2_ABS_TOL):
            return (f"Fit curve withheld: SM's reconstruction gives R²={recomputed:.3f}, "
                    f"the node stored {stored_r2:.3f}.")
    # t2_star [s] = 1e-9 * mean over signs of 1/decay [1/ns] (analysis.py,
    # line 300 ``tau = 1 / decay`` and line 397 ``decay = 1e-9 * tau.mean(dim="detuning_signs")``).
    # Only checkable when every branch decays (the node NaNs it otherwise) and
    # the exponential path set it (a mixed fit rewrites t2_star).
    mixed = _scalar(fit, "used_mixed", fidx)
    stored_t2 = _scalar(fit, "t2_star", fidx) if "t2_star" in vars_ else None
    decays = np.array([p["decay"] for p in params], dtype=float)
    if (stored_t2 is not None and np.isfinite(stored_t2) and not (mixed and bool(mixed))
            and np.all(decays > 0)):
        recomputed = 1e-9 * float(np.mean(1.0 / decays))
        if not fitcheck.agrees_rel(recomputed, stored_t2, fitcheck.T2_REL_TOL):
            return (f"Fit curve withheld: the stored T2* ({stored_t2 * 1e6:.3g} µs) does not "
                    f"follow from the stored decay rates ({recomputed * 1e6:.3g} µs).")
    return ""


def _stored_curves(fit, fidx, n_signs, n_t):
    try:
        arr, dims = qslice(fit, "fit_curve", fidx)      # [detuning_signs, idle_time]
        arr = np.asarray(arr, dtype=float)
        if dims and dims[0] == "idle_time":
            arr = arr.T
        if n_t < 2 or arr.shape != (n_signs, n_t):
            return None
        return [arr[i] for i in range(n_signs)]
    except Exception:  # noqa: BLE001
        return None


def _scalar(src, var, qidx):
    if not src or var not in src.get("vars", {}):
        return None
    try:
        return float(np.asarray(qslice(src, var, qidx)[0], dtype=float).ravel()[0])
    except Exception:  # noqa: BLE001
        return None

