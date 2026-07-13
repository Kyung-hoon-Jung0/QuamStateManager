"""Readout optimization (1Q_15a/b g-e, 1Q_30/30a g-e-f) — interactive.

1Q_15a: g/e distance + |IQ| vs readout frequency; clickable x → resonator
frequency. 1Q_15b: readout fidelity vs readout amplitude; clickable x →
`resonator.operations.readout.amplitude`. (The 1Q_15b IQ-blob / confusion
figures are produced by the iq_blobs recipe family.)

GEF (g/e/f) variants reconstruct the saved ``fitted_distances`` figure as the
minimum-centroid-distance curve (plus a pairwise d_ge/d_ef/d_gf breakdown):
1Q_30a sweeps readout *amplitude* (raw + 3-pt-smoothed distance, amplitude-
prefactor twin axis, clickable x → `operations.<op>.amplitude`); 1Q_30 sweeps
readout *frequency* shift (view-only — the fit reports a detuning the node
*adds* to `GEF_frequency_shift`, so there's no unambiguous absolute target).
"""
from __future__ import annotations

import dataclasses

import numpy as np

from .. import plotbuild as pb
from . import iq_blobs as _iqb
from .base import FigureSpec, figure_key, qslice, qubit_index, qubits_of, split_key

FAMILY = ("1Q_15a_readout_frequency_optimization", "1Q_15b_readout_power_optimization",
          "1Q_30_gef_readout_frequency_optimization", "1Q_30a_gef_readout_power_optimization")

# Keys delegated to the iq_blobs recipe (1Q_15b persists these to ds_iq_blobs.h5).
_BLOB_KEYS = ("iq_blobs", "histograms", "confusion_matrix")


def _name(bundle) -> str:
    return (bundle.node_meta.get("metadata") or {}).get("name") \
        or getattr(bundle.run, "experiment_name", "") or ""


def _is_power(bundle) -> bool:
    from ..registry import _normalize_node_name
    n = _normalize_node_name(_name(bundle))
    return n.startswith("readout_power_optimization") or _name(bundle).startswith("1Q_15b")


def _is_gef(bundle) -> bool:
    from ..registry import _normalize_node_name
    n = _normalize_node_name(_name(bundle))
    # The FAMILY names normalize with gef_ FIRST ("gef_readout_frequency_optimization"
    # / "gef_readout_power_optimization"), so the old "readout_power_optimization_gef"
    # prefix was dead code and only the raw 1Q_30 fallback matched — standalone
    # "30(a)_gef_readout_*" runs fell through to the 1Q_15a frequency builder and got
    # the wrong (absolute f_01) click. Gate on the semantic marker (mirrors _is_ef).
    return n.startswith("gef_readout") or _name(bundle).startswith("1Q_30")


def _is_gef_power(bundle) -> bool:
    """1Q_30a sweeps readout *amplitude*; bare 1Q_30 sweeps readout *frequency*."""
    from ..registry import _normalize_node_name
    # 30a's normalized name equals 30's + suffix — disambiguate on the RAW
    # numeric marker in either spelling.
    import re as _re
    return bool(_re.search(r"(?:^|_)30a_", _name(bundle)))


def _freq_click(qname):
    return {"axis": "x", "qubit": qname, "label": "Set readout frequency",
            "targets": [{"path": "qubits.{q}.resonator.f_01", "scale": 1e9},
                        {"path": "qubits.{q}.resonator.RF_frequency", "scale": 1e9}]}


def _amp_click(qname):
    return {"axis": "x", "qubit": qname, "label": "Set readout amplitude",
            "targets": [{"path": "qubits.{q}.resonator.operations.readout.amplitude", "scale": 1}]}


def menu(bundle):
    if _is_gef(bundle):
        return _gef_menu(bundle)
    qubits = qubits_of(bundle) or ["q"]
    multi = len(qubits) > 1
    specs = []
    if _is_power(bundle):
        have = "valid_fidelity" in bundle.fit_vars or "valid_amps" in bundle.fit_vars
        # 1Q_15b also runs an IQ-blob discrimination, persisted to ds_iq_blobs.h5.
        have_rot = {"Ig_rot", "Ie_rot"} <= bundle.iqblobs_vars
        have_conf = {"gg", "ee"} <= bundle.iqblobs_vars
        for q in qubits:
            suffix = f" — {q}" if multi else ""
            specs.append(FigureSpec(figure_key("amplitude", q),
                                    "Readout fidelity vs amplitude" + suffix,
                                    "1d", available=have, reason="" if have else "no fit"))
            specs.append(FigureSpec(figure_key("iq_blobs", q), "IQ blobs (rotated)" + suffix,
                                    "2d", available=have_rot,
                                    reason="" if have_rot else "no rotated IQ"))
            specs.append(FigureSpec(figure_key("histograms", q), "Rotated-I histograms" + suffix,
                                    "1d", available=have_rot,
                                    reason="" if have_rot else "no rotated IQ"))
            specs.append(FigureSpec(figure_key("confusion_matrix", q), "Confusion matrix" + suffix,
                                    "2d", available=have_conf,
                                    reason="" if have_conf else "no confusion data"))
    else:
        have = "D" in bundle.raw_vars
        for q in qubits:
            suffix = f" — {q}" if multi else ""
            specs.append(FigureSpec(figure_key("distances", q), "g–e distance vs frequency" + suffix,
                                    "1d", available=have, reason="" if have else "no D"))
            specs.append(FigureSpec(figure_key("iq_abs", q), "|IQ| g/e vs frequency" + suffix,
                                    "1d", available="IQ_abs_g" in bundle.raw_vars,
                                    reason="" if "IQ_abs_g" in bundle.raw_vars else "no IQ_abs_g"))
    return specs


def build(bundle, key):
    base, qname = split_key(key)
    if _is_gef(bundle):
        return _gef_build(bundle, key)
    if _is_power(bundle):
        if base in _BLOB_KEYS:
            return _blob_fig(bundle, key, base, qname)
        return _power_amp(bundle, key, qname)
    return _freq_fig(bundle, key, base, qname)


def _blob_fig(bundle, key, base, qname):
    """Reuse the iq_blobs builders, but sourced from ds_iq_blobs.h5 (1Q_15b)."""
    if not bundle.iqblobs:
        return FigureSpec(key=key, title=base, available=False, reason="no ds_iq_blobs")
    return _iqb.build(dataclasses.replace(bundle, fit=bundle.iqblobs), key)


def _freq_fig(bundle, key, base, qname):
    raw = bundle.raw
    if not raw or not raw.get("vars"):
        return FigureSpec(key=key, title="Readout optimization", available=False, reason="no ds_raw")
    qidx = qubit_index(raw, qname)
    ff, _ = qslice(raw, "full_freq", qidx)
    x_ghz = np.asarray(ff, dtype=float) / 1e9
    opt = _scalar(raw, "optimal_frequency", qidx)
    shapes = [pb.vline(opt / 1e9, color=pb.FIT_COLOR, dash="dash")] if opt and np.isfinite(opt) else []

    if base == "iq_abs":
        data = []
        for var, nm, col in (("IQ_abs_g", "ground", "#4e79a7"), ("IQ_abs_e", "excited", "#e15759")):
            if var in raw["vars"]:
                y, _ = qslice(raw, var, qidx)
                data.append(pb.line(x_ghz, np.asarray(y, dtype=float) * 1e3, name=nm,
                                    color=col, customdata=[qname] * len(x_ghz)))
        ylabel, title = "|IQ| [mV]", "|IQ| g/e vs frequency"
    else:
        y, _ = qslice(raw, "D", qidx)
        data = [pb.line(x_ghz, np.asarray(y, dtype=float) * 1e3, name="distance",
                        color="#59a14f", customdata=[qname] * len(x_ghz))]
        ylabel, title = "g–e distance [mV]", "g–e distance vs frequency"
    layout = {"xaxis": {"title": {"text": "Readout RF frequency [GHz]"}},
              "yaxis": {"title": {"text": ylabel}}, "shapes": shapes,
              "hovermode": "closest", "margin": {"l": 60, "r": 30, "t": 40, "b": 50}}
    return FigureSpec(key=key, title=title, kind="1d",
                      figure={"data": data, "layout": layout}, clickable=_freq_click(qname))


def _power_amp(bundle, key, qname):
    fit = bundle.fit
    if not fit or "valid_amps" not in fit.get("vars", {}):
        return FigureSpec(key=key, title="Readout fidelity vs amplitude",
                          available=False, reason="no fit")
    qidx = qubit_index(fit, qname)
    amps, _ = qslice(fit, "valid_amps", qidx)
    fid, _ = qslice(fit, "valid_fidelity", qidx)
    data = [pb.line(np.asarray(amps, dtype=float), np.asarray(fid, dtype=float),
                    name=qname or "fidelity", color="#4e79a7", mode="lines+markers",
                    customdata=[qname] * len(np.asarray(amps).ravel()))]
    opt = _scalar(fit, "optimal_amp", qidx)
    shapes = [pb.vline(opt, color=pb.FIT_COLOR, dash="dash")] if opt and np.isfinite(opt) else []
    layout = {"xaxis": {"title": {"text": "Readout amplitude [V]"}},
              "yaxis": {"title": {"text": "fidelity"}}, "shapes": shapes,
              "hovermode": "closest", "margin": {"l": 60, "r": 30, "t": 40, "b": 50}}
    return FigureSpec(key=key, title="Readout fidelity vs amplitude", kind="1d",
                      figure={"data": data, "layout": layout}, clickable=_amp_click(qname))


# --- GEF (g/e/f) readout optimization: 1Q_30 frequency / 1Q_30a power ----

def _gef_operation(bundle) -> str:
    """Readout operation the node updates (node param ``operation``)."""
    op = (((bundle.node_meta.get("data") or {}).get("parameters") or {})
          .get("model") or {}).get("operation")
    return op if isinstance(op, str) and op else "readout_GEF"


def _gef_amp_click(bundle, qname):
    op = _gef_operation(bundle)
    return {"axis": "x", "qubit": qname, "label": "Set GEF readout amplitude",
            "targets": [{"path": f"qubits.{{q}}.resonator.operations.{op}.amplitude",
                         "scale": 1}]}


def _gef_menu(bundle):
    qubits = qubits_of(bundle) or ["q"]
    multi = len(qubits) > 1
    swept = "amplitude" if _is_gef_power(bundle) else "frequency"
    have_dist = "Distance" in bundle.fit_vars
    have_pairs = {"Dge", "Def", "Dgf"} <= bundle.fit_vars
    specs = []
    for q in qubits:
        suffix = f" — {q}" if multi else ""
        specs.append(FigureSpec(figure_key("fitted_distances", q),
                                f"Min centroid distance vs readout {swept}" + suffix,
                                "1d", available=have_dist,
                                reason="" if have_dist else "no Distance"))
        specs.append(FigureSpec(figure_key("gef_distances", q),
                                "Pairwise centroid distances (g–e, e–f, g–f)" + suffix,
                                "1d", available=have_pairs,
                                reason="" if have_pairs else "no pairwise distances"))
    return specs


def _gef_build(bundle, key):
    base, qname = split_key(key)
    fit = bundle.fit
    if not fit or not fit.get("vars"):
        return FigureSpec(key=key, title="GEF readout optimization",
                          available=False, reason="no ds_fit")
    qidx = qubit_index(fit, qname)
    x, xlabel, prefactor, vline_x, click = _gef_x_axis(bundle, fit, qname, qidx)
    if base == "fitted_distances":
        return _gef_fitted(bundle, key, qname, qidx, x, xlabel, prefactor, vline_x, click)
    if base == "gef_distances":
        return _gef_pairwise(bundle, key, qname, qidx, x, xlabel, prefactor, vline_x)
    return None


def _gef_x_axis(bundle, fit, qname, qidx):
    """Shared x-axis for the GEF distance figures.

    Power (1Q_30a): readout amplitude [V] with the prefactor as a twin top axis,
    clickable → the readout operation's amplitude. If the absolute-V
    ``readout_amplitude`` mapping is absent, degrade to the dimensionless
    *prefactor* sweep axis — view-only, since the clicked prefactor can't be
    converted back to a stored amplitude in V (so we never offer a unit-wrong
    write), and the optimum is marked from ``optimal_amp_prefactor`` instead.
    Frequency (1Q_30): readout frequency shift [MHz], view-only. Returns
    ``(x, xlabel, prefactor|None, vline_x|None, clickable|None)``.
    """
    if _is_gef_power(bundle):
        if "readout_amplitude" in fit.get("vars", {}):
            x = np.asarray(qslice(fit, "readout_amplitude", qidx)[0], dtype=float)
            pref = np.asarray(fit["coords"].get("amp_prefactor", []), dtype=float)
            opt = _scalar(fit, "optimal_amplitude", qidx)
            vline_x = opt if (opt is not None and np.isfinite(opt)) else None
            return x, "Readout amplitude [V]", pref, vline_x, _gef_amp_click(bundle, qname)
        # No absolute-V axis → plot the prefactor sweep itself; honest + view-only.
        x = np.asarray(fit["coords"].get("amp_prefactor", []), dtype=float)
        opt = _scalar(fit, "optimal_amp_prefactor", qidx)
        vline_x = opt if (opt is not None and np.isfinite(opt)) else None
        return x, "Amplitude prefactor", None, vline_x, None
    x = np.asarray(fit["coords"].get("frequency", []), dtype=float) / 1e6
    opt = _scalar(fit, "optimal_detuning", qidx)
    vline_x = opt / 1e6 if (opt is not None and np.isfinite(opt)) else None
    return x, "Readout frequency shift [MHz]", None, vline_x, None


def _gef_fitted(bundle, key, qname, qidx, x, xlabel, prefactor, vline_x, click):
    fit = bundle.fit
    if "Distance" not in fit.get("vars", {}):
        return FigureSpec(key=key, title="Min centroid distance",
                          available=False, reason="no Distance")
    dist = np.asarray(qslice(fit, "Distance", qidx)[0], dtype=float)
    cdata = [qname] * len(x)
    data = [pb.line(x, dist, name="raw", color="#4e79a7", customdata=cdata)]
    if "Distance_smooth" in fit.get("vars", {}):
        sm = np.asarray(qslice(fit, "Distance_smooth", qidx)[0], dtype=float)
        data.append(pb.line(x, sm, name="smoothed (3pt)", color="#f28e2b", dash="dash"))
    shapes = [pb.vline(vline_x, dash="dot")] if vline_x is not None else []
    swept = "amplitude" if _is_gef_power(bundle) else "frequency"
    layout = {"xaxis": {"title": {"text": xlabel}},
              "yaxis": {"title": {"text": "Min centroid distance [V]"}},
              "shapes": shapes, "hovermode": "closest",
              "margin": {"l": 65, "r": 30, "t": 50, "b": 50}}
    data = _gef_prefactor_axis(data, layout, prefactor, dist)
    return FigureSpec(key=key, title=f"Min centroid distance vs readout {swept}",
                      kind="1d", figure={"data": data, "layout": layout}, clickable=click)


def _gef_pairwise(bundle, key, qname, qidx, x, xlabel, prefactor, vline_x):
    fit = bundle.fit
    if not ({"Dge", "Def", "Dgf"} <= set(fit.get("vars", {}))):
        return FigureSpec(key=key, title="Pairwise centroid distances",
                          available=False, reason="no pairwise distances")
    cdata = [qname] * len(x)
    anchor = None
    data = []
    for var, nm, col in (("Dge", "d(g,e)", "#4e79a7"), ("Def", "d(e,f)", "#e15759"),
                         ("Dgf", "d(g,f)", "#59a14f")):
        y = np.asarray(qslice(fit, var, qidx)[0], dtype=float)
        if anchor is None:
            anchor = y
        data.append(pb.line(x, y, name=nm, color=col, customdata=cdata))
    if "Distance" in fit.get("vars", {}):
        y = np.asarray(qslice(fit, "Distance", qidx)[0], dtype=float)
        data.append(pb.line(x, y, name="min", color="#555", dash="dot"))
    shapes = [pb.vline(vline_x, dash="dot")] if vline_x is not None else []
    layout = {"xaxis": {"title": {"text": xlabel}},
              "yaxis": {"title": {"text": "Centroid distance [V]"}},
              "shapes": shapes, "hovermode": "closest",
              "margin": {"l": 65, "r": 30, "t": 50, "b": 50}}
    data = _gef_prefactor_axis(data, layout, prefactor, anchor)
    return FigureSpec(key=key, title="Pairwise centroid distances (g–e, e–f, g–f)",
                      kind="1d", figure={"data": data, "layout": layout})


def _gef_prefactor_axis(data, layout, prefactor, y):
    """Add an amplitude-prefactor twin top axis (power variant only).

    Mirrors the power_rabi pattern: a transparent 2-point marker trace anchors
    ``xaxis2``'s range to the prefactor span so its ticks/title render.
    """
    if prefactor is None or not len(prefactor) or y is None:
        return data
    pref = np.asarray(prefactor, dtype=float)
    yv = np.asarray(y, dtype=float)
    finite = yv[np.isfinite(yv)]
    if not pref.size or not finite.size:
        return data
    y0 = float(finite[0])
    data = list(data) + [{
        "x": [float(np.nanmin(pref)), float(np.nanmax(pref))], "y": [y0, y0],
        "xaxis": "x2", "type": "scatter", "mode": "markers",
        "marker": {"opacity": 0}, "showlegend": False, "hoverinfo": "skip"}]
    layout["xaxis2"] = {"overlaying": "x", "side": "top",
                        "title": {"text": "Amplitude prefactor"}}
    return data


def _scalar(src, var, qidx):
    if var not in src.get("vars", {}):
        return None
    try:
        return float(np.asarray(qslice(src, var, qidx)[0]).ravel()[0])
    except Exception:  # noqa: BLE001
        return None
