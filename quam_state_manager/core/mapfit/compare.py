"""Explicit numerical comparisons and evidence checks for proposed map values."""
from __future__ import annotations

import math
from .caps import AGREE_TOL


def tolerance(kind, name, step=None, node_error=None, hwhm=None, node_value=None, circular=False):
    """[derived] Frequency tolerance=max(step,0.1 HWHM); coupling=3 error else 5%.

    Other tolerances are design policy: reference prefactor 0.003 or one sweep step (a circular
    phase-per-gate map has no prefactor axis, so it gets one grid step).
    """
    if kind=="erroramp" and not circular and name in ("opt_amp_prefactor","a_pi"):
        return AGREE_TOL,"reference constant"
    if name=="two_j_hz":
        if node_error is not None and math.isfinite(node_error) and node_error>=0:
            return 3*node_error,"node's own error bar"
        return (.05*abs(node_value) if node_value is not None else None),"5% of node value"
    if step is None or not math.isfinite(step) or step<=0:
        return None,"no sweep step"
    if kind=="ridge" and name in ("frequency","qubit_frequency","f_01","y","asymptote"):
        return max(step,.1*(hwhm or 0)),"one sweep step or 0.1 HWHM"
    return step,"one sweep step"


def compare(node_value, sm_value, tol, source, node_name="node value", reason=None):
    """[derived] delta=SM-node; agree iff |delta|<=tolerance, with no apply veto."""
    out = {"node_name":node_name,"node_value":node_value,"sm_value":sm_value,"delta":None,
           "tol":tol,"tol_source":source,"verdict":"n/a","reason":reason or ""}
    if node_value is None or not math.isfinite(node_value):
        out["reason"] = "no node value"
    elif sm_value is None or not math.isfinite(sm_value):
        out["reason"] = reason or "no feature"
    elif tol is None or not math.isfinite(tol) or tol<0:
        out["reason"] = "no valid tolerance"
    else:
        out["delta"] = sm_value-node_value
        # Allow only floating-point roundoff at the stated inclusive boundary.
        out["verdict"] = "agree" if abs(out["delta"]) <= tol+1e-14*max(1.,abs(node_value)) else "disagree"
        out["reason"] = "within tolerance" if out["verdict"]=="agree" else "exceeds tolerance"
    return out


def saved_comparisons(kind, record, fitted, raw_fit, dx, dy, frequency_scale=1.,
                      time_scale=1., control_scale=1., lobe=None, landscape_fields=(),
                      frequency_offset=0., quantity_reason=""):
    """Compare in saved units. A chevron's first extreme and full revival differ by two.

    Ridge model axes are control/frequency; landscape values retain their model axes.
    The caller supplies coordinate scales from the figure and its source coordinate.
    """
    values, raw_values = fitted.get("values") or {}, raw_fit.get("values") or {}
    specifications = []
    def add(name, key, scale=1., offset=0., step=None, source_record=None):
        source_record = record if source_record is None else source_record
        node = source_record.get(name)
        if not isinstance(node, (int, float)) or not math.isfinite(node):
            node = None
        sm, raw = values.get(key), raw_values.get(key)
        sm = sm * scale + offset if sm is not None else None
        raw = raw * scale + offset if raw is not None else None
        tol, source = tolerance(kind, name, step, source_record.get("two_j_err_hz"),
            (source_record.get("fwhm") or 0) / 2, node)
        item = compare(node, sm, tol, source, name, fitted["verdict"])
        item.update(raw_value=raw, raw_smoothed_delta=sm-raw if sm is not None and raw is not None else None)
        specifications.append(item)
    if kind == "ridge":
        name = next((n for n in ("qubit_frequency", "resonator_frequency", "frequency") if n in record), "frequency")
        add(name, "asymptote" if "asymptote" in values or "asymptote" in raw_values else "y",
            frequency_scale, offset=frequency_offset or 0., step=dy * frequency_scale)
        specifications[-1]["quantity"] = quantity_reason or "frequency in Hz"
        if frequency_offset is None:
            specifications[-1].update(sm_value=None, raw_value=None, delta=None, raw_smoothed_delta=None,
                                      verdict="n/a", reason=quantity_reason)
        if "idle_offset" in record:
            add("idle_offset", "x", control_scale, step=dx * control_scale)
    elif kind == "chevron" and lobe in ("+", "-"):
        branch = (record.get("lobes") or {}).get(lobe, {})
        center = branch.get("centre_v")
        if isinstance(center, (int, float)) and math.isfinite(center):
            sign = 1 if lobe == "+" else -1
            add("v_res_plus_v" if sign == 1 else "v_res_minus_v", "dv0",
                sign * control_scale, center, step=.0005)
        add("two_j_hz", "two_j_hz", step=1., source_record=branch)
        add("t_lim_ns", "t_pi", 2 * (time_scale or 1.) * 1e9, step=dx * (time_scale or 1.) * 1e9, source_record=branch)
        if time_scale is None:
            specifications[-1].update(sm_value=None, raw_value=None, delta=None, raw_smoothed_delta=None,
                                      verdict="n/a", reason="time units unavailable; no verified conversion to ns")
    elif kind == "landscape":
        nominal = "kept the nominal amplitude" in str(record.get("pick_note", ""))
        pick_missing = record.get("pick_found") is False
        if "optimal_b_over_a" in record:
            b = record["optimal_b_over_a"]
            pick_missing |= not isinstance(b,(int,float)) or not math.isfinite(b)
        for name, key in landscape_fields:
            add(name, key, step=dx if key == "x" else dy, source_record={} if pick_missing or nominal else record)
            if nominal:
                specifications[-1]["reason"] = "different quantity: nominal-amplitude policy versus unrestricted contour minimum"
    return specifications


def contract_verdict(patch, current_old):
    """Check supplied evidence's old value. Learning a click contract belongs to a separate package."""
    if not isinstance(patch,dict) or current_old is None:
        return "display-only"
    old = patch.get("old")
    value = patch.get("value")
    if not isinstance(old,(int,float)) or not isinstance(value,(int,float)):
        return "display-only"
    if not all(math.isfinite(v) for v in (old,value,current_old)) or not math.isclose(old,current_old,rel_tol=1e-9,abs_tol=1e-12):
        return "display-only"
    return "verified"
