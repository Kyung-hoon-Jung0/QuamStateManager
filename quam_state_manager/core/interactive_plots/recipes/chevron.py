"""2Q CPhase-gate chevron (2Q_19) — interactive reproduction.

The node sweeps the flux-pulse *amplitude* × *duration* and measures the control
and target qubit state populations, producing the CZ "chevron". The saved figure
is the ``state_target`` heatmap over (duration, flux amplitude) with the fitted
optimal gate point (``cz_len``, ``cz_amp``) starred; this rebuilds it as an
interactive heatmap and adds the ``state_control`` companion.

View-only: the optimum is a jointly-fit (amplitude, duration) pair on a qubit
*pair*, not a single clickable scalar. Unlike the 1Q recipes this keys figures by
``qubit_pair`` (e.g. ``qA2-qA1``), so it slices that axis rather than ``qubit``.
"""
from __future__ import annotations

import numpy as np

from .. import plotbuild as pb
from .base import FigureSpec, figure_key, split_key
from .two_qubit_common import pair_index, pairs_of, pslice

FAMILY = ("2Q_19_chevron",)

# (figure base, dataset variable, title). The saved PNG is named "amplitude";
# keep that base so it dedups against the static tile.
_FIGS = (
    ("amplitude", "state_target", "CZ Chevron — target state"),
    ("state_control", "state_control", "CZ Chevron — control state"),
)


def menu(bundle):
    pairs = pairs_of(bundle)
    multi = len(pairs) > 1
    specs = []
    for p in pairs:
        suffix = f" — {p}" if multi else ""
        for base, var, title in _FIGS:
            have = var in bundle.fit_vars or var in bundle.raw_vars
            specs.append(FigureSpec(figure_key(base, p), title + suffix, "2d",
                                    available=have, reason="" if have else f"no {var}"))
    return specs


def build(bundle, key):
    base, pname = split_key(key)
    var = next((v for b, v, _ in _FIGS if b == base), base)
    # Prefer ds_fit, but the chevron arrays also live in ds_raw — use whichever has them.
    src = None
    for cand in (bundle.fit, bundle.raw):
        if cand and var in cand.get("vars", {}):
            src = cand
            break
    if src is None:
        return FigureSpec(key=key, title="CZ Chevron", available=False, reason=f"no {var}")
    return _chevron(bundle, key, pname, src, var, pair_index(src, pname))


def _chevron(bundle, key, pname, src, var, pidx):
    z, dims = pslice(src, var, pidx)
    z = np.asarray(z, dtype=float)
    time = np.asarray(src["coords"].get("time", []), dtype=float)
    # y = the actual flux-pulse amplitude in V (amp_full); fall back to the swept
    # prefactor coord if that per-point mapping isn't stored.
    if "amp_full" in src.get("vars", {}):
        y = np.asarray(pslice(src, "amp_full", pidx)[0], dtype=float)
        ylabel = "Flux pulse amplitude [V]"
    else:
        y = np.asarray(src["coords"].get("amplitude", []), dtype=float)
        ylabel = "Flux amplitude prefactor"
    if dims and dims[0] == "time":  # orient z as [y=amplitude, x=time]
        z = z.T
    data = [pb.heatmap(time, y, z, colorbar_title=var)]
    cz_len, cz_amp = _scalar(src, "cz_len", pidx), _scalar(src, "cz_amp", pidx)
    if None not in (cz_len, cz_amp) and np.isfinite(cz_len) and np.isfinite(cz_amp):
        data.append({"x": [cz_len], "y": [cz_amp], "type": "scatter", "mode": "markers",
                     "name": "fitted", "marker": {"color": pb.FIT_COLOR, "size": 14,
                                                   "symbol": "star", "line": {"color": "#fff", "width": 1}}})
    title = "CZ Chevron — " + ("target state" if var == "state_target" else "control state")
    layout = {"xaxis": {"title": {"text": "Pulse duration [ns]"}},
              "yaxis": {"title": {"text": ylabel}},
              "hovermode": "closest", "margin": {"l": 70, "r": 30, "t": 50, "b": 50}}
    # CONTRACT-FAITHFUL click (node 31 writes 5 fields from one (t, amp) point):
    # both CZ macro amplitudes = clicked y (V, absolute amp_full axis only) and
    # the three lengths = ceil(t/4)*4 (+20 for the flattop envelope) — the exact
    # update_state formulas. Only offered when the y-axis is absolute volts.
    clickable = None
    if "amp_full" in src.get("vars", {}) and pname:
        _pv = "qubit_pairs.%s.macros" % pname
        clickable = {"qubit": pname, "label": "Set CZ point (amp + length)",
                     "targets": [
                         {"path": f"{_pv}.cz_unipolar.flux_pulse_qubit.amplitude",
                          "axis": "y", "scale": 1.0,
                          "provenance": {"formula": "clicked V (absolute axis)", "inputs": []}},
                         {"path": f"{_pv}.cz_flattop.flux_pulse_qubit.amplitude",
                          "axis": "y", "scale": 1.0,
                          "provenance": {"formula": "clicked V (absolute axis)", "inputs": []}},
                         {"path": f"{_pv}.cz_unipolar.flux_pulse_qubit.length",
                          "axis": "x", "transform": {"type": "ceil4"},
                          "provenance": {"formula": "ceil(clicked ns / 4) × 4", "inputs": []}},
                         {"path": f"{_pv}.cz_flattop.flux_pulse_qubit.flat_length",
                          "axis": "x", "transform": {"type": "ceil4"},
                          "provenance": {"formula": "ceil(clicked ns / 4) × 4", "inputs": []}},
                         {"path": f"{_pv}.cz_flattop.flux_pulse_qubit.length",
                          "axis": "x", "transform": {"type": "ceil4", "add": 20},
                          "provenance": {"formula": "ceil(clicked ns / 4) × 4 + 20"
                                         " (flattop envelope)", "inputs": []}},
                     ]}
    return FigureSpec(key=key, title=title, kind="2d",
                      figure={"data": data, "layout": layout},
                      clickable=clickable)


def _scalar(src, var, pidx):
    if var not in src.get("vars", {}):
        return None
    try:
        return float(np.asarray(pslice(src, var, pidx)[0]).ravel()[0])
    except Exception:  # noqa: BLE001
        return None
