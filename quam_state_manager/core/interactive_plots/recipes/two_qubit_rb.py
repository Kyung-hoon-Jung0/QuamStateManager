"""Two-qubit randomized benchmarking (2Q_37 standard, 2Q_37b interleaved-CZ).

No saved figure — this is an additive interactive view. ds_raw holds the raw
two-qubit outcome ``state`` (0..3 for |00>,|01>,|10>,|11>) over
(qubit_pair, repeat, circuit_depth, average); the survival probability P(|00>)
is the fraction of shots back in the ground state per Clifford depth. The recipe
plots that decay (log-x) with an exponential overlay reconstructed from the
fitted depolarizing parameter ``alpha`` (asymptote 1/4 for two qubits) and prints
the extracted Clifford fidelity. View-only.
"""
from __future__ import annotations

import numpy as np

from .. import plotbuild as pb
from .base import FigureSpec, figure_key, split_key
from .two_qubit_common import pair_index, pairs_of, pslice

FAMILY = ("2Q_37_two_qubit_standard_rb", "2Q_37b_two_qubit_interleaved_cz_rb",
          "37_two_qubit_standard_rb")  # the no-"2Q_" node-name variant

_GROUND = 0  # |00> outcome code


def _fit_scalar(bundle, pname, key):
    fr = (bundle.fit_results or {}).get(pname) or {}
    v = fr.get(key)
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def menu(bundle):
    pairs = pairs_of(bundle)
    multi = len(pairs) > 1
    have = "state" in bundle.raw_vars
    # The node saves its one figure under the auto-name ``fig_<pair>``; keying the
    # interactive figure the same way lets it replace that static PNG.
    return [FigureSpec(figure_key(f"fig_{p}", p),
                       "Two-qubit RB" + (f" — {p}" if multi else ""),
                       "1d", available=have, reason="" if have else "no state")
            for p in pairs]


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
    alpha = _fit_scalar(bundle, pname, "alpha")
    fidelity = _fit_scalar(bundle, pname, "fidelity")
    if alpha and np.isfinite(alpha) and 0 < alpha < 1 and n >= 2:
        # Fit A, B in  surv ≈ A·alpha^m + B  (B is the 1/4 depolarizing floor) by a
        # linear least-squares in x = alpha^m, then draw a smooth overlay.
        x = alpha ** depths
        try:
            A, B = np.polyfit(x, surv, 1)
        except (np.linalg.LinAlgError, ValueError):
            A, B = float(surv[0]) - 0.25, 0.25
        dd = np.geomspace(max(float(depths.min()), 1.0), float(depths.max()), 60)
        data.append(pb.line(dd, A * alpha ** dd + B, name="fit", color=pb.FIT_COLOR, dash="dash"))
    title = "Two-qubit RB" + (f" — fidelity {fidelity:.4f}" if fidelity is not None else "")
    layout = {"xaxis": {"title": {"text": "Circuit depth (Cliffords)"}, "type": "log"},
              "yaxis": {"title": {"text": "P(|00⟩)"}},
              "hovermode": "closest", "margin": {"l": 60, "r": 30, "t": 50, "b": 50}}
    return FigureSpec(key=key, title=title, kind="1d",
                      figure={"data": data, "layout": layout})
