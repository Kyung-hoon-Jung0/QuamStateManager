"""CZ phase calibrations (2Q_20/20b conditional phase, 2Q_21/21b compensation).

Four related two-qubit nodes, all keyed by ``qubit_pair`` and view-only:

* 2Q_21  ``raw_and_fit`` — control/target measured state vs x90 frame rotation,
  with the fitted sinusoids overlaid (the saved figure).
* 2Q_21b ``phase_vs_operations`` — error-amplified mean signal vs frame for
  control/target, with the fitted residual-phase line + peak-mean star (saved fig).
* 2Q_20  ``conditional_phase`` — conditional phase (and its fit) vs flux amplitude,
  with the fitted ``optimal_amplitude`` marked (no saved figure — additive).
* 2Q_20b ``conditional_phase`` — error-amplified: conditional phase vs flux
  amplitude, one curve per number-of-operations, ``optimal_amplitude`` marked.
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
}


def _figure_for(name: str):
    """(base, required-fit-vars, title) for the node."""
    import re as _re
    if _re.search(r"(?:^|_)(?:21b|35a)_", name):
        return "phase_vs_operations", _REQUIRED_VARS["phase_vs_operations"], \
            "CZ phase compensation (error-amplified)"
    if _re.search(r"(?:^|_)(?:21|35)[a-z]?_", name):
        return "raw_and_fit", _REQUIRED_VARS["raw_and_fit"], "CZ phase compensation"
    # 2Q_20/20b save their plot as ``phase_figure`` — match it so we replace it.
    return "phase_figure", _REQUIRED_VARS["phase_figure"], "CZ conditional phase"


def menu(bundle):
    base, needs, title = _figure_for(_name(bundle))
    missing = [v for v in needs if v not in bundle.fit_vars]
    have = not missing
    pairs = pairs_of(bundle)
    multi = len(pairs) > 1
    return [FigureSpec(figure_key(base, p), title + (f" — {p}" if multi else ""),
                       "1d", available=have,
                       reason="" if have else "no " + "/".join(missing))
            for p in pairs]


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


def _conditional_phase(bundle, key, pname):
    fit = bundle.fit
    if not fit or "phase_diff" not in fit.get("vars", {}):
        return FigureSpec(key=key, title="CZ conditional phase", available=False, reason="no phase_diff")
    pidx = pair_index(fit, pname)
    x = np.asarray(pslice(fit, "amp_full", pidx)[0], dtype=float) if "amp_full" in fit["vars"] \
        else np.asarray(fit["coords"].get("amp", []), dtype=float)
    xlabel = "Flux pulse amplitude [V]" if "amp_full" in fit["vars"] else "Flux amplitude prefactor"
    pd = np.asarray(pslice(fit, "phase_diff", pidx)[0], dtype=float)
    data = []
    if pd.ndim <= 1:  # 2Q_20: single conditional-phase curve (+ fit overlay)
        data.append(pb.line(x, pd.ravel(), name="conditional phase", color=_CONTROL,
                            mode="lines+markers"))
        if "fitted_curve" in fit.get("vars", {}):
            data.append(pb.line(x, np.asarray(pslice(fit, "fitted_curve", pidx)[0], dtype=float),
                                name="fit", color=pb.FIT_COLOR, dash="dash"))
    else:  # 2Q_20b: one curve per number_of_operations (error amplification)
        ops = list(np.asarray(fit["coords"].get("number_of_operations", [])).ravel())
        for i in range(pd.shape[0]):
            label = f"{int(ops[i])} ops" if i < len(ops) else f"row {i}"
            data.append(pb.line(x, pd[i], name=label))  # auto-colored by Plotly
    opt = pair_scalar(fit, "optimal_amplitude", pidx)
    shapes = [pb.vline(opt, dash="dot")] if (opt is not None and np.isfinite(opt)) else []
    layout = {"xaxis": {"title": {"text": xlabel}},
              "yaxis": {"title": {"text": "Conditional phase [2π units]"}},
              "shapes": shapes, "hovermode": "closest", "margin": _M}
    # CONTRACT-FAITHFUL click (nodes 32/33): the written value is the CZ flux
    # amplitude — clicked x on the ABSOLUTE amp_full axis assigns it directly
    # (verified vs patches on example_lab3 #105/#106/#107). Prefactor axis → view-only.
    clickable = None
    if "amp_full" in fit.get("vars", {}) and pname:
        from .. import contracts
        op = contracts.run_operation(bundle)
        clickable = {"axis": "x", "qubit": pname, "label": "Set CZ amplitude",
                     "targets": [{
                         "path": f"qubit_pairs.{pname}.macros.{op}.flux_pulse_qubit.amplitude",
                         "axis": "x", "scale": 1.0,
                         "provenance": {"formula": "clicked V assigned directly"
                                        " (amp_full axis is absolute)",
                                        "inputs": []}}]}
    return FigureSpec(key=key, title="CZ conditional phase", kind="1d",
                      figure={"data": data, "layout": layout},
                      clickable=clickable)
