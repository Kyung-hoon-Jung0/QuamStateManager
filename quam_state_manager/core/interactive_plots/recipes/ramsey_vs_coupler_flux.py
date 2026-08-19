"""Ramsey vs COUPLER flux (17b / 21a / 10b) — interactive reproduction.

Two tiles per pair, mirroring the lab's own two figures:

* **Fringes** — the raw Ramsey ``state`` heatmap over (idle time × coupler
  flux), **idle time on x** — the lab's own figure for this family
  (``calibration_utils/ramsey_vs_coupler_flux/plotting.py``: xarray default,
  xlabel "Idle time (ns)"), matching the sibling 17a recipe and ndview's
  docs/122 axis rank. The first version shipped flux-on-x citing "the lab's
  convention for every flux sweep" — false for this family, and it rendered
  the Interactive tile transposed against BOTH the Raw-Data tab and the lab's
  static PNG beside it (docs/124 M-12; the red team's executed inversion).
* **Qubit frequency vs coupler flux** — the extracted per-flux qubit
  frequency (``ds_fit.qubit_frequency``), with the 17b branch-resolution
  context (``…_above``/``…_below`` dotted) and its detected crossings marked.

Before this recipe all three generations of the family (``17b_``, ``21a_``,
``10b_ramsey_vs_coupler_flux`` — 64 archived runs, incl. the newest
tunable-coupler chip's) resolved to ``fallback``: an EMPTY Interactive menu,
so the Data tab offered only the static PNGs (1.0-prep, docs/99).

**Deliberately not clickable.** The coupler-flux axis is a RELATIVE pulse
amplitude on top of ``coupler.decouple_offset`` (relative axes are always
view-only, docs/48), and the node's own ``update_state`` writes only
bookkeeping keys (``…_dispersion_load_id``, ``…_ramsey_freq_branch_sign``) —
no calibration scalar exists to stage. The lab calibrates
``decouple_offset`` via the ZZ-off (JAZZ) node, whose recipe carries that
increment contract already (``cz_2d_maps``).
"""
from __future__ import annotations

import numpy as np

from .. import plotbuild as pb
from .base import FigureSpec, figure_key, split_key
from .two_qubit_common import pair_index, pslice

FAMILY = (
    "1Q_17b_ramsey_vs_coupler_flux", "17b_ramsey_vs_coupler_flux",
    "1Q_21a_ramsey_vs_coupler_flux", "21a_ramsey_vs_coupler_flux",
    "1Q_10b_ramsey_vs_coupler_flux", "10b_ramsey_vs_coupler_flux",
)


def _pairs_of(bundle) -> list[str]:
    # these runs are qubit_pair-indexed (same convention as cz_2d_maps)
    for src in (bundle.fit, bundle.raw):
        if src and "qubit_pair" in src.get("coords", {}):
            return [str(p) for p in src["coords"]["qubit_pair"]]
    return [str(p) for p in (getattr(bundle.run, "qubit_pairs", None)
                             or [])] or ["pair"]


def menu(bundle):
    seen, targets = set(), []
    for t in _pairs_of(bundle):
        if t not in seen:
            seen.add(t)
            targets.append(t)
    multi = len(targets) > 1
    have_raw = "state" in bundle.raw_vars
    have_fit = "qubit_frequency" in ((bundle.fit or {}).get("vars") or {})
    out = []
    for t in targets:
        suff = f" — {t}" if multi else ""
        out.append(FigureSpec(figure_key("fringes", t),
                              "Ramsey fringes vs coupler flux" + suff, "2d",
                              available=have_raw,
                              reason="" if have_raw else "no state variable"))
        out.append(FigureSpec(figure_key("freq", t),
                              "Qubit frequency vs coupler flux" + suff, "1d",
                              available=have_fit,
                              reason="" if have_fit else "no qubit_frequency fit"))
    return out


def _flux(src):
    return np.asarray((src.get("coords") or {}).get("coupler_flux", []),
                      dtype=float)


def build(bundle, key):
    base, tname = split_key(key)
    if base == "fringes":
        return _fringes(bundle, key, tname)
    return _freq(bundle, key, tname)


def _fringes(bundle, key, tname):
    raw = bundle.raw
    if not raw or "state" not in raw.get("vars", {}):
        return FigureSpec(key=key, title="Ramsey fringes vs coupler flux",
                          available=False, reason="no state variable")
    pidx = pair_index(raw, tname)
    flux = _flux(raw)
    idle = np.asarray(raw["coords"].get("idle_times", []), dtype=float)
    z, dims = pslice(raw, "state", pidx)
    z = np.asarray(z, dtype=float)
    # heatmap z is [y][x] = [flux][idle] (idle on x — the lab's orientation,
    # docs/124 M-12); the cube ships (flux, idle) or the transpose depending
    # on generation — orient by the NAMED dims.
    if dims and dims[0] == "idle_times" and z.ndim == 2:
        z = z.T
    if z.ndim != 2 or z.shape != (flux.size, idle.size):
        return FigureSpec(key=key, title="Ramsey fringes vs coupler flux",
                          available=False,
                          reason=f"unexpected cube shape {z.shape} for "
                                 f"{flux.size} flux points x {idle.size} idle times")
    data = [pb.heatmap(idle, flux, z, colorbar_title="state", robust=True)]
    layout = {"xaxis": {"title": {"text": "idle time [ns]"}},
              "yaxis": {"title": {"text": "coupler flux amplitude [V]"}},
              "margin": {"l": 60, "r": 30, "t": 50, "b": 50}}
    return FigureSpec(key=key, title="Ramsey fringes vs coupler flux",
                      kind="2d", figure={"data": data, "layout": layout},
                      clickable=None)   # see the module docstring


def _freq(bundle, key, tname):
    fit = bundle.fit
    if not fit or "qubit_frequency" not in fit.get("vars", {}):
        return FigureSpec(key=key, title="Qubit frequency vs coupler flux",
                          available=False, reason="no qubit_frequency fit")
    fidx = pair_index(fit, tname)
    flux = _flux(fit)
    main = _series(fit, "qubit_frequency", fidx, flux.size)
    if main is None:
        return FigureSpec(key=key, title="Qubit frequency vs coupler flux",
                          available=False,
                          reason="qubit_frequency shape mismatch")
    # Auto-unit: these curves are Hz-valued across all three generations; a
    # sub-MHz curve (already-converted archive) stays honest in raw Hz.
    hz = np.nanmax(np.abs(main[np.isfinite(main)])) if np.any(
        np.isfinite(main)) else 0.0
    scale, unit = (1e6, "MHz") if hz >= 1e6 else (1.0, "Hz")
    data = [pb.line(flux, main / scale, name="qubit frequency",
                    color=pb.FIT_COLOR, mode="lines+markers")]
    # 17b branch context — dotted, never the headline.
    for var, dash in (("qubit_frequency_above", "dot"),
                      ("qubit_frequency_below", "dot")):
        s = _series(fit, var, fidx, flux.size)
        if s is not None:
            data.append(pb.line(flux, s / scale,
                                name=var.rsplit("_", 1)[-1], dash=dash,
                                mode="lines"))
    # detected avoided-crossing marks (17b)
    mask = _series(fit, "crossings_mask", fidx, flux.size)
    if mask is not None and np.any(mask > 0):
        mx = flux[mask > 0]
        my = main[mask > 0] / scale
        keep = np.isfinite(my)
        if np.any(keep):
            data.append({"x": mx[keep].tolist(), "y": my[keep].tolist(),
                         "type": "scatter", "mode": "markers",
                         "name": "crossing",
                         "marker": {"symbol": "x", "size": 9,
                                    "color": pb.ACCENT}})
    layout = {"xaxis": {"title": {"text": "coupler flux amplitude [V]"}},
              "yaxis": {"title": {"text": f"qubit frequency [{unit}]"}},
              "margin": {"l": 60, "r": 30, "t": 50, "b": 50}}
    return FigureSpec(key=key, title="Qubit frequency vs coupler flux",
                      kind="1d", figure={"data": data, "layout": layout},
                      clickable=None)


def _series(fit, var, qidx, n_flux):
    if var not in (fit.get("vars") or {}):
        return None
    try:
        arr, _ = pslice(fit, var, qidx)
        arr = np.asarray(arr, dtype=float)
    except Exception:  # noqa: BLE001
        return None
    if arr.ndim != 1 or arr.size != n_flux:
        return None
    return arr
