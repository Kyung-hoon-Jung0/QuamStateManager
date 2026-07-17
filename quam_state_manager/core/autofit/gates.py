"""Autofit deterministic gate pipeline (docs/56 §2c) — G1..G5 per (run, target).

Deterministic code runs FIRST and is authoritative for rejection: an LLM
accept can never override a deterministic fail (docs/47 Phase-0 — code beats
every cheap LLM on faithful-channel families at $0). The pipeline returns
``pass | suspect(failure_mode) | fail(failure_mode)`` per target; the engine
routes ``suspect`` to the LLM auditor (when enabled) and maps the final
verdict to a decision.

Failure modes: ``node_failed | no_signal | wrong_peak | noisy | out_of_band |
drifted | unverifiable``.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import numpy as np

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


@dataclass
class GateVerdict:
    target: str
    verdict: str                        # pass | suspect | fail
    failure_mode: str | None = None
    reasons: list[str] = field(default_factory=list)
    checks: dict[str, str] = field(default_factory=dict)   # gate -> ok/... (ledger)

    def as_dict(self) -> dict:
        return {"target": self.target, "verdict": self.verdict,
                "failure_mode": self.failure_mode, "reasons": self.reasons,
                "checks": self.checks}


def _attr(run, key, default=None):
    if isinstance(run, dict):
        return run.get(key, default)
    return getattr(run, key, default)


# ---------------------------------------------------------------------------
# G3 — raw-data feature cross-check
# ---------------------------------------------------------------------------

def _read_target_trace(raw_path: Path, fc: FeatureCheck, target: str,
                       kind: str) -> tuple[np.ndarray, np.ndarray] | str:
    """Return (axis, y) for *target*'s row, or an error string."""
    import h5py

    dim0 = "qubit" if kind == "qubits" else "qubit_pair"
    try:
        with h5py.File(raw_path, "r") as f:
            if fc.var not in f or dim0 not in f:
                return f"var {fc.var!r} or coord {dim0!r} missing in ds_raw"
            names = [n.decode() if isinstance(n, bytes) else str(n)
                     for n in f[dim0][()]]
            if target not in names:
                return f"target {target!r} not in {dim0} coord"
            idx = names.index(target)
            var = f[fc.var]
            if var.ndim < 2:
                return f"var {fc.var!r} is {var.ndim}-D (need ≥2-D target×sweep)"
            y = np.asarray(var[idx], dtype=float)
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
            if fc.axis_var not in f:
                return f"axis {fc.axis_var!r} missing"
            ax_ds = f[fc.axis_var]
            axis = np.asarray(ax_ds[idx] if ax_ds.ndim == 2 else ax_ds[()],
                              dtype=float)
            if axis.shape != y.shape:
                return "axis/trace shape mismatch"
            return axis, y
    except (OSError, ValueError, KeyError) as exc:
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
        if ratio < _SPECTRAL_RATIO_MIN:
            return "no_signal", (f"trace carries no coherent structure (spectral "
                                 f"peak/median {ratio:.0f} < {_SPECTRAL_RATIO_MIN:.0f})")
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
    if z < _FEATURE_Z_MIN:
        return "no_signal", (f"no significant {fc.mode} in the swept window "
                             f"(prominence z={z:.1f} < {_FEATURE_Z_MIN})")

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
    feature_x = float(axis[idx])
    if abs(feature_x - claim) > tol:
        return "wrong_peak", (f"data {fc.mode} at {feature_x:.6g} but claim is "
                              f"{claim:.6g} (|Δ|={abs(feature_x - claim):.3g} "
                              f"> tol {tol:.3g})")
    return "ok", f"claim within {tol:.3g} of the data {fc.mode} (z={z:.1f})"


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
        return v
    v.checks["G1_node_outcome"] = "ok"

    suspects: list[tuple[str, str]] = []      # (failure_mode, reason)

    # --- G4 first for HARD physical bands (cheapest hard reject) ------------
    for pl in fam.plausibility:
        val = entry.get(pl.key)
        if not isinstance(val, (int, float)) or isinstance(val, bool) \
                or not math.isfinite(val):
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
    if history_points and len(history_points) >= 3 \
            and isinstance(val, (int, float)) and not isinstance(val, bool):
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
