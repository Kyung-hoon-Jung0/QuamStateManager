"""Explicit numerical comparisons and evidence checks for proposed map values."""
from __future__ import annotations

import math
from .caps import AGREE_TOL


def tolerance(kind, name, step=None, node_error=None, hwhm=None, node_value=None):
    """[derived] Frequency tolerance=max(step,0.1 HWHM); coupling=3 error else 5%.

    Other tolerances are design policy: reference prefactor 0.003 or one sweep step.
    """
    if kind=="erroramp" and name in ("opt_amp_prefactor","a_pi"):
        return AGREE_TOL,"reference constant"
    if name=="two_j_hz":
        if node_error is not None and math.isfinite(node_error) and node_error>=0:
            return 3*node_error,"node's own error bar"
        return (.05*abs(node_value) if node_value is not None else None),"5% of node value"
    if step is None or not math.isfinite(step) or step<=0:
        return None,"no sweep step"
    if kind=="ridge" and name in ("frequency","f_01","y","asymptote"):
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
