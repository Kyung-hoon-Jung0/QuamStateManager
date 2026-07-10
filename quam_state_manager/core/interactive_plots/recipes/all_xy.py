"""All-XY (1Q_22b) — interactive reproduction (view-only).

Categorical bar of the measured state across the 21 All-XY gate sequences.
No fit / no ds_fit. View-only.
"""
from __future__ import annotations

import numpy as np

from ..plotbuild import bar
from .base import FigureSpec, figure_key, qslice, qubit_index, qubits_of, split_key

FAMILY = ("1Q_22b_all_xy",)


def menu(bundle):
    qubits = qubits_of(bundle) or ["q"]
    multi = len(qubits) > 1
    have = "state" in bundle.raw_vars
    return [FigureSpec(figure_key("all_xy", q),
                       "All-XY" + (f" — {q}" if multi else ""), "1d",
                       available=have, reason="" if have else "no state")
            for q in qubits]


def build(bundle, key):
    base, qname = split_key(key)
    raw = bundle.raw
    if not raw or "state" not in raw.get("vars", {}):
        return FigureSpec(key=key, title="All-XY", available=False, reason="no state")
    qidx = qubit_index(raw, qname)
    y, _ = qslice(raw, "state", qidx)
    y = np.asarray(y, dtype=float)
    idx = list(raw["coords"].get("sequence_index", list(range(len(y)))))
    data = [bar([str(int(i)) for i in idx], y, name=qname or "state", color="#4e79a7")]
    layout = {"xaxis": {"title": {"text": "All-XY sequence index"}},
              "yaxis": {"title": {"text": "state"}}, "hovermode": "closest",
              "margin": {"l": 60, "r": 30, "t": 40, "b": 50}}
    return FigureSpec(key=key, title="All-XY", kind="1d",
                      figure={"data": data, "layout": layout})
