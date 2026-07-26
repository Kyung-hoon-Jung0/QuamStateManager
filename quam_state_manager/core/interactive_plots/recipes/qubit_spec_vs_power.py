"""Qubit spectroscopy vs drive power (1Q_08b) — interactive reproduction.

|IQ|/I_rot heatmap over (RF frequency × drive power[dBm]) with the per-power
fitted GE peak trace and the optimal drive power marked. The map carries TWO
physically distinct lines — the GE line (→ f_01) and the 2-photon g→f/2 line
(→ anharmonicity) — and one click cannot be both interpretations (Apply All
would stage contradictions), so the recipe emits TWO tiles per qubit:

* ``power_dbm`` — "set f_01 + drive power". x assigns ``f_01`` +
  ``xy.RF_frequency`` absolutely (the node assigns the fitted absolute —
  patch-verified). y stages the node's COUPLED drive-power pair:
  ``full_scale_power_dbm`` on the 1 dB grid — written to the PORT path the
  run snapshot's pointer resolves to, exactly where the node's
  ``get_reference`` write landed — plus ``operations.saturation.amplitude``,
  both via the ``dbm_gridfs`` client transform (fs = clamp(ceil(P −
  20·log10(max_amp)), −11…16); amp = 10^((P−fs)/20); digit-exact vs a real
  run's patches, clamp branch included). PAIR-OR-NOTHING: when the port
  pointer can't be resolved or the swept operation isn't "saturation" (the
  rare op-override runs also rescale companion gates — not reproducible from
  one click), the power axis stays a read-only context row.
* ``two_photon`` — "set anharmonicity". The g→f/2 line sits at f_01 − α/2,
  so a click assigns ``anharmonicity = 2·(f_01_fit − clicked)`` — plain
  affine anchored to THIS run's fitted GE frequency (ds_fit ``res_freq``,
  which equals the run's own f_01 patch value). qualibrate stores α as a
  positive magnitude; clicking the lower line yields exactly that. Only
  offered when the run's analysis carries the ef fit (older analyses don't).
"""
from __future__ import annotations

import numpy as np

from .. import contracts, plotbuild as pb
from .base import FigureSpec, figure_key, qslice, qubit_index, qubits_of, split_key
from .resonator_2d import _num, _oriented, _scalar

FAMILY = ("1Q_08b_qubit_spectroscopy_vs_power",)

# The node's 1 dB full-scale grid clamp (QOP 3.3–3.6 hardware range; the node
# hardcodes it, so the contract mirrors the node — not the newest hardware).
_FS_DBM_MIN, _FS_DBM_MAX = -11, 16

# ef-fit variables that mark the newer analysis (older runs lack the 2-photon
# fit entirely — their two_photon tile is unavailable, never wrongly clickable).
_EF_VARS = ("anharmonicity_fitted", "twophoton_freq_fitted", "ef_freq_fitted")


def fs_and_amp(power_dbm: float, max_amp: float) -> tuple[int, float]:
    """The node's ``_fs_and_amp``: smallest on-grid full-scale that keeps the
    waveform amplitude ≤ ``max_amp``, then the amplitude realising the power.
    Kept as a python mirror of the ``dbm_gridfs`` client transform so tests can
    pin server/client parity against real patch values."""
    fs = int(min(max(int(np.ceil(power_dbm - 20.0 * np.log10(max_amp))),
                     _FS_DBM_MIN), _FS_DBM_MAX))
    return fs, float(10 ** ((power_dbm - fs) / 20.0))


def _have_signal(bundle) -> bool:
    return bool({"I_rot", "IQ_abs"} & (bundle.fit_vars | bundle.raw_vars))


def _have_ef(bundle) -> bool:
    return bool(set(_EF_VARS) & bundle.fit_vars)


def menu(bundle):
    qubits = qubits_of(bundle) or ["q"]
    multi = len(qubits) > 1
    have = _have_signal(bundle)
    ef = _have_ef(bundle)
    specs = []
    for q in qubits:
        suffix = f" — {q}" if multi else ""
        specs.append(FigureSpec(
            figure_key("power_dbm", q), "Spectroscopy vs power" + suffix, "2d",
            available=have, reason="" if have else "no I_rot/IQ_abs"))
        specs.append(FigureSpec(
            figure_key("two_photon", q), "2-photon line (anharmonicity)" + suffix, "2d",
            available=have and ef,
            reason="" if (have and ef) else
            ("no I_rot/IQ_abs" if not have else "no 2-photon (ef) fit in this run's analysis")))
    return specs


def build(bundle, key):
    base, qname = split_key(key)
    raw, fit = bundle.raw, bundle.fit
    if not raw or not _have_signal(bundle):
        return FigureSpec(key=key, title="Spectroscopy vs power",
                          available=False, reason="no ds_raw signal")
    qidx = qubit_index(fit if (fit and fit.get("vars")) else raw, qname)

    rf_run = contracts.rf_at_run(bundle, qname, resonator=False)
    if "full_freq" in raw.get("vars", {}):
        ff, _ = qslice(raw, "full_freq", qidx)
        x_ghz = np.asarray(ff, dtype=float) / 1e9
    else:
        det = np.asarray(raw["coords"].get("detuning", []), dtype=float)
        if rf_run is None or not det.size:
            return FigureSpec(key=key, title="Spectroscopy vs power",
                              available=False,
                              reason="no absolute frequency axis (full_freq/RF)")
        x_ghz = (rf_run + det) / 1e9
    power = np.asarray(raw["coords"].get("power", []), dtype=float)
    if not power.size:
        return FigureSpec(key=key, title="Spectroscopy vs power",
                          available=False, reason="no power coord")
    src = fit if (fit and "I_rot" in fit.get("vars", {})) else raw
    zvar = "I_rot" if "I_rot" in src.get("vars", {}) else "IQ_abs"
    z = _oriented(src, zvar, qidx, "power")             # [power, detuning]
    data = [pb.heatmap(x_ghz, power, np.asarray(z, dtype=float) * 1e3,
                       colorbar_title=f"{zvar} [mV]", robust=True)]

    shapes = []
    # Per-power fitted GE peak (detuning-relative in ds_fit) over the map.
    if fit and "peak_position" in fit.get("vars", {}) and rf_run is not None:
        pp, _dims = qslice(fit, "peak_position", qidx)
        pp = np.asarray(pp, dtype=float)
        if pp.size == power.size:
            data.append(pb.line((rf_run + pp) / 1e9, power, name="GE peak",
                                color=pb.FIT_COLOR, mode="markers"))
    opt = _scalar(fit, "optimal_power", qidx)
    if opt is not None and np.isfinite(opt):
        shapes.append(pb.hline(opt, color=pb.ACCENT, dash="solid", width=1.5))

    layout = {"xaxis": pb.axis("RF frequency [GHz]"),
              "yaxis": pb.axis("Drive power [dBm]"),
              "shapes": shapes, "margin": {"l": 60, "r": 70, "t": 50, "b": 50}}

    if base == "two_photon":
        return _two_photon(bundle, key, qname, qidx, data, layout)
    return _main(bundle, key, qname, qidx, data, layout, power)


def _max_amp(bundle):
    """The run's ``max_amp`` (amp ceiling the node derives fs from): dataset
    root attrs first (HDF5 round-trips scalars as 1-element arrays), then the
    run parameters. Unlike resonator_2d's fs-first ``_amp_conversion``, the
    attr IS the faithful source here — the 08b node derives its full-scale
    FROM max_amp (its own 1 dB formula), it never reads the current port."""
    for src in ((bundle.raw or {}).get("root_attrs") or {},
                getattr(bundle.run, "parameters", None) or {}):
        v = _num(src.get("max_amp"))
        if v is not None and v > 0:
            return v
    return None


def _power_pair_targets(bundle, qname):
    """The drive-power click pair, or None (→ power stays view-only context).

    Mirrors the node's update exactly: fs on the 1 dB grid written through the
    xy port pointer (resolved against the run's frozen snapshot — the same
    resolution the node's ``get_reference`` write performed), amplitude on the
    saturation op. Pair-or-nothing — staging one half under a moved full-scale
    would realise the wrong power.
    """
    if contracts.run_operation(bundle, default="saturation") != "saturation":
        return None
    ma = _max_amp(bundle)
    merged = getattr(bundle, "quam_state", None) or {}
    if ma is None or not merged:
        return None
    try:
        from quam_state_manager.core.pointer_path import resolve_field_target
        ft = resolve_field_target(
            merged, f"qubits.{qname}.xy.opx_output.full_scale_power_dbm")
    except Exception:
        return None
    if not ft.get("resolvable") or _num(ft.get("resolved_value")) is None:
        return None
    fs_path = ft["resolved_path"]
    tr = {"type": "dbm_gridfs", "max_amp": ma,
          "fs_min": _FS_DBM_MIN, "fs_max": _FS_DBM_MAX}
    targets = [
        {"path": fs_path, "axis": "y", "transform": dict(tr, part="fs"),
         "provenance": contracts._prov(
             "full-scale = clamp(ceil(clicked dBm − 20·log10(max_amp)), −11…16)"
             " — the node's 1 dB grid, written through the run's pointer"
             " xy.opx_output.full_scale_power_dbm",
             [{"label": "max_amp (run)", "frozen_value": ma}])},
        {"path": f"qubits.{qname}.xy.operations.saturation.amplitude",
         "axis": "y", "transform": dict(tr, part="amp"),
         "provenance": contracts._prov(
             "amplitude = 10^((clicked dBm − full-scale)/20) — realises the"
             " clicked power under the staged full-scale",
             [{"label": "max_amp (run)", "frozen_value": ma}])},
    ]
    # The fitted power/amp is only valid for the swept pulse length: when the
    # run overrode it, the node persists that length too — mirror it as a
    # click-independent assign (scale 0) so the staged trio stays consistent.
    params = getattr(bundle.run, "parameters", None) or {}
    length = params.get("operation_len_in_ns")
    if not isinstance(length, bool) and isinstance(length, (int, float)):
        targets.append({
            "path": f"qubits.{qname}.xy.operations.saturation.length",
            "axis": "y", "scale": 0.0, "offset": float(int(length)),
            "provenance": contracts._prov(
                "constant: the run's operation_len_in_ns (the fitted power is"
                " only valid at this pulse length)", [])})
    return targets


def _main(bundle, key, qname, qidx, data, layout, power):
    clickable = {"qubit": qname, "label": "Set qubit frequency + drive power",
                 "targets": [
                     {"path": "qubits.{q}.f_01", "axis": "x", "scale": 1e9,
                      "provenance": contracts._prov(
                          "clicked GHz × 1e9 (the node ASSIGNS the fitted"
                          " absolute frequency)", [])},
                     {"path": "qubits.{q}.xy.RF_frequency", "axis": "x", "scale": 1e9,
                      "provenance": contracts._prov(
                          "clicked GHz × 1e9 (assigned in lock-step with f_01)", [])},
                 ],
                 "context": []}
    pair = _power_pair_targets(bundle, qname)
    if pair:
        clickable["targets"] += pair
        # Twin log-amplitude axis, exactly aligned with the linear dBm axis.
        ma = _max_amp(bundle)
        ref = _num(((bundle.raw or {}).get("root_attrs") or {}).get("max_power_dbm"))
        if ref is None:
            ref = _num((getattr(bundle.run, "parameters", None) or {}).get("max_power_dbm"))
        if ma is not None and ref is not None and power.size:
            amp_lo = ma * 10 ** ((float(power.min()) - ref) / 20)
            amp_hi = ma * 10 ** ((float(power.max()) - ref) / 20)
            x0 = data[0]["x"][0] if data and data[0].get("x") else 0
            data.append({"x": [x0, x0], "y": [amp_lo, amp_hi], "yaxis": "y2",
                         "type": "scatter", "mode": "markers",
                         "marker": {"opacity": 0}, "showlegend": False,
                         "hoverinfo": "skip"})
            layout["yaxis2"] = {"overlaying": "y", "side": "right", "type": "log",
                                "title": {"text": "Drive amplitude [V]"},
                                "showgrid": False}
    else:
        clickable["context"].append({"label": "Drive power", "axis": "y",
                                     "scale": 1, "unit": "dBm", "decimals": 2})
    return FigureSpec(key=key, title="Spectroscopy vs power", kind="2d",
                      figure={"data": data, "layout": layout}, clickable=clickable)


def _two_photon(bundle, key, qname, qidx, data, layout):
    fit = bundle.fit
    ge = _scalar(fit, "res_freq", qidx)          # this run's fitted GE freq
    if ge is None or not np.isfinite(ge):
        ge = contracts._num(((bundle.fit_results or {}).get(qname) or {}).get("frequency"))
    two = _scalar(fit, "twophoton_freq_fitted", qidx)
    if two is None:
        two = _scalar(fit, "twophoton_freq", qidx)   # expected loc (stored α)
    if ge is not None and np.isfinite(ge):
        layout["shapes"].append(pb.vline(ge / 1e9, color=pb.FIT_COLOR, dash="dash", width=1.2))
    if two is not None and np.isfinite(two):
        layout["shapes"].append(pb.vline(two / 1e9, color=pb.ACCENT, dash="dash", width=1.2))

    clickable = None
    if ge is not None and np.isfinite(ge):
        clickable = {
            "axis": "x", "qubit": qname,
            "label": "Set anharmonicity (click the 2-photon line)",
            "targets": [{
                "path": "qubits.{q}.anharmonicity", "axis": "x",
                "scale": -2e9, "offset": 2.0 * ge,
                "provenance": contracts._prov(
                    "α = 2·(f_01_fit − clicked) — the g→f/2 line sits at"
                    " f_01 − α/2; anchored to THIS run's fitted GE frequency"
                    " (click the LOWER line; a click at/above the GE line"
                    " would imply α ≤ 0)",
                    [{"label": "GE fit frequency (this run)", "frozen_value": ge}]),
            }]}
    return FigureSpec(key=key, title="2-photon line (anharmonicity)", kind="2d",
                      figure={"data": data, "layout": layout}, clickable=clickable)
