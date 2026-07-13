"""2Q CZ-family 2-D maps — contract-faithful interactive reproductions.

Covers the CZ amplitude/leakage/SNZ/JAZZ/coupler-zero nodes whose decision
variable IS a plotted axis (per the LabB anatomy):

  * 20c/20d leakage amplification (+PALEA)  → coupler_flux_pulse.amplitude = x
  * 33b/33c JAZZ-N / JAZZ2-N                → flux_pulse_qubit.amplitude  = x(V)
  * 39b JAZZ2-N SNZ                         → amplitude = x(V), t_phi_eff = y
  * 38(_2) SNZ b-over-a / 39(_2) SNZ cond-phase → same two + flat_length
    (snapshot-derived); ONLY the "_2" twins write state — the exploratory
    38/39 stay view-only (their update_state is `pass`).
  * 18a coupler zero point                  → qp.detuning = x,
    coupler.decouple_offset = y + offset_at_run (offset recovered from ds
    coords — never the POST-update snapshot)
  * 1Q_24 zz_off_jazz                       → coupler.decouple_offset += x
    (the family's only INCREMENT; pre-update patches-first)

Every amplitude axis is plotted ABSOLUTE (ds ``amp_full``) so clicks need no
snapshot provenance; y (n_ops / N / frame) is amplification context except for
SNZ t_phi_eff which is a real second write. All writes are pair-level
(qubit_pairs.<pair>...). Phase-compensation (35/35a) lives in cz_phase.py.
"""
from __future__ import annotations

import numpy as np

from .. import plotbuild as pb
from .base import FigureSpec, figure_key, split_key
from .two_qubit_common import pair_index, pair_scalar, pslice

FAMILY = (
    "2Q_18a_coupler_zero_point", "18a_coupler_zero_point",
    "18a_coupler_zero_point_coarse",
    "20c_cz_leakage_amplification", "20c_leakage_error_amp",
    "20d_cz_leakage_amplification_palea",
    "33b_JAZZ_N", "33c_JAZZ2_N", "39b_JAZZ2_N_SNZ",
    "38_snz_b_over_a", "39_snz_conditional_phase",
    "1Q_24_zz_off_jazz", "24_zz_off_jazz",
)


# ── pair helpers (these runs are qubit_pair-indexed) ───────────────────────

def _pairs_of(bundle) -> list[str]:
    for src in (bundle.fit, bundle.raw):
        if src and "qubit_pair" in src.get("coords", {}):
            return [str(p) for p in src["coords"]["qubit_pair"]]
    return [str(p) for p in (getattr(bundle.run, "qubit_pairs", None) or [])] or ["pair"]


def _name(bundle) -> str:
    # node.json stores the name under metadata.name — a bare top-level .get("name")
    # is always None, so this recipe used to fall back to the folder name (breaking
    # _kind/_norm family classification). Match the sibling recipes.
    return ((bundle.node_meta or {}).get("metadata") or {}).get("name") \
        or getattr(bundle.run, "experiment_name", "") or ""


def _norm(bundle) -> str:
    from ..registry import _normalize_node_name
    return _normalize_node_name(_name(bundle))


def _amp_full_axis(src, pidx, scale_coord="amp"):
    """(x_values_V, ok): the persisted absolute amplitude axis for this pair."""
    if src and "amp_full" in src.get("vars", {}):
        af, _ = pslice(src, "amp_full", pidx)
        af = np.asarray(af, dtype=float)
        if af.ndim > 1:                        # tolerate extra singleton dims
            af = af.reshape(-1)
        if af.size and np.all(np.isfinite(af)):
            return af, True
    return np.asarray(src.get("coords", {}).get(scale_coord, []),
                      dtype=float), False


def _oriented2d(src, var, pidx, ydim):
    """2-D slice as [y, x] with dims resolved from dim_order (never guessed)."""
    z, dims = pslice(src, var, pidx)
    z = np.asarray(z, dtype=float)
    while z.ndim > 2:                          # collapse leading singleton dims
        z = z[0] if z.shape[0] == 1 else np.nanmean(z, axis=0)
        dims = dims[1:]
    if len(dims) == 2 and dims[0] != ydim:
        z = z.T
    return z


# ── family classification ──────────────────────────────────────────────────

def _kind(bundle) -> str:
    n = _norm(bundle)
    if "coupler_zero_point" in n:
        return "coupler_zero"
    if "leakage" in n:
        return "leakage"
    if "jazz2_n_snz" in n:
        return "jazz_snz"
    if "jazz2_n" in n or "jazz_n" in n:
        return "jazz"
    if "zz_off_jazz" in n:
        return "zz"
    if "snz_b_over_a" in n:
        return "snz_boa"
    if "snz_conditional_phase" in n:
        return "snz_phase"
    return ""


def _is_updating_snz(bundle) -> bool:
    """Only the "_2" twins (38_2/39_2) write state; 38/39 are exploratory."""
    import re
    return bool(re.search(r"(?:^|_)(?:38|39)_2_", _name(bundle)))


# ── menu / build ────────────────────────────────────────────────────────────

_PRIMARY_VAR = {
    "coupler_zero": ("state_target", "I_target"),
    "leakage": ("state_control_target", "I_control"),
    "jazz": ("state_target", "p"),
    "jazz_snz": ("p",),
    "zz": ("state_target",),
    "snz_boa": ("f_state_control", "I_control"),
    "snz_phase": ("phase_diff", "f_state_control"),
}


def menu(bundle):
    kind = _kind(bundle)
    if not kind:
        return []
    have = bundle.raw_vars | bundle.fit_vars
    ok = any(v in have for v in _PRIMARY_VAR[kind])
    specs = []
    for p in _pairs_of(bundle):
        title = {
            "coupler_zero": "Coupler zero point",
            "leakage": "CZ leakage amplification",
            "jazz": "JAZZ-N amplitude",
            "jazz_snz": "SNZ scan (JAZZ2-N)",
            "zz": "ZZ-off coupling",
            "snz_boa": "SNZ b/a leakage",
            "snz_phase": "SNZ conditional phase",
        }[kind]
        specs.append(FigureSpec(figure_key("map", p), f"{title} — {p}", "2d",
                                available=ok,
                                reason="" if ok else "no data vars"))
    return specs


def build(bundle, key):
    base, pname = split_key(key)
    if base != "map":
        return None
    kind = _kind(bundle)
    builder = {
        "coupler_zero": _coupler_zero,
        "leakage": _leakage,
        "jazz": _jazz,
        "jazz_snz": _jazz_snz,
        "zz": _zz,
        "snz_boa": _snz_map,
        "snz_phase": _snz_map,
    }.get(kind)
    return builder(bundle, key, pname) if builder else None


# ── builders ────────────────────────────────────────────────────────────────

def _src(bundle, var):
    for s in (bundle.fit, bundle.raw):
        if s and var in s.get("vars", {}):
            return s
    return None


def _leakage(bundle, key, pname):
    from .. import contracts
    var = next((v for v in _PRIMARY_VAR["leakage"] if _src(bundle, v)), None)
    if var is None:
        return FigureSpec(key=key, title="CZ leakage", available=False, reason="no data")
    src = _src(bundle, var)
    pidx = pair_index(src, pname)
    x, absolute = _amp_full_axis(bundle.fit if (bundle.fit and "amp_full" in
                                 bundle.fit.get("vars", {})) else src, pidx)
    nops = np.asarray(src["coords"].get("number_of_operations", []), dtype=float)
    z = _oriented2d(src, var, pidx, "number_of_operations")
    data = [pb.heatmap(x, nops, z, colorbar_title="P(11)", robust=True)]
    shapes = []
    opt = pair_scalar(bundle.fit, "optimal_amplitude", pidx)
    if opt is not None:
        shapes.append(pb.vline(opt, color=pb.FIT_COLOR, dash="dash"))
    op = contracts.run_operation(bundle)
    clickable = None
    if absolute:
        clickable = {"axis": "x", "qubit": pname,
                     "label": "Set CZ coupler amplitude",
                     "targets": [{
                         "path": f"qubit_pairs.{pname}.macros.{op}.coupler_flux_pulse.amplitude",
                         "axis": "x", "scale": 1.0,
                         "provenance": {"formula": "clicked V assigned directly"
                                        " (amp_full axis is absolute)",
                                        "inputs": []}}]}
    layout = {"xaxis": {"title": {"text": "coupler pulse amplitude [V]"}},
              "yaxis": {"title": {"text": "# CZ operations"}}, "shapes": shapes,
              "margin": {"l": 60, "r": 30, "t": 40, "b": 50}}
    return FigureSpec(key=key, title="CZ leakage amplification", kind="2d",
                      figure={"data": data, "layout": layout},
                      clickable=clickable)


def _jazz(bundle, key, pname):
    from .. import contracts
    var = next((v for v in _PRIMARY_VAR["jazz"] if _src(bundle, v)), None)
    if var is None:
        return FigureSpec(key=key, title="JAZZ-N", available=False, reason="no data")
    src = _src(bundle, var)
    pidx = pair_index(src, pname)
    x, absolute = _amp_full_axis(bundle.fit if (bundle.fit and "amp_full" in
                                 bundle.fit.get("vars", {})) else src, pidx)
    ncoord = "N" if "N" in src.get("coords", {}) else next(
        (c for c in src.get("coords", {}) if c not in ("amp", "qubit_pair")), "N")
    ns = np.asarray(src["coords"].get(ncoord, []), dtype=float)
    z = _oriented2d(src, var, pidx, ncoord)
    data = [pb.heatmap(x, ns, z, colorbar_title="P", robust=True)]
    shapes = []
    opt = pair_scalar(bundle.fit, "optimal_amplitude", pidx)
    if opt is not None and absolute:
        shapes.append(pb.vline(opt, color=pb.FIT_COLOR, dash="dash"))
    op = contracts.run_operation(bundle)
    clickable = None
    if absolute:
        clickable = {"axis": "x", "qubit": pname, "label": "Set CZ amplitude",
                     "targets": [{
                         "path": f"qubit_pairs.{pname}.macros.{op}.flux_pulse_qubit.amplitude",
                         "axis": "x", "scale": 1.0,
                         "provenance": {"formula": "clicked V assigned directly"
                                        " (amp_full axis is absolute)",
                                        "inputs": []}}]}
    layout = {"xaxis": {"title": {"text": "CZ pulse amplitude [V]"}},
              "yaxis": {"title": {"text": "echo count N"}}, "shapes": shapes,
              "margin": {"l": 60, "r": 30, "t": 40, "b": 50}}
    return FigureSpec(key=key, title="JAZZ-N amplitude", kind="2d",
                      figure={"data": data, "layout": layout},
                      clickable=clickable)


def _jazz_snz(bundle, key, pname):
    from .. import contracts
    src = _src(bundle, "p")
    if src is None:
        return FigureSpec(key=key, title="SNZ scan", available=False, reason="no data")
    pidx = pair_index(src, pname)
    tpe = np.asarray(src["coords"].get("t_phi_eff", []), dtype=float)
    x, absolute = _amp_full_axis(bundle.fit if (bundle.fit and "amp_full" in
                                 bundle.fit.get("vars", {})) else src, pidx,
                                 scale_coord="amplitude")
    arr, dims = pslice(src, "p", pidx)
    arr = np.asarray(arr, dtype=float)
    if "N" in dims:
        arr = np.nanmean(arr, axis=dims.index("N"))
        dims = [d for d in dims if d != "N"]
    if len(dims) == 2 and dims[0] != "t_phi_eff":
        arr = arr.T
    data = [pb.heatmap(x, tpe, arr, colorbar_title="⟨P00⟩", robust=True)]
    shapes = []
    opt_a = pair_scalar(bundle.fit, "optimal_amplitude", pidx)
    opt_t = pair_scalar(bundle.fit, "optimal_t_phi_eff", pidx)
    if opt_a is not None and absolute:
        shapes.append(pb.vline(opt_a, color=pb.FIT_COLOR, dash="dash"))
    if opt_t is not None:
        shapes.append(pb.hline(opt_t, color=pb.FIT_COLOR, dash="dash"))
    clickable = None
    if absolute and tpe.size:
        clickable = {"qubit": pname, "label": "Set SNZ amplitude + t_phi_eff",
                     "targets": [
                         {"path": f"qubit_pairs.{pname}.macros.cz_SNZ.flux_pulse_qubit.amplitude",
                          "axis": "x", "scale": 1.0,
                          "provenance": {"formula": "clicked V (absolute axis)",
                                         "inputs": []}},
                         {"path": f"qubit_pairs.{pname}.macros.cz_SNZ.flux_pulse_qubit.t_phi_eff",
                          "axis": "y", "scale": 1.0,
                          "provenance": {"formula": "clicked t_phi_eff [ns]",
                                         "inputs": []}},
                     ]}
    layout = {"xaxis": {"title": {"text": "SNZ amplitude [V]"}},
              "yaxis": {"title": {"text": "t_phi_eff [ns]"}}, "shapes": shapes,
              "margin": {"l": 60, "r": 30, "t": 40, "b": 50}}
    return FigureSpec(key=key, title="SNZ scan (JAZZ2-N)", kind="2d",
                      figure={"data": data, "layout": layout},
                      clickable=clickable)


def _snz_map(bundle, key, pname):
    from .. import contracts
    kind = _kind(bundle)
    var = next((v for v in _PRIMARY_VAR[kind] if _src(bundle, v)), None)
    if var is None:
        return FigureSpec(key=key, title="SNZ", available=False, reason="no data")
    src = _src(bundle, var)
    pidx = pair_index(src, pname)
    tpe = np.asarray(src["coords"].get("t_phi_eff", []), dtype=float)
    x, absolute = _amp_full_axis(bundle.fit if (bundle.fit and "amp_full" in
                                 bundle.fit.get("vars", {})) else src, pidx,
                                 scale_coord="amplitude")
    arr, dims = pslice(src, var, pidx)
    arr = np.asarray(arr, dtype=float)
    while arr.ndim > 2:
        arr = np.nanmean(arr, axis=-1)
    if len(dims) >= 2 and dims[0] != "t_phi_eff":
        arr = arr.T
    ctitle = "phase diff [2π]" if var == "phase_diff" else "P(|f⟩ control)"
    data = [pb.heatmap(x, tpe, arr, colorbar_title=ctitle, robust=True)]
    clickable = None
    if _is_updating_snz(bundle) and absolute and tpe.size:
        # flat_length is state-derived (snapshot), not click-derived: a
        # constant target (scale 0) staged alongside — honest via provenance.
        # ALL targets are routed through the run's `operation` (the LabB
        # 38_2/39_2 update_state writes to the macro the run actually swept) —
        # read path and staged path always agree.
        op = contracts.run_operation(bundle, default="cz_SNZ")
        pulse_dot = f"qubit_pairs.{pname}.macros.{op}.flux_pulse_qubit"
        flat_leaf = "flat_length" if op == "cz_SNZ" else "length"
        flat_dot = f"{pulse_dot}.{flat_leaf}"
        flat = contracts.frozen_value(bundle, flat_dot)
        targets = [
            {"path": f"{pulse_dot}.amplitude",
             "axis": "x", "scale": 1.0,
             "provenance": {"formula": "clicked V (absolute axis)", "inputs": []}},
            {"path": f"{pulse_dot}.t_phi_eff",
             "axis": "y", "scale": 1.0,
             "provenance": {"formula": "clicked t_phi_eff [ns]", "inputs": []}},
        ]
        if flat is not None:
            targets.append({
                "path": flat_dot,
                "axis": "x", "scale": 0.0, "offset": float(int(flat)),
                "provenance": {"formula": "int(scanned length) — from the run's"
                               " snapshot, not the click",
                               "inputs": [{"label": "scanned length",
                                           "frozen_value": float(int(flat))}]}})
        clickable = {"qubit": pname, "label": f"Seed {op} from this point",
                     "targets": targets}
    layout = {"xaxis": {"title": {"text": "SNZ amplitude [V]"}},
              "yaxis": {"title": {"text": "t_phi_eff [ns]"}},
              "margin": {"l": 60, "r": 30, "t": 40, "b": 50}}
    title = ("SNZ conditional phase" if kind == "snz_phase" else "SNZ b/a leakage")
    if not _is_updating_snz(bundle):
        title += " (exploratory — no state update)"
    return FigureSpec(key=key, title=title, kind="2d",
                      figure={"data": data, "layout": layout},
                      clickable=clickable)


def _coupler_zero(bundle, key, pname):
    var = next((v for v in _PRIMARY_VAR["coupler_zero"] if _src(bundle, v)), None)
    if var is None:
        return FigureSpec(key=key, title="Coupler zero point",
                          available=False, reason="no data")
    src = _src(bundle, var)
    pidx = pair_index(src, pname)
    qf = src.get("vars", {}).get("qubit_flux_full")
    cf_full = src.get("vars", {}).get("coupler_flux_full")
    cf_rel = src.get("coords", {}).get("coupler_flux")
    if qf is None or cf_full is None or cf_rel is None:
        return FigureSpec(key=key, title="Coupler zero point",
                          available=False, reason="missing flux coords")
    qx = np.asarray(pslice(src, "qubit_flux_full", pidx)[0], dtype=float).reshape(-1)
    cff = np.asarray(pslice(src, "coupler_flux_full", pidx)[0], dtype=float).reshape(-1)
    cfr = np.asarray(cf_rel, dtype=float)
    offset_run = float(cff[0] - cfr[0]) if (cff.size and cfr.size) else 0.0
    z = _oriented2d(src, var, pidx, "coupler_flux")
    data = [pb.heatmap(qx, cfr, z, colorbar_title=var, robust=True)]
    layout = {"xaxis": {"title": {"text": "qubit flux (absolute) [V]"}},
              "yaxis": {"title": {"text": "coupler flux Δ [V]"}},
              "margin": {"l": 60, "r": 30, "t": 40, "b": 50}}
    clickable = {"qubit": pname, "label": "Set pair detuning + coupler zero",
                 "targets": [
                     {"path": f"qubit_pairs.{pname}.detuning", "axis": "x",
                      "scale": 1.0,
                      "provenance": {"formula": "clicked qubit-flux V (absolute)",
                                     "inputs": []}},
                     {"path": f"qubit_pairs.{pname}.coupler.decouple_offset",
                      "axis": "y", "scale": 1.0, "offset": offset_run,
                      "provenance": {"formula": "clicked ΔV + offset-at-run"
                                     " (recovered from ds coords, never the"
                                     " post-update snapshot)",
                                     "inputs": [{"label": "offset at run",
                                                 "frozen_value": offset_run}]}},
                 ]}
    return FigureSpec(key=key, title="Coupler zero point", kind="2d",
                      figure={"data": data, "layout": layout},
                      clickable=clickable)


def _zz(bundle, key, pname):
    from .. import contracts
    fit = bundle.fit
    src = _src(bundle, "state_target")
    if fit is None or "jeff_smooth" not in (fit.get("vars", {}) or {}):
        if src is None:
            return FigureSpec(key=key, title="ZZ-off", available=False, reason="no data")
    pidx = pair_index(fit or src, pname)
    amp = np.asarray(((fit or src).get("coords", {})).get("amp", []), dtype=float)
    # DatasetStore flattens node.json's parameters.model to the top level —
    # read the flattened key first, nested `model` as fallback (mirrors
    # contracts.run_operation).
    run = getattr(bundle, "run", None)
    params = getattr(run, "parameters", None)
    if not isinstance(params, dict):
        params = {}
    det = params.get("artificial_detuning_in_mhz")
    if det is None and isinstance(params.get("model"), dict):
        det = params["model"].get("artificial_detuning_in_mhz")
    try:
        det = float(det) if det is not None else None
    except (TypeError, ValueError):
        det = None
    y = None
    for cand in ("jeff_smooth", "jeff_raw"):
        if fit and cand in fit.get("vars", {}):
            y = np.asarray(pslice(fit, cand, pidx)[0], dtype=float).reshape(-1)
            break
    if y is None or not amp.size:
        return FigureSpec(key=key, title="ZZ-off coupling", available=False,
                          reason="no fit vars")
    # The node injects an ARTIFICIAL detuning so J_eff=0 shows as a beat at
    # `det` MHz — the decision curve is |J_eff − det| (its minimum is the
    # decoupling point). Plot raw J_eff only when det is truly unavailable,
    # and label honestly either way.
    ydisp = np.abs(y - det) if det is not None else y
    ylabel = "|J_eff − detuning| [MHz]" if det is not None else "J_eff [MHz]"
    data = [pb.line(amp, ydisp,
                    name="|J_eff − detuning|" if det is not None else "J_eff",
                    color="#e8a838", mode="lines+markers")]
    shapes = []
    opt = pair_scalar(fit, "optimal_amplitude", pidx)
    if opt is not None:
        shapes.append(pb.vline(opt, color=pb.FIT_COLOR, dash="dash"))
    pre, source = contracts.pair_pre_update(
        bundle, pname, "/coupler/decouple_offset",
        f"qubit_pairs.{pname}.coupler.decouple_offset")
    clickable = None
    if pre is not None:
        clickable = {"axis": "x", "qubit": pname,
                     "label": "Shift coupler decouple offset",
                     "targets": [{
                         "path": f"qubit_pairs.{pname}.coupler.decouple_offset",
                         "axis": "x", "scale": 1.0, "offset": pre,
                         "provenance": {
                             "formula": "pre-update offset + clicked ΔV"
                                        f"  (node uses '+='; pre from {source})",
                             "inputs": [{"label": f"pre-update offset ({source})",
                                         "frozen_value": pre}]}}]}
    layout = {"xaxis": {"title": {"text": "coupler amplitude Δ [V]"}},
              "yaxis": {"title": {"text": ylabel}},
              "shapes": shapes, "hovermode": "closest",
              "margin": {"l": 60, "r": 30, "t": 40, "b": 50}}
    return FigureSpec(key=key, title="ZZ-off coupling", kind="1d",
                      figure={"data": data, "layout": layout},
                      clickable=clickable)
