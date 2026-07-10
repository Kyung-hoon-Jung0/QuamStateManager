"""IQ blobs / readout discrimination (1Q_16) — interactive reproduction.

Three figures: the rotated g/e IQ scatter, the rotated-I histograms, and the
readout confusion matrix. Clickable (on the histograms): the rotated-I axis →
the two discrimination thresholds `readout.threshold` and
`readout.rus_exit_threshold`, seeded from the clicked I and scaled by
`length/4096` (mirrors the node's `threshold = ge_threshold * length / 2**12`).
"""
from __future__ import annotations

import numpy as np

from .. import plotbuild as pb
from .base import FigureSpec, figure_key, qslice, qubit_index, qubits_of, split_key

FAMILY = ("1Q_16_iq_blobs",)

_FIGS = [
    ("iq_blobs", "IQ blobs (rotated)", "2d"),
    ("histograms", "Rotated-I histograms", "1d"),
    ("confusion_matrix", "Confusion matrix", "2d"),
]


def _readout_length(bundle, qname):
    merged = bundle.quam_state or {}
    if not merged or not qname:
        return None
    from quam_state_manager.core.pointer_path import resolve_field_target
    ft = resolve_field_target(merged, f"qubits.{qname}.resonator.operations.readout.length")
    v = ft.get("resolved_value")
    return float(v) if ft.get("resolvable") and isinstance(v, (int, float)) else None


def _threshold_click(bundle, qname):
    length = _readout_length(bundle, qname)
    if not length:
        return None  # can't map clicked rotated-I → stored (scaled) threshold
    scale = length / (4096.0 * 1e3)   # clicked I is in mV; node scales V × length/2**12
    return {"axis": "x", "qubit": qname, "label": "Set discrimination threshold",
            "targets": [
                {"path": "qubits.{q}.resonator.operations.readout.threshold", "scale": scale},
                {"path": "qubits.{q}.resonator.operations.readout.rus_exit_threshold", "scale": scale},
            ]}


def menu(bundle):
    qubits = qubits_of(bundle) or ["q"]
    multi = len(qubits) > 1
    have_rot = {"Ig_rot", "Ie_rot"} <= bundle.fit_vars
    have_conf = {"gg", "ee"} <= bundle.fit_vars
    specs = []
    for q in qubits:
        for base, title, kind in _FIGS:
            if base == "confusion_matrix":
                avail, reason = (have_conf, "" if have_conf else "no confusion data")
            else:
                avail, reason = (have_rot, "" if have_rot else "no rotated IQ")
            specs.append(FigureSpec(figure_key(base, q), title + (f" — {q}" if multi else ""),
                                    kind=kind, available=avail, reason=reason))
    return specs


def build(bundle, key):
    base, qname = split_key(key)
    fit = bundle.fit
    if not fit or not fit.get("vars"):
        return FigureSpec(key=key, title=base, available=False, reason="no ds_fit")
    qidx = qubit_index(fit, qname)
    if base == "iq_blobs":
        return _blobs(bundle, key, qname, qidx)
    if base == "histograms":
        return _histograms(bundle, key, qname, qidx)
    if base == "confusion_matrix":
        return _confusion(bundle, key, qname, qidx)
    return None


def _rot(fit, var, qidx):
    return np.asarray(qslice(fit, var, qidx)[0], dtype=float) * 1e3  # → mV


def _blobs(bundle, key, qname, qidx):
    fit = bundle.fit
    if not ({"Ig_rot", "Ie_rot"} <= set(fit["vars"])):
        return FigureSpec(key=key, title="IQ blobs", available=False, reason="no rotated IQ")
    data = [
        pb.scatter(_rot(fit, "Ig_rot", qidx), _rot(fit, "Qg_rot", qidx),
                   name="ground", color="#4e79a7", size=3, opacity=0.3),
        pb.scatter(_rot(fit, "Ie_rot", qidx), _rot(fit, "Qe_rot", qidx),
                   name="excited", color="#e15759", size=3, opacity=0.3),
    ]
    shapes = _threshold_shapes(fit, qidx, vertical=True)
    layout = {"xaxis": {"title": {"text": "I (rotated) [mV]"}},
              "yaxis": {"title": {"text": "Q (rotated) [mV]"}, "scaleanchor": "x", "scaleratio": 1},
              "shapes": shapes, "hovermode": "closest", "margin": {"l": 60, "r": 30, "t": 50, "b": 50}}
    return FigureSpec(key=key, title="IQ blobs (rotated)", kind="2d",
                      figure={"data": data, "layout": layout})


def _histograms(bundle, key, qname, qidx):
    fit = bundle.fit
    if not ({"Ig_rot", "Ie_rot"} <= set(fit["vars"])):
        return FigureSpec(key=key, title="Histograms", available=False, reason="no rotated IQ")
    ig = _rot(fit, "Ig_rot", qidx)
    ie = _rot(fit, "Ie_rot", qidx)
    lo = float(np.nanmin([ig.min(), ie.min()]))
    hi = float(np.nanmax([ig.max(), ie.max()]))
    edges = np.linspace(lo, hi, 81)
    centers = (edges[:-1] + edges[1:]) / 2
    cg, _ = np.histogram(ig, bins=edges)
    ce, _ = np.histogram(ie, bins=edges)
    data = [pb.bar(centers, cg, name="ground", color="#4e79a7"),
            pb.bar(centers, ce, name="excited", color="#e15759")]
    shapes = _threshold_shapes(fit, qidx, vertical=True)
    layout = {"xaxis": {"title": {"text": "I (rotated) [mV]"}}, "yaxis": {"title": {"text": "counts"}},
              "barmode": "overlay", "shapes": shapes, "hovermode": "closest",
              "margin": {"l": 60, "r": 30, "t": 50, "b": 50}}
    # bars at ~50% opacity so both populations are visible
    for tr in data:
        tr["opacity"] = 0.6
    return FigureSpec(key=key, title="Rotated-I histograms", kind="1d",
                      figure={"data": data, "layout": layout},
                      clickable=_threshold_click(bundle, qname))


def _confusion(bundle, key, qname, qidx):
    fit = bundle.fit
    if not ({"gg", "ee"} <= set(fit["vars"])):
        return FigureSpec(key=key, title="Confusion matrix", available=False, reason="no confusion data")
    gg = _scalar(fit, "gg", qidx); ge = _scalar(fit, "ge", qidx)
    eg = _scalar(fit, "eg", qidx); ee = _scalar(fit, "ee", qidx)
    z = [[gg, ge], [eg, ee]]
    trace, annotations = pb.confusion_matrix(z, labels=("g", "e"))
    layout = {"xaxis": {"title": {"text": "measured"}},
              "yaxis": {"title": {"text": "prepared"}, "autorange": "reversed"},
              "annotations": annotations, "margin": {"l": 70, "r": 30, "t": 50, "b": 50}}
    return FigureSpec(key=key, title="Confusion matrix", kind="2d",
                      figure={"data": [trace], "layout": layout})


def _threshold_shapes(fit, qidx, vertical=True):
    shapes = []
    for var, color in (("ge_threshold", pb.FIT_COLOR), ("rus_threshold", "#555")):
        v = _scalar(fit, var, qidx)
        if v is not None and np.isfinite(v):
            shapes.append(pb.vline(v * 1e3, color=color, dash="dash", width=1.2))
    return shapes


def _scalar(fit, var, qidx):
    if var not in fit.get("vars", {}):
        return None
    try:
        return float(np.asarray(qslice(fit, var, qidx)[0]).ravel()[0])
    except Exception:  # noqa: BLE001
        return None
