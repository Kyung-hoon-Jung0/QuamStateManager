"""Resonator spectroscopy 2D maps — vs power (1Q_05), vs flux (1Q_06) and vs
coupler flux (1Q_07).

vs_power: |IQ| heatmap over (frequency × readout power[dBm]) with a **twin
amplitude axis** (amp = max_amp·10^((dBm−max_power_dbm)/20)), the optimal power
marked. Clickable on the power axis → `resonator.operations.readout.amplitude`
via the `dbm_to_amp` transform (the node updates amplitude, not dBm).

vs_flux: |IQ| heatmap over (flux bias × frequency) — **flux on x**, frequency on
y, the lab's own convention for every flux sweep (docs/78 §4.1); the power map
above keeps frequency on x because the lab draws THAT one the other way round.
Clickable sets **two values** from one click: `x(flux)→z.joint_offset` and
`y(freq)→resonator.f_01`+`resonator.RF_frequency`. The coupler-flux variant is
view-only (its per-qubit z-offset target does not apply).
"""
from __future__ import annotations

import numpy as np

from .. import contracts, plotbuild as pb
from .base import FigureSpec, figure_key, qslice, qubit_index, qubits_of, split_key

FAMILY = ("1Q_05_resonator_spectroscopy_vs_power", "1Q_06_resonator_spectroscopy_vs_flux",
          "1Q_07_resonator_spectroscopy_vs_coupler_flux")


def _name(bundle) -> str:
    return (bundle.node_meta.get("metadata") or {}).get("name") \
        or getattr(bundle.run, "experiment_name", "") or ""


def _norm_name(bundle) -> str:
    from ..registry import _normalize_node_name
    return _normalize_node_name(_name(bundle))


def _is_power(bundle) -> bool:
    # Normalized match: LabB standalone runs drop the "1Q_05" prefix
    # ("05b_..." → "resonator_spectroscopy_vs_power_iq"); a raw-prefix guard
    # mis-routed them.
    return "vs_power" in _norm_name(bundle)


def _is_coupler(bundle) -> bool:
    # Sweeps the *coupler* flux; the per-qubit z-offset click target of the
    # qubit-flux variant doesn't apply → view-only. Normalized match: the raw
    # "1Q_07" prefix guard let LabB standalone "07_..." coupler maps through as
    # CLICKABLE into the qubit's z.joint_offset (a wrong-field write).
    return "coupler" in _norm_name(bundle)


def menu(bundle):
    qubits = qubits_of(bundle) or ["q"]
    multi = len(qubits) > 1
    have = "IQ_abs" in bundle.raw_vars
    title = ("Resonator vs power" if _is_power(bundle)
             else "Resonator vs coupler flux" if _is_coupler(bundle)
             else "Resonator vs flux")
    return [FigureSpec(figure_key("amplitude", q), title + (f" — {q}" if multi else ""),
                       "2d", available=have, reason="" if have else "no IQ_abs")
            for q in qubits]


def build(bundle, key):
    base, qname = split_key(key)
    raw = bundle.raw
    if not raw or "IQ_abs" not in raw.get("vars", {}):
        return FigureSpec(key=key, title="Resonator 2D", available=False, reason="no IQ_abs")
    qidx = qubit_index(raw, qname)
    ff, _ = qslice(raw, "full_freq", qidx)
    x_ghz = np.asarray(ff, dtype=float) / 1e9
    return _vs_power(bundle, key, qname, qidx, x_ghz) if _is_power(bundle) \
        else _vs_flux(bundle, key, qname, qidx, x_ghz)


def _oriented(src, var, qidx, y_name):
    z, dims = qslice(src, var, qidx)
    z = np.asarray(z, dtype=float)
    if dims and dims[0] != y_name and z.ndim == 2:
        z = z.T
    return z


def _num(v):
    """Coerce a scalar / numpy scalar / single-element list-or-array to float.

    Dataset root attrs round-trip through netCDF/HDF5 as 1-element arrays
    (e.g. ``max_amp`` → ``[0.1]``), which a bare ``isinstance(v, (int, float))``
    check rejects → silent fallback to the wrong conversion. Returns ``None``
    when ``v`` is not a single finite number.
    """
    if isinstance(v, bool):
        return None
    if isinstance(v, (list, tuple, np.ndarray)):
        arr = np.asarray(v).ravel()
        if arr.size != 1:
            return None
        v = arr[0]
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if np.isfinite(f) else None


def _amp_conversion(bundle, qname):
    """(ref_dbm, scale, source) for amp = scale·10^((dBm−ref_dbm)/20), or None.

    The node realises a readout power via ``set_output_power`` — the written
    amplitude is ``10^((P − fs)/20)`` against the chip's ACTUAL readout
    full-scale, so the faithful reference is the port full-scale the run
    snapshot's pointer chain resolves to (scale = 1; PATCHES-AWARE: when this
    run's update moved the full-scale, the amplitude it wrote used the NEW
    value). The dataset's ``max_power_dbm``/``max_amp`` attr pair only implies
    that full-scale when the legacy 3 dB power grid didn't snap — on a real
    archive run they disagreed by exactly 3 dB (fs −2 vs implied −5) and the
    staged amplitude came out 3 dB hot; the attr pair (then the run
    parameters) stays as the fallback when the snapshot can't resolve the
    port. Attrs round-trip through HDF5 as 1-element arrays → :func:`_num`.
    """
    merged = bundle.quam_state or {}
    if merged and qname:
        from quam_state_manager.core.pointer_path import resolve_field_target
        try:
            ft = resolve_field_target(
                merged, f"qubits.{qname}.resonator.opx_output.full_scale_power_dbm")
        except Exception:
            ft = {}
        v = _num(ft.get("resolved_value"))
        if ft.get("resolvable") and v is not None:
            patches = (getattr(bundle, "node_meta", None) or {}).get("patches")
            if isinstance(patches, list):
                jp = "/quam/" + str(ft.get("resolved_path", "")).replace(".", "/")
                for p in patches:
                    if str(p.get("path", "")) == jp:
                        pv = _num(p.get("value"))
                        if pv is not None:
                            v = pv
                        break
            return v, 1.0, "port full-scale (run snapshot, patches-aware)"
    for src, label in (((bundle.raw or {}).get("root_attrs") or {}, "dataset attrs"),
                       (getattr(bundle.run, "parameters", None) or {}, "run parameters")):
        mp, ma = _num(src.get("max_power_dbm")), _num(src.get("max_amp"))
        if mp is not None and ma is not None:
            return mp, ma, f"max_power/max_amp ({label})"
    return None


def _vs_power(bundle, key, qname, qidx, x_ghz):
    raw, fit = bundle.raw, bundle.fit
    power = np.asarray(raw["coords"].get("power", []), dtype=float)
    # Mirror the experiment figure: render IQ_abs_norm (per-row normalized) with
    # robust 2–98% color clipping; fall back to raw IQ_abs when norm is absent.
    zvar = "IQ_abs_norm" if "IQ_abs_norm" in raw.get("vars", {}) else "IQ_abs"
    z = _oriented(raw, zvar, qidx, "power")            # [power, detuning]
    label = "|IQ| (norm)" if zvar == "IQ_abs_norm" else "|IQ|"
    data = [pb.heatmap(x_ghz, power, z, colorbar_title=label, robust=True)]
    shapes = []
    opt = _scalar(fit, "optimal_power", qidx)
    if opt is not None and np.isfinite(opt):
        shapes.append(pb.hline(opt, color=pb.ACCENT, dash="solid", width=1.5))

    layout = {"xaxis": {"title": {"text": "RF frequency [GHz]"}},
              "yaxis": {"title": {"text": "Readout power [dBm]"}},
              "shapes": shapes, "margin": {"l": 60, "r": 70, "t": 50, "b": 50}}
    clickable = None
    conv = _amp_conversion(bundle, qname)
    if conv is not None and power.size:
        ref_dbm, scale, conv_src = conv
        amp_lo = scale * 10 ** ((float(power.min()) - ref_dbm) / 20)
        amp_hi = scale * 10 ** ((float(power.max()) - ref_dbm) / 20)
        # transparent trace establishes the twin y-axis range (amplitude)
        data.append({"x": [float(x_ghz[0])] * 2 if len(x_ghz) else [0, 0],
                     "y": [amp_lo, amp_hi], "yaxis": "y2", "type": "scatter",
                     "mode": "markers", "marker": {"opacity": 0},
                     "showlegend": False, "hoverinfo": "skip"})
        # Log amp axis: log10(amp)=log10(scale)+(dBm−ref)/20 is linear in dBm,
        # so the twin axis lines up exactly with the linear dBm axis *and* with
        # the clicked-point amplitude the popup computes via dbm_to_amp.
        layout["yaxis2"] = {"overlaying": "y", "side": "right", "type": "log",
                            "title": {"text": "Readout amplitude [V]"}, "showgrid": False}
        # Only readout.amplitude is written (the node encodes power as
        # full_scale_power_dbm + amplitude, and for in-range clicks only the
        # amplitude changes). The clicked power is shown read-only for context.
        clickable = {"axis": "y", "qubit": qname, "label": "Set readout amplitude",
                     "targets": [{"path": "qubits.{q}.resonator.operations.readout.amplitude",
                                  "transform": {"type": "dbm_to_amp", "ref_dbm": ref_dbm, "scale": scale},
                                  "provenance": contracts._prov(
                                      "amp = scale·10^((clicked dBm − ref)/20) — ref: "
                                      + conv_src + ". If a click is far enough out of"
                                      " range that the node would also move the"
                                      " full-scale, this amplitude-only staging cannot"
                                      " reproduce that pair.",
                                      [{"label": f"ref dBm ({conv_src})", "frozen_value": ref_dbm},
                                       {"label": "scale", "frozen_value": scale}])}],
                     "context": [{"label": "Readout power", "axis": "y", "scale": 1,
                                  "unit": "dBm", "decimals": 2}]}
        # x-axis → frequency with the node's INCREMENT semantics (05b does
        # ``f_01 += clicked − RF_at_run``; an absolute overwrite would be wrong
        # whenever f_01 ≠ RF). Offsets baked from the run's own dataset +
        # frozen snapshot (see contracts.freq_increment_targets). View-only on
        # x when RF-at-run can't be established.
        _freq = contracts.freq_increment_targets(bundle, qname, axis="x",
                                                 axis_scale=1e9, resonator=True)
        if _freq:
            clickable["targets"] = clickable["targets"] + _freq["targets"]
    return FigureSpec(key=key, title="Resonator vs power", kind="2d",
                      figure={"data": data, "layout": layout}, clickable=clickable)


def _vs_flux(bundle, key, qname, qidx, x_ghz):
    """|IQ| over (flux × RF frequency) — **flux on x**, frequency on y.

    The lab's own ``plotting.py`` draws every flux sweep this way
    (``IQ_abs.plot(x="flux_bias", y="freq_GHz")``), while it puts frequency on x
    for POWER sweeps. SM used to apply "frequency is always x" universally, so
    this family rendered transposed: the physicist's near-flat resonator band
    became a vertical stripe, and the flux arch a sideways C (docs/78 §4.2).
    """
    raw, fit = bundle.raw, bundle.fit
    flux = np.asarray(raw["coords"].get("flux_bias", []), dtype=float)
    z = _oriented(raw, "IQ_abs", qidx, "flux_bias")     # [flux_bias, detuning]
    # heatmap z is indexed [y][x]; y is now frequency, so transpose the cube.
    data = [pb.heatmap(flux, x_ghz, np.asarray(z).T if np.ndim(z) == 2 else z,
                       colorbar_title="|IQ|", robust=True)]
    shapes = []
    idle = _scalar(fit, "idle_offset", qidx)
    if idle is not None and np.isfinite(idle):
        # the idle offset is a FLUX value → a vertical line now
        shapes.append(pb.vline(idle, color=pb.FIT_COLOR, dash="dash", width=1.2))
    coupler = _is_coupler(bundle)
    xlabel = "coupler flux bias [V]" if coupler else "flux bias [V]"
    layout = {"xaxis": {"title": {"text": xlabel}},
              "yaxis": {"title": {"text": "RF frequency [GHz]"}},
              "shapes": shapes, "margin": {"l": 60, "r": 30, "t": 50, "b": 50}}
    clickable = None
    if not coupler:
        # CONTRACT-FAITHFUL: node 06 sweeps ABSOLUTE DC offsets and ASSIGNS the
        # flux sweet spot (flux_point-routed field), assigns RF absolutely (its
        # += with shift = clicked − RF_at_run collapses to the clicked value),
        # but f_01 is a true INCREMENT — wrong by (f_01 − RF divergence) if
        # overwritten absolutely (real chips diverge, e.g. qB5 −1.23 MHz).
        # The AXES MOVE WITH THE FIGURE: flux now reads x, frequency reads y.
        # (Transposing the plot without swapping these writes the clicked
        # frequency into the flux field — docs/78 §4.3.) The value math
        # (scale/offset) is untouched, so the click goldens still hold.
        flux_t = (contracts.flux_absolute_targets(bundle, qname, axis="x")
                  or {}).get("targets", [])
        f01_t = []
        _inc = contracts.freq_increment_targets(bundle, qname, axis="y",
                                                axis_scale=1e9, resonator=True)
        if _inc:
            f01_t = [t for t in _inc["targets"] if t["path"].endswith(".f_01")]
        clickable = {
            "qubit": qname, "label": "Set flux offset + resonator frequency",
            "targets": (flux_t + f01_t + [
                {"path": "qubits.{q}.resonator.RF_frequency", "axis": "y", "scale": 1e9},
            ])}
    title = "Resonator vs coupler flux" if coupler else "Resonator vs flux"
    return FigureSpec(key=key, title=title, kind="2d",
                      figure={"data": data, "layout": layout}, clickable=clickable)


def _scalar(src, var, qidx):
    if not src or var not in src.get("vars", {}):
        return None
    try:
        return float(np.asarray(qslice(src, var, qidx)[0]).ravel()[0])
    except Exception:  # noqa: BLE001
        return None
