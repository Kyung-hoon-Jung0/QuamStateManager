"""2Q CPhase-gate chevron (2Q_19) — interactive reproduction.

The node sweeps the flux-pulse *amplitude* × *duration* and measures the control
and target qubit state populations, producing the CZ "chevron". The saved figure
is the ``state_target`` heatmap over (duration, flux amplitude) with the fitted
optimal gate point (``cz_len``, ``cz_amp``) starred; this rebuilds it as an
interactive heatmap and adds the ``state_control`` companion.

View-only: the optimum is a jointly-fit (amplitude, duration) pair on a qubit
*pair*, not a single clickable scalar. Unlike the 1Q recipes this keys figures by
``qubit_pair`` (e.g. ``qA2-qA1``), so it slices that axis rather than ``qubit``.

A second node generation names the two qubits by ROLE instead: the qubit whose
flux is pulsed (``state_moving`` over amplitude x time, or ``state_mover`` over
``flux_dv`` x ``pulse_duration`` with a ``prep`` axis) and the one that is not
(``state_stationary`` / ``state_static``). Those runs save their figures as
``moving_qubit`` / ``stationary_qubit``, so the tiles take those bases. They
stay view-only: the click contract above names the ``cz_unipolar`` /
``cz_flattop`` macros of the first generation, which a role-named run does not
prove it writes.
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


# Role-named generation: (figure base, candidate variables, title). The first
# candidate the run holds is drawn.
_ROLE_FIGS = (
    ("moving_qubit", ("state_moving", "state_mover"), "CZ Chevron \u2014 moving qubit"),
    ("stationary_qubit", ("state_stationary", "state_static"),
     "CZ Chevron \u2014 stationary qubit"),
)
_TITLES = {var: title for _b, var, title in _FIGS}
_TITLES.update({var: title for _b, vs, title in _ROLE_FIGS for var in vs})
# (x coordinate, y coordinate, x label, y label when no amp_full) per layout.
_AXES = (
    ("time", "amplitude", "Pulse duration [ns]", "Flux amplitude prefactor"),
    ("pulse_duration", "flux_dv", "Pulse duration [ns]", "Mover flux excursion [V]"),
)


def _have_vars(bundle) -> set:
    return set(bundle.fit_vars) | set(bundle.raw_vars)


def _role_named(have) -> bool:
    """A role-named run: none of the first generation's variables, one of the
    role-named ones."""
    if any(v in have for _b, v, _t in _FIGS):
        return False
    return any(v in have for _b, vs, _t in _ROLE_FIGS for v in vs)


def menu(bundle):
    pairs = pairs_of(bundle)
    multi = len(pairs) > 1
    have_vars = _have_vars(bundle)
    role = _role_named(have_vars)
    specs = []
    for p in pairs:
        suffix = f" — {p}" if multi else ""
        if role:
            for base, cands, title in _ROLE_FIGS:
                var = next((v for v in cands if v in have_vars), None)
                specs.append(FigureSpec(
                    figure_key(base, p), title + suffix, "2d", available=var is not None,
                    reason="" if var else "no " + " or ".join(cands)))
            continue
        for base, var, title in _FIGS:
            have = var in have_vars
            reason = ""
            if not have:
                # Say every name the recipe looks for, not only the first one.
                alts = [v for b2, vs, _t in _ROLE_FIGS for v in vs
                        if (b2 == "moving_qubit") == (var == "state_target")]
                reason = "no " + " / ".join([var] + alts)
            specs.append(FigureSpec(figure_key(base, p), title + suffix, "2d",
                                    available=have, reason=reason))
    return specs


def build(bundle, key):
    base, pname = split_key(key)
    role = next((cands for b, cands, _t in _ROLE_FIGS if b == base), None)
    cands = role if role else (next((v for b, v, _ in _FIGS if b == base), base),)
    # Prefer ds_fit, but the chevron arrays also live in ds_raw — use whichever has them.
    src = var = None
    for cand_var in cands:
        for cand in (bundle.fit, bundle.raw):
            if cand and cand_var in cand.get("vars", {}):
                src, var = cand, cand_var
                break
        if src is not None:
            break
    if src is None:
        return FigureSpec(key=key, title="CZ Chevron", available=False,
                          reason="no " + " or ".join(cands))
    return _chevron(bundle, key, pname, src, var, pair_index(src, pname),
                    role_named=bool(role))


def _chevron(bundle, key, pname, src, var, pidx, role_named=False):
    z, dims = pslice(src, var, pidx)
    z = np.asarray(z, dtype=float)
    coords = src.get("coords", {})
    axes = next((a for a in _AXES if a[0] in dims and a[1] in dims), None)
    if axes is None:
        return FigureSpec(key=key, title=_TITLES.get(var, "CZ Chevron"), available=False,
                          reason=f"{var} has dims ({', '.join(map(str, dims))}), "
                                 "not amplitude x time")
    xname, yname, xlabel, ylabel = axes
    notes = []
    # Any further axis (the role-named generation's ``prep``): one slice, said.
    for i in range(len(dims) - 1, -1, -1):
        d = dims[i]
        if d in (xname, yname) or z.ndim <= 2:
            continue
        if z.shape[i] > 1:
            lab = (coords.get(d) or [None])[0]
            notes.append(f"Showing {d} = {lab} (first of {z.shape[i]}).")
        z = np.take(z, 0, axis=i)
        dims = dims[:i] + dims[i + 1:]
    if z.ndim != 2 or not np.isfinite(z).any():
        why = (f"{var} holds no finite values" if z.ndim == 2
               else f"{var} is not a 2-D map after slicing")
        return FigureSpec(key=key, title=_TITLES.get(var, "CZ Chevron"),
                          available=False, reason=why)
    time = np.asarray(coords.get(xname, []), dtype=float)
    # y = the actual flux-pulse amplitude in V (amp_full); fall back to the swept
    # prefactor coord if that per-point mapping isn't stored.
    if "amp_full" in src.get("vars", {}):
        y = np.asarray(pslice(src, "amp_full", pidx)[0], dtype=float)
        ylabel = "Flux pulse amplitude [V]"
    else:
        y = np.asarray(coords.get(yname, []), dtype=float)
    if dims and dims[0] == xname:  # orient z as [y=amplitude, x=time]
        z = z.T
    data = [pb.heatmap(time, y, z, colorbar_title=var)]
    cz_len, cz_amp = _scalar(src, "cz_len", pidx), _scalar(src, "cz_amp", pidx)
    # The role-named generation reports a failed fit in fit_results; its star
    # is then not drawn (the first generation keeps its figure unchanged).
    failed = role_named and ((bundle.fit_results or {}).get(pname) or {}).get("success") is False
    if failed and cz_len is not None:
        notes.append("No fitted point: the node marked this fit as failed.")
    if (not failed and None not in (cz_len, cz_amp)
            and np.isfinite(cz_len) and np.isfinite(cz_amp)):
        data.append({"x": [cz_len], "y": [cz_amp], "type": "scatter", "mode": "markers",
                     "name": "fitted", "marker": {"color": pb.FIT_COLOR, "size": 14,
                                                   "symbol": "star", "line": {"color": "#fff", "width": 1}}})
    title = _TITLES.get(var, "CZ Chevron")
    layout = {"xaxis": {"title": {"text": xlabel}},
              "yaxis": {"title": {"text": ylabel}},
              "hovermode": "closest", "margin": {"l": 70, "r": 30, "t": 50, "b": 50}}
    if notes:
        layout["annotations"] = [pb.note(notes)]
    if role_named:
        return FigureSpec(key=key, title=title, kind="2d",
                          figure={"data": data, "layout": layout})
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
