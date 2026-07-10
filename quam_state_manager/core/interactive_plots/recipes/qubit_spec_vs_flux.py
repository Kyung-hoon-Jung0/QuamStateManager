"""Qubit spectroscopy vs flux (1Q_09) — interactive reproduction.

|IQ| heatmap over (qubit frequency × flux bias), with the per-flux peak position
and the idle-flux marker. Clickable sets **two values** from one click:
`y(flux)→z.joint_offset` and `x(freq)→f_01`+`xy.RF_frequency`.
"""
from __future__ import annotations

import numpy as np

from .. import plotbuild as pb
from .base import FigureSpec, figure_key, qslice, qubit_index, qubits_of, split_key

FAMILY = ("1Q_09_qubit_spectroscopy_vs_flux",)


def menu(bundle):
    qubits = qubits_of(bundle) or ["q"]
    multi = len(qubits) > 1
    have = "IQ_abs" in bundle.raw_vars
    return [FigureSpec(figure_key("amplitude", q),
                       "Qubit spectroscopy vs flux" + (f" — {q}" if multi else ""),
                       "2d", available=have, reason="" if have else "no IQ_abs")
            for q in qubits]


def build(bundle, key):
    base, qname = split_key(key)
    raw, fit = bundle.raw, bundle.fit
    if not raw or "IQ_abs" not in raw.get("vars", {}):
        return FigureSpec(key=key, title="Qubit spec vs flux", available=False, reason="no IQ_abs")
    qidx = qubit_index(raw, qname)

    ff, _ = qslice(raw, "full_freq", qidx)
    ff = np.asarray(ff, dtype=float)
    x_ghz = ff / 1e9
    det_hz = np.asarray(raw["coords"].get("detuning", []), dtype=float)
    flux = np.asarray(raw["coords"].get("flux_bias", []), dtype=float)

    z, dims = qslice(raw, "IQ_abs", qidx)
    z = np.asarray(z, dtype=float)
    if dims and dims[0] != "flux_bias" and z.ndim == 2:
        z = z.T                                  # → [flux_bias, detuning]
    data = [pb.heatmap(x_ghz, flux, z, colorbar_title="|IQ|", robust=True)]

    # Overlay: per-flux peak position (converted detuning → absolute GHz).
    shapes = []
    if fit and "peak_freq" in fit.get("vars", {}) and ff.size and det_hz.size:
        try:
            pk, _ = qslice(fit, "peak_freq", qidx)
            pk = np.asarray(pk, dtype=float)
            center = ff[0] - det_hz[0]           # absolute center of the sweep
            if pk.shape[-1] == flux.size:
                data.append(pb.line((center + pk) / 1e9, flux, name="peak",
                                    color=pb.FIT_COLOR, mode="lines"))
        except Exception:  # noqa: BLE001
            pass
    idle = _scalar(fit, "idle_offset", qidx)
    if idle is not None and np.isfinite(idle):
        shapes.append(pb.hline(idle, color=pb.FIT_COLOR, dash="dash", width=1.2))

    layout = {"xaxis": {"title": {"text": "RF frequency [GHz]"}},
              "yaxis": {"title": {"text": "flux bias \u0394 [V]"}},
              "shapes": shapes, "margin": {"l": 60, "r": 30, "t": 50, "b": 50}}
    # CONTRACT-FAITHFUL (P0 fix): node 09's flux axis is a DELTA played on top
    # of the parked offset, and the node INCREMENTS joint_offset (assigns the
    # delta for independent flux points). The old clickable staged the clicked
    # DELTA as an ABSOLUTE offset — proven data-destroying on LabC #220
    # (0.072 V parked point would have become −0.0024 V). Frequency legs stay
    # absolute assigns (the node assigns both f_01 and RF the same absolute).
    from .. import contracts as _contracts
    _flux = _contracts.flux_delta_targets(bundle, qname, axis="y",
                                          independent_assigns_delta=True)
    clickable = {"qubit": qname, "label": "Set flux offset + qubit frequency",
                 "targets": ([
                     {"path": "qubits.{q}.f_01", "axis": "x", "scale": 1e9},
                     {"path": "qubits.{q}.xy.RF_frequency", "axis": "x", "scale": 1e9},
                 ] + ((_flux or {}).get("targets", [])))}
    return FigureSpec(key=key, title="Qubit spectroscopy vs flux", kind="2d",
                      figure={"data": data, "layout": layout}, clickable=clickable)


def _scalar(src, var, qidx):
    if not src or var not in src.get("vars", {}):
        return None
    try:
        return float(np.asarray(qslice(src, var, qidx)[0]).ravel()[0])
    except Exception:  # noqa: BLE001
        return None
