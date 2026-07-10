"""XYZ delay (1Q_17) — interactive reproduction.

1D state-difference vs the relative delay between pulses. Clickable: the x-axis
is the delay → `z.opx_output.delay` (ns); the fitted delay is marked.
"""
from __future__ import annotations

import numpy as np

from ..plotbuild import FIT_COLOR, line, vline
from .base import FigureSpec, figure_key, qslice, qubit_index, qubits_of, split_key

FAMILY = ("1Q_17_xyz_delay",)


def _click(qname):
    return {"axis": "x", "qubit": qname, "label": "Set Z delay",
            "targets": [{"path": "qubits.{q}.z.opx_output.delay", "scale": 1}]}


def menu(bundle):
    qubits = qubits_of(bundle) or ["q"]
    multi = len(qubits) > 1
    have = "difference" in bundle.raw_vars or "state" in bundle.raw_vars
    return [FigureSpec(figure_key("delay_scan", q),
                       "XYZ delay" + (f" — {q}" if multi else ""), "1d",
                       available=have, reason="" if have else "no data")
            for q in qubits]


def build(bundle, key):
    base, qname = split_key(key)
    raw, fit = bundle.raw, bundle.fit
    if not raw or not raw.get("vars"):
        return FigureSpec(key=key, title="XYZ delay", available=False, reason="no ds_raw")
    qidx = qubit_index(raw, qname)
    t = np.asarray(raw["coords"].get("relative_time", []), dtype=float)
    if "difference" in raw["vars"]:
        y, _ = qslice(raw, "difference", qidx)
        ylabel = "state difference"
    else:
        y, _ = qslice(raw, "state", qidx)
        ylabel = "state"
    data = [line(t, np.asarray(y, dtype=float), name=qname or "delay",
                 color="#4e79a7", mode="lines+markers", customdata=[qname] * len(t))]
    shapes = []
    if fit and "flux_delay" in fit.get("vars", {}):
        try:
            d = float(np.asarray(qslice(fit, "flux_delay", qidx)[0]).ravel()[0])
            if np.isfinite(d):
                shapes.append(vline(d, color=FIT_COLOR, dash="dash"))
        except Exception:  # noqa: BLE001
            pass
    layout = {"xaxis": {"title": {"text": "relative delay [ns]"}},
              "yaxis": {"title": {"text": ylabel}}, "shapes": shapes,
              "hovermode": "closest", "margin": {"l": 60, "r": 30, "t": 40, "b": 50}}
    return FigureSpec(key=key, title="XYZ delay", kind="1d",
                      figure={"data": data, "layout": layout}, clickable=_click(qname))
