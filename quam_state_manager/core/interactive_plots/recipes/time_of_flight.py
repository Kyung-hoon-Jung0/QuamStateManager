"""Time of flight (1Q_01) — interactive reproduction (view-only).

ADC traces vs readout time: a single-shot run and the averaged run, with the
detected time-of-flight delay (vertical) and amplitude threshold (horizontal)
marked. View-only: the node adds a tof *delta*, not a figure-point read-off.
"""
from __future__ import annotations

import numpy as np

from .. import plotbuild as pb
from .base import FigureSpec, figure_key, qslice, qubit_index, qubits_of, split_key

FAMILY = ("1Q_01_time_of_flight",)


def menu(bundle):
    qubits = qubits_of(bundle) or ["q"]
    multi = len(qubits) > 1
    rv = bundle.raw_vars
    specs = []
    for q in qubits:
        suffix = f" — {q}" if multi else ""
        specs.append(FigureSpec(figure_key("averaged_run", q), "Averaged ADC" + suffix, "1d",
                                available=("adcI" in rv or "filtered_adc" in rv),
                                reason="" if ("adcI" in rv or "filtered_adc" in rv) else "no adc"))
        specs.append(FigureSpec(figure_key("single_run", q), "Single-shot ADC" + suffix, "1d",
                                available=("adc_single_runI" in rv),
                                reason="" if "adc_single_runI" in rv else "no single-shot adc"))
    return specs


def build(bundle, key):
    base, qname = split_key(key)
    raw = bundle.raw
    if not raw or not raw.get("vars"):
        return FigureSpec(key=key, title="Time of flight", available=False, reason="no ds_raw")
    qidx = qubit_index(raw, qname)
    t = np.asarray(raw["coords"].get("readout_time", []), dtype=float)

    if base == "single_run":
        pairs = [("adc_single_runI", "I", "#4e79a7"), ("adc_single_runQ", "Q", "#f28e2b")]
        title = "Single-shot ADC"
    else:
        pairs = [("adcI", "I", "#4e79a7"), ("adcQ", "Q", "#f28e2b")]
        if "filtered_adc" in raw["vars"]:
            pairs.append(("filtered_adc", "|IQ| filtered", "#59a14f"))
        title = "Averaged ADC"

    data = []
    for var, nm, col in pairs:
        if var in raw["vars"]:
            y, _ = qslice(raw, var, qidx)
            data.append(pb.line(t, np.asarray(y, dtype=float) * 1e3, name=nm, color=col))
    shapes = []
    delay = _scalar(raw, "delay", qidx)
    if delay is not None and np.isfinite(delay):
        shapes.append(pb.vline(delay, color=pb.FIT_COLOR, dash="dash"))
    thr = _scalar(raw, "threshold", qidx)
    if base != "single_run" and thr is not None and np.isfinite(thr):
        shapes.append(pb.hline(thr * 1e3, color="#555", dash="dot", width=1))
    layout = {"xaxis": {"title": {"text": "readout time [ns]"}},
              "yaxis": {"title": {"text": "ADC [mV]"}}, "shapes": shapes,
              "hovermode": "closest", "margin": {"l": 60, "r": 30, "t": 50, "b": 50}}
    return FigureSpec(key=key, title=title, kind="1d", figure={"data": data, "layout": layout})


def _scalar(raw, var, qidx):
    if var not in raw.get("vars", {}):
        return None
    try:
        return float(np.asarray(qslice(raw, var, qidx)[0]).ravel()[0])
    except Exception:  # noqa: BLE001
        return None
