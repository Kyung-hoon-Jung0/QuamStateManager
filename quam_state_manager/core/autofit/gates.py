"""Autofit deterministic gate pipeline (docs/56 §2c) — G1..G5 per (run, target).

Deterministic code runs FIRST and is authoritative for rejection: an LLM
accept can never override a deterministic fail (docs/47 Phase-0 — code beats
every cheap LLM on faithful-channel families at $0). The pipeline returns
``pass | suspect(failure_mode) | fail(failure_mode)`` per target; the engine
routes ``suspect`` to the LLM auditor (when enabled) and maps the final
verdict to a decision.

Failure modes: ``node_failed | no_signal | wrong_peak | noisy | out_of_band |
drifted | unverifiable | feature_present_fit_failed``.

v2 (docs/56, LOOP_STUDY): a node-declared failure is no longer opaque — when
the family has a raw-data localizer, a claim-free PRESENCE probe over ds_raw
splits it into ``feature_present_fit_failed`` (the archive's #194 class: dip
clearly visible, fit died — prescription: refine the step) vs ``no_signal``
(genuinely empty window — prescription: the widen/drive/seed ladder), keeping
``node_failed`` only when the data itself is unavailable. On an empty window
the probe also derives a qualitative ``direction_hint`` from window-edge
evidence (a truncated feature tail) — it selects WHICH way a seed-shift rung
looks, never a number.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import numpy as np

from quam_state_manager.core.autofit import families as fam_mod
from quam_state_manager.core.autofit.families import Family, FeatureCheck

logger = logging.getLogger(__name__)

# feature significance: robust z of the extremum vs the trace, below which the
# window provably contains no feature (docs/47 — the NO_FEATURE gate LLMs lack)
_FEATURE_Z_MIN = 5.0
# mode="span" signal presence: spectral peak-to-median power ratio. Flat noise
# tops out around ~10–20 (max/median of χ²₂ periodogram bins); any resolved
# oscillation/decay lands at 10³+ — robust where a point-noise estimator reads
# a fast fringe's derivative as noise.
_SPECTRAL_RATIO_MIN = 50.0
_ERROR_RATIO_MAX = 0.25     # <key>_error / |<key>| above this ⇒ noisy
_HISTORY_Z_MAX = 6.0        # robust z vs param-history trend ⇒ drifted


def _spectral_floor(fc) -> float:
    """The presence floor for THIS family (docs/78 §27).

    One constant cannot serve both shapes. Span mode reduces a cube to its
    most-structured ROW: a 1-D oscillation puts all its power there, a 2-D arc
    spreads it over every row. Measured across the corpus, accepted runs bottom
    out at 18-79 for the 1-D families and at 4-6 for the 2-D ones, so the
    shared 50 rejected 122 of 185 accepted qubit-flux targets — two thirds of
    the good ones. Families that declare nothing keep the default.
    """
    v = getattr(fc, "spectral_min", None)
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool)         else _SPECTRAL_RATIO_MIN


def _z_floor(fc) -> float:
    """Per-family prominence-z floor for peak/dip modes (docs/127).

    The same §27 argument as ``_spectral_floor``: a significance floor is a
    claim about the lab's SNR, and one constant cannot serve every chip. On
    the lab-B corpus 98 of 318 neighbor-corroborated qubit-spectroscopy claims
    sat below the module default of 5 — a third of the confirmed-good fits
    read as "no signal". Families that declare nothing keep the default.
    """
    v = getattr(fc, "z_min", None)
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) \
        else _FEATURE_Z_MIN


@dataclass
class GateVerdict:
    target: str
    verdict: str                        # pass | suspect | fail
    failure_mode: str | None = None
    reasons: list[str] = field(default_factory=list)
    checks: dict[str, str] = field(default_factory=dict)   # gate -> ok/... (ledger)
    # v2 qualitative signals (never numbers): raw-data feature presence under
    # a failed fit, and the window-edge direction hint for a seed-shift rung
    feature_present: bool | None = None
    direction_hint: str | None = None   # left | right | None
    # two-stage looking (docs/78 §18). `vision` is the §1.3 terminator's answer
    # — clear | unclear | absent | overview_only | unavailable — and
    # `panel_kind` says whether the judge saw this target ALONE or on a shared
    # sheet. A report that cannot tell those apart would imply a dedicated look
    # that never happened.
    vision: str | None = None
    panel_kind: str | None = None       # panel | sheet
    # the verification triple this verdict is only valid inside (docs/78 D-13.3,
    # §17 B3) — see core.autofit.verification. Sixteen of these bands were
    # overturned by measurement in one session; a verdict that cannot name the
    # revision that produced it silently mixes with verdicts that mean
    # something else.
    context: dict | None = None

    def as_dict(self) -> dict:
        return {"target": self.target, "verdict": self.verdict,
                "failure_mode": self.failure_mode, "reasons": self.reasons,
                "checks": self.checks, "feature_present": self.feature_present,
                "direction_hint": self.direction_hint,
                "vision": self.vision, "panel_kind": self.panel_kind,
                "context": self.context}


def _attr(run, key, default=None):
    if isinstance(run, dict):
        return run.get(key, default)
    return getattr(run, key, default)


# ---------------------------------------------------------------------------
# G3 — raw-data feature cross-check
# ---------------------------------------------------------------------------

def _row_of(reader, handle, idx) -> np.ndarray:
    """One target's row. h5py slices lazily; the NetCDF adapter has already
    read the variable, so it is indexed after the fact."""
    try:
        return np.asarray(handle[idx], dtype=float)
    except TypeError:                       # not sliceable (the NetCDF handle)
        return np.asarray(reader.read(handle)[idx], dtype=float)


# The SAME physical trace under another recording convention (docs/127).
# Tried in declaration order when ``fc.var`` is absent from ds_raw — an
# equivalence, never a guess: each row names a rename verified against the
# generation's own analysis code.
#   I -> state          use_state_discrimination=True saves the fitted
#                       population trace as 'state' and writes no I/Q at all
#                       (423 lab-B targets sat unverifiable on this alone)
#   snr -> D            readout-freq-opt: this generation saves the |g>-|e>
#                       distance D — the very quantity its fit maximizes
#   state_target -> state_moving   coupler-node rename (target = moving qubit)
_VAR_EQUIVALENTS: dict[str, tuple[str, ...]] = {
    "I": ("state",),
    "snr": ("D",),
    "state_target": ("state_moving",),
}


def _read_target_trace(raw_path: Path, fc: FeatureCheck, target: str,
                       kind: str) -> tuple[np.ndarray, np.ndarray] | str:
    """Return (axis, y) for *target*'s row, or an error string.

    Routed through the ndview reader adapter (docs/67), NOT raw h5py: newer
    runner envs write NetCDF-classic under an ``.h5`` name, and h5py answers
    those with "file signature not found". This gate is the raw-data
    cross-check — the one thing that can tell a fit that missed the feature
    from one that found it — so a format it cannot open is not a degraded
    check, it is no check, silently, on every run from those envs.
    """
    from quam_state_manager.core.ndview import _h5_lock_for, _open_reader

    # Pair-kind cubes of the current coupler-node generation rename the
    # qubit_pair dim to plain "qubit" while keeping PAIR NAMES as its values
    # (the same rename run_fit_audit._derive_pairs handles) — so a pair family
    # falls back to the "qubit" coord, and the target-membership check below
    # still guarantees identity: a coord that doesn't carry this pair's name
    # is refused exactly as before.
    dim0s = ("qubit",) if kind == "qubits" else ("qubit_pair", "qubit")
    try:
        with _h5_lock_for(str(raw_path)), _open_reader(Path(raw_path)) as f:
            var = f.get(fc.var)
            if var is None:
                for alt in _VAR_EQUIVALENTS.get(fc.var, ()):
                    var = f.get(alt)
                    if var is not None:
                        break
            dim0 = dim0s[0]
            coord = None
            for d0 in dim0s:
                coord = f.read_coord(d0)
                if coord is not None:
                    dim0 = d0
                    break
            if var is None or coord is None:
                return f"var {fc.var!r} or coord {dim0s[0]!r} missing in ds_raw"
            names = [n.decode() if isinstance(n, bytes) else str(n)
                     for n in np.asarray(coord).tolist()]
            if target not in names:
                return f"target {target!r} not in {dim0} coord"
            idx = names.index(target)
            if var.ndim < 2:
                return f"var {fc.var!r} is {var.ndim}-D (need ≥2-D target×sweep)"
            y = _row_of(f, var, idx)
            if fc.mode == "span":
                # signal-presence only. Orientation-aware reduction: treat the
                # LONGEST axis as the sweep, then keep the most-structured
                # 1-D row across the remaining dims (a naive flatten would
                # interleave e.g. ramsey's ± detuning branches and read the
                # branch-to-branch jump as point noise).
                if y.ndim > 1:
                    sweep_ax = int(np.argmax(y.shape))
                    rows = np.moveaxis(y, sweep_ax, -1).reshape(-1, y.shape[sweep_ax])
                    y = rows[int(np.argmax(rows.var(axis=1)))]
                return np.arange(y.size, dtype=float), y
            if var.ndim != 2:
                return f"var {fc.var!r} is {var.ndim}-D (peak/dip needs 2-D)"
            ax_ds = f.get(fc.axis_var)
            if ax_ds is None:
                return f"axis {fc.axis_var!r} missing"
            axis = (_row_of(f, ax_ds, idx) if ax_ds.ndim == 2
                    else np.asarray(f.read(ax_ds), dtype=float))
            if axis.shape != y.shape:
                return "axis/trace shape mismatch"
            return axis, y
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return f"ds_raw unreadable: {exc}"


def _feature_check(raw_path: Path, fc: FeatureCheck, target: str, kind: str,
                   fit_entry: dict,
                   pre_update_value_of: Callable[[str], Any] | None,
                   ) -> tuple[str, str]:
    """Returns (status, detail): status ∈ ok | no_signal | wrong_peak |
    out_of_band | unverifiable."""
    got = _read_target_trace(raw_path, fc, target, kind)
    if isinstance(got, str):
        return "unverifiable", got
    axis, y = got

    if fc.mode == "span":
        # signal PRESENCE only (oscillation/decay families have no single
        # feature to localize): spectral peak vs the flat-noise periodogram.
        if y.size < 24:
            return "unverifiable", "trace too short for a span check"
        y0 = y - float(np.mean(y))
        psd = np.abs(np.fft.rfft(y0)) ** 2 / y0.size
        psd = psd[1:]                       # DC guard
        ratio = float(np.max(psd)) / (float(np.median(psd)) + 1e-30)
        floor = _spectral_floor(fc)
        if ratio < floor:
            return "no_signal", (f"trace carries no coherent structure (spectral "
                                 f"peak/median {ratio:.0f} < {floor:.0f})")
        return "ok", f"signal present (spectral peak/median {ratio:.0f})"

    claim = fit_entry.get(fc.claim_key)
    if not isinstance(claim, (int, float)) or isinstance(claim, bool) \
            or not math.isfinite(claim):
        return "unverifiable", f"claim {fc.claim_key!r} not numeric"

    if fc.axis_offset_path and pre_update_value_of is not None:
        path = fc.axis_offset_path.replace("{q}", target).replace("{pair}", target)
        center = pre_update_value_of(path)
        if not isinstance(center, (int, float)) or isinstance(center, bool):
            return "unverifiable", f"axis center at {path} unresolvable"
        axis = axis + float(center)

    # feature significance: extremum prominence over the POINT-noise floor
    # (adjacent-diff based — MAD of the trace itself collapses when the
    # feature is broad relative to the window)
    med = float(np.median(y))
    noise = float(np.median(np.abs(np.diff(y)))) * 1.4826 / math.sqrt(2) + 1e-30
    idx = int(np.argmax(y)) if fc.mode == "peak" else int(np.argmin(y))
    z = abs(float(y[idx]) - med) / noise
    zf = _z_floor(fc)
    if z < zf:
        return "no_signal", (f"no significant {fc.mode} in the swept window "
                             f"(prominence z={z:.1f} < {zf})")

    lo, hi = float(np.min(axis)), float(np.max(axis))
    if not (lo <= claim <= hi):
        return "out_of_band", (f"claimed {fc.claim_key}={claim:.6g} lies outside "
                               f"the swept window [{lo:.6g}, {hi:.6g}]")

    fwhm = fit_entry.get(fc.fwhm_key)
    if fc.tol_fwhm > 0 and isinstance(fwhm, (int, float)) \
            and not isinstance(fwhm, bool) and math.isfinite(fwhm) and fwhm > 0:
        tol = fc.tol_fwhm * float(fwhm)
    else:
        tol = fc.fallback_tol

    if z < _FEATURE_Z_MIN:
        # The three-zone contract (docs/127): a family that declares a lower
        # z_min buys a MIDDLE zone, not a lower localization bar. Between the
        # floors a global SEARCH is unreliable both ways — max-of-N on a flat
        # window already sits near z≈3.3, and dropping straight into the
        # claim-vs-argmax comparison turned 91 corroborated-good lab-B claims
        # into "wrong_peak" (the argmax was a noise spike). TESTING the
        # claim's own region carries no look-elsewhere penalty: smooth by the
        # feature's own width (point noise shrinks by √w) and read the
        # deviation within ±tol of the claim. SIGN-AGNOSTIC deliberately —
        # measured on the corroborated lab-B claims, the qubit feature in
        # |IQ| is a dip as often as a peak (median smoothed deviation −44σ
        # with mode="peak"), because only the node's ROTATED projection has a
        # guaranteed orientation. A noise window tops out at |z|≈3.6 under
        # the same statistic (control p99 = 3.18), so the no-signal
        # corruption stays a hard FAIL instead of a judge call.
        sel = (axis >= claim - tol) & (axis <= claim + tol)
        n_sel = int(sel.sum())
        if n_sel < 1:
            return "unverifiable", (f"weak {fc.mode} (z={z:.1f}) and no "
                                    f"samples within ±{tol:.3g} of the claim")
        df = abs(float(axis[1] - axis[0])) if axis.size > 1 else 1.0
        if isinstance(fwhm, (int, float)) and not isinstance(fwhm, bool) \
                and math.isfinite(fwhm) and fwhm > 0 and df > 0:
            w = max(1, int(round(float(fwhm) / df)))
        else:
            w = max(1, n_sel // 2)
        w = min(w, int(y.size))
        # The edge-pinned-CLAIM rule (docs/134 §2, re-derived on the real
        # traces): _edge_hint promoted from hint to verdict, middle zone
        # only. It fires when the CLAIM itself sits in the outer 15% of the
        # window AND the feature-direction smoothed curve rises monotonically
        # into that boundary — the claimed optimum is then the window's
        # boundary maximum, not a bracketed extremum, and the claim-region
        # test would silently accept it (a rising ramp's edge region always
        # shows a large smoothed deviation — three real corpus runs, one of
        # which the 2-bin _edge_hint bar missed). Details that all came from
        # measurement, not taste: OVERLAP-normalized smoothing, because
        # zero-padded 'same' suppresses the boundary and hid two truncated
        # ramps whose apex sat at samples 0 and 97; DIRECTIONAL (mode-signed),
        # because an opposite-sign structure at an edge says nothing about
        # the optimum and firing sign-agnostically caught a corroborated-good
        # broad feature parked near the other edge; the side bar is the
        # FAMILY floor with overall structure gated at the module floor,
        # because one real truncated ramp tops out at side-z 4.8 while its
        # full swing is 8.7. A same-shape run whose follow-up lands within
        # one feature width exists in the corpus (a false alarm by hindsight)
        # — a single run cannot tell it from the true catches, so the honest
        # single-run verdict for ALL of them is out_of_band, which rides the
        # widen/shift ladder and never writes: the follow-up decides.
        ci = int(np.argmin(np.abs(axis - claim)))
        edge_n = max(int(round(y.size * 0.15)), 1)
        if ci < edge_n or ci >= y.size - edge_n:
            kern = np.ones(w) / w
            overlap = np.convolve(np.ones(y.size), kern, mode="same")
            sm_n = np.convolve(y - med, kern, mode="same") / overlap
            dev = sm_n if fc.mode == "peak" else -sm_n
            ns = noise / math.sqrt(w)
            left = ci < edge_n
            zone = dev[:edge_n] if left else dev[y.size - edge_n:]
            j_side = int(np.argmax(zone))
            z_side = float(zone[j_side]) / ns
            z_bound = float(dev[0] if left else dev[-1]) / ns
            z_range = (float(np.max(dev)) - float(np.min(dev))) / ns
            if (z_side >= zf and z_bound >= 0.9 * z_side
                    and z_range >= _FEATURE_Z_MIN):
                side = "left" if left else "right"
                return "out_of_band", (
                    f"claim sits at the {side} window edge and the trace "
                    f"rises into that boundary without turning over "
                    f"(edge z={z_side:.1f}, full swing {z_range:.1f}) — the "
                    f"optimum is not bracketed by this window")
        smooth = np.convolve(y - med, np.ones(w) / w, mode="same")
        noise_s = noise / math.sqrt(w)
        z_at = float(np.max(np.abs(smooth[sel]))) / noise_s
        if z_at >= _FEATURE_Z_MIN:
            return "ok", (f"weak window (z={z:.1f}) but the claim region "
                          f"carries a resolved feature (|z_at|={z_at:.1f}, "
                          f"width {w} samples)")
        return "no_signal", (f"weak window (z={z:.1f}) and the claim region "
                             f"shows no feature (|z_at|={z_at:.1f} over "
                             f"{n_sel} samples)")

    feature_x = float(axis[idx])
    if abs(feature_x - claim) > tol:
        return "wrong_peak", (f"data {fc.mode} at {feature_x:.6g} but claim is "
                              f"{claim:.6g} (|Δ|={abs(feature_x - claim):.3g} "
                              f"> tol {tol:.3g})")
    return "ok", f"claim within {tol:.3g} of the data {fc.mode} (z={z:.1f})"


def _edge_hint(y: np.ndarray, mode: str) -> str | None:
    """Direction hint from window-edge evidence: when the trace's extremum
    sits in the OUTER 15% of the window with mild significance (a truncated
    feature tail poking in), the feature likely continues past that edge.
    Qualitative only — it picks which way a seed-shift looks.

    2-bin support: a genuine tail is a COHERENT elevation spanning adjacent
    bins, while a lone point-noise spike (whose extremum z runs ~3 on a
    300-bin trace — above any usable bar) has a flat neighbor. Requiring the
    extremum AND its best neighbor to average past the bar rejects the spike
    without losing real tails."""
    if y.size < 20:
        return None
    med = float(np.median(y))
    noise = float(np.median(np.abs(np.diff(y)))) * 1.4826 / math.sqrt(2) + 1e-30
    dev = (y - med) if mode == "peak" else (med - y)
    sm = (dev[:-1] + dev[1:]) / 2.0          # adjacent-pair support
    edge = max(int(round(sm.size * 0.15)), 1)
    left = float(np.max(sm[:edge]))
    right = float(np.max(sm[-edge:]))
    interior = float(np.max(sm[edge:-edge])) if sm.size > 2 * edge else 0.0
    best, side = (left, "left") if left >= right else (right, "right")
    if best / noise < 2.5:
        return None
    # dominance: the edge elevation must clearly beat the interior's best
    # smoothed excursion, or flat noise (whose smoothed max lands anywhere)
    # would hint a direction ~30% of the time
    if interior > 0 and best < 1.3 * interior:
        return None
    return side


def _presence_probe(raw_path: Path, fc: FeatureCheck, target: str, kind: str,
                    ) -> tuple[bool | None, str, str | None]:
    """Claim-free feature presence over ds_raw (for node-declared failures):
    (present, detail, direction_hint). ``present=None`` = unverifiable."""
    got = _read_target_trace(raw_path, fc, target, kind)
    if isinstance(got, str):
        return None, got, None
    _axis, y = got
    if fc.mode == "span":
        if y.size < 24:
            return None, "trace too short for a presence probe", None
        y0 = y - float(np.mean(y))
        psd = np.abs(np.fft.rfft(y0)) ** 2 / y0.size
        psd = psd[1:]
        ratio = float(np.max(psd)) / (float(np.median(psd)) + 1e-30)
        if ratio >= _spectral_floor(fc):
            return True, (f"coherent structure present despite the failed fit "
                          f"(spectral peak/median {ratio:.0f})"), None
        return False, (f"no coherent structure (spectral peak/median "
                       f"{ratio:.0f})"), None
    med = float(np.median(y))
    noise = float(np.median(np.abs(np.diff(y)))) * 1.4826 / math.sqrt(2) + 1e-30
    idx = int(np.argmax(y)) if fc.mode == "peak" else int(np.argmin(y))
    z = abs(float(y[idx]) - med) / noise
    # deliberately the MODULE floor, never the family's z_min: this probe has
    # no claim to test, so "feature present" below the search-significance bar
    # would send the adaptation ladder chasing a noise maximum (max-of-N on a
    # flat window sits around z≈3.3)
    if z >= _FEATURE_Z_MIN:
        return True, (f"a clear {fc.mode} is visible in the window "
                      f"(prominence z={z:.1f}) — the failure is the FIT, "
                      "not the experiment"), None
    return False, f"window is featureless (prominence z={z:.1f})", \
        _edge_hint(y, fc.mode)


# ---------------------------------------------------------------------------
# The pipeline
# ---------------------------------------------------------------------------

def evaluate_target(run, fam: Family, target: str, *,
                    current_value_of: Callable[[str], Any],
                    pre_update_value_of: Callable[[str], Any] | None = None,
                    history_points: list[float] | None = None) -> GateVerdict:
    """Run G1..G5 for one target of one finished run."""
    v = GateVerdict(target=target, verdict="pass")
    fits = _attr(run, "fit_results", None) or {}
    entry = fits.get(target)
    outcomes = _attr(run, "outcomes", None) or {}

    # --- G1: the node's own gate --------------------------------------------
    node_failed = (outcomes.get(target) == "failed"
                   or not isinstance(entry, dict)
                   or entry.get("success") is False)
    if node_failed:
        v.verdict, v.failure_mode = "fail", "node_failed"
        v.checks["G1_node_outcome"] = "fail"
        v.reasons.append("the node's own analysis marked this target failed")
        # v2: split the opaque node failure by what the RAW DATA says —
        # feature visible ⇒ the fit died, not the experiment (#194 class,
        # step-refine ladder); provably empty window ⇒ no_signal ladder;
        # data unavailable ⇒ stays node_failed (defer)
        if fam.feature_check is not None:
            folder = _attr(run, "folder_path", None)
            raw = Path(folder) / "ds_raw.h5" if folder else None
            if raw is not None and raw.exists():
                present, detail, hint = _presence_probe(
                    raw, fam.feature_check, target, fam.kind)
                v.feature_present = present
                if present is True:
                    v.failure_mode = "feature_present_fit_failed"
                    v.checks["G1_presence"] = "feature_present"
                    v.reasons.append(detail)
                elif present is False:
                    v.failure_mode = "no_signal"
                    v.direction_hint = hint
                    v.checks["G1_presence"] = "no_feature"
                    v.reasons.append(detail)
                    if hint:
                        v.reasons.append(f"edge evidence suggests the feature "
                                         f"lies to the {hint}")
                else:
                    v.checks["G1_presence"] = "unverifiable"
                    v.reasons.append(detail)
        return v
    v.checks["G1_node_outcome"] = "ok"

    suspects: list[tuple[str, str]] = []      # (failure_mode, reason)

    # --- G4 first for HARD physical bands (cheapest hard reject) ------------
    for pl in fam.plausibility:
        # through the family's one reader, so the band and the WRITE can never
        # disagree about the unit (docs/78 §22.4 — the T1 defect)
        val = fam_mod.fit_value(fam, entry, pl.key)
        if val is None:
            continue
        if (pl.lo is not None and val < pl.lo) or \
                (pl.hi is not None and val > pl.hi):
            v.verdict, v.failure_mode = "fail", "out_of_band"
            v.checks["G4_plausibility"] = "fail"
            v.reasons.append(f"{pl.key}={val:.6g} outside physical band "
                             f"[{pl.lo}, {pl.hi}]")
            return v
        if (pl.max_abs_jump is not None or pl.max_rel_jump is not None) \
                and pl.state_path:
            path = pl.state_path.replace("{q}", target).replace("{pair}", target)
            if "{operation}" in path:
                op = (_attr(run, "parameters", None) or {}).get("operation")
                path = path.replace("{operation}", str(op)) if op else None
            anchor = None
            if path:
                # PRE-update anchor: the node may already have applied this very
                # value to the state — comparing against the post-update state
                # would make every jump zero.
                for getter in (pre_update_value_of, current_value_of):
                    if getter is None:
                        continue
                    try:
                        anchor = getter(path)
                    except Exception:
                        anchor = None
                    if isinstance(anchor, (int, float)) \
                            and not isinstance(anchor, bool) \
                            and math.isfinite(anchor):
                        break
                    anchor = None
            if anchor is not None:
                jump = abs(val - anchor)
                if pl.max_abs_jump is not None and jump > pl.max_abs_jump:
                    suspects.append(("drifted",
                                     f"{pl.key} jumped {jump:.3g} "
                                     f"(> {pl.max_abs_jump:.3g}) vs pre-run state"))
                elif pl.max_rel_jump is not None and abs(anchor) > 0 \
                        and jump / abs(anchor) > pl.max_rel_jump:
                    suspects.append(("drifted",
                                     f"{pl.key} jumped ×{jump / abs(anchor):.1f} "
                                     f"(> ×{pl.max_rel_jump:.1f}) vs pre-run state"))
    v.checks.setdefault("G4_plausibility", "ok")

    # --- G3: raw-data feature cross-check (family-gated) --------------------
    if fam.feature_check is not None:
        folder = _attr(run, "folder_path", None)
        raw = Path(folder) / "ds_raw.h5" if folder else None
        if raw is None or not raw.exists():
            suspects.append(("unverifiable", "ds_raw.h5 missing — feature "
                                             "x-check impossible"))
            v.checks["G3_feature"] = "unverifiable"
        else:
            status, detail = _feature_check(raw, fam.feature_check, target,
                                            fam.kind, entry,
                                            pre_update_value_of)
            v.checks["G3_feature"] = status
            if status in ("no_signal", "wrong_peak", "out_of_band"):
                v.verdict, v.failure_mode = "fail", status
                v.reasons.append(detail)
                if status == "no_signal":
                    # empty window: derive the seed-shift direction hint from
                    # edge evidence (a truncated feature tail)
                    present, _d, hint = _presence_probe(
                        raw, fam.feature_check, target, fam.kind)
                    v.feature_present = present
                    v.direction_hint = hint
                    if hint:
                        v.reasons.append(f"edge evidence suggests the feature "
                                         f"lies to the {hint}")
                return v
            if status == "unverifiable":
                suspects.append(("unverifiable", detail))
            else:
                v.reasons.append(detail)

    # --- G2: the node's own fit metrics --------------------------------------
    g2 = "ok"
    for mg in fam.metric_gates:
        val = entry.get(mg.key)
        if not isinstance(val, (int, float)) or isinstance(val, bool) \
                or not math.isfinite(val):
            continue
        bad = (mg.min is not None and val < mg.min) or \
              (mg.max is not None and val > mg.max)
        if bad:
            g2 = "suspect"
            mode = {"r2": "noisy", "contrast": "no_signal",
                    "readout_fidelity": "noisy", "fwhm": "noisy"}.get(mg.key, "noisy")
            suspects.append((mode, f"{mg.key}={val:.4g} violates "
                                   f"[{mg.min}, {mg.max}] ({mg.reason})"))
    # cross-metric internal consistency (e.g. chevron cz_len vs 1/(2J))
    for check in fam.consistency_checks:
        try:
            # a check may ask for the run's own parameters — the sweep window
            # is knowable only from them, and a window claim cannot be judged
            # against a constant (docs/78 §26). One-argument checks are the
            # majority and keep working untouched.
            try:
                why = check(entry, _attr(run, "parameters", None) or {})
            except TypeError:
                why = check(entry)
        except Exception:  # noqa: BLE001
            why = None
        if why:
            g2 = "suspect"
            suspects.append(("wrong_peak", why))
    # generic error-bar ratio over EVERY <key>_error sibling the fit reports
    # (the headline key alone would miss e.g. ramsey's decay_error)
    for ek, err in entry.items():
        if not isinstance(ek, str) or not ek.endswith("_error"):
            continue
        base = entry.get(ek[: -len("_error")])
        if isinstance(err, (int, float)) and isinstance(base, (int, float)) \
                and not isinstance(err, bool) and not isinstance(base, bool) \
                and math.isfinite(err) and math.isfinite(base) and base != 0 \
                and abs(err / base) > _ERROR_RATIO_MAX:
            g2 = "suspect"
            suspects.append(("noisy", f"{ek}/{ek[:-6]} = {abs(err / base):.2f} "
                                      f"> {_ERROR_RATIO_MAX}"))
    v.checks["G2_metrics"] = g2

    # --- G5: history drift (optional; engine supplies trend points) ---------
    # The compared quantity is the family's HEADLINE value — reading the loop
    # variable left over from the metric-gate pass above would compare the last
    # metric (e.g. an r² of 0.99) against a flux-offset trend and report a
    # 600-σ drift on a perfectly good run. The gate had never been supplied with
    # points in production, so nothing had exercised it (docs/78 P2d).
    # the same reader again: the history trend comes from STATE, so an unscaled
    # fit value here would compare nanoseconds against a seconds series — the
    # very shape of the P2d bug this gate was fixed for
    hist_val = fam_mod.fit_value(fam, entry, fam.value_key)
    if history_points and len(history_points) >= 3 and hist_val is not None:
        val = hist_val
        pts = np.asarray(history_points, dtype=float)
        med = float(np.median(pts))
        mad = float(np.median(np.abs(pts - med))) * 1.4826
        if mad > 0:
            z = abs(float(val) - med) / mad
            v.checks["G5_history"] = "ok" if z <= _HISTORY_Z_MAX else "suspect"
            if z > _HISTORY_Z_MAX:
                suspects.append(("drifted",
                                 f"{fam.value_key}={val:.6g} is {z:.1f} robust-σ "
                                 f"off its own history (median {med:.6g})"))

    if suspects:
        v.verdict = "suspect"
        v.failure_mode = suspects[0][0]
        v.reasons.extend(r for _, r in suspects)
    return v


def evaluate_run(run, fam: Family, targets: list[str], *,
                 current_value_of: Callable[[str], Any],
                 pre_update_value_of: Callable[[str], Any] | None = None,
                 history_points_of: Callable[[str], list[float] | None] | None = None,
                 ) -> dict[str, GateVerdict]:
    """The pipeline over every target. Never raises — an unexpected error
    yields a ``suspect(unverifiable)`` verdict for that target (fail-safe:
    unverifiable is never silently accepted)."""
    out: dict[str, GateVerdict] = {}
    for t in targets:
        try:
            hp = history_points_of(t) if history_points_of else None
            out[t] = evaluate_target(run, fam, t,
                                     current_value_of=current_value_of,
                                     pre_update_value_of=pre_update_value_of,
                                     history_points=hp)
        except Exception as exc:  # noqa: BLE001 — gate crash must not kill a plan
            logger.exception("gate pipeline crashed for %s", t)
            out[t] = GateVerdict(target=t, verdict="suspect",
                                 failure_mode="unverifiable",
                                 reasons=[f"gate pipeline error: {exc}"])
    return out
