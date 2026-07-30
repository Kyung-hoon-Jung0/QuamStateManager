"""CZ phase calibrations (2Q_20/20b conditional phase, 2Q_21/21b compensation).

Four related two-qubit nodes, all keyed by ``qubit_pair`` and view-only:

* 2Q_21  ``raw_and_fit`` — control/target measured state vs x90 frame rotation,
  with the fitted sinusoids overlaid (the saved figure).
* 2Q_21b ``phase_vs_operations`` — error-amplified mean signal vs frame for
  control/target, with the fitted residual-phase line + peak-mean star (saved fig).
* 2Q_20  ``conditional_phase`` — conditional phase (and its fit) vs flux amplitude,
  with the fitted ``optimal_amplitude`` marked (no saved figure — additive).
* 2Q_20b/33 ``conditional_phase`` — error-amplified (IQCC names it
  ``33_cz_conditional_phase_error_amp``; same normalized family): a 2-D
  heatmap amp × #-CZ-operations of ``phase_diff`` — the node's own saved
  ``phase_figure`` form — with the ``optimal_amplitude`` line and a detuning
  [MHz] top ruler, plus a companion ``control_fractions`` tile (the saved
  figure's lower panel: control-qubit g/f fractions vs #ops at the optimum).
"""
from __future__ import annotations

import numpy as np

from .. import plotbuild as pb
from .base import FigureSpec, figure_key, split_key
from .two_qubit_common import pair_index, pair_scalar, pairs_of, pslice, star

FAMILY = ("2Q_20_cz_conditional_phase", "2Q_20b_cz_conditional_phase_error_amp",
          "2Q_21_cz_phase_compensation", "2Q_21b_cz_phase_compensation_error_amp")

_CONTROL, _TARGET = "#4e79a7", "#e15759"
_M = {"l": 60, "r": 30, "t": 50, "b": 50}

# matplotlib's cyclic ``twilight_shifted`` (13 anchors). ``phase_diff`` lives on
# a circle (2π units, 0 ≡ 1): the scale's endpoints must share one color or the
# wrap paints a false discontinuity mid-map. Matches the node's saved figure.
_TWILIGHT_SHIFTED = [
    [0.0, "#301437"], [0.0833, "#4e186f"], [0.1667, "#5e45a6"],
    [0.25, "#6276ba"], [0.3333, "#7ca2c2"], [0.4167, "#b3c6ce"],
    [0.5, "#e2d9e2"], [0.5833, "#d4bcac"], [0.6667, "#c6896c"],
    [0.75, "#b25652"], [0.8333, "#8e2c50"], [0.9167, "#581647"],
    [1.0, "#2f1436"],
]


def _name(bundle) -> str:
    return ((bundle.node_meta.get("metadata") or {}).get("name")
            or getattr(bundle.run, "experiment_name", "") or "")


# Every fit var a builder reads UNCONDITIONALLY — menu availability must gate
# on ALL of them, not just one: 5 archived old-schema 21 runs (2026-03-03
# #10076–#10080) carry fitted_control/fitted_target but store I_control /
# Q_control instead of state_control/state_target, and used to advertise a
# raw_and_fit tile that then KeyError'd at build time.
_REQUIRED_VARS = {
    "raw_and_fit": ("fitted_control", "fitted_target",
                    "state_control", "state_target"),
    "phase_vs_operations": ("control_mean_vs_frame", "target_mean_vs_frame"),
    "phase_figure": ("phase_diff",),
    "control_fractions": ("g_state_control", "f_state_control",
                          "optimal_index"),
}


def _is_error_amp(name: str) -> bool:
    """20b/33 error-amplification variant of the conditional-phase node."""
    return "error_amp" in name


def _figure_for(name: str):
    """(base, required-fit-vars, title) for the node."""
    import re as _re
    if _re.search(r"(?:^|_)(?:21b|35a)_", name):
        return "phase_vs_operations", _REQUIRED_VARS["phase_vs_operations"], \
            "CZ phase compensation (error-amplified)"
    if _re.search(r"(?:^|_)(?:21|35)[a-z]?_", name):
        return "raw_and_fit", _REQUIRED_VARS["raw_and_fit"], "CZ phase compensation"
    # 2Q_20/20b save their plot as ``phase_figure`` — match it so we replace it.
    if _is_error_amp(name):
        return "phase_figure", _REQUIRED_VARS["phase_figure"], \
            "CZ conditional phase (error-amplified)"
    return "phase_figure", _REQUIRED_VARS["phase_figure"], "CZ conditional phase"


def menu(bundle):
    name = _name(bundle)
    base, needs, title = _figure_for(name)
    missing = [v for v in needs if v not in bundle.fit_vars]
    have = not missing
    pairs = pairs_of(bundle)
    multi = len(pairs) > 1
    err_amp = base == "phase_figure" and _is_error_amp(name)
    specs = [FigureSpec(figure_key(base, p), title + (f" — {p}" if multi else ""),
                        "2d" if err_amp else "1d", available=have,
                        reason="" if have else "no " + "/".join(missing))
            for p in pairs]
    if err_amp:
        # The saved figure's lower panel (control g/f fractions vs #ops) as a
        # companion tile — gated on every var its builder reads (P2-B doctrine).
        have_vars = bundle.raw_vars | bundle.fit_vars
        fr_missing = [v for v in _REQUIRED_VARS["control_fractions"]
                      if v not in have_vars]
        specs.extend(
            FigureSpec(figure_key("control_fractions", p),
                       "Control state fractions" + (f" — {p}" if multi else ""),
                       "1d", available=not fr_missing,
                       reason="" if not fr_missing else "no " + "/".join(fr_missing))
            for p in pairs)
    return specs


def _missing_vars(fit, base) -> list[str]:
    """Build-time twin of the menu gate (defence for direct build calls)."""
    have = (fit or {}).get("vars", {})
    return [v for v in _REQUIRED_VARS[base] if v not in have]


def build(bundle, key):
    base, pname = split_key(key)
    if base == "raw_and_fit":
        return _raw_and_fit(bundle, key, pname)
    if base == "phase_vs_operations":
        return _phase_vs_ops(bundle, key, pname)
    if base == "phase_figure":
        return _conditional_phase(bundle, key, pname)
    if base == "control_fractions":
        return _control_fractions(bundle, key, pname)
    return None


def _raw_and_fit(bundle, key, pname):
    fit = bundle.fit
    missing = _missing_vars(fit, "raw_and_fit")
    if missing:
        return FigureSpec(key=key, title="CZ phase compensation",
                          available=False, reason="no " + "/".join(missing))
    pidx = pair_index(fit, pname)
    frame = np.asarray(fit["coords"].get("frame", []), dtype=float)

    def arr(v):
        return np.asarray(pslice(fit, v, pidx)[0], dtype=float)

    data = [pb.scatter(frame, arr("state_control"), name="control", color=_CONTROL, size=7),
            pb.line(frame, arr("fitted_control"), name="control fit", color=_CONTROL),
            pb.scatter(frame, arr("state_target"), name="target", color=_TARGET, size=7),
            pb.line(frame, arr("fitted_target"), name="target fit", color=_TARGET)]
    layout = {"xaxis": {"title": {"text": "x90 frame rotation [rad/2π]"}},
              "yaxis": {"title": {"text": "Measured state"}},
              "hovermode": "closest", "margin": _M}
    return FigureSpec(key=key, title="CZ phase compensation (raw + fit)", kind="1d",
                      figure={"data": data, "layout": layout})


def _phase_vs_ops(bundle, key, pname):
    fit = bundle.fit
    missing = _missing_vars(fit, "phase_vs_operations")
    if missing:
        return FigureSpec(key=key, title="CZ phase compensation (error-amplified)",
                          available=False, reason="no " + "/".join(missing))
    pidx = pair_index(fit, pname)
    frame = np.asarray(fit["coords"].get("frame", []), dtype=float)
    cm = np.asarray(pslice(fit, "control_mean_vs_frame", pidx)[0], dtype=float)
    tm = np.asarray(pslice(fit, "target_mean_vs_frame", pidx)[0], dtype=float)
    data = [pb.line(frame, cm, name="control", color=_CONTROL, mode="lines+markers"),
            pb.line(frame, tm, name="target", color=_TARGET, mode="lines+markers")]
    shapes = []
    cph = pair_scalar(fit, "fitted_control_phase", pidx)
    tph = pair_scalar(fit, "fitted_target_phase", pidx)
    cpk = pair_scalar(fit, "control_mean_at_peak", pidx)
    tpk = pair_scalar(fit, "target_mean_at_peak", pidx)
    if cph is not None and np.isfinite(cph):
        shapes.append(pb.vline(cph, color=_CONTROL, dash="dash"))
        if cpk is not None:
            data.append(star(cph, cpk, "control peak", _CONTROL))
    if tph is not None and np.isfinite(tph):
        shapes.append(pb.vline(tph, color=_TARGET, dash="dash"))
        if tpk is not None:
            data.append(star(tph, tpk, "target peak", _TARGET))
    layout = {"xaxis": {"title": {"text": "Frame rotation [2π]"}},
              "yaxis": {"title": {"text": "Mean signal"}},
              "shapes": shapes, "hovermode": "closest", "margin": _M}
    # CONTRACT-FAITHFUL click (35a / 21b): the node writes
    # phase_shift_X = (pre-update + clicked frame) % 1 — the mod-wrap contract
    # (wrap01 transform; pre from patches[].old else the frozen snapshot).
    from .. import contracts
    op = contracts.run_operation(bundle)
    targets = []
    for role in ("control", "target"):
        dot = f"qubit_pairs.{pname}.macros.{op}.phase_shift_{role}"
        pre, source = contracts.pair_pre_update(
            bundle, pname, f"/macros/{op}/phase_shift_{role}", dot)
        if pre is not None:
            targets.append(contracts.wrap01_target(
                dot, pre, axis="x", sign=+1,
                label=f"{role} phase shift", source=source))
    clickable = ({"axis": "x", "qubit": pname,
                  "label": "Shift CZ phase compensation",
                  "targets": targets} if targets else None)
    return FigureSpec(key=key, title="CZ phase compensation (error-amplified)", kind="1d",
                      figure={"data": data, "layout": layout},
                      clickable=clickable)


def _detuning_mhz(fit, pidx, nx):
    """Per-amp-column detuning [MHz] when the ds persisted it (else None)."""
    if "detuning" not in (fit.get("vars") or {}):
        return None
    det = np.asarray(pslice(fit, "detuning", pidx)[0], dtype=float).reshape(-1)
    if det.size != nx or not np.all(np.isfinite(det)):
        return None
    return det / 1e6


def _detuning_top_axis(data, layout, x, det_mhz, y_anchor):
    """Detuning [MHz] twin ruler on top (readout_opt transparent-trace pattern).

    A heatmap autoranges to cell EDGES while a scatter pads around its markers,
    so BOTH axis ranges are pinned to the same half-cell-extended endpoints —
    otherwise the MHz ruler sits visibly offset from the amplitude axis."""
    x = np.asarray(x, dtype=float)
    if x.size < 2 or det_mhz.size != x.size:
        return
    xr = [float(x[0] - (x[1] - x[0]) / 2), float(x[-1] + (x[-1] - x[-2]) / 2)]
    dr = [float(det_mhz[0] - (det_mhz[1] - det_mhz[0]) / 2),
          float(det_mhz[-1] + (det_mhz[-1] - det_mhz[-2]) / 2)]
    data.append({"x": dr, "y": [y_anchor, y_anchor], "xaxis": "x2",
                 "type": "scatter", "mode": "markers",
                 "marker": {"opacity": 0}, "showlegend": False,
                 "hoverinfo": "skip"})
    layout["xaxis"] = dict(layout.get("xaxis") or {}, range=xr)
    layout["xaxis2"] = {"overlaying": "x", "side": "top", "range": dr,
                        "title": {"text": "Detuning [MHz]"}}


def _conditional_phase(bundle, key, pname):
    fit = bundle.fit
    if not fit or "phase_diff" not in fit.get("vars", {}):
        return FigureSpec(key=key, title="CZ conditional phase", available=False, reason="no phase_diff")
    pidx = pair_index(fit, pname)
    absolute = "amp_full" in fit["vars"]
    x = np.asarray(pslice(fit, "amp_full", pidx)[0], dtype=float).reshape(-1) if absolute \
        else np.asarray(fit["coords"].get("amp", []), dtype=float)
    xlabel = "Flux pulse amplitude [V]" if absolute else "Flux amplitude prefactor"
    pd, pdims = pslice(fit, "phase_diff", pidx)
    pd = np.asarray(pd, dtype=float)
    opt = pair_scalar(fit, "optimal_amplitude", pidx)
    data = []
    title = "CZ conditional phase"
    kind = "1d"
    if pd.ndim <= 1:  # 2Q_20: single conditional-phase curve (+ fit overlay)
        data.append(pb.line(x, pd.ravel(), name="conditional phase", color=_CONTROL,
                            mode="lines+markers"))
        if "fitted_curve" in fit.get("vars", {}):
            data.append(pb.line(x, np.asarray(pslice(fit, "fitted_curve", pidx)[0], dtype=float),
                                name="fit", color=pb.FIT_COLOR, dash="dash"))
        shapes = [pb.vline(opt, dash="dot")] if (opt is not None and np.isfinite(opt)) else []
        layout = {"xaxis": {"title": {"text": xlabel}},
                  "yaxis": {"title": {"text": "Conditional phase [2π units]"}},
                  "shapes": shapes, "hovermode": "closest", "margin": _M}
    else:  # 2Q_20b/33 error amplification: 2-D map, the saved figure's form
        title = "CZ conditional phase (error-amplified)"
        kind = "2d"
        while pd.ndim > 2:                     # collapse stray singleton dims
            pd = pd[0] if pd.shape[0] == 1 else np.nanmean(pd, axis=0)
            pdims = pdims[1:]
        if len(pdims) == 2 and pdims[0] != "number_of_operations":
            pd = pd.T
        ops = np.asarray(fit["coords"].get("number_of_operations", []),
                         dtype=float).reshape(-1)
        if ops.size != pd.shape[0]:
            ops = np.arange(1, pd.shape[0] + 1, dtype=float)
        hm = pb.heatmap(x, ops, pd, colorscale=_TWILIGHT_SHIFTED,
                        zmin=0.0, zmax=1.0, colorbar_title="Phase diff [2π]")
        det_mhz = _detuning_mhz(fit, pidx, x.size)
        if det_mhz is not None:
            hm["customdata"] = pb.clean(np.tile(det_mhz, (pd.shape[0], 1)))
            hm["hovertemplate"] = (
                ("amp: %{x:.5f} V" if absolute else "amp: ×%{x:.4f}")
                + "<br>detuning: %{customdata:.1f} MHz<br># CZ ops: %{y}"
                + "<br>phase diff: %{z:.3f} [2π]<extra></extra>")
        data.append(hm)
        # optimal_amplitude is ABSOLUTE volts — only meaningful on amp_full.
        shapes = [pb.vline(opt, width=2)] \
            if (absolute and opt is not None and np.isfinite(opt)) else []
        layout = {"xaxis": {"title": {"text": xlabel}},
                  "yaxis": {"title": {"text": "# CZ operations"}},
                  "shapes": shapes, "margin": _M}
        if det_mhz is not None and ops.size:
            _detuning_top_axis(data, layout, x, det_mhz, float(ops[0]))
    # CONTRACT-FAITHFUL click (nodes 32/33): the written value is the CZ flux
    # amplitude — clicked x on the ABSOLUTE amp_full axis assigns it directly
    # (verified vs patches on example_lab3 #105/#106/#107). Prefactor axis → view-only.
    clickable = None
    if absolute and pname:
        from .. import contracts
        op = contracts.run_operation(bundle)
        clickable = {"axis": "x", "qubit": pname, "label": "Set CZ amplitude",
                     "targets": [{
                         "path": f"qubit_pairs.{pname}.macros.{op}.flux_pulse_qubit.amplitude",
                         "axis": "x", "scale": 1.0,
                         "provenance": {"formula": "clicked V assigned directly"
                                        " (amp_full axis is absolute)",
                                        "inputs": []}}]}
    return FigureSpec(key=key, title=title, kind=kind,
                      figure={"data": data, "layout": layout},
                      clickable=clickable)


def _control_fractions(bundle, key, pname):
    """The saved figure's lower panel: control-qubit |g⟩/|f⟩ fractions vs
    #-CZ-operations at the fitted optimum's amp column (control prepared
    excited — |g⟩ = residual decay, |f⟩ = leakage). View-only."""
    title = "Control state fractions"
    fit = bundle.fit
    src = next((s for s in (fit, bundle.raw)
                if s and all(v in s.get("vars", {})
                             for v in ("g_state_control", "f_state_control"))),
               None)
    if src is None:
        return FigureSpec(key=key, title=title, available=False,
                          reason="no g_state_control/f_state_control")
    pidx = pair_index(src, pname)
    j_opt = pair_scalar(fit, "optimal_index", pidx)
    if j_opt is None or j_opt < 0:
        return FigureSpec(key=key, title=title, available=False,
                          reason="no optimal_index (fit failed)")
    j_opt = int(j_opt)

    def frac(var):
        arr, dims = pslice(src, var, pidx)
        arr = np.asarray(arr, dtype=float)
        # optimal_index is a COLUMN index (the amp dim is a relative scale —
        # nearest-sel against absolute optimal_amplitude snaps to the edge);
        # control_axis=1 = control prepared |e⟩ (the leakage scenario).
        for dim, idx in (("amp", j_opt), ("control_axis", 1)):
            if dim not in dims:
                return None
            ax = dims.index(dim)
            if not 0 <= idx < arr.shape[ax]:
                return None
            arr = np.take(arr, idx, axis=ax)
            dims = [d for i, d in enumerate(dims) if i != ax]
        if "frame" in dims:
            ax = dims.index("frame")
            arr = np.nanmean(arr, axis=ax)
            dims = [d for i, d in enumerate(dims) if i != ax]
        return arr if dims == ["number_of_operations"] else None

    g = frac("g_state_control")
    fq = frac("f_state_control")
    if g is None or fq is None:
        return FigureSpec(key=key, title=title, available=False,
                          reason="unexpected g/f_state_control dims")
    ops = np.asarray(src.get("coords", {}).get("number_of_operations", []),
                     dtype=float).reshape(-1)
    if ops.size != g.size:
        ops = np.arange(1, g.size + 1, dtype=float)
    data = [pb.line(ops, g, name="g", color=_CONTROL, mode="lines+markers"),
            pb.line(ops, fq, name="f", color=pb.ACCENT, mode="lines+markers")]
    layout = {"xaxis": {"title": {"text": "# CZ operations"}},
              "yaxis": {"title": {"text": "Control qubit state fractions"},
                        "rangemode": "tozero"},
              "hovermode": "closest", "margin": _M}
    return FigureSpec(key=key, title="Control state fractions at optimum"
                      " (|g⟩ residual, |f⟩ leakage)", kind="1d",
                      figure={"data": data, "layout": layout})
