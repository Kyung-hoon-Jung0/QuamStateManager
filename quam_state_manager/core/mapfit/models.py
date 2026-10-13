"""Map estimators and explicit refusal verdicts. All equations here are [derived]."""
from __future__ import annotations

import numpy as np
from scipy.ndimage import gaussian_filter, map_coordinates
from scipy.optimize import least_squares
from scipy.signal import find_peaks

from .caps import erroramp_caps
from .contour import contours
from .smooth import smooth


def result(verdict, values=None, **diagnostics):
    return {"verdict": verdict, "verdict_line": "VERDICT: " + verdict,
            "values": values, **diagnostics}


def _prepare(z, x, y):
    a, x, y = np.asarray(z, float), np.asarray(x, float), np.asarray(y, float)
    if a.shape != (len(y), len(x)) or min(a.shape) < 3:
        raise ValueError("Map shape must match coordinates, with at least three cells per axis")
    for axis in (x, y):
        if not np.isfinite(axis).all() or not np.all(np.diff(axis) > 0):
            raise ValueError("Model coordinates must be finite and strictly increasing")
    return a, x, y


def _noise(a):
    """[derived] Noise estimate: min over axes of MAD(first differences)/(.67449 sqrt(2))."""
    noises = []
    for axis in (0, 1):
        diffs = np.diff(a, axis=axis).ravel()
        diffs = diffs[np.isfinite(diffs)]
        if diffs.size:
            noises.append(np.median(np.abs(diffs-np.median(diffs))) / (.67448975*np.sqrt(2)))
    return min(noises) if noises else 0.


def _status(a, raw=None, gain=1.):
    """Refusal gate: "flat", "no feature" or None (a feature is present).

    The noise floor comes from the RAW map scaled by the smoothing gain (smooth.noise_gain):
    differences of a smoothed map are correlated and would underestimate its noise.
    A feature needs one of: p98-p2 >= 6 noise (a broad feature); a peak excursion
    max|z-median| >= (sqrt(2 ln N_eff)+1.5) noise, N_eff = cells*gain^2 independent cells
    [derived: expected maximum of N_eff Gaussian samples, plus a 1.5-sigma margin] (a sparse
    line); or raw rows whose spectrum concentrates in one bin (an oscillation).
    """
    source = a if raw is None else np.asarray(raw, float)
    finite = a[np.isfinite(a)]
    if finite.size < 8:
        return "no feature"
    span = float(np.ptp(finite))
    if span <= 1e-12 * max(1., float(np.max(np.abs(finite)))):
        return "flat"
    gain = gain if raw is not None else 1.
    noise = _noise(source) * gain
    effective = max(2., finite.size * gain**2)
    peak = float(np.max(np.abs(finite-np.median(finite))))
    if peak >= (np.sqrt(2*np.log(effective))+1.5)*noise:
        return None
    if np.percentile(finite,98)-np.percentile(finite,2) < 6*noise:
        rows = source[np.all(np.isfinite(source),axis=1)]
        if not len(rows):
            return "no feature"
        power = np.abs(np.fft.rfft(rows-rows.mean(axis=1,keepdims=True),axis=1))**2
        power[:,0] = 0
        concentration = np.max(power,axis=1)/np.maximum(np.sum(power,axis=1),1e-15)
        if np.median(concentration)<.35:
            return "no feature"
    return None


def _gate(a, raw, meta):
    return _status(a, raw, (meta or {}).get("noise_gain", 1.))


def _floor(a, raw, meta):
    """Noise std of the map being modeled (raw noise times the smoothing gain)."""
    return _noise(a) if raw is None else _noise(np.asarray(raw, float)) * (meta or {}).get("noise_gain", 1.)


def _planar(a, x, y, raw=None):
    """A plane explains the MEASURED map (raw when given; smoothing bends a ramp at the edges):
    residual within 2 raw noise floors AND under 30% of the map's spread -> a monotonic ramp."""
    m = a if raw is None else np.asarray(raw, float)
    xx, yy = np.meshgrid(x, y)
    ok = np.isfinite(m)
    basis = np.column_stack((np.ones(ok.sum()), xx[ok], yy[ok]))
    residual = m[ok] - basis @ np.linalg.lstsq(basis, m[ok], rcond=None)[0]
    spread, rest = float(np.std(m[ok])), float(np.std(residual))
    return rest < 1e-8*max(1., spread) or (rest < 2*_noise(m) and rest < .3*spread)


def _parabolic_offset(left, middle, right):
    """[derived] Three equal-spaced samples give offset=(left-right)/(2(left-2mid+right))."""
    den = left-2*middle+right
    return float(np.clip(.5*(left-right)/den, -1, 1)) if den else 0.


def _alignment(a, x, counts, theta, pi):
    """[derived] Free cos/sin row fits vote when extremum phase is within pi/4.

    Reference code algorithm; 50% explained variance and half a visible fringe define usability.
    """
    votes = []
    span = float(np.ptp(x))
    for row, n in zip(a, counts):
        ok = np.isfinite(row)
        xx, yy = x[ok], row[ok]-np.mean(row[ok]) if ok.any() else row[ok]
        ss = float(np.sum(yy**2))
        if len(xx) < 8 or ss <= 0:
            continue
        best = None
        for f in np.linspace(.15,3,300)*n*theta/(2*np.pi):
            c, s = np.cos(2*np.pi*f*(xx-pi)), np.sin(2*np.pi*f*(xx-pi))
            m = np.column_stack((c-c.mean(),s-s.mean()))
            coef = np.linalg.lstsq(m,yy,rcond=None)[0]
            rss = float(np.sum((yy-m@coef)**2))
            if best is None or rss < best[0]:
                best = (rss, f, coef)
        rss, f, (cc, sc) = best
        if 1-rss/ss < .5 or f*span < .5:
            continue
        off = (np.arctan2(sc,cc)+np.pi/2)%np.pi-np.pi/2
        votes.append(int(np.sign(cc)) if abs(off) < np.pi/4 else 0)
    return (max(sum(v>0 for v in votes),sum(v<0 for v in votes))/len(votes),len(votes)) if votes else (0.,0)


def shared_pi(z, x, counts, theta=np.pi):
    """[reference implementation, no paper] The shared-pi scan of the lab's power-Rabi analysis:
    y_N=c_N+d_N cos(N theta (1+k(a/a_pi-1))), one sign of d_N, k on linspace(.25,3,56) for >= 3 rows.

    Eliminate c,d by centered least squares: gain=(basis dot row)^2/(basis dot basis).
    Quality=1-min RSS/median(coarse RSS). Coarse sweep then 41-point refinement. NaN cells are
    masked here (the reference's sums propagate them). Reproduced to 1e-10 against the reference
    module by tests/test_mapfit_reference.py.
    """
    a, x, counts = _prepare(z,x,counts) if len(counts) >= 3 else (
        np.asarray(z,float), np.asarray(x,float), np.asarray(counts,float))
    if a.shape != (len(counts),len(x)) or len(x) < 8 or np.any(counts <= 0) or np.any(x <= 0):
        raise ValueError("Shared-pi needs positive amplitude and count coordinates and eight samples")
    mask = np.isfinite(a)
    usable = np.sum(mask,axis=1) >= 8
    a, counts, mask = a[usable], counts[usable], mask[usable]
    if not len(a):
        return {"a_pi": np.nan, "k": np.nan, "quality": 0., "alignment": 0., "rows_used": 0,
                "has_pi": False, "candidates": []}
    means = np.sum(np.where(mask,a,0),axis=1)/np.sum(mask,axis=1)
    centered = np.where(mask,a-means[:,None],0)
    total = float(np.einsum("ij,ij->",centered,centered))
    ks = np.array([1.]) if len(a)<3 else np.linspace(.25,3,56)
    step = float(np.median(np.diff(x)))
    coarse = np.arange(x.min()*.85,x.max()*1.15+step/2,step)

    def surface(candidates):
        out = np.empty((len(candidates),len(ks)))
        for j,candidate in enumerate(candidates):
            basis = np.cos(theta*counts[None,:,None]*(1+ks[:,None,None]*(x[None,None,:]/candidate-1)))
            if mask.all():
                basis -= basis.mean(axis=2,keepdims=True)
            else:
                basis -= (np.sum(basis*mask,axis=2)/np.sum(mask,axis=1))[:,:,None]
                basis *= mask
            norm = np.einsum("knx,knx->kn",basis,basis)
            projection = np.einsum("knx,nx->kn",basis,centered)
            gain = np.where(norm>1e-12,projection**2/np.where(norm>1e-12,norm,1),0)
            out[j] = total-np.maximum(np.where(projection>0,gain,0).sum(1),np.where(projection<0,gain,0).sum(1))
        return out

    surf = surface(coarse)
    reference = float(np.median(surf))
    profile = surf.min(axis=1)
    jc = int(np.argmin(profile))
    fine = np.linspace(coarse[max(jc-2,0)],coarse[min(jc+2,len(coarse)-1)],41)
    refined = surface(fine)
    ik = int(np.argmin(refined.min(axis=0)))
    rss = refined[:,ik]
    i = int(np.argmin(rss))
    pi = float(fine[i])
    if 0 < i < len(fine)-1:
        den = rss[i-1]-2*rss[i]+rss[i+1]
        off = .5*(rss[i-1]-rss[i+1])/den if den else 0
        if abs(off)<=1:
            pi += off*(fine[1]-fine[0])
    quality = float(1-rss[i]/reference) if reference>0 else 0.
    k = float(ks[ik])
    aligned, used = _alignment(a,x,counts,theta,pi) if len(a)>=3 else (1.,-1)
    has_pi = bool(x.min() <= pi <= x.max() and quality >= .30 and
                  (used<0 or (used>=3 and aligned>=.75 and .25+1e-9<k<3-1e-9)))
    # Distinct residual minima: coarse-grid wiggles closer than three steps are one minimum.
    minima = find_peaks(-profile,distance=3)[0]
    candidates = [float(coarse[j]) for j in minima if profile[j] <= profile[jc]+.03*total
                  and x.min() <= coarse[j] <= x.max()]
    return {"a_pi":pi,"k":k,"quality":quality,"alignment":aligned,"rows_used":used,
            "has_pi":has_pi,"candidates":candidates}


def erroramp(z,x,y,meta=None,raw=None):
    """[derived] Agreement score |mean centered rows|/mean |centered rows|.

    Circular phase agreement minimizes 1-|mean exp(i(phi_N-N target))|.
    """
    meta = meta or {}
    a,x,y = _prepare(z,x,y)
    if meta.get("circular"):
        period = meta["circular"]
        target = meta.get("target_deg",180)*period/360
        residual = (a-y[:,None]*target)*2*np.pi/period
        score = 1-np.abs(np.nanmean(np.exp(1j*residual),axis=0))
        j = int(np.nanargmin(score))
        if np.ptp(score)<.05:
            return result("ambiguous",candidates=[])
        if j in (0,len(x)-1):
            return result("outside the sweep")
        peaks = find_peaks(-score,prominence=.1)[0]
        candidates = [float(x[i]) for i in peaks if score[i]<score[j]+.02]
        if len(candidates)>1:
            return result("ambiguous",candidates=candidates)
        position = x[j]+_parabolic_offset(*score[j-1:j+2])*(x[1]-x[0])
        return result("ok",{"a_pi":float(position),"quality":float(1-score[j]),"has_pi":True},score=score.tolist())
    status = _gate(a,raw,meta)
    if status:
        return result(status)
    fitted = shared_pi(a,x,y,meta.get("theta",np.pi))
    verdict = "ok"
    if not x.min() <= fitted["a_pi"] <= x.max():
        verdict = "outside the sweep"
    elif len(fitted["candidates"])>1:
        verdict = "ambiguous"
    elif not fitted["has_pi"]:
        verdict = "no feature"
    mask = np.isfinite(a)
    means = np.sum(np.where(mask,a,0),axis=1)/np.maximum(mask.sum(axis=1),1)
    centered = np.where(mask,a-means[:,None],0)
    score = np.abs(centered.sum(axis=0))/np.maximum(np.abs(centered).sum(axis=0),1e-15)
    return result(verdict, fitted if verdict=="ok" else None, diagnostics=fitted, agreement_score=score.tolist(),
                  candidates=fitted["candidates"] if verdict=="ambiguous" else [])


def erroramp_smoothing(z,x,counts,sigma=2.,meta=None,reference_compat=False):
    """Fit raw then capped-smoothed maps; reference_compat uses k=1 and row-mean NaN fill."""
    meta = meta or {}
    raw = shared_pi(z,x,counts,meta.get("theta",np.pi))
    k = 1. if reference_compat else meta.get("multipulse_slope_ratio",1.)
    rows = np.minimum(sigma,erroramp_caps(counts,raw["a_pi"],float(np.median(np.diff(x))),meta.get("theta",np.pi),k))
    a = np.asarray(z,float)
    if reference_compat:
        a = np.array([np.where(np.isfinite(row),row,np.nanmean(row)) for row in a])
        rows = np.where(rows>.05,rows,0)
    smoothed = smooth(a,row_sigmas=rows)
    fitted = shared_pi(smoothed,x,counts,meta.get("theta",np.pi))
    gap = fitted["a_pi"]-raw["a_pi"] if raw["has_pi"] else np.nan
    return {"raw":raw,"smoothed":fitted,"z":smoothed,"row_sigmas":rows,
            "opt_amp_prefactor_smoothed":fitted["a_pi"],"smoothing_pi_gap":float(gap),
            "smoothing_verdict":("agree" if abs(gap)<=.003 else "disagree") if np.isfinite(gap) else "n/a (no pi in the sweep)"}


def extremum(z,x,y,meta=None,raw=None):
    """[derived] Fit local q=c+b.u+0.5 u.H.u; vertex u=-solve(H,b) on a 5x5 window."""
    meta = meta or {}
    a,x,y = _prepare(z,x,y)
    status = _gate(a,raw,meta)
    if status:
        return result(status)
    signed = -a if meta.get("extreme","min")=="max" else a
    # A monotonic plane has no extremum even when its lowest pixel lies at an edge.
    valid = np.isfinite(signed)
    if _planar(a,x,y,raw):
        return result("no extremum")
    i,j = np.unravel_index(np.nanargmin(signed),signed.shape)
    if min(i,j,a.shape[0]-1-i,a.shape[1]-1-j)<2:
        return result("outside the sweep")
    filled = np.where(valid,signed,np.nanmax(signed))
    located = gaussian_filter(filled,1,mode="nearest")
    candidates = []
    from scipy.ndimage import minimum_filter
    local = (located==minimum_filter(located,size=7)) & (located<=np.min(located)+.05*np.ptp(located))
    for ci,cj in np.argwhere(local):
        if min(ci,cj,a.shape[0]-1-ci,a.shape[1]-1-cj)>=2:
            candidates.append([float(x[cj]),float(y[ci])])
    if len(candidates)>1:
        return result("ambiguous",candidates=candidates)
    patch = signed[i-2:i+3,j-2:j+3]
    u,v = np.meshgrid(np.arange(-2,3.),np.arange(-2,3.))
    ok = np.isfinite(patch)
    design = np.column_stack((np.ones(ok.sum()),u[ok],v[ok],.5*u[ok]**2,u[ok]*v[ok],.5*v[ok]**2))
    if ok.sum()<6:
        return result("no feature")
    c,bx,by,hxx,hxy,hyy = np.linalg.lstsq(design,patch[ok],rcond=None)[0]
    h = np.array([[hxx,hxy],[hxy,hyy]])
    if np.min(np.linalg.eigvalsh(h))<=0:
        return result("no extremum")
    offset = -np.linalg.solve(h,[bx,by])
    if np.max(np.abs(offset))>2:
        return result("no extremum")
    return result("ok",{"x":float(x[j]+offset[0]*(x[1]-x[0])),
                         "y":float(y[i]+offset[1]*(y[1]-y[0]))},nan_count=int(np.isnan(a).sum()))


def ridge(z,x,y,meta=None,raw=None):
    """[derived] Raw centroid sum(f*w)/sum(w); quadratic vertex=-b/(2a).

    Find on smoothed columns, measure positive baseline-subtracted weights on raw columns.
    """
    meta = meta or {}
    a,x,y = _prepare(z,x,y)
    source = a if raw is None else np.asarray(raw,float)
    status = _gate(a,raw,meta)
    if status:
        return result(status)
    track = np.full(len(x),np.nan)
    widths, pairs, featured = [],[],0
    floor = _floor(a,raw,meta)
    for j in range(len(x)):
        col, original = a[:,j],source[:,j]
        if np.isfinite(col).sum()<8 or np.isfinite(original).sum()<8:
            continue
        baseline = np.nanmedian(col)
        sign = 1 if np.nanmax(col)-baseline>=baseline-np.nanmin(col) else -1
        feature = sign*(col-baseline)
        mad = np.nanmedian(np.abs(col-baseline))
        if np.nanmax(feature)<5*mad:
            continue
        featured += 1
        # A second line must STAND >= 5 noise floors above the baseline (prominence alone counts a
        # noise peak from the noise trough below it, about twice its height).
        peaks = find_peaks(np.nan_to_num(feature,nan=0),height=max(.5*np.nanmax(feature),5*floor),
                           prominence=.25*np.nanmax(feature),distance=6)[0]
        if len(peaks)>1:
            # A second line in this column: skipped here, the map is ambiguous only when many columns agree.
            strongest = sorted(peaks[np.argsort(feature[peaks])[-2:]])
            pairs.append([float(y[p]) for p in strongest])
            continue
        i = int(np.nanargmax(feature))
        if i in (0,len(y)-1):
            continue
        half = feature>=feature[i]/2
        lo,hi = i,i
        while lo>0 and half[lo-1]:
            lo-=1
        while hi<len(y)-1 and half[hi+1]:
            hi+=1
        width = meta.get("hwhm_pts",max(.5,(hi-lo+1)/2))
        start,end = max(0,int(i-3*width)),min(len(y),int(i+3*width)+1)
        w = np.clip(sign*(original[start:end]-np.nanmedian(original)),0,None)
        ok = np.isfinite(w)
        if w[ok].sum()>0:
            track[j] = np.sum(y[start:end][ok]*w[ok])/w[ok].sum()
            widths.append(width)
    if pairs and len(pairs)>=max(3,.25*featured):
        return result("ambiguous",candidates=np.median(pairs,axis=0).tolist(),double_columns=len(pairs))
    ok = np.isfinite(track)
    if ok.sum()<max(5,len(x)//5):
        return result("outside the sweep" if np.nanargmax(np.nanmean(np.abs(a-np.nanmedian(a)),axis=1)) in (0,len(y)-1) else "no feature")
    values = {}
    if meta.get("summary")=="asymptote":
        selection = np.flatnonzero(ok & (x <= x[0]+.2*np.ptp(x)))
        if len(selection)<3:
            return result("no feature",reason="No resolved ridge in the lowest-power fifth of the sweep")
        spread = float(np.median(np.abs(track[selection]-np.median(track[selection]))))
        if spread > 3 * float(np.median(widths)) * (y[1]-y[0]):
            return result("no feature",reason="Low-power positions do not form a resolved asymptote",spread=spread)
        values["asymptote"] = float(np.median(track[selection]))
    elif meta.get("summary")=="sinusoid":
        period = meta.get("period")
        if not period or np.ptp(x[ok])<period:
            return result("no feature",reason="Less than one period")
        basis = np.column_stack((np.ones(ok.sum()),np.cos(2*np.pi*x[ok]/period),np.sin(2*np.pi*x[ok]/period)))
        c,cc,ss = np.linalg.lstsq(basis,track[ok],rcond=None)[0]
        values = {"offset":float(c),"amplitude":float(np.hypot(cc,ss)),
                  "phase":float(np.arctan2(ss,cc)),"period":float(period)}
    else:
        c = np.polyfit(x[ok],track[ok],2)
        vertex = -c[1]/(2*c[0]) if abs(c[0])>1e-12 else np.inf
        if not x.min()<=vertex<=x.max():
            return result("outside the sweep")
        values = {"x":float(vertex),"y":float(np.polyval(c,vertex))}
    return result("ok",values,polyline=np.column_stack((x,track)).tolist(),hwhm_pts=float(np.median(widths)),
                  nan_count=int(np.isnan(source).sum()),missing_columns=int((~ok).sum()))


def chevron(z,x,y,meta=None,raw=None):
    """[derived] f(c)^2=A c^2+B c+C; c0=-B/(2A), f0=sqrt(C-B^2/(4A)), t_pi=1/(2f0).

    Damped cosine rows start from FFT; time_units convert to seconds for two_j_hz.
    """
    meta = meta or {}
    a,x,y = _prepare(z,x,y)
    status = _gate(a,raw,meta)
    if status:
        return result(status)
    dt = float(np.median(np.diff(x)))
    floor = _floor(a,raw,meta)
    frequencies = np.full(len(y),np.nan)
    lowest, doubles = np.full(len(y),np.nan), 0
    for i,row in enumerate(a):
        ok = np.isfinite(row)
        if ok.sum()<12:
            continue
        filled = np.interp(x,x[ok],row[ok])
        # Outside the interaction window a row is often just readout noise. Its random
        # spectral peaks cannot establish a second oscillation branch.
        spectral_floor = 5 * floor * np.sqrt(ok.sum()/2)
        # Two oscillation frequencies in one row (8x zero-padded amplitude spectrum, each >= half the
        # strongest, two native bins apart; window sidelobes stay below 0.22) mean two chevrons.
        padded = np.abs(np.fft.rfft(filled-filled.mean(),n=8*len(x)))
        padded[:4] = 0
        lines = find_peaks(padded,height=max(.5*padded.max(),spectral_floor),distance=16)[0]
        if len(lines):
            lowest[i] = np.fft.rfftfreq(8*len(x),dt)[lines[0]]
            doubles += len(lines)>1
        spectrum = np.abs(np.fft.rfft(filled-filled.mean()))
        spectrum[0] = 0
        if spectrum.max() < spectral_floor:
            continue
        peak = int(np.argmax(spectrum))
        f = np.fft.rfftfreq(len(x),dt)[peak]
        if peak==0 or peak>=len(spectrum)-1:
            continue
        shifted = x[ok]-x[0]
        observed = row[ok]
        def residual(p):
            c,cc,ss,freq,decay = p
            angle = 2*np.pi*freq*shifted
            return c+np.exp(-decay*shifted)*(cc*np.cos(angle)+ss*np.sin(angle))-observed
        amp = np.ptp(observed)/2
        start = [np.mean(observed),amp,0,f,0.]
        fitrow = least_squares(residual,start,bounds=([-np.inf,-np.inf,-np.inf,max(1e-12,f-1/np.ptp(x)),0],
            [np.inf,np.inf,np.inf,min(.499/dt,f+1/np.ptp(x)),10/np.ptp(x)]),max_nfev=250)
        if np.sum(fitrow.fun**2)<.5*np.sum((observed-observed.mean())**2):
            frequencies[i]=fitrow.x[3]
    ok = np.isfinite(frequencies)
    if doubles>=max(3,.25*np.isfinite(lowest).sum()):
        # Each chevron's axis is a minimum of the lowest branch; list them all.
        branch = np.where(np.isfinite(lowest),lowest,np.nanmax(lowest))
        axes = find_peaks(-np.r_[np.inf,branch,np.inf],prominence=.05*np.ptp(branch))[0]-1
        return result("ambiguous",candidates=[float(y[i]) for i in axes],double_rows=int(doubles))
    if ok.sum()<5:
        return result("no feature")
    c = np.polyfit(y[ok],frequencies[ok]**2,2)
    if c[0]<=0:
        return result("no extremum")
    axis = -c[1]/(2*c[0])
    f0sq = np.polyval(c,axis)
    if not y.min()<=axis<=y.max() or f0sq<=0:
        return result("outside the sweep")
    f0 = float(np.sqrt(f0sq))
    scale = {"s":1.,"ms":1e-3,"us":1e-6,"ns":1e-9}.get(meta.get("time_units","s"))
    if scale is None:
        raise ValueError("Unknown time unit")
    t_pi = 1/(2*f0)
    if t_pi>np.ptp(x):
        return result("outside the sweep")
    return result("ok",{"dv0":float(axis),"two_j_hz":f0/scale,"t_pi":t_pi,"t_pi_ns":t_pi*scale*1e9},
                  apex_row=int(np.argmin(np.abs(y-axis))),frequencies=frequencies.tolist(),
                  nan_count=int(np.isnan(a).sum()),missing_rows=int((~ok).sum()))


def landscape(z,x,y,meta=None,raw=None):
    """[derived] Interpolate objective on target contour and refine a three-point arclength minimum."""
    meta = meta or {}
    a,x,y = _prepare(z,x,y)
    if meta.get("b_resolved") is False:
        return result("ambiguous", reason="Node reports an unresolved control minimum", candidates=[])
    status = _gate(a,raw,meta)
    if status:
        return result(status)
    phase = meta.get("phase")
    if phase is None:
        return extremum(a,x,y,meta,raw)
    target = meta.get("target_deg",180.)
    period = meta.get("circular",360.)
    target *= period/360
    geometry = contours(phase,x,y,[target],circular=period)[0]["full_lines"]
    if not geometry:
        return result("outside the sweep")
    options = []
    boundary_options = []
    floor = _floor(a,raw,meta)
    for line in geometry:
        if len(line)<2:
            continue
        indices = np.array([np.interp(line[:,1],y,np.arange(len(y))),np.interp(line[:,0],x,np.arange(len(x)))])
        scores = map_coordinates(a,indices,order=1,mode="nearest",prefilter=False)
        finite = np.isfinite(scores)
        if not finite.any():
            continue
        sigma = meta.get("objective_sigma",0.)
        if sigma > 0:
            near = line[finite & (scores <= np.nanmin(scores) + 2*sigma)]
            span = np.ptp(near / [x[1]-x[0],y[1]-y[0]],axis=0) if len(near) else [0.,0.]
            if np.max(span) > 4:
                return result("ambiguous", candidates=near.tolist(),
                    reason="Contour minimum is unresolved at the measured objective noise", objective_sigma=sigma)
        # Every distinct local minimum along the line competes, so two wells on one contour are seen.
        filled = np.where(finite,scores,np.nanmax(scores))
        prominence = max(.05*float(np.ptp(filled)),3*floor)
        picks = {int(np.nanargmin(scores))} | set(find_peaks(-filled,prominence=prominence)[0].tolist())
        arc = np.r_[0,np.cumsum(np.linalg.norm(np.diff(line,axis=0),axis=1))]
        for j in sorted(picks):
            point = line[j].copy()
            if j in (0,len(line)-1) and (point[0] in (x[0],x[-1]) or point[1] in (y[0],y[-1])):
                boundary_options.append(float(scores[j]))
                continue
            if 0<j<len(line)-1 and np.isfinite(scores[j-1:j+2]).all():
                coef = np.polyfit(arc[j-1:j+2]-arc[j],scores[j-1:j+2],2)
                if coef[0]>0:
                    position = np.clip(-coef[1]/(2*coef[0])+arc[j],arc[j-1],arc[j+1])
                    point = np.array([np.interp(position,arc,line[:,i]) for i in (0,1)])
            options.append((float(scores[j]),point))
    if not options:
        return result("outside the sweep" if boundary_options else "no feature")
    options.sort(key=lambda pair:pair[0])
    if boundary_options and min(boundary_options) < options[0][0]:
        return result("outside the sweep",reason="The lowest contour objective is on the sweep boundary")
    if len(options)>1 and abs(options[1][0]-options[0][0])<max(.02*np.nanstd(a),2*floor):
        return result("ambiguous",candidates=[p.tolist() for _,p in options])
    score,point = options[0]
    return result("ok",{"x":float(point[0]),"y":float(point[1]),"objective":score},
                  contour=[line.tolist() for line in geometry],pick_method=meta.get("pick_method","contour_fit_smoothed"),
                  nan_count=int(np.isnan(a).sum()))


MODELS = {"erroramp":erroramp,"ridge":ridge,"chevron":chevron,"landscape":landscape,"extremum":extremum}


def fit(model,z,x,y,meta=None,raw=None):
    """Dispatch an estimator; retain verdicts and the count of missing raw samples."""
    if model not in MODELS:
        raise ValueError("Unknown model")
    a,x,y = _prepare(z,x,y)
    status = _gate(a,raw,meta)
    if status:
        return result(status,nan_count=int(np.isnan(a).sum()))
    if _planar(a,x,y,raw):
        return result("no extremum",nan_count=int(np.isnan(a).sum()))
    out = MODELS[model](a,x,y,meta,raw)
    # Missing samples of the measured map: smoothing may bridge a gap, the count stays honest.
    out["nan_count"] = int(np.isnan(a if raw is None else np.asarray(raw,float)).sum())
    return out
