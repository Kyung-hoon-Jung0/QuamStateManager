"""Single-qubit randomized benchmarking (1Q_27) — interactive reproduction.

1D survival probability vs Clifford depth with the exponential-decay fit
overlay. View-only (reports error-per-Clifford/gate; no figure-point edit).
"""
from __future__ import annotations

import numpy as np

from ..plotbuild import FIT_COLOR, clean, line
from .base import FigureSpec, figure_key, qslice, qubit_index, qubits_of, split_key

FAMILY = ("1Q_27_single_qubit_randomized_benchmarking",
          "1Q_27b_single_qubit_randomized_benchmarking_interleaved")


def menu(bundle):
    qubits = qubits_of(bundle) or ["q"]
    multi = len(qubits) > 1
    have = "averaged_data" in bundle.raw_vars or "averaged_data" in bundle.fit_vars
    return [FigureSpec(figure_key("amplitude", q),
                       "Randomized benchmarking" + (f" — {q}" if multi else ""), "1d",
                       available=have, reason="" if have else "no data")
            for q in qubits]


def build(bundle, key):
    base, qname = split_key(key)
    src = bundle.fit if (bundle.fit and "averaged_data" in bundle.fit.get("vars", {})) else bundle.raw
    if not src or "averaged_data" not in src.get("vars", {}):
        return FigureSpec(key=key, title="Randomized benchmarking", available=False, reason="no data")
    qidx = qubit_index(src, qname)
    depths = np.asarray(src["coords"].get("depths", []), dtype=float)
    y, _ = qslice(src, "averaged_data", qidx)
    data = [line(depths, np.asarray(y, dtype=float), name=qname or "data",
                 color="#4e79a7", mode="markers")]
    if bundle.fit and "fit_data" in bundle.fit.get("vars", {}):
        yf, _ = qslice(bundle.fit, "fit_data", qidx)
        data.append(line(depths, np.asarray(yf, dtype=float), name="fit", color=FIT_COLOR))
    layout = {"xaxis": {"title": {"text": "Number of Cliffords"}},
              "yaxis": {"title": {"text": "P(survival)"}}, "hovermode": "closest",
              "margin": {"l": 60, "r": 30, "t": 40, "b": 50}}
    return FigureSpec(key=key, title="Randomized benchmarking", kind="1d",
                      figure={"data": data, "layout": layout})
