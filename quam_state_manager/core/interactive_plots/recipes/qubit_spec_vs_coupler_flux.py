"""Qubit spectroscopy vs COUPLER flux (1Q_10) — interactive reproduction.

|IQ| over (coupler flux bias × qubit frequency) — **flux on x**, frequency on y,
the lab's convention for every flux sweep (docs/78 §4.1) — with the detected
avoided-crossing structure overlaid: the per-flux peak ridge (raw and smoothed).

Before this recipe the node resolved to ``fallback``: an EMPTY interactive menu,
so the Data tab offered only the static PNG and no click-to-apply at all
(docs/78 §4.2). It is one of the nine x180-chain families.

**Deliberately not clickable.** The node's own ``update_state`` writes only a
bookkeeping key (``qubit.extras[f"{coupler}_dispersion_load_id"]``) and no
calibration scalar — its purpose is to SHOW the avoided crossings. Offering a
click here would have to invent a write target the node never performs, which
is exactly the "figure axis ≠ state value" trap docs/78 D-1 refuses. The figure
is read-only until a node update makes a real target exist.
"""
from __future__ import annotations

import numpy as np

from .. import plotbuild as pb
from .base import FigureSpec, figure_key, qslice, qubit_index, qubits_of, split_key

FAMILY = ("1Q_10_qubit_spectroscopy_vs_coupler_flux",)


def menu(bundle):
    # The cube's "qubit" entries are the run's targets — for this node they may
    # be pair names or measured-qubit names depending on the archive generation
    # (docs/78 §13.4); either way they are what every downstream .sel uses.
    # They are NOT unique by construction: with the default measure_qubit, a
    # star layout (qA1-qC, qA2-qC, qA3-qC) reports the same measured qubit for
    # every pair. Emitting one tile per duplicate would give several identical
    # keys that all resolve to slice 0 — three tiles claiming to be different
    # pairs while showing one. Offer each distinct target once, and say so.
    seen, targets = set(), []
    for t in (qubits_of(bundle) or ["q"]):
        if t not in seen:
            seen.add(t)
            targets.append(t)
    multi = len(targets) > 1
    # build() needs full_freq for its absolute-GHz axis; advertising on IQ_abs
    # alone would list a tile that then renders unavailable.
    have = "IQ_abs" in bundle.raw_vars and "full_freq" in bundle.raw_vars
    reason = "" if have else (
        "no IQ_abs" if "IQ_abs" not in bundle.raw_vars else "no full_freq axis")
    return [FigureSpec(figure_key("amplitude", t),
                       "Qubit spectroscopy vs coupler flux"
                       + (f" — {t}" if multi else ""),
                       "2d", available=have, reason=reason)
            for t in targets]


def build(bundle, key):
    _base, tname = split_key(key)
    raw, fit = bundle.raw, bundle.fit
    if not raw or "IQ_abs" not in raw.get("vars", {}) \
            or "full_freq" not in raw.get("vars", {}):
        return FigureSpec(key=key, title="Qubit spec vs coupler flux",
                          available=False,
                          reason="no IQ_abs" if not raw or "IQ_abs" not in
                          raw.get("vars", {}) else "no full_freq axis")
    qidx = qubit_index(raw, tname)

    ff, _ = qslice(raw, "full_freq", qidx)
    ff = np.asarray(ff, dtype=float)
    y_ghz = ff / 1e9
    det_hz = np.asarray(raw["coords"].get("detuning", []), dtype=float)
    flux = np.asarray(raw["coords"].get("flux_bias", []), dtype=float)

    z, dims = qslice(raw, "IQ_abs", qidx)
    z = np.asarray(z, dtype=float)
    # heatmap z is [y][x] = [frequency][flux]; the cube ships either order.
    if dims and dims[0] == "flux_bias" and z.ndim == 2:
        z = z.T
    if z.ndim != 2 or z.shape != (y_ghz.size, flux.size):
        # an unexpected entity dim (a cube that is not (target, det, flux))
        # would otherwise ship a 3-D z and index ticks instead of coordinates
        return FigureSpec(key=key, title="Qubit spec vs coupler flux",
                          available=False,
                          reason=f"unexpected cube shape {z.shape} for "
                                 f"{y_ghz.size} frequencies x {flux.size} flux points")
    data = [pb.heatmap(flux, y_ghz, z, colorbar_title="|IQ|", robust=True)]

    # Ridge overlays. `abs_peak_frequency` is already absolute Hz; the plain
    # `peak_frequency` is a detuning that needs the sweep centre added back.
    # The fit index is resolved by NAME against ds_fit's own coord — this is the
    # one family whose fit and cube vocabularies are documented to diverge
    # (docs/78 §13.4), so reusing the cube's positional index could overlay one
    # target's ridge on another's map.
    fidx = qubit_index(fit, tname) if fit else 0
    fit_names = [str(q) for q in ((fit or {}).get("coords", {}).get("qubit", []))]
    if fit_names and tname not in fit_names:
        fit = None                       # no honest ridge for this target
    center = (ff[0] - det_hz[0]) if (ff.size and det_hz.size) else None
    for var, name, dash in (("abs_peak_frequency", "peak", None),
                            ("smoothed_peak_frequency", "peak (smoothed)", "dot")):
        line = _ridge(fit, var, fidx, flux.size, center)
        if line is not None:
            data.append(pb.line(flux, line / 1e9, name=name,
                                color=pb.FIT_COLOR, dash=dash, mode="lines"))

    layout = {"xaxis": {"title": {"text": "coupler flux bias [V]"}},
              "yaxis": {"title": {"text": "RF frequency [GHz]"}},
              "margin": {"l": 60, "r": 30, "t": 50, "b": 50}}
    return FigureSpec(key=key, title="Qubit spectroscopy vs coupler flux",
                      kind="2d", figure={"data": data, "layout": layout},
                      clickable=None)   # see the module docstring


def _ridge(fit, var, qidx, n_flux, center):
    """A per-flux frequency ridge in absolute Hz, or None.

    ``center`` is the sweep's absolute carrier, or ``None`` when it could not be
    computed. A detuning-valued ridge is UNPLOTTABLE without it: drawing one on
    an absolute-GHz axis would put a ~0 Hz line at the bottom of the panel and
    silently claim the qubit sits at DC. Better no overlay than a wrong one.
    """
    if not fit or var not in fit.get("vars", {}):
        return None
    try:
        arr, _ = qslice(fit, var, qidx)
        arr = np.asarray(arr, dtype=float)
    except Exception:  # noqa: BLE001
        return None
    if arr.ndim != 1 or arr.size != n_flux or not np.any(np.isfinite(arr)):
        return None
    # a detuning-valued ridge sits near 0; an absolute one near the RF carrier
    finite = arr[np.isfinite(arr)]
    looks_relative = bool(finite.size) and float(np.nanmax(np.abs(finite))) < 1e9
    if looks_relative:
        if not center:
            return None                  # can't place it — draw nothing
        arr = arr + center
    return arr
