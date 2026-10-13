"""Pure deterministic request API; no worker, browser or application state."""
from __future__ import annotations

import hashlib
import json
import numpy as np

from .caps import (DEFAULTS, apply_caps, asymmetry_cap, chevron_caps, erroramp_caps, landscape_cap,
                   phase_slope_cap, ridge_caps)
from .classify import circular_period, classify
from .compare import compare, tolerance, contract_verdict
from .contour import centers, levels, contours
from .models import fit, shared_pi
from .smooth import noise_gain, smooth

API_VERSION = "mapfit/v1"
SLIDER_MAX = 8.


def _json_value(value, floats=False):
    """JSON-safe copy: arrays to lists, non-finite to None; floats=True spells every number as a float."""
    if isinstance(value,np.ndarray):
        return _json_value(value.tolist(),floats)
    if isinstance(value,dict):
        return {str(k):_json_value(v,floats) for k,v in value.items()}
    if isinstance(value,(list,tuple)):
        return [_json_value(v,floats) for v in value]
    if isinstance(value,(bool,np.bool_)):
        return bool(value)
    if isinstance(value,(float,np.floating)):
        return float(value) if np.isfinite(value) else None
    if isinstance(value,(int,np.integer)):
        return float(value) if floats else int(value)
    return value


def canonical_json(value, floats=False):
    return json.dumps(_json_value(value,floats),sort_keys=True,separators=(",",":"),ensure_ascii=True,allow_nan=False)


def request_hash(request):
    """Versioned SHA256 of canonical JSON (sorted keys, every number a float) of the full request."""
    return hashlib.sha256(canonical_json({"version":API_VERSION,"request":request},True).encode("ascii")).hexdigest()


def _step(axis):
    return float(np.median(np.abs(np.diff(axis))))


def _cap_info(kind,a,x,y,meta,raw_fit):
    """Infer cap inputs from raw model diagnostics or explicit saved physical scales."""
    dx,dy = _step(x),_step(y)
    values = raw_fit.get("values") or {}
    if kind=="erroramp" and not meta.get("circular"):
        scan = raw_fit.get("diagnostics") or shared_pi(a,x,y,meta.get("theta",np.pi))
        pi = meta.get("a_pi",scan.get("a_pi"))
        if not pi or not np.isfinite(pi):
            pi = 1.  # the reference implementation's fallback when the raw fit has no number
        rows = erroramp_caps(y,pi,dx,meta.get("theta",np.pi),meta.get("multipulse_slope_ratio",1.))
        return float(np.max(rows)),0.,rows,(DEFAULTS["erroramp"]["x"],0.)
    if kind=="erroramp":
        period = meta["circular"]
        unwrapped = np.unwrap(a*2*np.pi/period,axis=1)*360/(2*np.pi)
        slopes = np.nanmedian(np.abs(np.diff(unwrapped,axis=1)/dx),axis=1)
        rows = np.array([phase_slope_cap(s,dx) if np.isfinite(s) else 0 for s in slopes])
        return float(np.max(rows)),0.,rows,(DEFAULTS["erroramp"]["x"],0.)
    if kind=="ridge":
        # Design 4.2: width and slope come from a first-pass track on a lightly smoothed map
        # (1 pt each axis), measured against the raw map; a saved node width wins.
        first = fit("ridge",smooth(a,1.,1.),x,y,{**meta,"noise_gain":noise_gain(a.shape,1.,1.)},raw=a)
        raw_fit = first if first.get("polyline") else raw_fit
        width = meta.get("hwhm_pts",raw_fit.get("hwhm_pts",1.))
        if "fwhm" in meta:
            width = meta["fwhm"]/2/dy
        polyline = np.asarray(raw_fit.get("polyline",[]),float)
        slope = 0.
        if len(polyline)>2:
            good = np.isfinite(polyline[:,1])
            if good.sum()>2:
                slope = float(np.max(np.abs(np.gradient(polyline[good,1],polyline[good,0]))))
        cap = ridge_caps(width*dy,dy,meta.get("ridge_slope",slope),dx)
        # A flat ridge gives an infinite control cap; the slider range ends at SLIDER_MAX (design 5.2).
        rule = DEFAULTS["ridge"]
        return min(SLIDER_MAX,cap["control"]),cap["frequency"],None,(rule["x"],rule["y_hwhm"]*width)
    if kind=="chevron":
        freq = np.asarray(raw_fit.get("frequencies",[]),float)
        if len(freq)!=len(y):
            filled = np.nan_to_num(a-np.nanmean(a,axis=1,keepdims=True))
            spectra = np.abs(np.fft.rfft(filled,axis=1))
            spectra[:,0] = 0
            freq = np.fft.rfftfreq(len(x),dx)[np.argmax(spectra,axis=1)]
        good = np.isfinite(freq)&(freq>0)
        if not good.any():
            return 0.,0.,np.zeros(len(y)),(0.,0.)
        f0 = meta.get("f0",float(np.min(freq[good])))
        coef = np.polyfit(y[good],freq[good]**2,2) if good.sum()>2 else [0,0,0]
        slope = meta.get("chevron_slope",float(np.sqrt(max(coef[0],1e-12))))
        safe = np.where(good,freq,float(np.max(freq[good])))
        cap = chevron_caps(safe,dx,f0,slope,dy)
        rule = DEFAULTS["chevron"]
        return (float(np.max(cap["time"])),cap["control"],cap["time"],
                (rule["x_cap"]*float(np.min(cap["time"])),rule["y"]))
    finite = np.isfinite(a)
    if not finite.any():
        return 0.,0.,None,(0.,0.)
    index = np.unravel_index(np.nanargmax(a) if meta.get("extreme")=="max" else np.nanargmin(a),a.shape)
    spacing = meta.get("contour_spacing_pts",float("inf"))
    phase = meta.get("phase")
    if phase is not None and "contour_spacing_pts" not in meta:
        period = meta.get("circular",360.)
        angle = np.unwrap(np.unwrap(np.asarray(phase,float)*2*np.pi/period,axis=1),axis=0)*360/(2*np.pi)
        gradients = np.hypot(*np.gradient(angle))
        slope = float(np.nanmax(gradients))
        spacing = 5/slope if slope>0 else float("inf")
    caps = [landscape_cap(a.shape,index,spacing)]*2
    # Asymmetry: a local cubic through the extremum (1 pt pre-smoothing) bounds the vertex shift.
    light = smooth(a,1.,1.)
    for axis,(line,center) in enumerate(((light[index[0],:],index[1]),(light[:,index[1]],index[0]))):
        half = min(10,center,len(line)-1-center)
        u = np.arange(-half,half+1.)
        segment = line[center-half:center+half+1]
        good = np.isfinite(segment)
        if half>=3 and good.sum()>=5:
            c3,c2,_,_ = np.polyfit(u[good],segment[good],3)
            caps[axis] = min(caps[axis],asymmetry_cap(2*c2,6*c3))
    rule = DEFAULTS["landscape"]
    return caps[0],caps[1],None,(rule["x"],rule["y"])


def analyze(request):
    """Analyze full arrays. Same request produces identical numbers and hash across processes.

    Request: z, x, y (centers or edges), meta, smooth, contour, fit. Reproduced plot binding
    metadata lives in meta.x/meta.y; meta.phase is an optional same-grid sibling phase map.
    """
    a = np.asarray(request["z"],float)
    if a.ndim!=2 or min(a.shape)<3 or a.size>4_000_000:
        raise ValueError("Analysis requires a 2-D map of at most four million cells")
    x,y = centers(request["x"],a.shape[1]),centers(request["y"],a.shape[0])
    meta = dict(request.get("meta") or {})
    classification = classify(x,y,meta,a)
    kind = classification["type"]
    settings = request.get("fit") or {}
    model = settings.get("model","auto")
    model = classification["models"][0] if model=="auto" else model
    if model not in classification["models"]:
        raise ValueError("Model is not available for this map type")
    # Models use count/frequency on y, time on x. Sort descending axes before modeling.
    if np.any(np.diff(x)<0):
        x,a = x[::-1],a[:,::-1]
        if "phase" in meta:
            meta["phase"] = np.asarray(meta["phase"])[:,::-1]
    if np.any(np.diff(y)<0):
        y,a = y[::-1],a[::-1,:]
        if "phase" in meta:
            meta["phase"] = np.asarray(meta["phase"])[::-1,:]
    transpose = ((kind in ("erroramp","ridge") and classification["feature_axis"]=="x") or
                 (kind=="chevron" and classification["feature_axis"]=="y"))
    if transpose:
        a,x,y = a.T,y,x
        if "phase" in meta:
            meta["phase"] = np.asarray(meta["phase"]).T
    # A circular sibling does not make the scalar objective itself circular.
    if "phase" not in meta:
        meta["circular"] = classification["circular"]
    elif not meta.get("circular"):
        # The sibling's own period by range: turns stored under a degree label still read as turns.
        meta["circular"] = circular_period(meta["phase"],{"signal_label":"phase",
                                           "signal_units":meta.get("phase_units","")}) or 360.
    raw_fit = fit(model,a,x,y,meta)
    cap_x,cap_y,rows,defaults = _cap_info(kind,a,x,y,meta,raw_fit)
    options = request.get("smooth") or {}
    sigma_x = float(options.get("sigma_x",defaults[0]))
    sigma_y = float(options.get("sigma_y",defaults[1]))
    if transpose:
        sigma_x,sigma_y = float(options.get("sigma_y",defaults[0])),float(options.get("sigma_x",defaults[1]))
    applied = apply_caps(sigma_x,sigma_y,cap_x,cap_y,bool(options.get("override_cap",False)),"y" if kind=="erroramp" else None)
    row_sigmas = None
    if rows is not None:
        row_sigmas = np.full(len(y),applied["sigma_x"]) if applied["override"] else np.minimum(rows,applied["sigma_x"])
        applied["row_sigmas"] = row_sigmas
    kernel = options.get("kernel","gaussian")
    circular = meta.get("circular") if "phase" not in meta else None
    if row_sigmas is None:
        zs = smooth(a,applied["sigma_x"],applied["sigma_y"],kernel,circular=circular)
    else:
        # Per-row strengths along x (erroramp counts never mix; chevron rows have their own period).
        zs = smooth(smooth(a,sigma_y=applied["sigma_y"],kernel=kernel,circular=circular),
                    kernel=kernel,circular=circular,row_sigmas=row_sigmas)
    fit_meta = dict(meta)
    if "phase" in meta:
        fit_meta["phase"] = smooth(meta["phase"],applied["sigma_x"],applied["sigma_y"],kernel,circular=meta.get("circular",360))
    fit_meta["noise_gain"] = noise_gain(a.shape,applied["sigma_x"],applied["sigma_y"],kernel,row_sigmas)
    fitted = fit(model,zs,x,y,fit_meta,raw=a)
    if applied["warning"]:
        fitted["verdict_line"] += "; "+applied["warning"]
        fitted["warnings"] = [applied["warning"]]
    if "patch" in meta:
        evidence = contract_verdict(meta["patch"],meta.get("current_old"))
        fitted["apply_allowed"] = evidence=="verified"
        if evidence=="display-only":
            fitted["verdict_line"] += "; display-only"
    node = meta.get("node_value")
    key = meta.get("value_key",{"erroramp":"a_pi","chevron":"dv0"}.get(model,"x"))
    sm_value = (fitted.get("values") or {}).get(key)
    raw_value = (raw_fit.get("values") or {}).get(key)
    step = _step(y) if key in ("y","dv0") else _step(x)
    tol,source = tolerance(kind,meta.get("node_name",key),step,meta.get("node_error"),meta.get("hwhm"),node,
                           circular=bool(meta.get("circular")) and "phase" not in meta)
    comparison = compare(node,sm_value,tol,source,meta.get("node_name",key),fitted["verdict"])
    comparison["raw_value"] = raw_value
    comparison["raw_smoothed_delta"] = sm_value-raw_value if sm_value is not None and raw_value is not None else None
    contour_settings = request.get("contour") or {}
    geometry = []
    stride = 2 if a.size>1_000_000 else 1
    if contour_settings.get("on",False):
        contour_period = classification["circular"] if "phase" in meta else meta.get("circular")
        contour_meta = {**meta,"circular":contour_period}
        chosen = levels(zs,kind,contour_meta,contour_settings.get("mode","auto"),contour_settings.get("level"),contour_settings.get("band"))
        geometry = contours(zs[::stride,::stride],x[::stride],y[::stride],chosen,contour_period)
        for item in geometry:
            item.pop("full_lines")
            if transpose:
                # Lines are traced in model orientation; hand them back in the figure's (x, y).
                item["lines"] = [np.asarray(line)[:,::-1] for line in item["lines"]]
    applied["contour_stride"] = stride
    applied["transposed"] = transpose
    # Fit values and polylines stay in model orientation; this names the figure axis of each.
    model_axes = {"x":"y","y":"x"} if transpose else {"x":"x","y":"y"}
    digest = request_hash(request)
    provenance = f"derived from smoothed map ({kernel}, sigma x={applied['sigma_x']:.3g} pt, y={applied['sigma_y']:.3g} pt; cap x={cap_x:.3g}, y={cap_y:.3g}); {model}; {fitted['verdict_line']}"
    return _json_value({"schema":API_VERSION,"request_hash":digest,"classification":classification,
        "smoothed":{"z":zs.T if transpose else zs,"x":y if transpose else x,"y":x if transpose else y},
        "fit":fitted,"raw_fit":raw_fit,"compare":comparison,"contours":geometry,
        "caps_applied":applied,"model_axes":model_axes,"provenance_text":provenance})
