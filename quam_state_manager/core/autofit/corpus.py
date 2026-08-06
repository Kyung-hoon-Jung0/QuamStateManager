"""Archive corpus index (docs/78 P0d, D-5).

The archives are the agent's evidence base: per family they hold the parameter
ranges this lab has ACTUALLY used (the honest bounds for the class-A action
space — never derive bounds from schema defaults, observed values leave them
far behind: ``num_shots=3`` vs default 100, flux ±2.5 V vs default ±0.5 V) and
the sweep steps that decide tier-B membership (docs/78 D-1: a target whose grid
is finer than its required precision may be written grid-exactly).

Pure JSON/stat work — no QM import, no h5 reads. Reads go through ``safe_io``
(a still-active experiment's writeback must never be blocked). The index is a
plain dict; callers persist it wherever they choose via :func:`save_index`.
No archive path is hardcoded here — roots always come from the caller.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

RUN_DIR_RE = re.compile(r"^#(\d+)_(.+)_(\d{6})$")

# Params that are execution plumbing, not physics knobs — excluded from ranges
# (they would pollute the agent's bound table with machine bookkeeping).
_NON_KNOB = {"load_data_id", "simulate", "simulation_duration_ns", "timeout",
             "qubits", "qubit_pairs", "use_waveform_report", "multiplexed"}

# family -> sweep-axis step rules, code-curated from the recorded parameter
# vocabulary (verified against real node.json 2026-08-06). Each rule computes
# the acquisition grid step from a run's params, or None when underivable.
# The flux vocabulary varies BY NODE GENERATION/FAMILY — three shapes exist in
# the real archives: min/max_flux_offset_in_v (resonator flux nodes),
# min_flux/max_flux (qubit-vs-coupler), and a centered flux_offset_span_in_v
# (qubit-vs-flux) — so the flux rule tries each in turn.
_FREQ = ("frequency", lambda p: _num(p.get("frequency_step_in_mhz")) * 1e6
         if _num(p.get("frequency_step_in_mhz")) is not None else None)


def _flux_step(p: dict):
    for lo, hi in (("min_flux_offset_in_v", "max_flux_offset_in_v"),
                   ("min_flux", "max_flux")):
        s = _span_step(p, lo, hi, "num_flux_points")
        if s is not None:
            return s
    span, n = _num(p.get("flux_offset_span_in_v")), _num(p.get("num_flux_points"))
    if span is not None and n is not None and n >= 2:
        return abs(span) / (n - 1)
    return None


_FLUX = ("flux_bias", _flux_step)
_POWER = ("power", lambda p: _span_step(p, "min_power_dbm", "max_power_dbm",
                                        "num_power_points"))
_AMP = ("amp_prefactor", lambda p: _num(p.get("amp_factor_step")))

STEP_RULES: dict[str, tuple] = {
    "resonator_spectroscopy": (_FREQ,),
    "resonator_spectroscopy_vs_power": (_FREQ, _POWER),
    "resonator_spectroscopy_vs_flux": (_FREQ, _FLUX),
    "resonator_spectroscopy_vs_coupler_flux": (_FREQ, _FLUX),
    "qubit_spectroscopy": (_FREQ,),
    "qubit_spectroscopy_vs_power": (_FREQ, _POWER),
    "qubit_spectroscopy_vs_flux": (_FREQ, _FLUX),
    "qubit_spectroscopy_vs_coupler_flux": (_FREQ, _FLUX),
    "power_rabi": (_AMP,),
}


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) \
        else None


def _span_step(p: dict, lo_key: str, hi_key: str, n_key: str):
    lo, hi, n = _num(p.get(lo_key)), _num(p.get(hi_key)), _num(p.get(n_key))
    if lo is None or hi is None or n is None or n < 2:
        return None
    return abs(hi - lo) / (n - 1)


# ---------------------------------------------------------------------------
# per-run indexing
# ---------------------------------------------------------------------------

def index_run(folder, *, with_generation: bool = True) -> dict | None:
    """One run folder -> its corpus record, or ``None`` if not a run.

    Family derivation reuses ``fit_audit`` (single choke point — the same
    normalization the replay uses, so corpus rows and replayability can't
    disagree about what a run IS).
    """
    from quam_state_manager.core import safe_io
    from quam_state_manager.core.fit_audit import family_for
    from quam_state_manager.generator.run_fit_audit import run_params

    folder = Path(folder)
    m = RUN_DIR_RE.match(folder.name)
    if not m:
        return None
    try:
        node = safe_io.read_json(folder / "node.json")
    except (OSError, ValueError):
        node = {}
    if not isinstance(node, dict):
        node = {}
    meta = node.get("metadata") or {}
    data = node.get("data") or {}
    name = meta.get("name") or m.group(2)
    # THE single unwrap, shared with the replay path — a private copy here would
    # drift and the agent's bound table would describe different values than the
    # fitter actually saw.
    model = run_params(node)

    rec = {
        "folder": str(folder),
        "run_id": int(m.group(1)),
        "name": name,
        "family": family_for(name),
        "run_start": meta.get("run_start"),
        "outcomes": data.get("outcomes") or {},
        "params": model if isinstance(model, dict) else {},
        "patches": len(node.get("patches") or []),
        "has_ds_raw": (folder / "ds_raw.h5").is_file(),
        "has_ds_fit": (folder / "ds_fit.h5").is_file(),
        "has_quam_state": (folder / "quam_state").is_dir(),
        "figures": sorted(p.name for p in folder.glob("*.png")),
        "generation": None,
    }
    if with_generation and rec["has_quam_state"]:
        from quam_state_manager.core.autofit import envmatrix
        rec["generation"] = envmatrix.generation_fingerprint(folder)
    return rec


def build_index(roots, *, with_generation: bool = True,
                families_only: bool = False, max_depth: int = 4) -> dict:
    """Find every ``#<id>_<name>_<hhmmss>`` run dir under each root.

    The layout is NOT fixed at ``root/<date>/#N``: real archives also nest as
    ``root/<chip>/<date>/#N`` (the sidebar tree v2 exists precisely because run
    trees have arbitrary real folder levels — docs/68). A hard-coded two-level
    walk indexes such a root to ZERO runs and says nothing, so descend up to
    ``max_depth`` levels and stop at each run dir (never inside one).

    ``families_only`` keeps only runs whose family is registered (the 9-scope);
    the full index keeps everything so coverage gaps stay visible.
    """
    runs: list[dict] = []
    for root in roots:
        root = Path(root)
        if not root.is_dir():
            logger.warning("corpus root missing: %s", root)
            continue
        found_here = 0
        stack = [(root, 0)]
        while stack:
            base, depth = stack.pop()
            try:
                children = sorted(d for d in base.iterdir() if d.is_dir())
            except OSError:
                continue
            for d in children:
                if RUN_DIR_RE.match(d.name):
                    rec = index_run(d, with_generation=with_generation)
                    if rec is None:
                        continue
                    found_here += 1
                    if families_only and rec["family"] is None:
                        continue
                    runs.append(rec)
                elif depth + 1 < max_depth:
                    stack.append((d, depth + 1))
        if not found_here:
            logger.warning("corpus root %s held no run folders within depth %d",
                           root, max_depth)

    by_family: dict[str, list[int]] = {}
    generations: dict[str, int] = {}
    for i, rec in enumerate(runs):
        if rec["family"]:
            by_family.setdefault(rec["family"], []).append(i)
        if rec["generation"]:
            generations[rec["generation"]] = generations.get(rec["generation"], 0) + 1
    return {"schema": "autofit-corpus/v1", "runs": runs,
            "by_family": by_family, "generations": generations}


# ---------------------------------------------------------------------------
# derived tables
# ---------------------------------------------------------------------------

def param_ranges(index: dict) -> dict:
    """Per family, the OBSERVED range of every recorded knob (docs/78 D-5.2).

    Numeric knobs -> {min, max, n}; non-numeric -> {values (≤12 shown), n}.
    Plumbing keys (``_NON_KNOB``) are excluded.
    """
    out: dict[str, dict] = {}
    runs = index.get("runs") or []
    for fam, idxs in (index.get("by_family") or {}).items():
        table: dict[str, dict] = {}
        for i in idxs:
            for k, v in (runs[i].get("params") or {}).items():
                if k in _NON_KNOB:
                    continue
                n = _num(v)
                slot = table.setdefault(k, {"min": None, "max": None, "n": 0,
                                            "values": []})
                slot["n"] += 1
                if n is not None:
                    slot["min"] = n if slot["min"] is None else min(slot["min"], n)
                    slot["max"] = n if slot["max"] is None else max(slot["max"], n)
                elif v is not None and not isinstance(v, (list, dict)):
                    if v not in slot["values"] and len(slot["values"]) < 12:
                        slot["values"].append(v)
        out[fam] = table
    return out


def sweep_steps(index: dict) -> dict:
    """Per family, per sweep axis: observed acquisition grid steps (docs/78
    D-1 tier-B input). ``{family: {axis: {min, median, max, n}}}``; an axis a
    family's rule can't derive is reported with ``n: 0`` — never silently
    dropped (a missing row would read as "no grid" instead of "unknown")."""
    out: dict[str, dict] = {}
    runs = index.get("runs") or []
    for fam, idxs in (index.get("by_family") or {}).items():
        rules = STEP_RULES.get(fam)
        if not rules:
            continue
        fam_out: dict[str, dict] = {}
        for axis, rule in rules:
            steps = []
            for i in idxs:
                s = rule(runs[i].get("params") or {})
                if s is not None and s > 0:
                    steps.append(s)
            steps.sort()
            fam_out[axis] = {
                "n": len(steps),
                "min": steps[0] if steps else None,
                "median": steps[len(steps) // 2] if steps else None,
                "max": steps[-1] if steps else None,
            }
        out[fam] = fam_out
    return out


# ---------------------------------------------------------------------------
# persistence
# ---------------------------------------------------------------------------

def save_index(index: dict, path) -> None:
    from quam_state_manager.core import safe_io
    safe_io.atomic_write_json(Path(path), index)


def load_index(path) -> dict | None:
    import json
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("schema") == "autofit-corpus/v1" \
        else None
