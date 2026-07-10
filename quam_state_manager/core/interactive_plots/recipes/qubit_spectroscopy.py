"""Qubit spectroscopy (1Q_08, 1Q_08_new, 1Q_28 e→f) — interactive reproduction.

1D rotated-I vs frequency with a Lorentzian-peak overlay + the fitted qubit
frequency marker. Handles both ds_fit schemas: new (`popt`/`f0`/`r2`) and old
(`base_line`/`position`/`width`/`amplitude`).

Clickable (ge spectroscopy only): a clicked frequency (full_freq, GHz) sets
`f_01` + `xy.RF_frequency` (×1e9 → Hz). The e→f node updates `anharmonicity`
(a delta), so it is view-only.
"""
from __future__ import annotations

import numpy as np

from .. import models
from ..plotbuild import FIT_COLOR, clean
from .base import FigureSpec, figure_key, qslice, qubit_index, qubits_of, split_key

# Both e→f node-name spellings occur on disk: the older "E_to_F" and the
# current "ef" (the node's name= is "28_Qubit_Spectroscopy_ef").
FAMILY = ("1Q_08_qubit_spectroscopy",
          "1Q_28_Qubit_Spectroscopy_E_to_F", "1Q_28_Qubit_Spectroscopy_ef")


def _is_ef(bundle) -> bool:
    name = (bundle.node_meta.get("metadata") or {}).get("name") \
        or getattr(bundle.run, "experiment_name", "") or ""
    # NORMALIZED gate (P0): the tier-2 matcher routes STANDALONE
    # "28_qubit_spectroscopy_e_to_f" here too — the old raw "1Q_28" prefix
    # missed it and the E→F peak became clickable into f_01 (wrong by the
    # anharmonicity, ~200-300 MHz). Match the semantic marker instead.
    from ..registry import _normalize_node_name
    n = _normalize_node_name(name)
    return ("e_to_f" in n) or n.endswith("_ef") or name.startswith("1Q_28")


def _click(qname):
    return {"axis": "x", "qubit": qname, "label": "Set qubit frequency",
            "targets": [{"path": "qubits.{q}.f_01", "scale": 1e9},
                        {"path": "qubits.{q}.xy.RF_frequency", "scale": 1e9}]}


def menu(bundle):
    qubits = qubits_of(bundle) or ["q"]
    multi = len(qubits) > 1
    have = ("I_rot" in bundle.fit_vars) or ("IQ_abs" in bundle.raw_vars)
    return [FigureSpec(figure_key("amplitude", q),
                       "Qubit spectroscopy" + (f" — {q}" if multi else ""), "1d",
                       available=have, reason="" if have else "no data")
            for q in qubits]


def build(bundle, key):
    base, qname = split_key(key)
    raw, fit = bundle.raw, bundle.fit
    if not raw or not raw.get("vars"):
        return FigureSpec(key=key, title="Qubit spectroscopy", available=False, reason="no ds_raw")
    qidx = qubit_index(fit if (fit and fit.get("vars")) else raw, qname)

    ff, _ = qslice(raw, "full_freq", qidx)
    ff = np.asarray(ff, dtype=float)
    x_ghz = ff / 1e9
    det_hz = np.asarray(raw["coords"].get("detuning", []), dtype=float)
    det_mhz = det_hz / 1e6

    if fit and "I_rot" in fit.get("vars", {}):
        y, _ = qslice(fit, "I_rot", qidx)
        ylabel = "rotated I [mV]"
    else:
        y, _ = qslice(raw, "IQ_abs", qidx)
        ylabel = "|IQ| [mV]"
    y_mv = np.asarray(y, dtype=float) * 1e3

    data = [
        {"x": clean(x_ghz), "y": clean(y_mv), "type": "scatter", "mode": "lines",
         "name": qname or "signal", "line": {"color": "#4e79a7"},
         "customdata": [qname] * len(x_ghz)},
        {"x": clean(det_mhz), "y": clean(y_mv), "type": "scatter", "mode": "lines",
         "xaxis": "x2", "showlegend": False, "hoverinfo": "skip",
         "line": {"color": "rgba(0,0,0,0)"}},
    ]
    shapes = []
    _add_overlay(data, fit, qidx, det_hz, x_ghz)
    res_hz = _res_freq(fit, qidx)
    if res_hz is not None and np.isfinite(res_hz):
        shapes.append({"type": "line", "xref": "x", "yref": "paper",
                       "x0": res_hz / 1e9, "x1": res_hz / 1e9, "y0": 0, "y1": 1,
                       "line": {"color": FIT_COLOR, "dash": "dash", "width": 1}})

    layout = {
        "xaxis": {"title": {"text": "RF frequency [GHz]"}},
        "xaxis2": {"overlaying": "x", "side": "top", "title": {"text": "Detuning [MHz]"}},
        "yaxis": {"title": {"text": ylabel}},
        "shapes": shapes, "hovermode": "closest",
        "margin": {"l": 60, "r": 30, "t": 50, "b": 50},
    }
    clickable = None if _is_ef(bundle) else _click(qname)
    return FigureSpec(key=key, title="Qubit spectroscopy", kind="1d",
                      figure={"data": data, "layout": layout}, clickable=clickable)


def _add_overlay(data, fit, qidx, det_hz, x_ghz):
    if not fit:
        return
    fv = fit.get("vars", {})
    try:
        if "popt" in fv:  # new schema (Lorentzian over detuning)
            popt, _ = qslice(fit, "popt", qidx)
            popt = np.asarray(popt, dtype=float).ravel()
            if popt.size == 5 and np.all(np.isfinite(popt)) and det_hz.size:
                curve = models.lorentzian_dip_linbg(det_hz, *popt) * 1e3
                data.append({"x": clean(x_ghz), "y": clean(curve), "type": "scatter",
                             "mode": "lines", "name": "fit",
                             "line": {"color": FIT_COLOR, "dash": "dash"}})
        elif "base_line" in fv:  # old peaks schema
            base, _ = qslice(fit, "base_line", qidx)
            pos = float(np.asarray(qslice(fit, "position", qidx)[0]).ravel()[0])
            wid = float(np.asarray(qslice(fit, "width", qidx)[0]).ravel()[0])
            amp = float(np.asarray(qslice(fit, "amplitude", qidx)[0]).ravel()[0])
            base = np.asarray(base, dtype=float)
            if det_hz.size == base.size and np.isfinite([pos, wid, amp]).all() and wid > 0:
                curve = models.lorentzian_peak_linbg(det_hz, pos, wid, amp, base) * 1e3
                data.append({"x": clean(x_ghz), "y": clean(curve), "type": "scatter",
                             "mode": "lines", "name": "fit",
                             "line": {"color": FIT_COLOR, "dash": "dash"}})
    except Exception:  # noqa: BLE001 — overlay is best-effort
        pass


def _res_freq(fit, qidx):
    if not fit:
        return None
    for var in ("res_freq",):
        if var in fit.get("vars", {}):
            try:
                return float(np.asarray(qslice(fit, var, qidx)[0]).ravel()[0])
            except Exception:  # noqa: BLE001
                return None
    return None
