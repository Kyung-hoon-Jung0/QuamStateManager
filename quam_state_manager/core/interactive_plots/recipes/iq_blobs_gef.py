"""GEF (g/e/f) IQ blobs / readout discrimination (1Q_30b) — interactive.

The node prepares the qubit in |g>, |e>, |f> (num_shots each) and records the
single-shot *rotated* IQ point of every shot, plus the three state centroids.
Two saved figures: the g/e/f IQ scatter (with centroids) and the 3×3 readout
confusion matrix.

The confusion matrix itself isn't persisted — only the centroids are — but the
node assigns each shot to its nearest centroid, so the matrix is reproduced here
by the same nearest-centroid rule (verified to match the saved PNG exactly). Both
figures are view-only (3-state discrimination has no single clickable scalar).
"""
from __future__ import annotations

import numpy as np

from .. import plotbuild as pb
from .base import FigureSpec, figure_key, qslice, qubit_index, qubits_of, split_key

FAMILY = ("1Q_30b_iq_blobs_gef",)

# state label, I-var, Q-var, blob color (aligned with the saved figure's g/e/f).
_STATES = (("g", "Ig", "Qg", "#4e79a7"), ("e", "Ie", "Qe", "#f28e2b"),
           ("f", "If", "Qf", "#59a14f"))
_PT_VARS = {iv for _, iv, _, _ in _STATES} | {qv for _, _, qv, _ in _STATES}


def menu(bundle):
    qubits = qubits_of(bundle) or ["q"]
    multi = len(qubits) > 1
    have = _PT_VARS <= bundle.fit_vars
    reason = "" if have else "no g/e/f IQ points"
    specs = []
    for q in qubits:
        suffix = f" — {q}" if multi else ""
        specs.append(FigureSpec(figure_key("iq_blobs", q), "IQ blobs (g/e/f)" + suffix,
                                "2d", available=have, reason=reason))
        specs.append(FigureSpec(figure_key("confusion_matrix", q),
                                "Confusion matrix (g/e/f)" + suffix,
                                "2d", available=have, reason=reason))
    return specs


def build(bundle, key):
    base, qname = split_key(key)
    fit = bundle.fit
    if not fit or not fit.get("vars"):
        return FigureSpec(key=key, title=base, available=False, reason="no ds_fit")
    qidx = qubit_index(fit, qname)
    if base == "iq_blobs":
        return _blobs(bundle, key, qidx)
    if base == "confusion_matrix":
        return _confusion(bundle, key, qidx)
    return None


def _pts(fit, ivar, qvar, qidx):
    """One state's single-shot (I, Q) clouds in mV."""
    i = np.asarray(qslice(fit, ivar, qidx)[0], dtype=float) * 1e3
    q = np.asarray(qslice(fit, qvar, qidx)[0], dtype=float) * 1e3
    return i, q


def _centroids(fit, qidx):
    """[g, e, f] × [I, Q] centroids in mV (from ``center_matrix`` or the
    per-state ``*_center`` scalars). Returns a (3, 2) array or ``None``."""
    if "center_matrix" in fit.get("vars", {}):
        cm = np.asarray(qslice(fit, "center_matrix", qidx)[0], dtype=float)
        if cm.shape == (3, 2):
            return cm * 1e3
    out = []
    for s, _, _, _ in _STATES:
        ci, cq = _scalar(fit, f"I_{s}_center", qidx), _scalar(fit, f"Q_{s}_center", qidx)
        if ci is None or cq is None:
            return None
        out.append([ci * 1e3, cq * 1e3])
    return np.asarray(out, dtype=float)


def _blobs(bundle, key, qidx):
    fit = bundle.fit
    if not (_PT_VARS <= set(fit.get("vars", {}))):
        return FigureSpec(key=key, title="IQ blobs (g/e/f)",
                          available=False, reason="no g/e/f IQ points")
    data = []
    for s, iv, qv, col in _STATES:
        i, q = _pts(fit, iv, qv, qidx)
        data.append(pb.scatter(i, q, name=s, color=col, size=3, opacity=0.3))
    cents = _centroids(fit, qidx)
    if cents is not None:
        data.append(pb.scatter(cents[:, 0], cents[:, 1], name="centroids",
                               color="#111", size=9))
    layout = {"xaxis": {"title": {"text": "I (rotated) [mV]"}},
              "yaxis": {"title": {"text": "Q (rotated) [mV]"},
                        "scaleanchor": "x", "scaleratio": 1},
              "hovermode": "closest", "margin": {"l": 60, "r": 30, "t": 50, "b": 50}}
    return FigureSpec(key=key, title="IQ blobs (g/e/f)", kind="2d",
                      figure={"data": data, "layout": layout})


def _confusion(bundle, key, qidx):
    fit = bundle.fit
    if not (_PT_VARS <= set(fit.get("vars", {}))):
        return FigureSpec(key=key, title="Confusion matrix (g/e/f)",
                          available=False, reason="no g/e/f IQ points")
    cents = _centroids(fit, qidx)
    if cents is None or cents.shape != (3, 2):
        return FigureSpec(key=key, title="Confusion matrix (g/e/f)",
                          available=False, reason="no centroids")
    z = []
    for s, iv, qv, _ in _STATES:
        i, q = _pts(fit, iv, qv, qidx)
        if not i.size:
            return FigureSpec(key=key, title="Confusion matrix (g/e/f)",
                              available=False, reason="no shots")
        pts = np.stack([i, q], axis=1)                              # (N, 2)
        d = np.linalg.norm(pts[:, None, :] - cents[None, :, :], axis=2)  # (N, 3)
        assigned = np.argmin(d, axis=1)
        z.append([float(np.mean(assigned == k)) for k in range(3)])
    trace, annotations = pb.confusion_matrix(z, labels=("g", "e", "f"))
    layout = {"xaxis": {"title": {"text": "measured"}},
              "yaxis": {"title": {"text": "prepared"}, "autorange": "reversed"},
              "annotations": annotations, "margin": {"l": 70, "r": 30, "t": 50, "b": 50}}
    return FigureSpec(key=key, title="Confusion matrix (g/e/f)", kind="2d",
                      figure={"data": [trace], "layout": layout})


def _scalar(fit, var, qidx):
    if var not in fit.get("vars", {}):
        return None
    try:
        return float(np.asarray(qslice(fit, var, qidx)[0]).ravel()[0])
    except Exception:  # noqa: BLE001
        return None
