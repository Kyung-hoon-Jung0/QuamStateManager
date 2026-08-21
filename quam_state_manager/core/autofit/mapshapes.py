"""Shared raw-map reading and shape analysis for the calibration families.

Every spectroscopy family in the x180 chain is the same measurement at heart —
sweep a frequency, find where the response has a feature — differing only in
what the SECOND axis is and what shape the feature's position traces along it:

    05 resonator vs power   two branches with a step   (punch-out)
    06 resonator vs flux    a smooth modulation curve
    07 resonator vs coupler the same, driven from the coupler
    09 qubit vs flux        an arch whose turning point is the sweet spot
    08 qubit spectroscopy   no second axis at all — one line

So the reading splits cleanly in two: the primitives here (background removal,
per-slice feature location, look-elsewhere-corrected significance, tracking)
are family-independent and measured once; the shape analysis
(``shape_curve``, ``shape_line``) answers the family's own question about the
track. Only the classification on top is family-specific.

Two lessons from the pilot are baked in and must not be undone (docs/130):

* **Work on the background-subtracted residual.** These maps carry a strong
  smooth transmission slope across frequency, and every statistic taken
  against the raw trace inherits it — a half-depth width measured from the
  global median counted a quarter of the window as feature and merged two
  branches into one line.
* **A row is a MAXIMUM over ``n`` samples.** Pure noise reaches roughly
  ``sqrt(2 ln n)`` on its own, so a flat 3-sigma bar sits on the noise
  expectation of a 400-point trace. The bar carries the look-elsewhere term.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

import numpy as np

logger = logging.getLogger(__name__)

Z_TRACE = 3.0
EDGE_PX = 3

# Candidate names for each role, in preference order. Different generations
# and labs name the same axis differently (flux_bias vs current vs
# attenuated_current); the role is what the shape analysis needs.
# ``RF_frequency`` is one lab's spelling of the same absolute frequency axis
# ``full_freq`` carries elsewhere; without it that lab's entire 1-D readout
# set — 96 of 96 targets — read as unreadable rather than as data.
FREQ_VARS = ("full_freq", "RF_frequency", "detuning")
SWEEP_VARS = ("power", "flux_bias", "current", "attenuated_current", "amplitude")
VALUE_VARS = ("IQ_abs", "I", "state")


def z_bar(n: int) -> float:
    """Per-slice significance bar, corrected for how many places the feature
    could be. Without the correction a 400-point qubit-spectroscopy trace
    reads noise as signal roughly every other row."""
    if n < 4:
        return Z_TRACE
    return max(Z_TRACE, math.sqrt(2.0 * math.log(float(n))) + 1.0)


def baseline(y: np.ndarray, deg: int = 3, rounds: int = 2) -> np.ndarray:
    """Smooth background under a trace, fitted with the feature excluded.

    Sigma-clipped polynomial rather than a median filter: a filter wide
    enough to be smooth eats a genuinely broad feature, and broad features
    are exactly the ones these families argue about.
    """
    n = y.size
    if n < 8:
        return np.full(n, float(np.median(y)))
    x = np.linspace(-1.0, 1.0, n)
    keep = np.ones(n, dtype=bool)
    fit = np.full(n, float(np.median(y)))
    for _ in range(rounds + 1):
        if keep.sum() <= deg + 1:
            break
        try:
            coef = np.polyfit(x[keep], y[keep], deg)
        except (np.linalg.LinAlgError, ValueError):
            break
        fit = np.polyval(coef, x)
        resid = y - fit
        scale = float(np.median(np.abs(resid))) * 1.4826 + 1e-30
        keep = np.abs(resid) < 2.0 * scale
        if keep.sum() <= deg + 1:
            keep = resid > -2.0 * scale
    return fit


def slice_features(col: np.ndarray, *, sign: int = -1
                   ) -> tuple[int, float, float, list[tuple[int, float]], bool]:
    """(index, depth in noise units, contiguous half-height width, secondary
    extrema, truncated-at-an-edge) for one 1-D trace.

    ``sign=-1`` looks for a dip (the feature points DOWN from the baseline),
    ``+1`` for a peak. The width GROWS from the extremum rather than counting
    every sample past a global threshold: a feature is a connected thing, not
    a population.
    """
    # Normalise so the feature is always NEGATIVE, whichever way it points:
    # a dip is already below the baseline, a peak has to be flipped. Getting
    # this backwards still tracks the stronger feature (the orientation probe
    # tries both), but it reports the wrong KIND, which is the one thing a
    # judge reading the ledger would take at face value.
    resid = -(col - baseline(col)) * sign
    noise = float(np.median(np.abs(np.diff(resid)))) * 1.4826 / math.sqrt(2) + 1e-30
    j = int(np.argmin(resid))
    depth = -float(resid[j]) / noise
    half = 0.5 * float(resid[j])
    lo = j
    while lo > 0 and resid[lo - 1] <= half:
        lo -= 1
    hi = j
    while hi < resid.size - 1 and resid[hi + 1] <= half:
        hi += 1
    width = float(hi - lo + 1)

    masked = resid.copy()
    pad = int(max(2, width))
    masked[max(0, lo - pad): min(resid.size, hi + pad + 1)] = np.inf
    others: list[tuple[int, float]] = []
    for _ in range(2):
        if not np.isfinite(masked).any():
            break
        k = int(np.argmin(masked))
        d2 = -float(masked[k]) / noise
        if d2 < Z_TRACE:
            break
        # A rival LINE is a resolved feature, not a tall sample: measure its
        # own width the same way and require it to be comparable. Depth alone
        # called broad noisy single peaks multi-feature on 92 of 217 real
        # qubit-spectroscopy targets, where a person sees one line.
        h2 = 0.5 * float(resid[k])
        l2 = k
        while l2 > 0 and np.isfinite(masked[l2 - 1]) and resid[l2 - 1] <= h2:
            l2 -= 1
        r2 = k
        while (r2 < resid.size - 1 and np.isfinite(masked[r2 + 1])
               and resid[r2 + 1] <= h2):
            r2 += 1
        w2 = float(r2 - l2 + 1)
        if w2 >= 0.4 * width:
            others.append((k, d2))
        masked[max(0, k - pad): min(resid.size, k + pad + 1)] = np.inf
    return j, depth, width, others, (lo == 0 or hi == resid.size - 1)


# ---------------------------------------------------------------------------
# reading a cube
# ---------------------------------------------------------------------------

@dataclass
class CubeRead:
    """A family-agnostic view of one target's raw map.

    ``z`` is always (frequency, sweep). Which stored axis was which is
    resolved by SIZE against the named coordinate arrays, because the axis
    order genuinely differs between families — the qubit-flux cubes are
    (qubit, freq, flux) and the resonator-flux cubes are (qubit, flux, freq).
    Assuming either one would silently transpose half the corpus.
    """
    freq: np.ndarray
    sweep: np.ndarray | None
    sweep_name: str | None
    z: np.ndarray                      # (n_freq, n_sweep); (n_freq, 1) when 1-D
    value_name: str

    @property
    def n_freq(self) -> int:
        return int(self.z.shape[0])

    @property
    def n_sweep(self) -> int:
        return int(self.z.shape[1])

    def freq_at(self, px: float | None) -> float | None:
        if px is None or not np.isfinite(px):
            return None
        i = int(round(px))
        if i < 0 or i >= self.freq.size:
            return None
        return float(self.freq[i])

    def freq_step(self) -> float:
        return float(abs(self.freq[1] - self.freq[0])) if self.freq.size > 1 else 0.0


def read_cube(folder: str | Path, target: str, *,
              value_vars: Sequence[str] = VALUE_VARS,
              freq_vars: Sequence[str] = FREQ_VARS,
              sweep_vars: Sequence[str] = SWEEP_VARS) -> CubeRead | None:
    """Read one target's map. Returns None when unreadable — never a guess."""
    from quam_state_manager.core.ndview import _h5_lock_for, _open_reader

    raw = Path(folder) / "ds_raw.h5"
    if not raw.exists():
        return None
    try:
        with _h5_lock_for(str(raw)), _open_reader(raw) as f:
            idx = None
            for dim in ("qubit", "qubit_pair"):
                coord = f.read_coord(dim)
                if coord is None:
                    continue
                names = [n.decode() if isinstance(n, bytes) else str(n)
                         for n in np.asarray(coord).tolist()]
                if target in names:
                    idx = names.index(target)
                    break
            if idx is None:
                return None
            value_name, cube = None, None
            for v in value_vars:
                ds = f.get(v)
                if ds is not None:
                    value_name = v
                    cube = np.asarray(f.read(ds), dtype=float)
                    break
            if cube is None or cube.ndim not in (2, 3):
                return None
            z = cube[idx]
            freq = None
            for v in freq_vars:
                ds = f.get(v)
                if ds is None:
                    continue
                arr = np.asarray(f.read(ds), dtype=float)
                freq = arr[idx] if arr.ndim == 2 else arr
                break
            sweep, sweep_name = None, None
            for v in sweep_vars:
                ds = f.get(v)
                if ds is None:
                    continue
                arr = np.asarray(f.read(ds), dtype=float)
                if arr.ndim == 1 and arr.size > 1:
                    sweep, sweep_name = arr, v
                    break
    except (OSError, ValueError, KeyError, TypeError) as exc:
        logger.debug("mapshapes: %s unreadable (%s)", raw, exc)
        return None
    if freq is None:
        return None

    if z.ndim == 1:
        if z.size != freq.size:
            return None
        return CubeRead(freq=freq, sweep=None, sweep_name=None,
                        z=z[:, None], value_name=value_name or "?")
    if sweep is None:
        return None
    if z.shape == (freq.size, sweep.size):
        pass
    elif z.shape == (sweep.size, freq.size):
        z = z.T
    else:
        return None
    order = np.argsort(sweep)
    return CubeRead(freq=freq, sweep=sweep[order], sweep_name=sweep_name,
                    z=z[:, order], value_name=value_name or "?")


# ---------------------------------------------------------------------------
# tracking the feature along the sweep
# ---------------------------------------------------------------------------

@dataclass
class Track:
    pos: np.ndarray = field(repr=False)        # feature index per slice, nan where lost
    depth: np.ndarray = field(repr=False)
    width_px: float
    truncated: np.ndarray = field(repr=False)
    seconds: list = field(repr=False)
    n_traceable: int
    coverage: float
    bar: float
    # which background revealed the track: a feature only the static
    # background can see did not move with the swept knob
    background: str = "static"

    @property
    def ok(self) -> np.ndarray:
        return ~np.isnan(self.pos)


def track_feature(cube: CubeRead, *, sign: int = -1) -> Track:
    """Locate the feature in every sweep slice. Slices where nothing clears
    the bar stay NaN — an untraceable slice is part of the picture, never a
    number to be filled in."""
    n_freq, n_sweep = cube.z.shape
    bar = z_bar(n_freq)
    pos = np.full(n_sweep, np.nan)
    depth = np.zeros(n_sweep)
    trunc = np.zeros(n_sweep, dtype=bool)
    widths: list[float] = []
    seconds: list[list[tuple[int, float]]] = []
    for p in range(n_sweep):
        j, d, w, others, tr = slice_features(cube.z[:, p], sign=sign)
        depth[p] = d
        widths.append(w)
        seconds.append(others)
        trunc[p] = tr
        if d >= bar:
            pos[p] = j
    ok = ~np.isnan(pos)
    n_ok = int(ok.sum())
    width = float(np.median([w for w, t in zip(widths, ok) if t]) if n_ok
                  else np.median(widths))
    return Track(pos=pos, depth=depth, width_px=width, truncated=trunc,
                 seconds=seconds, n_traceable=n_ok,
                 coverage=n_ok / float(n_sweep or 1), bar=bar)


def orient(cube: CubeRead) -> int:
    """Is the feature a dip or a peak in this recording?

    The readout rotation decides the sign, not the physics, and it differs
    between labs and even between qubits in one run — so it is measured.
    Decided from the STRONGEST slice, not the median across slices: on a map
    whose feature is visible over part of the sweep, the median is dominated
    by the empty slices and reports whichever way the noise happened to lean.
    """
    best_sign, best = -1, -np.inf
    for sign in (-1, 1):
        strongest = -np.inf
        for p in range(cube.z.shape[1]):
            _j, d, _w, _o, _t = slice_features(cube.z[:, p], sign=sign)
            strongest = max(strongest, d)
        if strongest > best:
            best_sign, best = sign, strongest
    return best_sign


def _residuals(cube: CubeRead, sign: int) -> tuple[np.ndarray, np.ndarray]:
    """(moving-feature residual, static residual), both sign-normalised so a
    feature is NEGATIVE.

    Two backgrounds, because they answer different questions:

    * **moving** — subtract, at each frequency, the median ACROSS the sweep.
      A feature that moves with the swept knob survives; the map's own
      transmission shape does not. This is what finds a broad band tracing an
      arch, which a per-slice polynomial cannot separate from its background
      (measured: a textbook flux arch tracked in 18% of slices under the
      polynomial and 100% under this).
    * **static** — subtract a smooth per-slice baseline. A feature that does
      NOT move survives here and vanishes from the moving residual, which is
      how a flat, unresponsive line is told from an empty window rather than
      guessed at.
    """
    z = cube.z
    move = -(z - np.median(z, axis=1)[:, None]) * sign
    static = np.empty_like(z)
    for p in range(z.shape[1]):
        static[:, p] = -(z[:, p] - baseline(z[:, p])) * sign
    return move, static


def _normalise(resid: np.ndarray) -> np.ndarray:
    """Per-slice noise normalisation -> positive z where a feature sits."""
    out = np.empty_like(resid)
    for p in range(resid.shape[1]):
        col = resid[:, p]
        noise = float(np.median(np.abs(np.diff(col)))) * 1.4826 / math.sqrt(2) + 1e-30
        out[:, p] = -col / noise
    return out


def _walk(zn: np.ndarray, cube: CubeRead, sign: int, w0: float,
          p0: int, j0: int, max_gap: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    n_freq, n_sweep = zn.shape
    pos = np.full(n_sweep, np.nan)
    widths = np.zeros(n_sweep)
    trunc = np.zeros(n_sweep, dtype=bool)
    pos[p0] = j0
    widths[p0] = w0

    def go(order):
        prev, drift, misses = j0, 0.0, 0
        for p in order:
            half = int(max(3, round(3.0 * max(1.0, w0) + 2.0 * abs(drift))))
            lo = max(0, int(prev + drift - half))
            hi = min(n_freq, int(prev + drift + half) + 1)
            if hi - lo < 3:
                break
            seg = zn[lo:hi, p]
            k = int(np.argmax(seg))
            # the bar corrected for how many places we ACTUALLY looked
            local = max(Z_TRACE, math.sqrt(2.0 * math.log(float(hi - lo))) + 1.0)
            if seg[k] < local:
                misses += 1
                if misses > max_gap:
                    break
                continue
            misses = 0
            j = lo + k
            drift = 0.6 * drift + 0.4 * float(j - prev)
            prev = j
            pos[p] = j
            _jj, _dd, ww, _oo, tt = slice_features(cube.z[:, p], sign=sign)
            widths[p] = ww
            trunc[p] = tt

    go(range(p0 + 1, n_sweep))
    go(range(p0 - 1, -1, -1))
    return pos, widths, trunc


def track_ridge(cube: CubeRead, *, sign: int = -1,
                max_gap: int = 3) -> Track:
    """Follow the feature as a CONNECTED ridge across the sweep.

    Judging every slice independently against a whole-trace bar is the wrong
    test for a 2-D map, and measurably so: on the real corpus it found the
    ridge in 2-27% of slices on maps where a person sees it running clean
    across the whole window. Two reasons, both structural —

    * a single slice is a weak measurement, so many slices are individually
      marginal while the ridge they form is obvious;
    * the whole-trace bar carries a look-elsewhere penalty for searching
      ``n_freq`` positions, but once the ridge is anchored we are not
      searching the trace — we are searching a neighbourhood of where it was.

    So: anchor on the strongest slice, then walk outward looking only near
    the previous position, with the bar corrected for THAT multiplicity.
    A few consecutive misses are tolerated and RECORDED (an anticrossing
    dims the ridge exactly this way); more than ``max_gap`` ends the walk.

    Tracked on the MOVING residual first (see ``_residuals``); a feature that
    only the static residual can see is stationary, and the walk is repeated
    there so that a flat line is reported as flat rather than as empty.
    """
    n_freq, n_sweep = cube.z.shape
    bar = z_bar(n_freq)
    move, static = _residuals(cube, sign)
    empty = Track(pos=np.full(n_sweep, np.nan), depth=np.zeros(n_sweep),
                  width_px=1.0, truncated=np.zeros(n_sweep, dtype=bool),
                  seconds=[[] for _ in range(n_sweep)], n_traceable=0,
                  coverage=0.0, bar=bar)

    best: Track | None = None
    for which, resid in (("moving", move), ("static", static)):
        zn = _normalise(resid)
        depth = np.nanmax(zn, axis=0)
        p0 = int(np.argmax(depth))
        if depth[p0] < bar:
            continue
        j0 = int(np.argmax(zn[:, p0]))
        _jj, _dd, w0, _oo, _tt = slice_features(cube.z[:, p0], sign=sign)
        pos, widths, trunc = _walk(zn, cube, sign, w0, p0, j0, max_gap)
        ok = ~np.isnan(pos)
        ws = widths[ok & (widths > 0)]
        t = Track(pos=pos, depth=depth, width_px=float(np.median(ws)) if ws.size
                  else float(w0), truncated=trunc,
                  seconds=[[] for _ in range(n_sweep)],
                  n_traceable=int(ok.sum()),
                  coverage=int(ok.sum()) / float(n_sweep or 1), bar=bar)
        t.background = which
        if best is None or t.coverage > best.coverage + 0.05:
            best = t
    return best if best is not None else empty


# ---------------------------------------------------------------------------
# shapes
# ---------------------------------------------------------------------------

@dataclass
class CurveShape:
    """What the feature's position does along the sweep — the question the
    flux families ask (and the punch-out families answer differently)."""
    span_px: float                 # peak-to-peak motion of the track
    moves: bool                    # motion beyond the feature's own width
    vertex_sweep: float | None     # sweep value of the fitted turning point
    vertex_inside: bool
    vertex_px: float | None
    curvature: float | None        # quadratic coefficient (px per sweep^2)
    curvature_significant: bool
    resid_rms_px: float | None     # how parabolic the track actually is
    monotonic: bool
    breaks: int                    # gaps in an otherwise-traceable track
    periods: float | None          # significant turning points / 2
    turns: int                     # significant turning points
    extremum_inside: bool          # an observed (not fitted) turning point
    coverage: float


def shape_curve(cube: CubeRead, tr: Track) -> CurveShape:
    ok = tr.ok
    n_ok = int(ok.sum())
    sweep = cube.sweep if cube.sweep is not None else np.arange(cube.n_sweep, dtype=float)
    if n_ok < 4:
        return CurveShape(span_px=0.0, moves=False, vertex_sweep=None,
                          vertex_inside=False, vertex_px=None, curvature=None,
                          curvature_significant=False, resid_rms_px=None,
                          monotonic=False, breaks=0, periods=None,
                          turns=0, extremum_inside=False,
                          coverage=tr.coverage)
    x = sweep[ok]
    y = tr.pos[ok]
    span = float(np.ptp(y))
    # "moves" is judged against the feature's OWN width: a track wandering by
    # less than a linewidth has not demonstrated any response to the sweep.
    moves = span > max(1.0, tr.width_px)

    vertex_sweep = vertex_px = curvature = resid = None
    vertex_inside = curv_sig = False
    if x.size >= 5 and float(np.ptp(x)) > 0:
        xs = (x - x.mean()) / (np.ptp(x) or 1.0)
        try:
            a, b, c = np.polyfit(xs, y, 2)
            pred = np.polyval((a, b, c), xs)
            resid = float(np.sqrt(np.mean((y - pred) ** 2)))
            curvature = float(a)
            if abs(a) > 1e-12:
                vx = -b / (2.0 * a)
                vertex_sweep = float(vx * (np.ptp(x) or 1.0) + x.mean())
                vertex_px = float(np.polyval((a, b, c), vx))
                vertex_inside = bool(x.min() <= vertex_sweep <= x.max())
            # the curvature matters only if it beats the scatter it is fitted
            # through: a noisy flat track always fits SOME parabola
            lin = np.polyfit(xs, y, 1)
            lin_resid = float(np.sqrt(np.mean((y - np.polyval(lin, xs)) ** 2)))
            curv_sig = bool(resid is not None and lin_resid > 0
                            and resid < 0.7 * lin_resid
                            and abs(a) > 0.5 * max(1.0, tr.width_px))
        except (np.linalg.LinAlgError, ValueError, TypeError):
            pass

    d = np.diff(y)
    monotonic = bool(d.size and (np.all(d >= -0.5 * max(1.0, tr.width_px))
                                 or np.all(d <= 0.5 * max(1.0, tr.width_px))))
    # an OBSERVED turning point: the track rises then falls (or the reverse)
    # by more than its own width on both sides
    extremum_inside = False
    if y.size >= 5 and moves:
        k = int(np.argmax(y)) if y[0] < y.max() else int(np.argmin(y))
        for k in (int(np.argmax(y)), int(np.argmin(y))):
            if 1 < k < y.size - 2:
                left = abs(y[k] - y[0])
                right = abs(y[k] - y[-1])
                if left > tr.width_px and right > tr.width_px:
                    extremum_inside = True
                    break

    # breaks: interior stretches where the track is lost although it is
    # traceable on both sides — an anticrossing looks exactly like this
    idx = np.where(ok)[0]
    breaks = 0
    if idx.size >= 2:
        gaps = np.diff(idx)
        breaks = int(np.sum(gaps > 1))

    # Count only turns the ridge actually MAKES: smooth over a fraction of
    # the sweep, then keep an extremum only when the ridge travels more than
    # its own width between it and the next one. Counting raw sign changes
    # called a single clean arch "multi-period" on 105 of 149 real maps.
    periods = None
    turns = 0
    if y.size >= 8 and moves:
        win = max(3, int(round(y.size / 8.0)) | 1)
        pad = win // 2
        ext = np.concatenate([np.full(pad, y[0]), y, np.full(pad, y[-1])])
        sm = np.convolve(ext, np.ones(win) / win, mode="valid")[:y.size]
        # Sign of the smoothed slope, with flat stretches carrying the last
        # real direction: a rounded arch has EQUAL samples at its apex, and a
        # strict product test finds no turn there at all — the very shape the
        # count exists to recognise.
        d = np.diff(sm)
        sgn = np.zeros(d.size, dtype=int)
        last = 0
        for i, v in enumerate(d):
            if v > 0:
                last = 1
            elif v < 0:
                last = -1
            sgn[i] = last
        cand = [0]
        for i in range(1, sgn.size):
            if sgn[i] != 0 and sgn[i - 1] != 0 and sgn[i] != sgn[i - 1]:
                cand.append(i)
        cand.append(sm.size - 1)
        kept = [cand[0]]
        for i in cand[1:]:
            if abs(sm[i] - sm[kept[-1]]) > max(1.0, tr.width_px):
                kept.append(i)
        turns = max(0, len(kept) - 2)          # endpoints are not turns
        periods = turns / 2.0

    return CurveShape(span_px=span, moves=moves, vertex_sweep=vertex_sweep,
                      vertex_inside=vertex_inside, vertex_px=vertex_px,
                      curvature=curvature, curvature_significant=curv_sig,
                      resid_rms_px=resid, monotonic=monotonic, breaks=breaks,
                      periods=periods, turns=turns,
                      extremum_inside=extremum_inside,
                      coverage=tr.coverage)


@dataclass
class LineShape:
    """One 1-D trace — the qubit-spectroscopy question."""
    pos_px: float | None
    depth_z: float
    width_px: float
    truncated: bool
    near_edge: bool
    secondaries: list
    n_significant: int
    flat_top: bool


def shape_line(cube: CubeRead, *, sign: int = -1) -> LineShape:
    col = cube.z[:, 0]
    j, d, w, others, trunc = slice_features(col, sign=sign)
    bar = z_bar(col.size)
    # A second LINE is a rival to the first, not merely a bump that clears the
    # bar: on 400-point traces the bar alone called 126 of 217 real runs
    # multi-feature, against a handful a person sees. Requiring a substantial
    # fraction of the primary's depth is what separates a second transition
    # from the structure every real trace has.
    strong = [(k, dz) for k, dz in others if dz >= max(bar, 0.5 * d)]
    # a flat-topped line is wide AND has a plateau rather than a single apex
    resid = -(col - baseline(col)) * sign
    lo = max(0, int(j - w))
    hi = min(resid.size, int(j + w) + 1)
    seg = resid[lo:hi]
    flat_top = bool(seg.size >= 5 and
                    np.sum(seg <= 0.85 * float(resid[j])) >= 0.5 * seg.size)
    return LineShape(
        pos_px=float(j) if d >= bar else None, depth_z=d, width_px=w,
        truncated=trunc,
        near_edge=bool(j <= EDGE_PX or j >= col.size - 1 - EDGE_PX),
        secondaries=strong, n_significant=(1 if d >= bar else 0) + len(strong),
        flat_top=flat_top)
