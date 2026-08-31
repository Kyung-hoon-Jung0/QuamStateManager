"""Future-blind path replay: walk an archived session as if it were live.

The benchmark this exists for (docs/129): a calibration chain is SEQUENTIAL —
every run's parameters come from the state the previous run wrote — so a
plausibly-wrong fit adopted at step 1 poisons everything after it. Diagnosing
that after the fact is nearly worthless; the loop has to catch it AT the run.

This module replays one archived session run by run:

    session -> reveal(k) -> measure raw map -> classify case -> decide
            -> (adopt | reject+retune | reject+stop | reconfirm) -> score

with two properties that make the score mean something:

**Future-blindness is structural, not promised.** ``Session.reveal(k)`` hands
back runs ``0..k`` and nothing else; reaching past ``k`` raises
``FutureBlindError``. The classifier's signature cannot even name a later run.
This matters because every convenient shortcut here is a form of cheating: the
operator's next action, the value the chip eventually settled on, and the
annotations written with hindsight are all forbidden inputs.

**The answer key is not the operator.** Scoring compares against a hand-built
golden path (``tests/golden/calib_paths/``) whose author had full hindsight and
was free to say the operator was wrong. Agreeing with a human who burned three
runs is not success; reaching the same conclusion sooner is.

The case decision is currently made by a deterministic reader of the raw map
(``measure`` + ``classify``) — geometry the same way a person reads the figure,
not a fit-flag echo. That reader occupies the seat a vision judge will take:
``classify`` returns a case id and the knowledge pack turns that id into
bounded knob moves, so swapping the reader never lets a model emit a number
(docs/129 architecture). A reader that cannot tell ABSTAINS, and an abstention
never adopts.
"""
from __future__ import annotations

import copy
import json
import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import numpy as np

logger = logging.getLogger(__name__)

# --- reader thresholds ------------------------------------------------------
# A dip must clear the point-noise floor by this much to be "seen" at all.
# 3.0 is the same order as the gates' presence probe and was checked against
# the pilot corpus: rows below it are visibly speckle in the exemplars.
Z_TRACE = 3.0


def _z_bar(n_freq: int) -> float:
    """The per-row bar, corrected for how many places the dip could be.

    A row is a MAXIMUM over ``n_freq`` samples, so pure noise reaches roughly
    ``sqrt(2 ln n)`` on its own — about 2.9 for a 64-point row, which sits
    right on a flat 3.0 bar. Left uncorrected, a noise-only map produced
    enough "traceable" rows to be read as a resolution problem, which invites
    a knob move on a window that holds nothing at all.
    """
    if n_freq < 4:
        return Z_TRACE
    return max(Z_TRACE, math.sqrt(2.0 * math.log(float(n_freq))) + 1.0)
# Two branch positions closer than this fraction of a linewidth are one line.
SEP_LINEWIDTH_FRAC = 0.5
# A transition spanning more than this fraction of the power axis is a drift
# (C4a) rather than a snap (C1).
SHARP_TRANSITION_FRAC = 0.20
# An optimum sitting within this fraction of the power window's bottom is
# floor-pinned (F1) — a boundary artifact of the picker, not physics.
FLOOR_PIN_FRAC = 0.12
# Dip within this many pixels of a frequency edge is clipped (N4).
EDGE_PX = 3
# Fewer than this many pixels across the dip and the window cannot resolve a
# branch step (N6).
MIN_LINEWIDTH_PX = 2.0
# Median depth below this reads as weak contrast (N7).
WEAK_DEPTH_Z = 6.0


class FutureBlindError(RuntimeError):
    """Raised when a replay tries to look at a run it has not reached."""


# ---------------------------------------------------------------------------
# The session
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RunView:
    """Everything a future-blind reader may see about ONE archived run."""
    run_id: str
    run_no: int
    date: str
    folder: Path
    params: dict
    fit_results: dict
    outcomes: dict
    patches: list
    snapshot: dict = field(default_factory=dict, repr=False)

    def fit(self, qubit: str) -> dict:
        e = self.fit_results.get(qubit)
        return e if isinstance(e, dict) else {}

    def outcome(self, qubit: str) -> str | None:
        return self.outcomes.get(qubit)


def load_run(folder: str | Path) -> RunView | None:
    """Read one archived run folder into a view (no raw cube — that is read
    lazily by ``measure``, which is the expensive part)."""
    folder = Path(folder)
    try:
        node = json.loads((folder / "node.json").read_text(encoding="utf-8"))
        data = json.loads((folder / "data.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    try:
        snap = json.loads((folder / "quam_state" / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        snap = {}
    head = folder.name.split("_")[0]
    try:
        run_no = int(head.lstrip("#"))
    except ValueError:
        run_no = -1
    return RunView(
        run_id=folder.name, run_no=run_no, date=folder.parent.name, folder=folder,
        params=((node.get("data") or {}).get("parameters") or {}).get("model") or {},
        fit_results=data.get("fit_results") or {},
        outcomes=(node.get("data") or {}).get("outcomes") or {},
        patches=node.get("patches") or [],
        snapshot=snap,
    )


class Session:
    """A chronological run list with a hard future-blind guard."""

    def __init__(self, session_id: str, runs: list[RunView]):
        self.session_id = session_id
        self._runs = sorted(runs, key=lambda r: (r.date, r.run_no))

    def __len__(self) -> int:
        return len(self._runs)

    @property
    def run_ids(self) -> list[str]:
        return [r.run_id for r in self._runs]

    def runs_for(self, qubit: str) -> list[int]:
        """Indices of the runs that measured *qubit* (an index list is not a
        peek — which runs exist is the session's shape, not their content)."""
        return [i for i, r in enumerate(self._runs) if qubit in r.outcomes]

    def reveal(self, k: int) -> list[RunView]:
        """Runs 0..k inclusive. Anything beyond k is not merely withheld —
        asking for it is an error, so a replay cannot cheat by accident."""
        if k < 0 or k >= len(self._runs):
            raise FutureBlindError(
                f"run index {k} is outside the revealed range "
                f"[0, {len(self._runs) - 1}]")
        return list(self._runs[: k + 1])

    def at(self, k: int) -> RunView:
        return self.reveal(k)[-1]


# ---------------------------------------------------------------------------
# Reading the raw map — the geometry a person sees in the figure
# ---------------------------------------------------------------------------

@dataclass
class Geometry:
    n_freq: int
    n_power: int
    freq: np.ndarray = field(repr=False)
    power: np.ndarray = field(repr=False)
    dip_idx: np.ndarray = field(repr=False)      # nan where untraceable
    depth_z: np.ndarray = field(repr=False)
    linewidth_px: float
    n_traceable: int
    hot_pos: float | None                        # pixel index, high power
    cold_pos: float | None                       # pixel index, low power
    separation_px: float | None
    transition_frac: float | None                # width of the crossover
    coexist_rows: int
    branch_hops: int
    hot_scatter_px: float
    saturated_rows: int
    edge_clipped: bool
    gradient_edge: str | None                    # 'low' | 'high' | None
    multi_feature: bool
    bottom_speckle_frac: float
    background_slope: float

    def freq_at(self, px: float | None) -> float | None:
        if px is None or not np.isfinite(px):
            return None
        i = int(round(px))
        if i < 0 or i >= self.freq.size:
            return None
        return float(self.freq[i])

    def linewidth_hz(self) -> float | None:
        if self.freq.size < 2:
            return None
        step = float(abs(self.freq[1] - self.freq[0]))
        return self.linewidth_px * step or None


def _baseline(y: np.ndarray, deg: int = 3, rounds: int = 2) -> np.ndarray:
    """Smooth frequency background under a trace, fitted with the dip excluded.

    These maps carry a strong, smooth transmission slope across the frequency
    axis, and every naive statistic taken against the raw trace inherits it:
    a "half-depth width" measured from the global median counted a quarter of
    the window as dip, which merged the two branches into one line and turned
    real punch-outs into "stationary" readings. Sigma-clipped polynomial
    baselines survive a dip that is genuinely broad, which a median filter
    wide enough to be smooth does not.
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
        keep = resid > -2.0 * scale          # drop the dip, keep the rest
    return fit


def _row_features(col: np.ndarray) -> tuple[int, float, float, list[tuple[int, float]], bool]:
    """(dip index, depth in noise units, CONTIGUOUS half-depth width px,
    secondary minima as (index, depth)).

    Everything is measured on the background-subtracted residual, and the
    width grows outward from the minimum rather than counting every sample
    under a global threshold — a dip is a connected feature, not a population.
    """
    resid = col - _baseline(col)
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

    # secondary minima: mask the primary and its shoulders, then look again
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
        others.append((k, d2))
        masked[max(0, k - pad): min(resid.size, k + pad + 1)] = np.inf
    truncated = (lo == 0) or (hi == resid.size - 1)
    return j, depth, width, others, truncated


def measure(folder: str | Path, qubit: str) -> Geometry | None:
    """Measure the raw punch-out map for one qubit. Returns None when the
    cube is unreadable or the qubit is absent — never a fabricated shape."""
    from quam_state_manager.core.ndview import _h5_lock_for, _open_reader

    folder = Path(folder)
    raw = folder / "ds_raw.h5"
    if not raw.exists():
        return None
    try:
        with _h5_lock_for(str(raw)), _open_reader(raw) as f:
            coord = f.read_coord("qubit")
            if coord is None:
                return None
            names = [n.decode() if isinstance(n, bytes) else str(n)
                     for n in np.asarray(coord).tolist()]
            if qubit not in names:
                return None
            i = names.index(qubit)
            var = f.get("IQ_abs")
            if var is None:
                return None
            cube = np.asarray(f.read(var), dtype=float)
            if cube.ndim != 3:
                return None
            z = cube[i]
            pw = f.get("power")
            power = np.asarray(f.read(pw), dtype=float) if pw is not None else None
            ff = f.get("full_freq")
            if ff is not None:
                fa = np.asarray(f.read(ff), dtype=float)
                freq = fa[i] if fa.ndim == 2 else fa
            else:
                det = f.get("detuning")
                freq = np.asarray(f.read(det), dtype=float) if det is not None else None
    except (OSError, ValueError, KeyError, TypeError) as exc:
        logger.debug("pathreplay: %s unreadable (%s)", raw, exc)
        return None
    if power is None or freq is None:
        return None
    if z.shape != (freq.size, power.size):
        if z.shape == (power.size, freq.size):
            z = z.T
        else:
            return None

    order = np.argsort(power)                    # ascending power
    power, z = power[order], z[:, order]
    n_freq, n_power = z.shape

    z_bar = _z_bar(n_freq)
    dip_idx = np.full(n_power, np.nan)
    depth_z = np.zeros(n_power)
    widths: list[float] = []
    truncs: list[bool] = []
    seconds: list[list[tuple[int, float]]] = []
    row_med = np.median(z, axis=0)
    for p in range(n_power):
        j, depth, width, others, trunc = _row_features(z[:, p])
        truncs.append(trunc)
        depth_z[p] = depth
        widths.append(width)
        seconds.append(others)
        if depth >= z_bar:
            dip_idx[p] = j

    ok = ~np.isnan(dip_idx)
    n_traceable = int(ok.sum())
    linewidth_px = float(np.median([w for w, t in zip(widths, ok) if t]) if n_traceable
                         else np.median(widths))

    # Whole-row saturation is an outlier against the power TREND, not against
    # the global median: these maps brighten monotonically with power, so a
    # global comparison flags the ends of every healthy ramp (it flagged 78
    # rows across the pilot corpus before this was detrended).
    lev = row_med - _baseline(row_med, deg=3, rounds=0)
    lev_scale = float(np.median(np.abs(lev))) * 1.4826 + 1e-30
    saturated_rows = int(np.sum(np.abs(lev) > 8.0 * lev_scale))

    # Branch positions by CLUSTERING, not by the extreme rows: the lowest
    # power rows are where the signal dies, so their argmin is often noise —
    # taking them as "the cold branch" put one pilot map's dressed position 8
    # pixels away from the dip a reader can plainly see.
    hot_pos = cold_pos = separation_px = transition_frac = None
    hot_scatter_px = 0.0
    # An argmin pinned to the frequency edge is an artifact or a clipped
    # feature, never a branch position we could verify — and letting those
    # rows vote put one map's "bare branch" at pixel 1.6 of 50, taken from
    # three saturated top rows, when the real branch pair sat 8 pixels apart
    # in mid-window. They are excluded from the branch estimate (they still
    # count as traceable, and still drive the edge-clipped verdict).
    inner = ok & (dip_idx > EDGE_PX) & (dip_idx < (n_freq - 1 - EDGE_PX))
    # A FIXED membership bar, deliberately: the bare branch is physically far
    # deeper than the dressed one (that is what punch-out means), so an
    # adaptive bar scaled to the map's strongest rows discards the dressed
    # branch entirely — measured, it erased all 15 dressed rows of a textbook
    # punch-out and reported a single stationary line. Depth asymmetry between
    # the branches is physics, not a quality difference.
    strong = np.where(inner & (depth_z >= z_bar))[0]
    if strong.size < 4:
        strong = np.where(inner)[0]
    if strong.size >= 4:
        pos = dip_idx[strong]
        # Seed the two clusters from the two POWER regimes, not from the
        # position extremes: the branches are defined by power, the clusters
        # are usually unbalanced (a punch-out holds one branch far longer than
        # the other), and percentile-of-position seeds collapse onto the
        # bigger one — which read a textbook punch-out as a single line.
        third = max(1, strong.size // 3)
        by_power = np.argsort(power[strong])
        c1 = float(np.median(pos[by_power[-third:]]))     # hot third
        c2 = float(np.median(pos[by_power[:third]]))      # cold third
        if abs(c1 - c2) < 1e-9:
            c1, c2 = float(np.min(pos)), float(np.max(pos))
        for _ in range(25):
            g1 = pos[np.abs(pos - c1) <= np.abs(pos - c2)]
            g2 = pos[np.abs(pos - c1) > np.abs(pos - c2)]
            n1 = float(np.mean(g1)) if g1.size else c1
            n2 = float(np.mean(g2)) if g2.size else c2
            if abs(n1 - c1) < 1e-6 and abs(n2 - c2) < 1e-6:
                break
            c1, c2 = n1, n2
        # drop members far from their own centre, then re-centre: a branch is
        # a tight cluster, and a straggler must not drag its centre off the dip
        near1 = np.abs(pos - c1) <= np.abs(pos - c2)
        tol_out = max(3.0, 3.0 * linewidth_px)
        g1 = pos[near1][np.abs(pos[near1] - c1) <= tol_out] if near1.any() else pos[:0]
        g2 = pos[~near1][np.abs(pos[~near1] - c2) <= tol_out] if (~near1).any() else pos[:0]
        if g1.size:
            c1 = float(np.median(g1))
        if g2.size:
            c2 = float(np.median(g2))
        near1 = np.abs(pos - c1) <= np.abs(pos - c2)
        p1 = float(np.mean(power[strong][near1])) if near1.any() else -np.inf
        p2 = float(np.mean(power[strong][~near1])) if (~near1).any() else -np.inf
        hot_pos, cold_pos = (c1, c2) if p1 >= p2 else (c2, c1)
        hot_members = pos[near1] if p1 >= p2 else pos[~near1]
        if hot_members.size:
            hot_scatter_px = float(np.median(np.abs(hot_members - hot_pos)) * 1.4826)
        separation_px = abs(hot_pos - cold_pos)
        if separation_px > max(1.0, SEP_LINEWIDTH_FRAC * linewidth_px):
            lo_b = min(hot_pos, cold_pos) + 0.25 * separation_px
            hi_b = max(hot_pos, cold_pos) - 0.25 * separation_px
            band = [p for p in np.where(ok)[0] if lo_b <= dip_idx[p] <= hi_b]
            transition_frac = len(band) / float(n_power) if n_power else None

    # Bistable coexistence: a row carrying TWO significant minima, one at each
    # branch — measured from the residual's secondary minima, so a sloped
    # background can no longer manufacture a second "dip".
    coexist_rows = 0
    branch_hops = 0
    if hot_pos is not None and separation_px and \
            separation_px > max(2.0, linewidth_px):
        # How often the tracked dip changes branch as power rises. A clean
        # punch-out changes once; a bistable crossover hops repeatedly. This
        # is the honest C1-vs-C4b discriminator — a coexistence COUNT alone
        # called a textbook map bistable off six rows out of fifty.
        # Only the rows that DEFINED the branches get a vote on hopping —
        # counting edge-pinned and marginal rows lets a speckled bottom half
        # decide the case (it read a clean punch-out as bistable).
        hop_rows = strong if strong.size >= 4 else np.where(ok)[0]
        hop_rows = hop_rows[np.argsort(power[hop_rows])]
        labels = [abs(dip_idx[p] - hot_pos) <= abs(dip_idx[p] - cold_pos)
                  for p in hop_rows]
        # Smooth single-row excursions away first: on a map whose bottom half
        # is speckle, lone noise rows flip the label and manufacture hops (a
        # clean punch-out counted 12). A bistable crossover holds each branch
        # for a stretch — that is what makes it bistable rather than noisy.
        sm = list(labels)
        for i in range(1, len(sm) - 1):
            if labels[i] != labels[i - 1] and labels[i] != labels[i + 1]:
                sm[i] = labels[i - 1]
        branch_hops = int(sum(1 for a, b in zip(sm, sm[1:]) if a != b))
        # the two acceptance windows must not overlap, or every row "carries
        # both branches" by construction
        tol = min(max(2.0, 0.75 * linewidth_px), 0.4 * separation_px)
        for p in range(n_power):
            if not ok[p]:
                continue
            hits = [dip_idx[p]] + [float(k) for k, _d in seconds[p]]
            near_hot = any(abs(h - hot_pos) <= tol for h in hits)
            near_cold = any(abs(h - cold_pos) <= tol for h in hits)
            if near_hot and near_cold:
                coexist_rows += 1

    # Clipped means the dip's own SHAPE runs off the window — a feature that
    # merely sits near one side is not clipped, and treating it as such sent
    # the loop chasing a re-centre on maps that were perfectly readable.
    edge_clipped = False
    if n_traceable:
        clipped = [bool(truncs[p]) and
                   (dip_idx[p] <= EDGE_PX or dip_idx[p] >= n_freq - 1 - EDGE_PX)
                   for p in np.where(ok)[0]]
        edge_clipped = bool(np.mean(clipped) > 0.6)

    # a feature just outside the span shows only as a monotone shoulder
    gradient_edge = None
    if n_traceable == 0:
        prof = np.median(z, axis=1)
        if prof.size >= 8:
            half = prof.size // 2
            d = float(np.mean(prof[:half]) - np.mean(prof[half:]))
            scale = float(np.median(np.abs(np.diff(prof)))) * 1.4826 + 1e-30
            if abs(d) > 6.0 * scale:
                gradient_edge = "low" if d < 0 else "high"

    # A genuine neighbouring resonance shows as a SECOND significant minimum,
    # separated by more than a linewidth, in most traceable rows — and it must
    # not simply be the other branch of this qubit's own punch-out.
    def _second_positions(p: int) -> list[float]:
        out = []
        for k, d2 in seconds[p]:
            if d2 < max(5.0, 0.4 * depth_z[p]):
                continue                       # a shallow wobble is not a line
            if abs(k - dip_idx[p]) <= max(2.0, linewidth_px):
                continue
            if hot_pos is not None and cold_pos is not None and (
                    abs(k - hot_pos) <= max(2.0, linewidth_px)
                    or abs(k - cold_pos) <= max(2.0, linewidth_px)):
                continue                       # that is our own other branch
            out.append(float(k))
        return out

    # A real neighbouring resonance stands at the SAME frequency in row after
    # row; noise minima scatter. Requiring positional agreement is what
    # separates them — without it every sloped, speckled map read as
    # multi-feature (43 of 172 pilot targets, against 10 by a human reader).
    sec_all = [k for p in np.where(ok)[0] for k in _second_positions(p)]
    multi_feature = False
    if n_traceable >= 4 and sec_all:
        arr = np.asarray(sec_all)
        tolc = max(2.0, linewidth_px)
        for centre in np.unique(np.round(arr)):
            share = float(np.sum(np.abs(arr - centre) <= tolc))
            if share / max(1, n_traceable) <= 0.5:
                continue
            # A neighbour line in the window is only the STORY when the
            # tracker actually visits it — otherwise the map is a clean read
            # that happens to have company, and calling it multi-feature
            # buries the real case (it turned stationary-line maps into C5).
            if any(abs(dip_idx[p] - centre) <= tolc for p in np.where(ok)[0]):
                multi_feature = True
                break

    bottom = max(1, int(round(0.3 * n_power)))
    bottom_speckle_frac = float(np.mean(~ok[:bottom]))

    prof = np.median(z, axis=1)
    bslope = 0.0
    if prof.size >= 4:
        scale = float(np.median(np.abs(np.diff(prof)))) * 1.4826 + 1e-30
        bslope = float(abs(prof[-1] - prof[0]) / (scale * prof.size))

    return Geometry(
        n_freq=n_freq, n_power=n_power, freq=freq, power=power,
        dip_idx=dip_idx, depth_z=depth_z, linewidth_px=linewidth_px,
        n_traceable=n_traceable, hot_pos=hot_pos, cold_pos=cold_pos,
        separation_px=separation_px, transition_frac=transition_frac,
        coexist_rows=coexist_rows, branch_hops=branch_hops,
        hot_scatter_px=hot_scatter_px,
        saturated_rows=saturated_rows,
        edge_clipped=edge_clipped, gradient_edge=gradient_edge,
        multi_feature=multi_feature, bottom_speckle_frac=bottom_speckle_frac,
        background_slope=bslope)


# ---------------------------------------------------------------------------
# Classification — geometry first, record second
# ---------------------------------------------------------------------------

@dataclass
class CaseVerdict:
    case: str | None                     # None = abstain (never adopts)
    flags: list[str] = field(default_factory=list)
    confidence: str = "med"
    reasons: list[str] = field(default_factory=list)
    corrected: dict = field(default_factory=dict)   # geometry-derived values


def _state_get(state: dict, dotted: str):
    node: Any = state
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def pre_update_state(view: RunView, dotted: str):
    """The value a path held BEFORE this run wrote (patches-first, docs/48):
    a run that patched its own snapshot leaves the post-update value on disk,
    and comparing a claim against that makes every step look like zero."""
    for p in view.patches:
        path = (p.get("path") or "").lstrip("/")
        if path.startswith("quam/"):
            path = path[len("quam/"):]
        if path.replace("/", ".") == dotted:
            return p.get("old")
    return _state_get(view.snapshot, dotted)


def classify(view: RunView, qubit: str, geom: Geometry | None, *,
             sm_state: dict | None = None,
             prior: list[tuple[RunView, "CaseVerdict"]] | None = None) -> CaseVerdict:
    """Read one run the way a person reads its figure, using ONLY this run's
    data, this run's state, and verdicts already reached on earlier runs."""
    fit = view.fit(qubit)
    v = CaseVerdict(case=None)

    if geom is None:
        v.reasons.append("raw map unreadable — nothing to read")
        v.confidence = "low"
        return v

    # ---- the record's own claims (evidence, never the verdict) -------------
    rec_dressed = fit.get("resonator_frequency")
    rec_bare = fit.get("bare_resonator_frequency")
    rec_power = fit.get("optimal_power")
    punchout = fit.get("punchout")
    success = fit.get("success")
    pmin = view.params.get("min_power_dbm")
    pmax = view.params.get("max_power_dbm")

    # ---- flags ------------------------------------------------------------
    # F1: optimum at (or outside) the swept floor
    if isinstance(rec_power, (int, float)) and isinstance(pmin, (int, float)) \
            and isinstance(pmax, (int, float)) and pmax > pmin:
        if rec_power < pmin or rec_power > pmax:
            v.flags.append("F1")
            v.reasons.append("the reported optimum lies outside the swept "
                             "power window — the field is invalid, not merely "
                             "unlucky")
        elif (rec_power - pmin) / (pmax - pmin) <= FLOOR_PIN_FRAC:
            v.flags.append("F1")
            v.reasons.append("the reported optimum sits at the bottom edge of "
                             "the swept window, where the dip is fading")
        else:
            # Same principle one step further: a power is only a measurement
            # if the map HAS a dip at that power. An optimum landing in the
            # speckle band is not floor-pinned by the letter, and must be
            # refused by the same rule.
            row = int(np.argmin(np.abs(geom.power - rec_power)))
            if not np.isfinite(geom.dip_idx[row]):
                v.flags.append("F1")
                v.reasons.append("the reported optimum falls on a power row "
                                 "where no dip is traceable — a value read "
                                 "out of the noise band")

    # F2: a "success" that admits no punch-out and still writes state
    wrote_fallback = any(
        ("full_scale_power_dbm" in (p.get("path") or "")
         or "/amplitude" in (p.get("path") or ""))
        for p in view.patches)
    if success is True and punchout is False:
        v.flags.append("F2")
        v.reasons.append("the record claims success while admitting no "
                         "punch-out" + (" and wrote fallback values into state"
                                        if wrote_fallback else ""))

    # F3: the record's branch labels against the map's own geometry.
    # The power-INDEPENDENT hot-row position is bare by definition.
    hot_hz = geom.freq_at(geom.hot_pos)
    cold_hz = geom.freq_at(geom.cold_pos)
    lw_hz = geom.linewidth_hz() or 0.0
    if (hot_hz is not None and cold_hz is not None and lw_hz
            and isinstance(rec_dressed, (int, float))
            and isinstance(rec_bare, (int, float))
            and geom.separation_px and geom.separation_px > geom.linewidth_px):
        d_ok = abs(rec_dressed - cold_hz) + abs(rec_bare - hot_hz)
        d_swap = abs(rec_dressed - hot_hz) + abs(rec_bare - cold_hz)
        if d_swap + lw_hz < d_ok:
            v.flags.append("F3")
            v.reasons.append("the record's dressed/bare labels contradict the "
                             "map: its 'bare' sits on the low-power branch "
                             "while the power-independent hot-row position is "
                             "bare by definition")
            v.corrected = {"resonator_frequency": cold_hz,
                           "bare_resonator_frequency": hot_hz}

    # ---- map case ---------------------------------------------------------
    # A handful of rows crossing a 3-sigma bar is what noise DOES; requiring a
    # share of the window before believing in a feature is what separates an
    # empty map from a real one (without it a pure-noise map reported a
    # resolution problem, which invites the wrong knob).
    min_rows = max(3, int(round(0.10 * geom.n_power)))
    if geom.n_traceable < min_rows:
        if geom.gradient_edge:
            v.case, v.confidence = "N5", "med"
            v.reasons.append("no dip anywhere, but the background rises "
                             "monotonically toward one frequency edge — the "
                             "shoulder of a feature outside the span")
        else:
            v.case, v.confidence = "C6", "high"
            v.reasons.append("no feature clears the noise at any power")
    elif geom.edge_clipped:
        v.case, v.confidence = "N4", "high"
        v.reasons.append("the feature hugs a frequency-window edge, so "
                         "neither branch position is verifiable")
    elif (geom.multi_feature and not (geom.separation_px
                                      and geom.separation_px
                                      > max(1.0, SEP_LINEWIDTH_FRAC * geom.linewidth_px))):
        # Company in the window is only the CASE when nothing else resolves.
        # Where a branch pair is present, the punch-out is the story and the
        # neighbour is a flag — reading it the other way buried real reads.
        v.case, v.confidence = "C5", "med"
        v.reasons.append("more than one resonance line sits in the window and "
                         "no branch pair resolves")
    elif (geom.bottom_speckle_frac > 0.5 and geom.n_traceable >= 3
          and not (geom.separation_px
                   and geom.separation_px > max(1.0, geom.linewidth_px))):
        # Speckle is the CASE only when it stops the read. A punch-out plainly
        # visible above a noisy bottom is still a punch-out (the manual says
        # so in as many words); calling it snr-floor buried 30-odd readable
        # maps in the pilot corpus.
        v.case, v.confidence = "N1", "high"
        v.reasons.append("the lower part of the power window is speckle — the "
                         "dip is untraceable there while the line is healthy "
                         "above it")
    elif geom.linewidth_px <= MIN_LINEWIDTH_PX:
        v.case, v.confidence = "N6", "med"
        v.reasons.append("the dip spans too few pixels for this span to "
                         "resolve a branch step — a stationary reading here "
                         "would be a resolution artifact")
    elif geom.separation_px is None:
        v.reasons.append("too few traceable rows to judge branch structure")
        v.confidence = "low"
    elif geom.separation_px <= max(1.0, SEP_LINEWIDTH_FRAC * geom.linewidth_px):
        # one stationary line — dressed or bare is a STATE question
        line_hz = geom.freq_at(np.nanmedian(geom.dip_idx))
        st_dressed = (sm_state or {}).get("resonator_frequency")
        if st_dressed is None:
            st_dressed = pre_update_state(view, f"qubits.{qubit}.resonator.f_01")
        st_bare = (sm_state or {}).get("bare_resonator_frequency")
        if st_bare is None:
            st_bare = pre_update_state(view, f"qubits.{qubit}.resonator.frequency_bare")
        # Which branch this line IS cannot be read off one figure — it is a
        # question about the chip's known frequencies, and the answer decides
        # whether to raise the ceiling (C2) or lower the floor (C3). Our OWN
        # adopted values are preferred over the snapshot's, because a chip's
        # stored pair is routinely stale mid-bring-up: on one pilot map the
        # recorded bare sat 15 MHz from the line the map actually carried.
        near = 5.0 * (lw_hz or 0.0)
        d_dressed = (abs(line_hz - st_dressed)
                     if line_hz is not None and isinstance(st_dressed, (int, float))
                     else None)
        d_bare = (abs(line_hz - st_bare)
                  if line_hz is not None and isinstance(st_bare, (int, float))
                  else None)
        cand = [(d, name) for d, name in ((d_dressed, "dressed"), (d_bare, "bare"))
                if d is not None and (not near or d <= near)]
        if cand:
            cand.sort()
            if cand[0][1] == "dressed":
                v.case, v.confidence = "C2", "med"
                v.reasons.append("one stationary line, sitting where the "
                                 "dressed resonance is already known to be — "
                                 "the ceiling never reached the punch-out")
            else:
                v.case, v.confidence = "C3", "med"
                v.reasons.append("one stationary line at the known bare "
                                 "position — no dressed branch in this window")
        else:
            v.reasons.append("one stationary line, but no trustworthy stored "
                             "frequency sits near it — which branch this is "
                             "cannot be told from this run alone")
            v.confidence = "low"
    elif geom.separation_px < geom.linewidth_px:
        v.case, v.confidence = "N3", "med"
        v.reasons.append("the branch step is smaller than the dip's own "
                         "linewidth — the shift is real but unresolved")
    else:
        # two branches with a real step
        if geom.branch_hops >= 4:
            v.case, v.confidence = "C4b", "med"
            v.reasons.append("both branches appear in the same rows over a "
                             "band of powers — a bistable crossover")
        elif (geom.transition_frac or 0) > SHARP_TRANSITION_FRAC:
            v.case, v.confidence = "C4a", "med"
            v.reasons.append("the dip is pulled smoothly between the branches "
                             "over a broad band rather than snapping")
        else:
            v.case, v.confidence = "C1", "high"
            v.reasons.append("two branches with a sharp transition inside the "
                             "window")

    # F4 is only meaningful once the map case is settled: on an unresolved
    # map the "visible dip" it compares against is not established, and it
    # fired on 18 pilot targets whose branch estimate was itself the doubt.
    if (v.case in ("C1", "C4a") and v.confidence == "high"
            and "F3" not in v.flags and cold_hz is not None and lw_hz
            and isinstance(rec_dressed, (int, float))
            and abs(rec_dressed - cold_hz) > 3.0 * lw_hz):
        v.flags.append("F4")
        v.reasons.append("the claimed frequency sits several linewidths off "
                         "the dip the map actually carries")

    # the high-power end swinging over many linewidths is not a branch
    if (v.case in ("C1", "C4a") and geom.hot_scatter_px
            and geom.hot_scatter_px > 2.0 * geom.linewidth_px):
        v.flags.append("N8")
        v.reasons.append("the highest-power rows swing over many linewidths — "
                         "the bare position read from them is not reliable")

    # a speckled bottom that did not stop the read is still worth saying
    if v.case and v.case != "N1" and geom.bottom_speckle_frac > 0.5:
        v.flags.append("N1")
        v.reasons.append("the lowest powers are speckle — a value taken from "
                         "those rows would not be a measurement")

    # N7 rides on top of a weak read
    if v.case in ("C3", "N1", "C5", None) and geom.n_traceable:
        med_depth = float(np.median(geom.depth_z[~np.isnan(geom.dip_idx)]))
        if med_depth < WEAK_DEPTH_Z and geom.background_slope > 0.5:
            v.case = "N7"
            v.reasons.append("a shallow minimum riding a strongly sloped "
                             "background — the dip position itself is "
                             "ill-defined")

    # N8: saturation streaks at the top of the power axis
    if geom.saturated_rows and geom.n_power and \
            geom.saturated_rows / geom.n_power > 0.05:
        v.flags.append("N8")
        v.reasons.append("whole-row streaks at the highest powers — a "
                         "transition claimed from those rows is not physics")

    # F5: the record's verdict against what the map supports
    fittable = v.case in ("C1", "C4a", "C4b", "N3")
    if success is False and fittable:
        v.flags.append("F5")
        v.reasons.append("the node refused a map that carries a readable "
                         "punch-out")
    elif success is True and v.case in ("C6", "N5"):
        v.flags.append("F5")
        v.reasons.append("the node accepted a map that supports nothing")

    # F6: this qubit flickering across near-identical earlier runs
    for pview, pverdict in reversed(prior or []):
        if qubit not in pview.outcomes:
            continue
        same_window = all(
            abs(float(pview.params.get(k, 0) or 0) - float(view.params.get(k, 0) or 0))
            <= 1e-9 for k in ("min_power_dbm", "max_power_dbm",
                              "frequency_span_in_mhz"))
        if same_window and pverdict.case and v.case and pverdict.case != v.case \
                and {pverdict.case, v.case} & {"C1", "C4a", "C4b"} \
                and {pverdict.case, v.case} & {"C3", "C6", "N1"}:
            v.flags.append("F6")
            v.reasons.append(f"the same window read {pverdict.case} on "
                             f"{pview.run_id} and {v.case} here — the feature "
                             f"is flickering, not the settings")
        break

    return v


# ---------------------------------------------------------------------------
# Deciding — the knowledge pack turns a case into bounded knob moves
# ---------------------------------------------------------------------------

@dataclass
class Decision:
    action: str                       # adopt | retune | stop | reconfirm | abstain
    adopt: dict = field(default_factory=dict)
    refused: list[str] = field(default_factory=list)
    reverts: list[str] = field(default_factory=list)
    next_params: dict = field(default_factory=dict)
    case: str | None = None
    flags: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


def _window(params: dict) -> tuple[float, float] | None:
    lo, hi = params.get("min_power_dbm"), params.get("max_power_dbm")
    if isinstance(lo, (int, float)) and isinstance(hi, (int, float)) and hi > lo:
        return float(lo), float(hi)
    return None


def _bounded_moves(case: str, params: dict) -> dict:
    """The prescriptions' knob arithmetic. Every move is a bounded fraction of
    the CURRENT window — never an absolute value, so the same rule serves any
    chip (the manual's chip-independence rule, made executable)."""
    out: dict[str, Any] = {}
    win = _window(params)
    span = params.get("frequency_span_in_mhz")
    step = params.get("frequency_step_in_mhz")
    shots = params.get("num_shots")

    if case == "C2" and win:                      # ceiling below punch-out
        lo, hi = win
        out["max_power_dbm"] = hi + 0.5 * (hi - lo)
    elif case == "C3" and win:                    # no dressed branch: go lower
        lo, hi = win
        out["min_power_dbm"] = lo - 0.5 * (hi - lo)
    elif case == "N1" and win:                    # speckle bottom: come up
        lo, hi = win
        out["min_power_dbm"] = lo + (hi - lo) / 3.0
        if isinstance(shots, (int, float)):
            out["num_shots"] = int(shots * 2)
    elif case in ("N5", "C6") and isinstance(span, (int, float)):
        out["frequency_span_in_mhz"] = span * 3.0
        if isinstance(step, (int, float)):
            out["frequency_step_in_mhz"] = step * 3.0
    elif case in ("N6", "C5", "N3") and isinstance(span, (int, float)):
        out["frequency_span_in_mhz"] = span / 3.0
        if isinstance(step, (int, float)):
            out["frequency_step_in_mhz"] = step / 3.0
    elif case == "N7" and win:                    # provisional P2
        lo, hi = win
        out["max_power_dbm"] = hi + 0.3 * (hi - lo)
        if isinstance(shots, (int, float)):
            out["num_shots"] = int(shots * 2)
    elif case == "C4b" and isinstance(shots, (int, float)):
        out["num_shots"] = int(shots * 2)
    elif case == "N2" and isinstance(span, (int, float)):
        out["frequency_span_in_mhz"] = span / 2.0
    return out


def decide(view: RunView, qubit: str, verdict: CaseVerdict, *,
           pack: dict | None = None) -> Decision:
    """Turn a case verdict into an action. The knowledge pack supplies the
    prescription TEXT (and so the audit trail); the numbers come from
    ``_bounded_moves`` — a case id can never carry a value."""
    fit = view.fit(qubit)
    d = Decision(action="abstain", case=verdict.case, flags=list(verdict.flags),
                 reasons=list(verdict.reasons))

    # Manual rule R-batch (expert Q6): a multiplexed run shares one power
    # budget across its members, so when a large share of them fail the run
    # did not calibrate anything — the survivors are not evidence either. One
    # pilot batch failed 7 of 8 and its one "success" was 23 MHz from the
    # value the chip actually held.
    if view.params.get("multiplexed") and len(view.outcomes) > 1:
        failed = sum(1 for o in view.outcomes.values() if o != "successful")
        if failed / float(len(view.outcomes)) >= (1.0 / 3.0):
            d.action = "retune"
            d.flags.append("R-batch")
            d.reasons.append(f"{failed} of {len(view.outcomes)} qubits in this "
                             f"multiplexed run failed — a shared power budget "
                             f"that missed most of its targets is not evidence "
                             f"for the rest; re-run this qubit alone")
            d.next_params = {"multiplexed": False}
            return d

    # F2 first: a poisoned write is undone whatever else we decide, because it
    # damages the NEXT run on this feedline, not this one (expert rule).
    if "F2" in verdict.flags:
        for p in view.patches:
            path = p.get("path") or ""
            if "full_scale_power_dbm" in path or "/amplitude" in path:
                d.reverts.append(path)

    if verdict.case is None:
        d.reasons.append("the reader could not tell — abstaining, which never "
                         "adopts")
        return d

    # N8 says the HIGH-POWER rows are unreliable. The only value read from
    # them is the bare frequency — which the expert rule does not require at
    # all — so it suppresses that field and lets the dressed value through.
    # Blocking the whole adopt on it threw away textbook punch-outs.
    drop_bare = "N8" in verdict.flags

    if verdict.case in ("C1", "C4a", "N3") or \
            (verdict.case == "C4b" and "F6" not in verdict.flags):
        dressed = fit.get("resonator_frequency")
        bare = fit.get("bare_resonator_frequency")
        if "F3" in verdict.flags and verdict.corrected:
            dressed = verdict.corrected.get("resonator_frequency", dressed)
            bare = verdict.corrected.get("bare_resonator_frequency", bare)
            d.reasons.append("adopting the geometry-corrected branch values, "
                             "not the record's labels")
        if "F4" in verdict.flags:
            d.action = "retune"
            d.next_params = _bounded_moves("N6", view.params)
            d.reasons.append("the claim sits off the visible dip — re-reading "
                             "rather than adopting")
            return d
        if isinstance(dressed, (int, float)):
            d.adopt["resonator_frequency"] = float(dressed)
        # N3: the bare value is not required and is NOT written (expert rule)
        if verdict.case != "N3" and isinstance(bare, (int, float)) and not drop_bare:
            d.adopt["bare_resonator_frequency"] = float(bare)
        elif drop_bare:
            d.reasons.append("the highest-power rows are unreliable, so the "
                             "bare value read from them is not recorded — the "
                             "dressed frequency does not depend on it")
        elif verdict.case == "N3":
            d.reasons.append("sub-linewidth shift: the dressed frequency is "
                             "adopted, the bare value deliberately not "
                             "recorded")
        power = fit.get("optimal_power")
        if "F1" in verdict.flags:
            d.refused.append("optimal_power")
            d.reasons.append("refusing the reported power: it is a boundary "
                             "artifact of the picker, not a measurement")
        elif isinstance(power, (int, float)):
            d.adopt["optimal_power"] = float(power)
        if "F6" in verdict.flags:
            d.action = "reconfirm"
            d.next_params = dict(view.params)
            span = view.params.get("frequency_span_in_mhz")
            if isinstance(span, (int, float)):
                # perturbed, not identical: an identical repeat is a coin flip
                d.next_params["frequency_span_in_mhz"] = span * 1.1
            d.reasons.append("re-running with perturbed settings before "
                             "trusting an intermittent feature")
            return d
        if d.adopt:
            d.action = "adopt"
            if "F1" in verdict.flags:
                # the frequency is good, the power is not: take one more run
                d.next_params = {}
                win = _window(view.params)
                if win:
                    lo, hi = win
                    d.next_params["min_power_dbm"] = lo + 0.25 * (hi - lo)
        else:
            d.action = "retune"
        return d

    if verdict.case == "N2":
        d.action = "retune"
        d.next_params = _bounded_moves("N2", view.params)
        d.reasons.append("the record locked onto a power-independent spike — "
                         "rejecting outright and excluding it from the window")
        return d

    if verdict.case == "C4b" and "F6" in verdict.flags:
        d.action = "reconfirm"
        d.next_params = _bounded_moves("C4b", view.params)
        return d

    if verdict.case in ("C2", "C3", "N1", "N4", "N5", "N6", "N7", "C5", "C6"):
        d.action = "retune"
        d.next_params = _bounded_moves(verdict.case, view.params)
        if verdict.case == "N4":
            d.reasons.append("re-centering the frequency window on the visible "
                             "feature — an identical retry cannot succeed")
        if not d.next_params and verdict.case == "N4":
            d.next_params = {"recenter_on_feature": True}
        return d

    return d


# ---------------------------------------------------------------------------
# The replay
# ---------------------------------------------------------------------------

@dataclass
class StepRecord:
    index: int
    run_id: str
    case: str | None
    flags: list[str]
    action: str
    adopted: dict
    refused: list[str]
    reverts: list[str]
    next_params: dict
    proposal_matched: str | None      # run_id that answered the proposal
    reasons: list[str]


@dataclass
class ReplayResult:
    session_id: str
    qubit: str
    steps: list[StepRecord]
    final_state: dict
    terminated_at: str | None
    runs_consumed: int
    unresolved: bool
    unscoreable_proposals: int
    # runs needed to reach the FIRST trustworthy value (the efficiency number)
    # versus the value still held at the end (the correctness number). They
    # differ whenever the chip moves mid-session, and collapsing them would
    # score a correct early calibration as a wrong answer.
    runs_to_first_value: int = 0
    first_value: dict = field(default_factory=dict)
    first_value_at: str | None = None
    revisions: list[dict] = field(default_factory=list)


def _params_match(proposal: dict, params: dict, rel: float = 0.15) -> bool:
    """Did a later archived run answer this proposal? Same DECISION, not the
    same number — an operator who typed 80 where we asked for 78 made the same
    move (the replay_score tolerance argument)."""
    keys = [k for k in proposal if k in params
            and isinstance(proposal[k], (int, float))
            and isinstance(params[k], (int, float))]
    if not keys:
        return False
    for k in keys:
        a, b = float(proposal[k]), float(params[k])
        if a == b:
            continue
        scale = max(abs(a), abs(b))
        if scale == 0 or abs(a - b) / scale > rel:
            return False
    return True


def replay(session: Session, qubit: str, *, pack: dict | None = None,
           stop_on_adopt: bool = True,
           measure_fn: Callable[..., Geometry | None] = measure) -> ReplayResult:
    """Walk the session for one qubit, future-blind.

    A value is adopted the moment the map earns it, and that is the
    efficiency answer. The walk then CONTINUES in watch mode, because the
    manual's R-bias rule says a chip that has moved must be re-measured
    before anything downstream runs: a later clean map whose dressed branch
    sits several linewidths from the held value is the chip moving, and the
    held value is revised. Stopping dead at the first adopt scored four
    correct early calibrations as wrong answers when the operating point
    drifted later in the same session.
    """
    steps: list[StepRecord] = []
    state: dict[str, Any] = {}
    prior: list[tuple[RunView, CaseVerdict]] = []
    terminated_at = None
    unscoreable = 0
    held: float | None = None
    first_value: dict = {}
    first_value_at: str | None = None
    runs_to_first = 0
    revisions: list[dict] = []
    idxs = session.runs_for(qubit)

    for pos, k in enumerate(idxs):
        view = session.at(k)                       # guarded: never past k
        geom = measure_fn(view.folder, qubit)
        verdict = classify(view, qubit, geom, sm_state=state, prior=prior)
        d = decide(view, qubit, verdict, pack=pack)
        prior.append((view, verdict))

        # did any LATER run answer the proposal? This is scoring, computed
        # after the decision was made and never fed back into it.
        matched = None
        if d.next_params:
            for k2 in idxs[pos + 1:]:
                later = session.at(k2)
                if _params_match(d.next_params, later.params):
                    matched = later.run_id
                    break
            if matched is None:
                unscoreable += 1

        for path in d.reverts:
            state.setdefault("_reverted", []).append(path)
        state.update(d.adopt)

        steps.append(StepRecord(
            index=pos, run_id=view.run_id, case=d.case, flags=d.flags,
            action=d.action, adopted=dict(d.adopt), refused=list(d.refused),
            reverts=list(d.reverts), next_params=dict(d.next_params),
            proposal_matched=matched, reasons=d.reasons[:4]))

        if d.action == "adopt" and not d.next_params:
            if terminated_at is None:
                terminated_at = view.run_id
                first_value = dict(d.adopt)
                first_value_at = view.run_id
                runs_to_first = len(steps)
            elif held is not None:
                got = d.adopt.get("resonator_frequency")
                lw = (geom.linewidth_hz() or 0.0) if geom else 0.0
                if isinstance(got, (int, float)) and lw and                         abs(got - held) > 3.0 * lw:
                    revisions.append({"run": view.run_id, "from": held,
                                      "to": got,
                                      "why": "a later clean map puts the "
                                             "dressed branch several "
                                             "linewidths away — the chip "
                                             "moved, so the held value is "
                                             "re-measured (rule R-bias)"})
                    terminated_at = view.run_id
            if isinstance(d.adopt.get("resonator_frequency"), (int, float)):
                held = float(d.adopt["resonator_frequency"])
            if stop_on_adopt and len(idxs) == pos + 1:
                break

    return ReplayResult(
        session_id=session.session_id, qubit=qubit, steps=steps,
        final_state={k: v for k, v in state.items() if not k.startswith("_")},
        terminated_at=terminated_at, runs_consumed=len(steps),
        unresolved=terminated_at is None,
        unscoreable_proposals=unscoreable,
        runs_to_first_value=runs_to_first, first_value=first_value,
        first_value_at=first_value_at, revisions=revisions)


# ---------------------------------------------------------------------------
# Scoring against the answer key
# ---------------------------------------------------------------------------

FREQ_TOL_HZ = 2e6          # a readout resonance is "the same value" within this
POWER_TOL_DB = 3.0


def score(result: ReplayResult, key: dict) -> dict:
    """Compare a replay against the hand-built answer key for that qubit.

    The key's author had hindsight and was free to call the operator wrong, so
    a disagreement here is a real disagreement — not a failure to imitate.
    """
    term = key.get("termination") or {}
    want_freq = term.get("final_resonator_frequency")
    want_power = term.get("final_optimal_power")
    key_unresolved = bool(term.get("unresolved"))
    ideal_len = key.get("ideal_length")
    got_freq = result.final_state.get("resonator_frequency")
    got_power = result.final_state.get("optimal_power")

    out: dict[str, Any] = {
        "session": result.session_id, "qubit": result.qubit,
        "key_confidence": key.get("confidence"),
        "runs_consumed": result.runs_consumed,
        "ideal_length": ideal_len,
        "actual_operator_length": key.get("actual_length"),
        "unscoreable_proposals": result.unscoreable_proposals,
        "terminated_at": result.terminated_at,
        "key_terminates_at": term.get("at_run"),
        "sm_unresolved": result.unresolved,
        "key_unresolved": key_unresolved,
    }

    if key_unresolved:
        # the key says no trustworthy value existed: adopting one is the error
        out["frequency_verdict"] = ("correctly_abstained" if result.unresolved
                                    else "adopted_where_key_says_unresolved")
    elif not isinstance(want_freq, (int, float)):
        out["frequency_verdict"] = "unscoreable_key_has_no_value"
    elif not isinstance(got_freq, (int, float)):
        out["frequency_verdict"] = "missed_no_value_adopted"
    else:
        out["frequency_delta_hz"] = abs(got_freq - want_freq)
        out["frequency_verdict"] = ("match" if abs(got_freq - want_freq) <= FREQ_TOL_HZ
                                    else "wrong_value")

    if isinstance(want_power, (int, float)) and isinstance(got_power, (int, float)):
        out["power_delta_db"] = abs(got_power - want_power)
        out["power_verdict"] = ("match" if abs(got_power - want_power) <= POWER_TOL_DB
                                else "wrong_value")
    elif isinstance(want_power, (int, float)):
        out["power_verdict"] = "missed_no_value_adopted"
    else:
        out["power_verdict"] = "unscoreable_key_has_no_value"

    out["runs_to_first_value"] = result.runs_to_first_value
    out["revisions"] = len(result.revisions)
    fv = (result.first_value or {}).get("resonator_frequency")
    if isinstance(want_freq, (int, float)) and isinstance(fv, (int, float)):
        out["first_value_delta_hz"] = abs(fv - want_freq)
    if isinstance(ideal_len, (int, float)) and ideal_len > 0:
        out["length_vs_ideal"] = (result.runs_to_first_value or
                                  result.runs_consumed) - int(ideal_len)
    act = key.get("actual_length")
    if isinstance(act, (int, float)) and act > 0:
        # measured against the runs needed to REACH a value, not against the
        # whole watch-mode walk — otherwise the efficiency number is zero by
        # construction the moment re-verification is switched on
        out["runs_saved_vs_operator"] = int(act) - (result.runs_to_first_value
                                                    or result.runs_consumed)

    # Poisoned writes, checked FIELD BY FIELD. The key names a state path at
    # a specific run; the question is whether the replay took THAT field from
    # THAT run, not whether it happened to act on the same run at all.
    _FIELD_OF = {"f_01": "resonator_frequency",
                 "RF_frequency": "resonator_frequency",
                 "frequency_bare": "bare_resonator_frequency",
                 "amplitude": "optimal_power"}
    poisoned: dict[str, set[str]] = {}
    for pw in (key.get("poisoned_writes") or []):
        run = (pw.get("run") or "").split("_")[0]
        leaf = (pw.get("path") or "").rstrip("/").split("/")[-1]
        field = _FIELD_OF.get(leaf)
        if run and field:
            poisoned.setdefault(run, set()).add(field)
    reverted = {r for st in result.steps for r in st.reverts}
    adopted_poison = []
    for st in result.steps:
        want = poisoned.get(st.run_id.split("_")[0])
        if not want or st.action != "adopt":
            continue
        for field in sorted(want & set(st.adopted)):
            adopted_poison.append({"run": st.run_id, "field": field})
    out["poisoned_fields_in_key"] = sum(len(v) for v in poisoned.values())
    out["poison_reverted"] = len(reverted)
    out["poison_adopted"] = adopted_poison

    # per-step case agreement where the key names an expected case
    exp = {p.get("run", "").split("_")[0]: p.get("expected_case")
           for p in (key.get("ideal_path") or []) if p.get("run")}
    agree = total = 0
    for s in result.steps:
        want = exp.get(s.run_id.split("_")[0])
        if not want:
            continue
        total += 1
        if s.case and (s.case == want or s.case in str(want) or str(want) in (s.case or "")):
            agree += 1
    out["case_agreement"] = f"{agree}/{total}" if total else "n/a"
    return out
