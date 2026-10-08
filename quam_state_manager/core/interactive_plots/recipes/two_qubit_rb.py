"""Two-qubit randomized benchmarking (2Q_37 standard, 2Q_37b interleaved-CZ).

ds_raw holds the raw two-qubit outcome ``state`` (0..3 for |00>,|01>,|10>,|11>)
per (qubit_pair, ..., circuit_depth, ...); the survival probability P(|00>) is
the fraction of shots back in the ground state per Clifford depth. The recipe
plots that decay with the node's own fit and prints the extracted Clifford
fidelity. View-only.

The fit curve (docs/300). SM never fits: it draws ``A * alpha**m + B`` only
from the amplitude, alpha and offset THE NODE STORED -- the model of
calibration_utils/two_qubit_rb/fit_utils.py, lines 41-43:
``def rb_decay_curve(x, A, alpha, B):`` / ``return A * alpha**x + B`` -- and
only when the node judged the fit a success (plotting.py line 245:
``if success:``). Where ds_fit also stores ``fitted_curve`` the curve must
reproduce it, else it is withheld. Older node generations stored only alpha
and the fidelity: the amplitude and offset are not on disk, so no curve is
drawn (the previous overlay re-fitted them by least squares, which matched
the node on some runs and not on others), and the tile is keyed so that the
node's own saved figure stays visible beside it.
"""
from __future__ import annotations

import numpy as np

from .. import plotbuild as pb
from .base import FigureSpec, figure_key, split_key
from .two_qubit_common import pair_index, pair_scalar, pairs_of, pslice

FAMILY = ("2Q_37_two_qubit_standard_rb", "2Q_37b_two_qubit_interleaved_cz_rb",
          "37_two_qubit_standard_rb")  # the no-"2Q_" node-name variant

_GROUND = 0  # |00> outcome code


def _fit_scalar(bundle, pname, key):
    fr = (bundle.fit_results or {}).get(pname) or {}
    v = fr.get(key)
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _node_params(bundle, pname):
    """``(A, alpha, B, success)`` as the node stored them, or ``None`` if any is missing."""
    fit = bundle.fit
    if _missing_fit_pair(bundle, pname):
        return None
    pidx = pair_index(fit, pname) if fit else 0
    if fit and {"fit_amplitude", "fit_alpha", "fit_offset"} <= set(fit.get("vars", {})):
        vals = [pair_scalar(fit, v, pidx) for v in ("fit_amplitude", "fit_alpha", "fit_offset")]
        ok = pair_scalar(fit, "success", pidx) if "success" in fit.get("vars", {}) else None
        success = None if ok is None else bool(ok)
    else:
        vals = [_fit_scalar(bundle, pname, k) for k in ("fit_amplitude", "alpha", "fit_offset")]
        s = ((bundle.fit_results or {}).get(pname) or {}).get("success")
        success = s if isinstance(s, bool) else None
    if any(v is None or not np.isfinite(v) for v in vals):
        return None
    return vals[0], vals[1], vals[2], success


def _missing_fit_pair(bundle, pname):
    fit = bundle.fit
    return (fit is not None and "qubit_pair" in fit.get("coords", {})
            and pname not in [str(p) for p in fit["coords"]["qubit_pair"]])


def _stores_amplitude(bundle, pname) -> bool:
    fr = (bundle.fit_results or {}).get(pname) or {}
    return "fit_amplitude" in fr or "fit_amplitude" in (bundle.fit_vars or set())


def menu(bundle):
    pairs = pairs_of(bundle)
    multi = len(pairs) > 1
    have = "state" in bundle.raw_vars
    specs = []
    for p in pairs:
        # The node saves its one figure under the auto-name ``fig_<pair>``. Only
        # a tile that can draw the node's own curve may take that key (and so
        # replace the PNG); an alpha-only generation keeps the PNG beside it.
        base = f"fig_{p}" if _stores_amplitude(bundle, p) else "survival"
        specs.append(FigureSpec(figure_key(base, p),
                                "Two-qubit RB" + (f" — {p}" if multi else ""),
                                "1d", available=have, reason="" if have else "no state"))
    return specs


def build(bundle, key):
    base, pname = split_key(key)
    raw = bundle.raw
    if not raw or "state" not in raw.get("vars", {}):
        return FigureSpec(key=key, title="Two-qubit RB", available=False, reason="no state")
    pidx = pair_index(raw, pname)
    state, dims = pslice(raw, "state", pidx)
    state = np.asarray(state)
    depths = np.asarray(raw["coords"].get("circuit_depth", []), dtype=float)
    if not depths.size:
        return FigureSpec(key=key, title="Two-qubit RB", available=False, reason="no circuit_depth")
    # Survival = P(|00>) averaged over every axis except circuit_depth.
    depth_ax = dims.index("circuit_depth") if "circuit_depth" in dims else min(1, state.ndim - 1)
    other = tuple(i for i in range(state.ndim) if i != depth_ax)
    surv = (state == _GROUND).mean(axis=other) if other else (state == _GROUND).astype(float)
    surv = np.asarray(surv, dtype=float).ravel()
    n = min(len(depths), len(surv))
    depths, surv = depths[:n], surv[:n]

    data = [pb.scatter(depths, surv, name="P(|00⟩)", color="#4e79a7", size=8)]
    notes = []
    params = _node_params(bundle, pname)
    if params is None:
        if _missing_fit_pair(bundle, pname):
            notes.append("No fit curve: the node saved no fit for this pair.")
        else:
            notes.append("No fit curve: the node saved only α, not the amplitude/offset.")
    else:
        A, alpha, B, success = params
        if success is False:
            notes.append("No fit curve: the node marked this fit as failed.")
        else:
            why = _curve_mismatch(bundle, pname, depths, A, alpha, B)
            if why:
                notes.append(why)
            elif n >= 2:
                dd = np.linspace(float(depths.min()), float(depths.max()), 100)
                data.append(pb.line(dd, A * alpha ** dd + B, name="fit", color=pb.FIT_COLOR, dash="dash"))
    fidelity = _fit_scalar(bundle, pname, "fidelity")
    title = "Two-qubit RB" + (f" — fidelity {fidelity:.4f}" if fidelity is not None else "")
    log_x = bool((getattr(bundle.run, "parameters", None) or {}).get("rb_plot_log_x", False))
    xaxis = {"title": {"text": "Circuit depth (Cliffords)"}}
    if log_x:   # the node's own switch; its default is a linear axis (depth 0 is real data)
        xaxis["type"] = "log"
    layout = {"xaxis": xaxis, "yaxis": {"title": {"text": "P(|00⟩)"}},
              "hovermode": "closest", "margin": {"l": 60, "r": 30, "t": 50, "b": 50}}
    if notes:
        layout["annotations"] = [pb.note(notes, where="bottom")]
    return FigureSpec(key=key, title=title, kind="1d",
                      figure={"data": data, "layout": layout})


def _curve_mismatch(bundle, pname, depths, A, alpha, B):
    """A note when the stored ``fitted_curve`` contradicts the stored parameters, else ''."""
    fit = bundle.fit
    if not fit or "fitted_curve" not in fit.get("vars", {}):
        return ""
    try:
        stored = np.asarray(pslice(fit, "fitted_curve", pair_index(fit, pname))[0], dtype=float).ravel()
        fdepth = np.asarray(fit["coords"].get("circuit_depth", depths), dtype=float).ravel()
    except Exception:  # noqa: BLE001
        return ""
    if stored.size != fdepth.size or not np.any(np.isfinite(stored)):
        return ""
    mine = A * alpha ** fdepth + B
    m = np.isfinite(stored)
    if np.all(np.abs(mine[m] - stored[m]) <= 1e-6 * np.maximum(1.0, np.abs(stored[m]))):
        return ""
    return "Fit curve withheld: the stored parameters do not reproduce the node's stored curve."
